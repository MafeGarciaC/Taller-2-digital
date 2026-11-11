#!/bin/bash
# Genera los secretos (contraseñas y clave maestra) en la carpeta secretos/. Solo la primera vez.
set -e
cd "$(dirname "$0")"
mkdir -p secretos
gen() { [ -s "secretos/$1" ] || head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32 > "secretos/$1"; }
gen postgres_superusuario.txt
gen db_app_password.txt
gen db_respaldo_password.txt
gen s3_access_key.txt
gen s3_secret_key.txt
[ -s secretos/clave_maestra.txt ] || head -c 32 /dev/urandom | base64 > secretos/clave_maestra.txt
chmod 644 secretos/*.txt
echo "Secretos listos en ./secretos (no los suba a git)."
