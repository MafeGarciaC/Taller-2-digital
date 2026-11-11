#!/bin/bash
# Respaldo de PostgreSQL con los backups nativos de PostgreSQL 17:
#   completo     -> pg_basebackup (copia física completa)
#   incremental  -> pg_basebackup --incremental (solo lo que cambió desde el respaldo anterior)
#   listar       -> muestra la cadena de respaldos
#   perder       -> SIMULA la pérdida de la base de datos (borra sus archivos)
#   restaurar    -> pg_combinebackup (completo + incrementales) y deja la BD lista para arrancar
# Los respaldos se guardan en /respaldos, un volumen distinto al de la base de datos en operación.
set -euo pipefail

DIR=/respaldos
CADENA="$DIR/cadena_actual.txt"
PGDATA_DESTINO=/var/lib/postgresql/data

preparar_clave() {
  export PGPASSFILE=/tmp/.pgpass
  echo "*:*:*:${PGUSER:-respaldo}:$(cat /run/secrets/db_respaldo_password)" > "$PGPASSFILE"
  chmod 600 "$PGPASSFILE"
}

completo() {
  preparar_clave
  local nombre
  nombre="$(date +%Y%m%d-%H%M%S)-completo"
  echo ">> Backup COMPLETO: $nombre"
  pg_basebackup -D "$DIR/$nombre" -Fp -X stream -c fast --manifest-checksums=SHA256
  echo "$nombre" > "$CADENA"          # un completo inicia una cadena nueva
  echo ">> Listo. Tamaño: $(du -sh "$DIR/$nombre" | cut -f1)"
}

incremental() {
  preparar_clave
  if [ ! -s "$CADENA" ]; then
    echo "!! No hay un backup completo. Ejecute primero: respaldo completo" >&2
    exit 1
  fi
  local anterior nombre
  anterior="$(tail -n 1 "$CADENA")"
  nombre="$(date +%Y%m%d-%H%M%S)-incremental"
  echo ">> Backup INCREMENTAL: $nombre (cambios desde $anterior)"
  pg_basebackup -D "$DIR/$nombre" -Fp -X stream -c fast --manifest-checksums=SHA256 \
    --incremental="$DIR/$anterior/backup_manifest"
  echo "$nombre" >> "$CADENA"
  echo ">> Listo. Tamaño: $(du -sh "$DIR/$nombre" | cut -f1) (el completo ocupa $(du -sh "$DIR/$(head -n 1 "$CADENA")" | cut -f1))"
}

listar() {
  echo "Respaldos guardados en $DIR:"
  for d in "$DIR"/*/; do
    [ -d "$d" ] || continue
    printf '  %-32s %8s\n' "$(basename "$d")" "$(du -sh "$d" | cut -f1)"
  done
  echo
  if [ -s "$CADENA" ]; then
    echo "Cadena que se usaría para restaurar (en orden):"
    nl -w2 -s'. ' "$CADENA" | sed 's/^/  /'
  else
    echo "Todavía no hay una cadena de respaldos."
  fi
}

bd_encendida() {
  pg_isready -h "${PGHOST:-postgres}" -t 2 >/dev/null 2>&1
}

perder() {
  if bd_encendida; then
    echo "!! PostgreSQL sigue encendido. Primero: docker compose stop postgres" >&2
    exit 1
  fi
  echo ">> SIMULANDO PÉRDIDA: se borran todos los archivos de la base de datos"
  find "$PGDATA_DESTINO" -mindepth 1 -delete
  echo ">> La base de datos ya no existe (volumen vacío)."
}

restaurar() {
  if bd_encendida; then
    echo "!! PostgreSQL sigue encendido. Primero: docker compose stop postgres" >&2
    exit 1
  fi
  if [ ! -s "$CADENA" ]; then
    echo "!! No hay respaldos para restaurar." >&2
    exit 1
  fi
  local partes=()
  while read -r p; do partes+=("$DIR/$p"); done < "$CADENA"
  echo ">> Restaurando con ${#partes[@]} respaldo(s):"
  printf '   %s\n' "${partes[@]}"
  rm -rf /tmp/restaurado
  pg_combinebackup -o /tmp/restaurado "${partes[@]}"
  echo ">> Copiando la base reconstruida al volumen de PostgreSQL"
  find "$PGDATA_DESTINO" -mindepth 1 -delete
  cp -a /tmp/restaurado/. "$PGDATA_DESTINO/"
  chown -R postgres:postgres "$PGDATA_DESTINO"
  chmod 700 "$PGDATA_DESTINO"
  rm -rf /tmp/restaurado
  echo ">> Restauración terminada. Encienda la base: docker compose start postgres"
}

case "${1:-}" in
  completo)    completo ;;
  incremental) incremental ;;
  listar)      listar ;;
  perder)      perder ;;
  restaurar)   restaurar ;;
  *)
    echo "Uso: respaldo {completo|incremental|listar|perder|restaurar}"
    exit 1 ;;
esac
