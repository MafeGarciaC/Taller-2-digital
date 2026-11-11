# PepiCloud: enciende todo el prototipo. Uso (en PowerShell, dentro de esta carpeta):
#     powershell -ExecutionPolicy Bypass -File .\iniciar.ps1
# Hace: encender Docker Desktop, generar secretos, elegir un puerto libre, levantar los
# contenedores, cargar datos de prueba (solo si no hay) y abrir el navegador.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Paso($texto) { Write-Host ""; Write-Host "==> $texto" -ForegroundColor Cyan }
function Falla($texto) {
    Write-Host ""; Write-Host "ERROR: $texto" -ForegroundColor Red
    Write-Host "Mande una foto de esta ventana para revisarlo." -ForegroundColor Yellow
    exit 1
}
function DockerListo { cmd /c "docker info >nul 2>&1"; return ($LASTEXITCODE -eq 0) }
function PuertoLibre($p) { return -not (Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue) }

Write-Host "Carpeta del proyecto: $PSScriptRoot" -ForegroundColor Gray

# 1. Docker Desktop encendido
Paso "1/6 Verificando Docker Desktop"
if (-not (DockerListo)) {
    $exe = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $exe)) { Falla "No se encontró Docker Desktop. Instálelo desde https://www.docker.com/products/docker-desktop/" }
    Write-Host "Abriendo Docker Desktop y esperando a que arranque (1 a 3 minutos)..."
    Start-Process $exe
    $espera = 0
    while (-not (DockerListo)) {
        Start-Sleep -Seconds 5
        $espera += 5
        if ($espera -ge 300) { Falla "Docker no arrancó en 5 minutos. Ábralo a mano, espere a que diga 'Engine running' y vuelva a ejecutar este script." }
    }
}
Write-Host "Docker está listo."

# 2. Secretos de ESTA carpeta (si faltan, se generan y se borran datos viejos que usaban otras contraseñas)
Paso "2/6 Revisando los secretos"
$faltaban = -not (Test-Path "secretos\db_app_password.txt") -or -not (Test-Path "secretos\clave_maestra.txt")
& (Join-Path $PSScriptRoot "generar_secretos.ps1")
if ($faltaban) {
    Write-Host "Secretos nuevos: se borran los datos de intentos anteriores (usaban otras contraseñas)."
    cmd /c "docker compose down -v >nul 2>&1"
}

# 3. Puerto libre para la página web
Paso "3/6 Eligiendo el puerto de la página"
$puerto = $null
# Si la página ya está encendida, se conserva su puerto (no se apaga nada: LocalStack perdería los archivos)
$enUso = (cmd /c "docker compose port frontend 80 2>nul") | Select-Object -First 1
if ($enUso -match ":(\d+)$") { $puerto = [int]$Matches[1] }
elseif (Test-Path ".env") {
    $linea = Get-Content ".env" | Where-Object { $_ -like "PUERTO_WEB=*" } | Select-Object -First 1
    if ($linea) { $puerto = [int]($linea -replace "PUERTO_WEB=", "") }
}
if (-not $puerto -or (-not $enUso -and -not (PuertoLibre $puerto))) {
    foreach ($p in 8080, 8081, 8082, 8090, 8888, 9080, 9090) { if (PuertoLibre $p) { $puerto = $p; break } }
}
if (-not $puerto) { Falla "No se encontró un puerto libre (8080, 8081, 8082, 8090, 8888, 9080, 9090)." }
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), "PUERTO_WEB=$puerto`n")
Write-Host "La página usará el puerto $puerto"

# 4. Construir y encender
Paso "4/6 Construyendo y encendiendo los 7 contenedores (la primera vez tarda varios minutos)"
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Falla "docker compose no pudo encender todo (vea el mensaje de arriba)." }

# 5. Esperar a que la aplicación responda
Paso "5/6 Esperando a que la aplicación responda (hasta 3 minutos)"
$url = "http://localhost:$puerto"
$estado = $null
for ($i = 0; $i -lt 60; $i++) {
    try {
        $estado = Invoke-RestMethod -Uri "$url/api/estado" -TimeoutSec 5
        if ($estado.base_datos -or $estado.error -like "Faltan secretos*") { break }
    } catch { }
    Start-Sleep -Seconds 3
}
if (-not $estado -or -not $estado.base_datos) {
    docker compose ps
    docker compose logs backend --tail 30
    if ($estado.error) { Falla $estado.error }
    Falla "La aplicación no respondió a tiempo."
}

# 6. Datos de prueba (solo si la base está vacía)
Paso "6/6 Datos de prueba"
if ($estado.personas -eq 0) {
    docker compose exec -T backend python -m app.semilla 40
} else { Write-Host "Ya hay $($estado.personas) pacientes." }
if ($estado.documentos -eq 0) {
    docker compose exec -T backend python -m app.semilla documentos 1
    try { Invoke-RestMethod -Method Post -Uri "$url/api/tareas/respaldo-documental" | Out-Null } catch { }
} else { Write-Host "Ya hay $($estado.documentos) documentos." }

docker compose ps
Write-Host ""
Write-Host "LISTO. PepiCloud está funcionando en $url" -ForegroundColor Green
Write-Host "Para apagarlo:  docker compose stop   (ojo: se borran los archivos guardados en LocalStack)"
Start-Process $url
