"""API REST de PepiCloud (prototipo del Taller Práctico 2)."""
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import almacenamiento as alm
from . import config, documentos
from . import db
from .db import conexion, evento

_detener = threading.Event()
_problemas: list[str] = []


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    _problemas.extend(config.problemas_de_secretos())
    if _problemas:
        print("ERROR DE CONFIGURACIÓN:", "; ".join(_problemas), flush=True)
        yield                                 # sigue vivo para informar el problema en /api/estado
        return
    db.iniciar()
    for _ in range(30):                       # espera a PostgreSQL y prepara los buckets
        try:
            with conexion() as con:
                con.execute("SELECT 1")
            break
        except Exception:
            time.sleep(2)
    for nodo in alm.urls():
        try:
            alm.asegurar_bucket(nodo)
        except Exception:
            pass                              # la tarea de reparación lo intentará de nuevo
    documentos.iniciar_tareas(_detener)
    evento("inicio", "Backend iniciado")
    yield
    _detener.set()
    db.cerrar()


app = FastAPI(title="PepiCloud · Prototipo", lifespan=ciclo_de_vida)


@app.exception_handler(RuntimeError)
def _error_configuracion(_, e: RuntimeError):
    return JSONResponse(status_code=503, content={"detail": str(e)})


@app.exception_handler(documentos.ErrorDocumento)
def _error_documento(_, e: documentos.ErrorDocumento):
    return JSONResponse(status_code=e.estado_http, content={"detail": e.mensaje})


# ---------------------------------------------------------------- estado general

@app.get("/api/estado")
def estado():
    if _problemas:
        return {"nodos": {n: False for n in alm.urls()}, "base_datos": False,
                "error": "Faltan secretos o no se pueden leer: " + "; ".join(_problemas) +
                         ". Ejecute iniciar.ps1 desde la carpeta del proyecto."}
    nodos = {n: alm.disponible(n) for n in alm.urls()}
    resultado = {"nodos": nodos, "base_datos": False, "retencion_minutos": config.RETENCION_MINUTOS,
                 "intervalo_respaldo_seg": config.INTERVALO_RESPALDO_DOCUMENTAL_SEG,
                 "intervalo_reparacion_seg": config.INTERVALO_REPARACION_SEG}
    try:
        with conexion() as con:
            resultado["base_datos"] = True
            resultado["personas"] = con.execute("SELECT count(*) AS n FROM personas").fetchone()["n"]
            resultado["documentos"] = con.execute(
                "SELECT count(*) AS n FROM documentos WHERE eliminado_en IS NULL").fetchone()["n"]
            filas = con.execute(
                """SELECT c.ubicacion, count(*) AS n FROM copias_documento c
                   JOIN documentos d ON d.id = c.documento_id
                   WHERE c.estado = 'ok' AND d.eliminado_en IS NULL GROUP BY c.ubicacion""").fetchall()
            resultado["copias_ok"] = {f["ubicacion"]: f["n"] for f in filas}
    except Exception as e:
        resultado["error"] = f"No hay conexión con PostgreSQL: {type(e).__name__}: {e}"
    return resultado


@app.get("/api/eventos")
def eventos(limite: int = 60):
    with conexion() as con:
        return con.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT %s", (min(limite, 500),)).fetchall()


@app.post("/api/tareas/respaldo-documental")
def ejecutar_respaldo():
    return documentos.respaldo_documental()


@app.post("/api/tareas/reparar")
def ejecutar_reparacion():
    return documentos.reparar()


# ---------------------------------------------------------------- personas (CRUD)

class Persona(BaseModel):
    tipo_identificacion: str = Field("CC", max_length=5)
    numero_identificacion: str = Field(..., min_length=3, max_length=20)
    nombres: str = Field(..., min_length=1, max_length=100)
    apellidos: str = Field(..., min_length=1, max_length=100)
    fecha_nacimiento: date | None = None
    sexo: str | None = Field(None, pattern="^[FMO]$")
    telefono: str | None = Field(None, max_length=30)
    correo: str | None = Field(None, max_length=120)


CAMPOS = list(Persona.model_fields)


@app.get("/api/personas")
def listar_personas(buscar: str = ""):
    patron = f"%{buscar.strip()}%"
    with conexion() as con:
        return con.execute(
            """SELECT p.*, (SELECT count(*) FROM documentos d
                            WHERE d.persona_id = p.id AND d.eliminado_en IS NULL) AS documentos
               FROM personas p
               WHERE p.numero_identificacion ILIKE %s OR p.nombres ILIKE %s OR p.apellidos ILIKE %s
               ORDER BY p.id DESC LIMIT 500""", (patron, patron, patron)).fetchall()


@app.get("/api/personas/{persona_id}")
def obtener_persona(persona_id: int):
    with conexion() as con:
        p = con.execute("SELECT * FROM personas WHERE id = %s", (persona_id,)).fetchone()
    if p is None:
        raise HTTPException(404, "Persona no encontrada")
    return p


def _duplicada(e: Exception) -> bool:
    return "duplicate key" in str(e)


@app.post("/api/personas", status_code=201)
def crear_persona(p: Persona):
    try:
        with conexion() as con:
            fila = con.execute(
                f"INSERT INTO personas ({', '.join(CAMPOS)}) VALUES ({', '.join(['%s'] * len(CAMPOS))}) RETURNING *",
                [getattr(p, c) for c in CAMPOS]).fetchone()
            evento("persona", f"Persona creada: {p.tipo_identificacion} {p.numero_identificacion}", con=con)
            return fila
    except Exception as e:
        if _duplicada(e):
            raise HTTPException(409, "Ya existe una persona con esa identificación")
        raise


@app.put("/api/personas/{persona_id}")
def actualizar_persona(persona_id: int, p: Persona):
    try:
        with conexion() as con:
            fila = con.execute(
                f"UPDATE personas SET {', '.join(f'{c} = %s' for c in CAMPOS)}, actualizado_en = now() "
                "WHERE id = %s RETURNING *", [getattr(p, c) for c in CAMPOS] + [persona_id]).fetchone()
    except Exception as e:
        if _duplicada(e):
            raise HTTPException(409, "Ya existe una persona con esa identificación")
        raise
    if fila is None:
        raise HTTPException(404, "Persona no encontrada")
    return fila


@app.delete("/api/personas/{persona_id}", status_code=204)
def eliminar_persona(persona_id: int):
    with conexion() as con:
        if con.execute("SELECT 1 FROM documentos WHERE persona_id = %s", (persona_id,)).fetchone():
            raise HTTPException(409, "La persona tiene documentos clínicos asociados y no se puede eliminar")
        if con.execute("DELETE FROM personas WHERE id = %s", (persona_id,)).rowcount == 0:
            raise HTTPException(404, "Persona no encontrada")
    return Response(status_code=204)


# ---------------------------------------------------------------- documentos

@app.get("/api/tipos-documento")
def tipos_documento():
    with conexion() as con:
        return con.execute("SELECT * FROM tipos_documento ORDER BY nombre").fetchall()


@app.get("/api/personas/{persona_id}/documentos")
def documentos_de_persona(persona_id: int):
    with conexion() as con:
        return con.execute(
            """SELECT d.id, d.titulo, d.descripcion, d.fecha_documento, d.nombre_archivo, d.tipo_contenido,
                      d.tamano_bytes, d.sha256, d.clave_objeto, d.retencion_hasta, d.creado_en,
                      t.nombre AS tipo, now() < d.retencion_hasta AS protegido,
                      COALESCE((SELECT json_object_agg(c.ubicacion, c.estado) FROM copias_documento c
                                WHERE c.documento_id = d.id), '{}') AS copias
               FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_documento_id
               WHERE d.persona_id = %s AND d.eliminado_en IS NULL
               ORDER BY d.creado_en DESC""", (persona_id,)).fetchall()


@app.post("/api/personas/{persona_id}/documentos", status_code=201)
async def cargar_documento(persona_id: int, tipo_documento_id: int = Form(...), titulo: str = Form(...),
                           descripcion: str | None = Form(None), fecha_documento: date | None = Form(None),
                           archivo: UploadFile = File(...)):
    datos = await archivo.read()
    return documentos.subir(persona_id, tipo_documento_id, titulo.strip(), descripcion, fecha_documento,
                            archivo.filename or "documento", archivo.content_type or "application/octet-stream",
                            datos)


@app.get("/api/documentos/{documento_id}/descargar")
def descargar(documento_id: str, preferir: str | None = None, ver: bool = False):
    doc, datos, ubicacion, fallos = documentos.descargar(documento_id, preferir)
    disposicion = "inline" if ver else "attachment"
    return Response(content=datos, media_type=doc["tipo_contenido"], headers={
        "Content-Disposition": f"{disposicion}; filename*=UTF-8''{quote(doc['nombre_archivo'])}",
        "X-Copia-Usada": ubicacion,
        "X-Fallos-Previos": quote("; ".join(fallos)),
        "X-Integridad": "sha256-verificado",
        "Access-Control-Expose-Headers": "X-Copia-Usada, X-Fallos-Previos, X-Integridad"})


@app.get("/api/documentos/{documento_id}/copias")
def copias(documento_id: str):
    return documentos.estado_copias(documento_id)


@app.get("/api/documentos/{documento_id}/crudo")
def crudo(documento_id: str, ubicacion: str = "nodo-a"):
    if ubicacion not in alm.urls():
        raise HTTPException(400, "Ubicación no válida")
    return documentos.objeto_crudo(documento_id, ubicacion)


@app.delete("/api/documentos/{documento_id}")
def eliminar_documento(documento_id: str):
    return documentos.eliminar(documento_id)


@app.put("/api/documentos/{documento_id}/archivo")
def modificar_documento(documento_id: str):
    return documentos.intentar_modificar(documento_id)
