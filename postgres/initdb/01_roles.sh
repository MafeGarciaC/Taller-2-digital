#!/bin/bash
# Crea los usuarios de la aplicación y de respaldo leyendo sus contraseñas de los secretos de Docker
# (ninguna contraseña está escrita en el código ni dentro de la imagen).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_pass="$(cat /run/secrets/db_app_password)" \
  -v resp_pass="$(cat /run/secrets/db_respaldo_password)" <<'EOSQL'
CREATE ROLE pepicloud_app LOGIN PASSWORD :'app_pass';
CREATE ROLE respaldo LOGIN REPLICATION PASSWORD :'resp_pass';
EOSQL

# Permitir que el usuario de respaldo haga copias físicas (pg_basebackup) desde otra máquina de la red
echo "host replication respaldo all scram-sha-256" >> "$PGDATA/pg_hba.conf"
