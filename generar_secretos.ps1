# Genera los secretos (contraseñas y clave maestra) en la carpeta secretos\. Solo la primera vez.
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path secretos | Out-Null
function Nuevo-Secreto($archivo) {
    $ruta = Join-Path "secretos" $archivo
    if (-not (Test-Path $ruta) -or (Get-Item $ruta).Length -eq 0) {
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $texto = ([Convert]::ToBase64String($bytes) -replace '[/+=]', '').Substring(0, 32)
        [System.IO.File]::WriteAllText((Join-Path $PWD $ruta), $texto)
    }
}
"postgres_superusuario.txt", "db_app_password.txt", "db_respaldo_password.txt", "s3_access_key.txt", "s3_secret_key.txt" | ForEach-Object { Nuevo-Secreto $_ }
$clave = Join-Path "secretos" "clave_maestra.txt"
if (-not (Test-Path $clave) -or (Get-Item $clave).Length -eq 0) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [System.IO.File]::WriteAllText((Join-Path $PWD $clave), [Convert]::ToBase64String($bytes))
}
Write-Host "Secretos listos en .\secretos (no los suba a git)."
