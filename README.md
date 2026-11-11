# PepiCloud · Prototipo del Taller Práctico 2

Implementación a escala de la gestión de documentos clínicos de PepiCloud, desplegada **on-premise con Docker Compose**, usando **PostgreSQL** para los datos y **LocalStack (S3)** como almacenamiento de objetos.

## 1. Qué hay en el proyecto

| Contenedor | Qué hace |
|---|---|
| `frontend` | Nginx. Sirve las 3 pantallas y reenvía `/api` al backend. Puerto **8080** (o el que elija `iniciar.ps1`, guardado en `.env`) |
| `backend` | API REST (Python + FastAPI): pacientes, documentos, copias, backup documental y reparación |
| `postgres` | PostgreSQL 17. Personas, tipos de documento, metadatos, referencias a las copias y bitácora. **Sin archivos (sin BLOB)** |
| `almacenamiento-a` | LocalStack S3 · **copia operativa A** (activo-activo) |
| `almacenamiento-b` | LocalStack S3 · **copia operativa B** (activo-activo) |
| `almacenamiento-respaldo` | LocalStack S3 · **backup documental independiente** (no es copia activa) |
| `respaldo-bd` | Hace los backups de PostgreSQL (completo + incrementales) y los guarda en **su propio volumen** |
| `restaurador` | Solo para la prueba de restauración de la BD |

Las redes están separadas como en el Taller 1: `red_aplicacion`, `red_datos`, `red_almacenamiento` y `red_respaldo`.

```
navegador ──► frontend (nginx :8080) ──► backend ──┬─► postgres ◄── respaldo-bd ──► volumen respaldos_bd
                                                    ├─► almacenamiento-a  ┐ 2 copias activo-activo
                                                    ├─► almacenamiento-b  ┘ (cifradas + Object Lock)
                                                    └─► almacenamiento-respaldo  (backup documental)
```

## 2. Requisitos

- **Docker Desktop** (Windows/Mac) o Docker Engine + Compose (Linux).
- Unos **8 GB de RAM** libres (son 3 LocalStack, PostgreSQL y el backend).
- Internet la primera vez, para descargar las imágenes.

## 3. Cómo ponerlo a funcionar

**Forma rápida (Windows), recomendada.** Abran PowerShell **dentro de la carpeta del proyecto** (en el Explorador escriban `powershell` en la barra de dirección y presionen Enter) y peguen:

```powershell
Get-ChildItem -Recurse | Unblock-File
powershell -ExecutionPolicy Bypass -File .\iniciar.ps1
```

El script enciende Docker Desktop si está cerrado, genera los secretos, **elige un puerto libre** (8080, o el siguiente libre si otro programa lo ocupa), construye y enciende los 7 contenedores, carga los datos de prueba y abre el navegador. Al final muestra la dirección exacta, por ejemplo `http://localhost:8081`. Siempre usen **la misma carpeta**: los secretos (contraseñas) viven en `secretos/` de esa carpeta.

Forma manual, paso a paso:

Abrir una terminal **dentro de la carpeta `taller_practico_2`**.

**Paso 1. Generar los secretos (solo la primera vez).** Crea contraseñas y la clave maestra de cifrado en `secretos/`. Esa carpeta **no** se sube a git.

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File .\generar_secretos.ps1
```
```bash
# Linux / Mac
./generar_secretos.sh
```

**Paso 2. Construir y encender todo**
```
docker compose up -d --build
```
La primera vez tarda unos minutos. Para ver que todo esté arriba: `docker compose ps`.

**Paso 3. Abrir** http://localhost:8080

**Paso 4. Datos de prueba**
```
docker compose exec backend python -m app.semilla 40
docker compose exec backend python -m app.semilla documentos 1
```
El primero crea 40 pacientes. El segundo le carga al paciente 1 los **5 documentos médicos colombianos descargados de internet** que están en `documentos_ejemplo/`: hemograma, laboratorio clínico, radiología, prescripción y resumen de historia clínica. Sus fuentes están en `documentos_ejemplo/FUENTES.md`. Ninguno tiene datos de pacientes reales.

Opcional: `docker compose exec backend python -m app.semilla ficticios 2` carga al paciente 2 otros 5 documentos con datos inventados (`documentos_ejemplo/ficticios/`), que muestran cómo se ve un resultado clínico completo.

**Apagar:** `docker compose stop` · **Encender otra vez:** `docker compose start`

> **Importante.** LocalStack Community guarda los objetos **en memoria**: si un contenedor de almacenamiento se reinicia, vuelve **vacío**. Lo usamos a propósito para simular "servidor reemplazado" (el sistema lo reconstruye solo). Pero si se reinicia **todo** el sistema, se pierden los archivos de los 3 almacenamientos aunque la BD los siga listando. **No apaguen el sistema entre la carga de documentos y la demostración**; si lo hacen, vuelvan a cargar los documentos de ejemplo. En producción esto lo resuelve Ceph (Taller 1), que sí guarda en disco.

## 4. Las 3 pantallas

| Pantalla | Para qué |
|---|---|
| **Pacientes** (`index.html`) | CRUD de personas: registrar, buscar, editar y eliminar |
| **Documentos del paciente** (`paciente.html`) | Cargar documentos y, por cada uno: descargar, descargar desde el backup, ver sus copias, ver el objeto cifrado, intentar modificar y eliminar. Muestra la cuenta regresiva de la inmutabilidad |
| **Estado y pruebas** (`estado.html`) | Estado de cada componente, contadores, botones de backup documental y reparación, y la **bitácora** con todo lo que hace el sistema (evidencia para la demostración) |

## 5. Cómo se cumple cada requerimiento

| # | Requerimiento | Cómo se cumple | Dónde verlo |
|---|---|---|---|
| 1 | Documento asociado a persona única, tipo, información básica y referencia al archivo | Tablas `personas` (identificación única), `tipos_documento` (7 tipos), `documentos` (título, fecha, nombre, tamaño, SHA-256, `clave_objeto`) y `copias_documento` (bucket, clave y versión de cada copia) | `postgres/initdb/02_esquema.sql` |
| 2 | PostgreSQL, sin BLOB | En la BD solo hay datos y referencias; ninguna columna `bytea` | `\d documentos` (ver sección 7) |
| 3 | Almacenamiento de objetos con 2 copias activo-activo + 1 backup | Cada carga se escribe **a la vez** en `almacenamiento-a` y `almacenamiento-b`; las lecturas se reparten entre las dos. El backup documental incremental copia al `almacenamiento-respaldo` solo lo nuevo (cada 5 min o con el botón) | Pantalla del paciente (chips de copias) y Estado |
| 4 | Recuperación ante pérdida de copias | Si falla una copia se lee la otra; si fallan las 2 se lee el backup. La reparación reconstruye la copia perdida **sin volver a cargar el archivo** | Demostración, pasos D3–D5 |
| 5 | Backup de la BD: completo + incrementales, en otra ubicación, con prueba de restauración | Backups nativos de PostgreSQL 17 (`pg_basebackup` y `pg_basebackup --incremental`) en el volumen `respaldos_bd`; restauración con `pg_combinebackup` | Demostración, paso D8 |
| 6 | Documentos cifrados en reposo y credenciales fuera del código | Cifrado AES-256-GCM antes de guardar (una clave por documento, protegida con una clave maestra). Contraseñas y clave maestra como **secretos de Docker** (`/run/secrets`), no en el código ni en las imágenes | Botón **Ver cifrado**; `docker compose exec backend ls /run/secrets` |
| 7 | Inmutable 30 minutos, incluso para un administrador | **S3 Object Lock en modo COMPLIANCE** con retención de 30 min en cada copia. El borrado lo rechaza el **almacenamiento**, no solo la aplicación. Al cumplirse, se puede eliminar | Demostración, paso D6 |
| 8 | Docker Compose, PostgreSQL, LocalStack | `docker-compose.yml` | — |

### ¿Cómo organizamos el bucket? (pregunta segura del profe)

```
pepicloud-documentos/                    ← un bucket por servidor de objetos (A y B)
└── pacientes/
    └── {id del paciente}/               ← una "carpeta" (prefijo) por paciente
        └── {tipo de documento}/         ← hemograma, radiografia, formula-medica…
            └── {id del documento}       ← UUID, único
```
Ejemplo: `pacientes/41/hemograma/afcab307-5a99-4f2f-ae69-0b315a7ee86d`

- **Una carpeta por paciente**: con un solo prefijo se encuentran todos sus documentos (`aws s3 ls …/pacientes/41/`).
- Dentro, **por tipo de documento**, para filtrar sin consultar la BD.
- Se usa el **id interno** del paciente y no la cédula, para no dejar datos personales en los nombres de los objetos.
- El nombre del objeto es el **UUID del documento**: no hay choques aunque dos archivos se llamen igual.
- La **clave completa** y el `version_id` de cada copia se guardan en PostgreSQL (`documentos.clave_objeto`, `copias_documento`). Así se relacionan copia ↔ documento ↔ persona.
- El backup usa la misma organización en otro bucket (`pepicloud-respaldo-documentos`), en otro servidor.
- Cada objeto también lleva en sus metadatos el id del documento, el id del paciente, el SHA-256 y la clave de datos cifrada: se pueden identificar y descifrar aunque se pierda la BD.

## 6. Guion de la demostración

Antes de empezar: el sistema encendido, 40 pacientes y los documentos colombianos cargados (paso 4 de la sección 3). Tengan abierta la pantalla **Estado y pruebas** en otra pestaña: la bitácora va mostrando cada evento.

### D1. CRUD y carga
1. En **Pacientes**, registrar un paciente nuevo, editarlo y buscarlo.
2. Entrar a **Documentos**, cargar un documento (por ejemplo `documentos_ejemplo/hemograma_interpretacion_medicina_laboratorio.pdf`).
3. Mostrar: chips **Nodo A: ok / Nodo B: ok**, la clave del objeto y la cuenta regresiva "protegido 29:59".
4. Botón **Copias**: las 2 copias, cada una con su versión y su Object Lock COMPLIANCE.
5. En Estado, pulsar **Ejecutar backup documental ahora**; el chip **Respaldo** pasa a ok.

### D2. Cifrado en reposo
1. Botón **Ver cifrado**: lo guardado en A y en B es ilegible (solo se ve la cabecera `PEPICLOUD-AES256GCM-v1`).
2. Entrar directo al almacenamiento, como administrador, sin pasar por la aplicación:
   ```
   docker compose exec almacenamiento-a awslocal s3 ls s3://pepicloud-documentos/pacientes/ --recursive
   docker compose exec almacenamiento-a awslocal s3 cp s3://pepicloud-documentos/<clave del objeto> /tmp/doc.pdf
   docker compose exec almacenamiento-a head -c 300 /tmp/doc.pdf
   ```
   Se ven bytes sin sentido: no es un PDF que se pueda abrir.
3. Botón **Descargar**: la aplicación lo descifra, comprueba el SHA-256 y entrega el PDF original.
4. Credenciales fuera del código:
   ```
   docker compose exec backend ls /run/secrets
   docker compose exec backend env
   ```
   Los secretos están montados como archivos y no aparecen en las variables de entorno, en el código ni en la imagen. (La variable `GPG_KEY` que se ve viene de la imagen oficial de Python: es la huella pública de quien firma Python, no una contraseña.)

### D3. Falla de una copia operativa
```
docker compose stop almacenamiento-a
```
Descargar el documento: el mensaje dice **"descargado desde: nodo-b"** y la bitácora registra "nodo-a: servidor no disponible". La pantalla Estado muestra el nodo A en rojo.

### D4. Falla de las 2 copias operativas → backup
```
docker compose stop almacenamiento-b
```
Descargar: **"descargado desde: respaldo"**. El backup solo se usó porque fallaron las 2 copias.

### D5. Vuelven los servidores (llegan vacíos) → reparación
```
docker compose start almacenamiento-a almacenamiento-b
```
Esperar unos 15 s y pulsar **Revisar y reparar copias ahora** (o esperar 1 minuto). La bitácora dice "nodo-a volvió sin datos (servidor reemplazado)…" y "Copia en nodo-a reconstruida desde respaldo **sin volver a cargar el archivo**". Los chips vuelven a ok.

> También se puede reiniciar solo uno (`docker compose restart almacenamiento-b`) para ver que se reconstruye desde la otra copia operativa.

### D6. Inmutabilidad de 30 minutos
1. Antes de los 30 min, pulsar **Eliminar** → *"El almacenamiento (nodo-a) rechazó la eliminación: el documento está protegido hasta…"*.
2. Pulsar **Modificar** → *"reemplazar la versión protegida: RECHAZADO… acortar la retención: RECHAZADO (Access Denied)"*.
3. Como **administrador**, directo en el almacenamiento:
   ```
   docker compose exec almacenamiento-a awslocal s3api list-object-versions --bucket pepicloud-documentos --prefix pacientes/
   docker compose exec almacenamiento-a awslocal s3api delete-object --bucket pepicloud-documentos --key <clave> --version-id <VersionId>
   ```
   Resultado: `AccessDenied`. El modo **COMPLIANCE** no lo puede saltar nadie, ni la cuenta raíz.
4. Mostrar que sigue disponible y sin cambios: **Descargar** (el SHA-256 coincide).
5. Después de los 30 min (la cuenta regresiva dice "retención cumplida"), **Eliminar** sí funciona.

> Para probarlo rápido sin esperar 30 minutos: `RETENCION_MINUTOS=2 docker compose up -d backend` (en PowerShell: `$env:RETENCION_MINUTOS=2; docker compose up -d backend`). Para la demostración real usen 30.

### D7. La BD no guarda archivos
```
docker compose exec postgres psql -U postgres -d pepicloud -c "\d documentos"
docker compose exec postgres psql -U postgres -d pepicloud -c "select d.titulo, c.ubicacion, c.bucket, c.clave_objeto, c.estado from documentos d join copias_documento c on c.documento_id = d.id limit 6"
```
No hay ninguna columna de tipo `bytea`: solo la referencia (`clave_objeto`) y la información de cada copia.

### D8. Backup de la BD: completo, incremental y restauración (el ejemplo del profe)
```
docker compose exec backend python -m app.semilla 40          # punto 0: 40 pacientes
docker compose exec respaldo-bd respaldo completo             # 1. backup COMPLETO
docker compose exec backend python -m app.semilla 5           # 2. se agregan 5 pacientes
docker compose exec respaldo-bd respaldo incremental          # 3. backup INCREMENTAL (solo los cambios)
docker compose exec respaldo-bd respaldo listar               # cadena: completo → incremental
```
Simular que se pierde la base de datos:
```
docker compose stop postgres
docker compose run --rm restaurador perder                    # borra todos los archivos de la BD
docker compose start postgres                                 # arranca VACÍA: la pantalla Pacientes muestra 0
```
Restaurar:
```
docker compose stop postgres
docker compose run --rm restaurador restaurar                 # completo + incrementales (pg_combinebackup)
docker compose start postgres
```
La pantalla Pacientes vuelve a mostrar **exactamente los 45**, incluidos los 5 del incremental. (Si ya había pacientes antes del punto 0, el total será ese número + 5.)

Puntos para explicar:
- Los backups están en el volumen `respaldos_bd`, que solo monta el contenedor `respaldo-bd`: **una ubicación distinta** a la de la BD (`datos_bd`).
- El incremental guarda solo los bloques que cambiaron. Ocupa unos 24 MB porque incluye 16 MB de registro de cambios (WAL) que necesita para quedar consistente.
- Para restaurar se usa siempre la **cadena** completa: el último completo y todos sus incrementales en orden.

## 7. Comandos útiles

| Para | Comando |
|---|---|
| Ver el estado | `docker compose ps` |
| Ver los logs del backend | `docker compose logs -f backend` |
| Entrar a la BD | `docker compose exec postgres psql -U postgres -d pepicloud` |
| Listar objetos de un nodo | `docker compose exec almacenamiento-a awslocal s3 ls s3://pepicloud-documentos --recursive` |
| Ver la retención de un objeto | `docker compose exec almacenamiento-a awslocal s3api get-object-retention --bucket pepicloud-documentos --key <clave>` |
| Borrar todo y empezar de cero | `docker compose down -v` (borra también los backups de la BD) |

## 8. Problemas comunes

| Problema | Solución |
|---|---|
| `secret … file not found` al encender | Falta el paso 1 (generar los secretos) |
| El puerto 8080 está ocupado (`port is already allocated`) | Usen `iniciar.ps1`, que elige otro puerto solo. A mano: creen un archivo `.env` con la línea `PUERTO_WEB=8081`, corran `docker compose up -d` y abran http://localhost:8081 |
| La página muestra "Faltan secretos…" o "502" | Encendieron el proyecto desde otra carpeta. Abran PowerShell en la carpeta del proyecto y corran `iniciar.ps1` desde ahí |
| Windows bloquea un archivo ("Control Inteligente de Aplicaciones") | No usen doble clic; usen los comandos de PowerShell de arriba |
| Error `\r` o `bad interpreter` en los scripts `.sh` (Windows) | El repositorio ya los fuerza a formato LF (`.gitattributes`). Si copiaron los archivos de otra forma, guárdenlos con fin de línea LF en VS Code |
| Un documento sale "sin copia" en A y B tras reiniciar el PC | LocalStack no guarda en disco (ver la nota de la sección 3). Si el backup también se perdió, vuelvan a cargar los documentos |
| La BD restaurada no arranca | Revisar `docker compose logs postgres`; repetir el paso D8 desde `stop postgres` |
