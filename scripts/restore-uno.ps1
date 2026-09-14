# restore-uno.ps1
# Restaura UNA base de datos desde un archivo de backup.
# Uso: .\restore-uno.ps1 -servicio productos -archivo "backups\2026-09-14_1530\productos.sql"
#
# ADVERTENCIA: esto sobreescribe los datos actuales de esa base. No hace
# falta borrar la PVC antes -- pg_dump genera un dump de datos que se
# aplica sobre el esquema ya existente via psql normal.

param(
    [Parameter(Mandatory=$true)]
    [string]$servicio,

    [Parameter(Mandatory=$true)]
    [string]$archivo
)

if (-not (Test-Path $archivo)) {
    Write-Host "No se encontro el archivo: $archivo" -ForegroundColor Red
    exit 1
}

$deployment = "db-$servicio"

Write-Host "Vas a restaurar '$archivo' sobre la base '$servicio' (deployment $deployment)." -ForegroundColor Yellow
$confirmar = Read-Host "Esto puede sobreescribir datos actuales. Escribe 'si' para continuar"

if ($confirmar -ne "si") {
    Write-Host "Cancelado." -ForegroundColor Cyan
    exit 0
}

Get-Content $archivo | kubectl exec -i "deploy/$deployment" -- psql -U admin -d $servicio

if ($LASTEXITCODE -eq 0) {
    Write-Host "Restauracion completada." -ForegroundColor Green
} else {
    Write-Host "Hubo errores durante la restauracion -- revisa la salida de arriba." -ForegroundColor Red
}