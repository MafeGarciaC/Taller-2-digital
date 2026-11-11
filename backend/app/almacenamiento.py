"""Acceso a los 3 servidores de objetos (LocalStack S3): nodo-a, nodo-b y respaldo."""
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from . import config

# Tiempos cortos: si un nodo está caído hay que pasar rápido a la otra copia
_CFG = Config(connect_timeout=2, read_timeout=10, retries={"max_attempts": 1}, s3={"addressing_style": "path"})
_clientes: dict[str, object] = {}


class NodoNoDisponible(Exception):
    pass


class ObjetoProtegido(Exception):
    """El almacenamiento rechazó la operación por Object Lock (retención)."""


def urls() -> dict[str, str]:
    return {**config.NODOS_OPERATIVOS, config.NODO_RESPALDO[0]: config.NODO_RESPALDO[1]}


def bucket_de(nodo: str) -> str:
    return config.BUCKET_RESPALDO if nodo == config.NODO_RESPALDO[0] else config.BUCKET_DOCUMENTOS


def cliente(nodo: str):
    if nodo not in _clientes:
        acceso, secreto = config.credenciales_s3()
        _clientes[nodo] = boto3.client(
            "s3", endpoint_url=urls()[nodo], region_name="us-east-1",
            aws_access_key_id=acceso, aws_secret_access_key=secreto, config=_CFG)
    return _clientes[nodo]


def _codigo(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def disponible(nodo: str) -> bool:
    try:
        cliente(nodo).list_buckets()
        return True
    except (BotoCoreError, ClientError):
        return False


def asegurar_bucket(nodo: str) -> bool:
    """Crea el bucket con Object Lock si no existe (por ejemplo, si el nodo se reemplazó vacío).
    Devuelve True si tuvo que crearlo."""
    s3, bucket = cliente(nodo), bucket_de(nodo)
    try:
        s3.head_bucket(Bucket=bucket)
        return False
    except ClientError as e:
        if _codigo(e) not in ("404", "NoSuchBucket", "NotFound"):
            raise
    except BotoCoreError as e:
        raise NodoNoDisponible(nodo) from e
    s3.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)  # activa también el versionado
    return True


def guardar(nodo: str, clave: str, datos: bytes, retencion_hasta: datetime, metadatos: dict) -> str:
    """Guarda el objeto protegido con Object Lock en modo COMPLIANCE. Devuelve el version_id."""
    params = dict(Bucket=bucket_de(nodo), Key=clave, Body=datos, Metadata=metadatos,
                  ContentType="application/octet-stream")
    if retencion_hasta > datetime.now(timezone.utc):
        params.update(ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=retencion_hasta)
    try:
        return cliente(nodo).put_object(**params)["VersionId"]
    except BotoCoreError as e:
        raise NodoNoDisponible(nodo) from e


def leer(nodo: str, clave: str, version_id: str | None) -> bytes:
    params = dict(Bucket=bucket_de(nodo), Key=clave)
    if version_id:
        params["VersionId"] = version_id
    try:
        return cliente(nodo).get_object(**params)["Body"].read()
    except BotoCoreError as e:
        raise NodoNoDisponible(nodo) from e


def existe(nodo: str, clave: str, version_id: str | None) -> bool:
    params = dict(Bucket=bucket_de(nodo), Key=clave)
    if version_id:
        params["VersionId"] = version_id
    try:
        cliente(nodo).head_object(**params)
        return True
    except ClientError as e:
        if _codigo(e) in ("404", "NoSuchKey", "NoSuchVersion", "NoSuchBucket", "NotFound", "400"):
            return False
        raise
    except BotoCoreError as e:
        raise NodoNoDisponible(nodo) from e


def retencion(nodo: str, clave: str, version_id: str | None) -> dict | None:
    params = dict(Bucket=bucket_de(nodo), Key=clave)
    if version_id:
        params["VersionId"] = version_id
    try:
        r = cliente(nodo).get_object_retention(**params)["Retention"]
        return {"modo": r["Mode"], "hasta": r["RetainUntilDate"].isoformat()}
    except ClientError:
        return None
    except BotoCoreError as e:
        raise NodoNoDisponible(nodo) from e


def eliminar_version(nodo: str, clave: str, version_id: str) -> None:
    """Borra definitivamente una versión. Durante la retención el almacenamiento lo rechaza."""
    try:
        cliente(nodo).delete_object(Bucket=bucket_de(nodo), Key=clave, VersionId=version_id)
    except ClientError as e:
        if _codigo(e) in ("AccessDenied", "403"):
            raise ObjetoProtegido(nodo) from e
        raise
    except BotoCoreError as e:
        raise NodoNoDisponible(nodo) from e


def acortar_retencion(nodo: str, clave: str, version_id: str) -> None:
    """Intenta reducir la retención a 'ahora'. En modo COMPLIANCE el almacenamiento lo rechaza."""
    try:
        cliente(nodo).put_object_retention(
            Bucket=bucket_de(nodo), Key=clave, VersionId=version_id,
            Retention={"Mode": "COMPLIANCE", "RetainUntilDate": datetime.now(timezone.utc)})
    except ClientError as e:
        if _codigo(e) in ("AccessDenied", "403", "InvalidRequest"):
            raise ObjetoProtegido(nodo) from e
        raise
    except BotoCoreError as e:
        raise NodoNoDisponible(nodo) from e
