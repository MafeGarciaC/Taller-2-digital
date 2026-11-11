"""Cifrado en reposo con el esquema de "sobre" (envelope encryption):

1. Cada documento se cifra con su propia clave aleatoria de 256 bits (AES-256-GCM).
2. Esa clave del documento se cifra con la clave maestra (también AES-256-GCM) y se guarda
   junto a los metadatos. La clave maestra solo existe como secreto de Docker.

Lo que llega al almacenamiento de objetos es siempre el archivo cifrado: si alguien abre el
objeto directamente en LocalStack solo ve bytes ilegibles.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config

CABECERA = b"PEPICLOUD-AES256GCM-v1\n"


def cifrar(datos: bytes, documento_id: str) -> tuple[bytes, str]:
    clave_doc = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    # El id del documento va como dato asociado: el cifrado solo es válido para ese documento
    cifrado = AESGCM(clave_doc).encrypt(nonce, datos, documento_id.encode())

    nonce_clave = os.urandom(12)
    clave_envuelta = AESGCM(config.clave_maestra()).encrypt(nonce_clave, clave_doc, b"clave-de-documento")
    return CABECERA + nonce + cifrado, base64.b64encode(nonce_clave + clave_envuelta).decode()


def descifrar(objeto: bytes, clave_datos_cifrada: str, documento_id: str) -> bytes:
    if not objeto.startswith(CABECERA):
        raise ValueError("El objeto no tiene el formato cifrado esperado")
    crudo = base64.b64decode(clave_datos_cifrada)
    clave_doc = AESGCM(config.clave_maestra()).decrypt(crudo[:12], crudo[12:], b"clave-de-documento")
    cuerpo = objeto[len(CABECERA):]
    return AESGCM(clave_doc).decrypt(cuerpo[:12], cuerpo[12:], documento_id.encode())
