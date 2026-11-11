"""Datos de prueba.
  docker compose exec backend python -m app.semilla 40                 -> agrega 40 personas
  docker compose exec backend python -m app.semilla documentos 41      -> carga los 5 documentos colombianos a la persona 41
  docker compose exec backend python -m app.semilla ficticios 41       -> carga los 5 documentos ficticios a la persona 41
"""
import mimetypes
import random
import sys
from pathlib import Path

from . import documentos
from . import db
from .db import conexion

NOMBRES = ["María", "José", "Luis", "Ana", "Carlos", "Laura", "Andrés", "Sofía", "Juan", "Valentina", "Diego",
           "Camila", "Jorge", "Daniela", "Pedro", "Isabel", "Miguel", "Paula", "Felipe", "Natalia"]
APELLIDOS = ["García", "Rodríguez", "Martínez", "Pérez", "Gómez", "López", "Díaz", "Hernández", "Romero",
             "Torres", "Álvarez", "Ruiz", "Vargas", "Castro", "Mendoza", "Ortiz", "Morales", "Suárez"]
EJEMPLOS = {  # documentos colombianos descargados de internet (ver documentos_ejemplo/FUENTES.md)
    "hemograma_interpretacion_medicina_laboratorio.pdf": ("hemograma", "Interpretación del hemograma automatizado"),
    "examen_laboratorio_ese_popayan.pdf": ("laboratorio-clinico", "Examen de laboratorio clínico - ESE Popayán"),
    "radiologia_hospital_san_jose_popayan.pdf": ("radiografia", "Radiología e imágenes diagnósticas - HU San José"),
    "prescripcion_mipres_minsalud.pdf": ("formula-medica", "Prescripción de medicamentos MIPRES - MinSalud"),
    "resumen_atencion_minsalud_res3905_1994.pdf": ("historia-clinica", "Formato de resumen de atención - Res. 3905/1994"),
}
FICTICIOS = {  # documentos generados con datos inventados (documentos_ejemplo/ficticios)
    "ficticios/hemograma.pdf": ("hemograma", "Hemograma de control (ficticio)"),
    "ficticios/uroanalisis.pdf": ("uroanalisis", "Parcial de orina (ficticio)"),
    "ficticios/formula_medica.pdf": ("formula-medica", "Fórmula médica (ficticia)"),
    "ficticios/electrocardiograma.pdf": ("electrocardiograma", "Electrocardiograma de reposo (ficticio)"),
    "ficticios/radiografia_torax.png": ("radiografia", "Radiografía de tórax PA (ficticia)"),
}


def personas(cantidad: int) -> None:
    with conexion() as con:
        base = con.execute("SELECT count(*) AS n FROM personas").fetchone()["n"]
        for i in range(cantidad):
            numero = str(1_000_000_000 + base + i + 1)
            con.execute(
                """INSERT INTO personas (tipo_identificacion, numero_identificacion, nombres, apellidos,
                     fecha_nacimiento, sexo, telefono, correo)
                   VALUES ('CC', %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (numero, random.choice(NOMBRES), f"{random.choice(APELLIDOS)} {random.choice(APELLIDOS)}",
                 f"{random.randint(1950, 2015)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
                 random.choice("FM"), f"300{random.randint(1000000, 9999999)}", f"paciente{numero[-4:]}@correo.co"))
        total = con.execute("SELECT count(*) AS n FROM personas").fetchone()["n"]
    print(f"Se agregaron {cantidad} personas. Total en la base de datos: {total}")


def documentos_ejemplo(persona_id: int, ejemplos: dict) -> None:
    carpeta = Path("/srv/documentos_ejemplo")
    with conexion() as con:
        tipos = {t["codigo"]: t["id"] for t in con.execute("SELECT id, codigo FROM tipos_documento").fetchall()}
    for archivo, (codigo, titulo) in ejemplos.items():
        ruta = carpeta / archivo
        r = documentos.subir(persona_id, tipos[codigo], titulo, "Documento de ejemplo (ver documentos_ejemplo/FUENTES.md)",
                             None, ruta.name, mimetypes.guess_type(archivo)[0] or "application/octet-stream",
                             ruta.read_bytes())
        print(f"{ruta.name:48s} -> {r['clave_objeto']}  copias: {r['copias']}")


if __name__ == "__main__":
    db.iniciar()
    db._pool.wait(timeout=30)
    if len(sys.argv) > 2 and sys.argv[1] == "documentos":
        documentos_ejemplo(int(sys.argv[2]), EJEMPLOS)
    elif len(sys.argv) > 2 and sys.argv[1] == "ficticios":
        documentos_ejemplo(int(sys.argv[2]), FICTICIOS)
    else:
        personas(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
