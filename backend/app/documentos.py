"""Lógica de los documentos: carga con 2 copias activo-activo, lectura con conmutación entre copias,
backup documental incremental, reparación de copias perdidas y eliminación con retención."""
import hashlib
import random
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import almacenamiento as alm
from . import cifrado, config
from .db import conexion, evento

OPERATIVOS = list(config.NODOS_OPERATIVOS)          # ["nodo-a", "nodo-b"]
RESPALDO = config.NODO_RESPALDO[0]                   # "respaldo"
_candado_tareas = threading.Lock()


class ErrorDocumento(Exception):
    def __init__(self, estado_http: int, mensaje: str):
        super().__init__(mensaje)
        self.estado_http = estado_http
        self.mensaje = mensaje


def clave_objeto(persona_id: int, codigo_tipo: str, documento_id: str) -> str:
    """Organización del bucket: una "carpeta" (prefijo) por paciente y dentro una por tipo de documento.
    Se usa el id interno del paciente, no su cédula, para no exponer datos personales en las claves."""
    return f"pacientes/{persona_id}/{codigo_tipo}/{documento_id}"


def _metadatos(doc: dict) -> dict:
    # Con estos metadatos cada objeto se puede identificar y descifrar aunque se pierda la BD
    return {"documento-id": str(doc["id"]), "persona-id": str(doc["persona_id"]),
            "sha256": doc["sha256"], "clave-datos-cifrada": doc["clave_datos_cifrada"]}


def _obtener_documento(con, documento_id: str) -> dict:
    doc = con.execute(
        """SELECT d.*, t.codigo AS codigo_tipo, t.nombre AS nombre_tipo
           FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_documento_id
           WHERE d.id = %s""", (documento_id,)).fetchone()
    if doc is None:
        raise ErrorDocumento(404, "Documento no encontrado")
    return doc


def _copias(con, documento_id) -> dict[str, dict]:
    filas = con.execute("SELECT * FROM copias_documento WHERE documento_id = %s", (documento_id,)).fetchall()
    return {f["ubicacion"]: f for f in filas}


def _guardar_copia(con, documento_id, ubicacion: str, version_id: str | None, estado: str, clave: str) -> None:
    con.execute(
        """INSERT INTO copias_documento (documento_id, ubicacion, rol, bucket, clave_objeto, version_id, estado)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (documento_id, ubicacion) DO UPDATE
             SET version_id = EXCLUDED.version_id, estado = EXCLUDED.estado, actualizado_en = now()""",
        (documento_id, ubicacion, "respaldo" if ubicacion == RESPALDO else "operativa",
         alm.bucket_de(ubicacion), clave, version_id, estado))


# ---------------------------------------------------------------- carga

def subir(persona_id: int, tipo_documento_id: int, titulo: str, descripcion: str | None,
          fecha_documento, nombre_archivo: str, tipo_contenido: str, datos: bytes) -> dict:
    if not datos:
        raise ErrorDocumento(400, "El archivo está vacío")
    if len(datos) > config.TAMANO_MAXIMO_MB * 1_000_000:
        raise ErrorDocumento(413, f"El archivo supera {config.TAMANO_MAXIMO_MB} MB")

    with conexion() as con:
        if con.execute("SELECT 1 FROM personas WHERE id = %s", (persona_id,)).fetchone() is None:
            raise ErrorDocumento(404, "La persona no existe")
        tipo = con.execute("SELECT codigo FROM tipos_documento WHERE id = %s", (tipo_documento_id,)).fetchone()
        if tipo is None:
            raise ErrorDocumento(400, "Tipo de documento no válido")

    documento_id = str(uuid.uuid4())
    clave = clave_objeto(persona_id, tipo["codigo"], documento_id)
    blob, clave_cifrada = cifrado.cifrar(datos, documento_id)
    retencion_hasta = (datetime.now(timezone.utc) + timedelta(minutes=config.RETENCION_MINUTOS)).replace(microsecond=0)
    doc = {"id": documento_id, "persona_id": persona_id, "sha256": hashlib.sha256(datos).hexdigest(),
           "clave_datos_cifrada": clave_cifrada}

    # Activo-activo: la misma copia se escribe en los dos servidores de objetos al mismo tiempo
    def escribir(nodo):
        try:
            alm.asegurar_bucket(nodo)
            return nodo, alm.guardar(nodo, clave, blob, retencion_hasta, _metadatos(doc)), None
        except Exception as e:  # nodo caído o error de escritura
            return nodo, None, e

    with ThreadPoolExecutor(max_workers=2) as ex:
        resultados = list(ex.map(escribir, OPERATIVOS))
    escritas = [n for n, v, _ in resultados if v]
    if not escritas:
        raise ErrorDocumento(503, "Ningún servidor de almacenamiento está disponible; el documento no se guardó")

    with conexion() as con, con.transaction():
        con.execute(
            """INSERT INTO documentos (id, persona_id, tipo_documento_id, titulo, descripcion, fecha_documento,
                 nombre_archivo, tipo_contenido, tamano_bytes, sha256, clave_objeto, clave_datos_cifrada,
                 retencion_hasta)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (documento_id, persona_id, tipo_documento_id, titulo, descripcion, fecha_documento, nombre_archivo,
             tipo_contenido, len(datos), doc["sha256"], clave, clave_cifrada, retencion_hasta))
        for nodo, version, _ in resultados:
            _guardar_copia(con, documento_id, nodo, version, "ok" if version else "pendiente", clave)
        evento("carga", f"Documento cifrado y guardado en {', '.join(escritas)} "
                        f"(clave {clave}); protegido hasta {retencion_hasta.astimezone():%H:%M:%S}", documento_id, con)
        for nodo, _, err in resultados:
            if err is not None:
                evento("copia-pendiente", f"{nodo} no respondió; la reparación creará esta copia cuando vuelva",
                       documento_id, con)
    return {"id": documento_id, "clave_objeto": clave, "copias": {n: bool(v) for n, v, _ in resultados},
            "retencion_hasta": retencion_hasta.isoformat()}


# ---------------------------------------------------------------- lectura

def _leer_verificado(ubicacion: str, doc: dict, copia: dict) -> tuple[bytes, bytes]:
    """Lee una copia y comprueba que se descifra y que su SHA-256 coincide con el original."""
    blob = alm.leer(ubicacion, copia["clave_objeto"], copia["version_id"])
    datos = cifrado.descifrar(blob, doc["clave_datos_cifrada"], str(doc["id"]))
    if hashlib.sha256(datos).hexdigest() != doc["sha256"]:
        raise ValueError("La huella SHA-256 no coincide")
    return blob, datos


def _orden_lectura(preferir: str | None) -> list[str]:
    operativos = OPERATIVOS[:]
    random.shuffle(operativos)                       # activo-activo: se reparte la lectura entre las 2 copias
    if preferir in operativos:
        operativos.remove(preferir)
        operativos.insert(0, preferir)
    orden = operativos + [RESPALDO]                  # el backup solo se usa si fallan las 2 copias operativas
    if preferir == RESPALDO:
        orden = [RESPALDO]
    return orden


def descargar(documento_id: str, preferir: str | None = None) -> tuple[dict, bytes, str, list[str]]:
    with conexion() as con:
        doc = _obtener_documento(con, documento_id)
        if doc["eliminado_en"] is not None:
            raise ErrorDocumento(410, "El documento fue eliminado")
        copias = _copias(con, documento_id)

    fallos = []
    for ubicacion in _orden_lectura(preferir):
        copia = copias.get(ubicacion)
        if copia is None or copia["estado"] != "ok":
            fallos.append(f"{ubicacion}: sin copia registrada")
            continue
        try:
            _, datos = _leer_verificado(ubicacion, doc, copia)
        except alm.NodoNoDisponible:
            fallos.append(f"{ubicacion}: servidor no disponible")
            continue
        except Exception as e:
            fallos.append(f"{ubicacion}: {type(e).__name__}")
            continue
        if fallos:
            evento("recuperacion", f"Descarga servida desde {ubicacion}. Fallaron antes: {'; '.join(fallos)}",
                   documento_id)
        return doc, datos, ubicacion, fallos
    evento("error-lectura", f"No se pudo leer ninguna copia: {'; '.join(fallos)}", documento_id)
    raise ErrorDocumento(503, "No hay ninguna copia disponible: " + "; ".join(fallos))


def objeto_crudo(documento_id: str, ubicacion: str) -> dict:
    """Lo que realmente está guardado en el almacenamiento (evidencia del cifrado en reposo)."""
    with conexion() as con:
        doc = _obtener_documento(con, documento_id)
        copia = _copias(con, documento_id).get(ubicacion)
    if copia is None:
        raise ErrorDocumento(404, f"No hay copia en {ubicacion}")
    try:
        blob = alm.leer(ubicacion, copia["clave_objeto"], copia["version_id"])
    except alm.NodoNoDisponible:
        raise ErrorDocumento(503, f"{ubicacion} no está disponible")
    muestra = blob[:384]
    return {"ubicacion": ubicacion, "bucket": copia["bucket"], "clave_objeto": copia["clave_objeto"],
            "version_id": copia["version_id"], "tamano_original": doc["tamano_bytes"], "tamano_guardado": len(blob),
            "hex": muestra.hex(" "),
            "como_texto": "".join(chr(b) if 32 <= b < 127 else "·" for b in muestra)}


def estado_copias(documento_id: str) -> list[dict]:
    with conexion() as con:
        doc = _obtener_documento(con, documento_id)
        copias = _copias(con, documento_id)
    salida = []
    for ubicacion in OPERATIVOS + [RESPALDO]:
        copia = copias.get(ubicacion)
        fila = {"ubicacion": ubicacion, "rol": "respaldo" if ubicacion == RESPALDO else "operativa",
                "registrada": copia is not None, "estado_bd": copia["estado"] if copia else None,
                "bucket": alm.bucket_de(ubicacion), "clave_objeto": doc["clave_objeto"],
                "version_id": copia["version_id"] if copia else None}
        try:
            fila["nodo_disponible"] = alm.disponible(ubicacion)
            if fila["nodo_disponible"] and copia and copia["version_id"]:
                fila["existe"] = alm.existe(ubicacion, copia["clave_objeto"], copia["version_id"])
                fila["retencion"] = alm.retencion(ubicacion, copia["clave_objeto"], copia["version_id"])
        except alm.NodoNoDisponible:
            fila["nodo_disponible"] = False
        salida.append(fila)
    return salida


# ---------------------------------------------------------------- eliminación y modificación

def eliminar(documento_id: str) -> dict:
    with conexion() as con:
        doc = _obtener_documento(con, documento_id)
        if doc["eliminado_en"] is not None:
            raise ErrorDocumento(410, "El documento ya fue eliminado")
        copias = _copias(con, documento_id)

    caidos = [n for n in OPERATIVOS if not alm.disponible(n)]
    if caidos:
        raise ErrorDocumento(503, f"No se puede eliminar mientras {', '.join(caidos)} esté caído")

    # No se revisa la fecha en la aplicación: se le pide el borrado al almacenamiento, que es quien
    # hace cumplir la retención (Object Lock COMPLIANCE), incluso para un administrador.
    for nodo in OPERATIVOS:
        copia = copias.get(nodo)
        if not copia or copia["estado"] != "ok":
            continue
        try:
            alm.eliminar_version(nodo, copia["clave_objeto"], copia["version_id"])
        except alm.ObjetoProtegido:
            evento("eliminacion-rechazada",
                   f"{nodo} rechazó el borrado (Access Denied por Object Lock). "
                   f"Protegido hasta {doc['retencion_hasta']:%Y-%m-%d %H:%M:%S %Z}", documento_id)
            raise ErrorDocumento(423, f"El almacenamiento ({nodo}) rechazó la eliminación: el documento está "
                                      f"protegido hasta {doc['retencion_hasta'].isoformat()}")

    with conexion() as con, con.transaction():
        con.execute("UPDATE documentos SET eliminado_en = now() WHERE id = %s", (documento_id,))
        con.execute("""UPDATE copias_documento SET estado = 'eliminada', actualizado_en = now()
                       WHERE documento_id = %s AND rol = 'operativa'""", (documento_id,))
        evento("eliminacion", "Copias operativas eliminadas (retención cumplida). El backup documental "
                              "se conserva según la política de respaldo", documento_id, con)
    return {"eliminado": True}


def intentar_modificar(documento_id: str) -> dict:
    """Los documentos clínicos no se modifican. Durante la retención se le pide al almacenamiento
    que quite la versión protegida para reemplazarla y que acorte la retención: ambos se rechazan."""
    with conexion() as con:
        doc = _obtener_documento(con, documento_id)
        copias = _copias(con, documento_id)
    if doc["eliminado_en"] is not None:
        raise ErrorDocumento(410, "El documento fue eliminado")
    copia = next((copias[n] for n in OPERATIVOS if n in copias and copias[n]["estado"] == "ok"), None)
    if copia is None:
        raise ErrorDocumento(503, "No hay copias operativas para comprobar la protección")
    if doc["retencion_hasta"] <= datetime.now(timezone.utc):
        raise ErrorDocumento(409, "Retención cumplida. Por política los documentos clínicos no se modifican: "
                                  "si hay un error se carga un documento nuevo")
    respuestas = []
    for nombre, accion in (("reemplazar la versión protegida", alm.eliminar_version),
                           ("acortar la retención", alm.acortar_retencion)):
        try:
            accion(copia["ubicacion"], copia["clave_objeto"], copia["version_id"])
            respuestas.append(f"{nombre}: PERMITIDO")
        except alm.ObjetoProtegido:
            respuestas.append(f"{nombre}: RECHAZADO por el almacenamiento (Access Denied)")
    evento("modificacion-rechazada", f"Intento de modificación en {copia['ubicacion']}: " + "; ".join(respuestas),
           documento_id)
    raise ErrorDocumento(423, "El almacenamiento rechazó la modificación. " + "; ".join(respuestas))


# ---------------------------------------------------------------- tareas automáticas

def _documentos_activos(con) -> list[dict]:
    return con.execute(
        """SELECT d.*, t.codigo AS codigo_tipo FROM documentos d
           JOIN tipos_documento t ON t.id = d.tipo_documento_id
           WHERE d.eliminado_en IS NULL ORDER BY d.creado_en""").fetchall()


def _fuente(doc: dict, copias: dict, excluir: str, orden: list[str]) -> tuple[bytes, str]:
    for ubicacion in orden:
        copia = copias.get(ubicacion)
        if ubicacion == excluir or copia is None or copia["estado"] != "ok":
            continue
        try:
            blob, _ = _leer_verificado(ubicacion, doc, copia)
            return blob, ubicacion
        except Exception:
            continue
    raise ErrorDocumento(503, "No hay ninguna copia sana para reconstruir")


def reparar() -> dict:
    """Comprueba que cada documento tenga sus 2 copias operativas y reconstruye las que falten
    (por ejemplo, si un nodo se reemplazó vacío) a partir de la otra copia o del backup."""
    with _candado_tareas:
        reconstruidas, sin_fuente, nodos_caidos = 0, 0, []
        with conexion() as con:
            docs = _documentos_activos(con)
        for nodo in OPERATIVOS:
            if not alm.disponible(nodo):
                nodos_caidos.append(nodo)
                continue
            if alm.asegurar_bucket(nodo):
                evento("nodo-vacio", f"{nodo} volvió sin datos (servidor reemplazado): se recreó el bucket con "
                                     "Object Lock y se reconstruirán sus copias")
            for doc in docs:
                with conexion() as con:
                    copias = _copias(con, doc["id"])
                copia = copias.get(nodo)
                if copia and copia["estado"] == "ok" and alm.existe(nodo, copia["clave_objeto"], copia["version_id"]):
                    continue
                otro = [n for n in OPERATIVOS if n != nodo]
                try:
                    blob, origen = _fuente(doc, copias, nodo, otro + [RESPALDO])
                    version = alm.guardar(nodo, doc["clave_objeto"], blob, doc["retencion_hasta"], _metadatos(doc))
                except (ErrorDocumento, alm.NodoNoDisponible):
                    sin_fuente += 1
                    with conexion() as con:
                        if copia:
                            con.execute("UPDATE copias_documento SET estado='perdida', actualizado_en=now() "
                                        "WHERE id=%s", (copia["id"],))
                    continue
                with conexion() as con:
                    _guardar_copia(con, doc["id"], nodo, version, "ok", doc["clave_objeto"])
                    evento("reparacion", f"Copia en {nodo} reconstruida desde {origen} sin volver a cargar el archivo",
                           doc["id"], con)
                reconstruidas += 1
        return {"copias_reconstruidas": reconstruidas, "sin_fuente": sin_fuente, "nodos_caidos": nodos_caidos}


def respaldo_documental() -> dict:
    """Backup documental incremental: copia al servidor de respaldo solo los documentos que todavía
    no tienen backup (o cuyo backup se perdió)."""
    with _candado_tareas:
        if not alm.disponible(RESPALDO):
            evento("respaldo-documental", "El servidor de respaldo no está disponible; se reintentará")
            return {"error": "Servidor de respaldo no disponible"}
        alm.asegurar_bucket(RESPALDO)
        with conexion() as con:
            docs = _documentos_activos(con)
        nuevos, ya, fallidos = 0, 0, 0
        for doc in docs:
            with conexion() as con:
                copias = _copias(con, doc["id"])
            copia = copias.get(RESPALDO)
            if copia and copia["estado"] == "ok" and alm.existe(RESPALDO, copia["clave_objeto"], copia["version_id"]):
                ya += 1
                continue
            try:
                blob, origen = _fuente(doc, copias, RESPALDO, random.sample(OPERATIVOS, 2))
                version = alm.guardar(RESPALDO, doc["clave_objeto"], blob, doc["retencion_hasta"], _metadatos(doc))
            except (ErrorDocumento, alm.NodoNoDisponible):
                fallidos += 1
                continue
            with conexion() as con:
                _guardar_copia(con, doc["id"], RESPALDO, version, "ok", doc["clave_objeto"])
            nuevos += 1
        evento("respaldo-documental", f"Backup incremental de documentos: {nuevos} nuevo(s) copiado(s), "
                                      f"{ya} ya estaban respaldados, {fallidos} con error")
        return {"nuevos": nuevos, "ya_respaldados": ya, "fallidos": fallidos}


def _ciclo(funcion, intervalo: int, detener: threading.Event) -> None:
    while not detener.wait(intervalo):
        try:
            funcion()
        except Exception as e:
            evento("error-tarea", f"{funcion.__name__}: {type(e).__name__}: {e}")


def iniciar_tareas(detener: threading.Event) -> None:
    threading.Thread(target=_ciclo, args=(reparar, config.INTERVALO_REPARACION_SEG, detener), daemon=True).start()
    threading.Thread(target=_ciclo, args=(respaldo_documental, config.INTERVALO_RESPALDO_DOCUMENTAL_SEG, detener),
                     daemon=True).start()
