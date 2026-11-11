"""Configuración del backend. Las credenciales se leen de los secretos de Docker (/run/secrets),
nunca del código fuente ni de la imagen."""
import base64
import os
from pathlib import Path

SECRETOS = Path(os.getenv("DIR_SECRETOS", "/run/secrets"))


SECRETOS_REQUERIDOS = ["db_app_password", "s3_access_key", "s3_secret_key", "clave_maestra"]


def secreto(nombre: str) -> str:
    return (SECRETOS / nombre).read_text(encoding="utf-8").strip()


def problemas_de_secretos() -> list[str]:
    """Secretos que faltan o no se pueden leer (por ejemplo, si se arrancó desde otra carpeta)."""
    problemas = []
    for nombre in SECRETOS_REQUERIDOS:
        try:
            if not secreto(nombre):
                problemas.append(f"{nombre} está vacío")
        except Exception as e:
            problemas.append(f"{nombre}: {type(e).__name__}")
    try:
        clave_maestra()
    except Exception as e:
        if "clave_maestra" not in " ".join(problemas):
            problemas.append(f"clave_maestra no es válida: {e}")
    return problemas


DB_HOST = os.getenv("DB_HOST", "postgres")
DB_NOMBRE = os.getenv("DB_NOMBRE", "pepicloud")
DB_USUARIO = os.getenv("DB_USUARIO", "pepicloud_app")

NODOS_OPERATIVOS = {
    "nodo-a": os.getenv("NODO_A_URL", "http://almacenamiento-a:4566"),
    "nodo-b": os.getenv("NODO_B_URL", "http://almacenamiento-b:4566"),
}
NODO_RESPALDO = ("respaldo", os.getenv("NODO_RESPALDO_URL", "http://almacenamiento-respaldo:4566"))

BUCKET_DOCUMENTOS = os.getenv("BUCKET_DOCUMENTOS", "pepicloud-documentos")
BUCKET_RESPALDO = os.getenv("BUCKET_RESPALDO", "pepicloud-respaldo-documentos")

RETENCION_MINUTOS = int(os.getenv("RETENCION_MINUTOS", "30"))
INTERVALO_RESPALDO_DOCUMENTAL_SEG = int(os.getenv("INTERVALO_RESPALDO_DOCUMENTAL_SEG", "300"))
INTERVALO_REPARACION_SEG = int(os.getenv("INTERVALO_REPARACION_SEG", "60"))
TAMANO_MAXIMO_MB = int(os.getenv("TAMANO_MAXIMO_MB", "20"))


def dsn() -> str:
    return (f"host={DB_HOST} dbname={DB_NOMBRE} user={DB_USUARIO} "
            f"password={secreto('db_app_password')} connect_timeout=3")


def credenciales_s3() -> tuple[str, str]:
    return secreto("s3_access_key"), secreto("s3_secret_key")


def clave_maestra() -> bytes:
    clave = base64.b64decode(secreto("clave_maestra"))
    if len(clave) != 32:
        raise RuntimeError("La clave maestra debe tener 32 bytes (AES-256)")
    return clave
