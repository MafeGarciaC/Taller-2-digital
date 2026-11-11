FROM postgres:17
# Scripts que crean los usuarios y el esquema la primera vez que arranca la base de datos
COPY initdb/ /docker-entrypoint-initdb.d/
