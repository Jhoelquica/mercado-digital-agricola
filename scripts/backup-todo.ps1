# backup-todo.ps1
# Respalda las 8 bases de datos de ChakraShop en un solo comando.
# Uso: .\backup-todo.ps1

$fecha = Get-Date -Format "yyyy-MM-dd_HHmm"
$carpeta = "backups\$fecha"
New-Item -ItemType Directory -Path $carpeta -Force | Out-Null

# nombre-servicio = nombre-base-de-datos (mismo patron: db-usuarios -> BD "usuarios", etc.)
$servicios = @(
    "usuarios",
    "productores",
    "productos",
    "pedidos",
    "transporte",
    "notificaciones",
    "pagos",
    "certificacion"
)

Write-Host "Iniciando backup de $($servicios.Count) bases de datos en '$carpeta'..." -ForegroundColor Cyan

$exitos = 0
$fallos = @()

foreach ($servicio in $servicios) {
    $deployment = "db-$servicio"
    $archivoSalida = "$carpeta\$servicio.sql"

    Write-Host "  -> $servicio ..." -NoNewline

    # Ejecuta pg_dump dentro del pod y redirige la salida a un archivo local
    kubectl exec "deploy/$deployment" -- pg_dump -U admin -d $servicio > $archivoSalida 2>$null

    if ($LASTEXITCODE -eq 0 -and (Get-Item $archivoSalida).Length -gt 0) {
        Write-Host " OK ($([math]::Round((Get-Item $archivoSalida).Length / 1KB, 1)) KB)" -ForegroundColor Green
        $exitos++
    } else {
        Write-Host " FALLO" -ForegroundColor Red
        $fallos += $servicio
        Remove-Item $archivoSalida -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Backup completo: $exitos/$($servicios.Count) bases respaldadas en '$carpeta'." -ForegroundColor Cyan

if ($fallos.Count -gt 0) {
    Write-Host "Fallaron: $($fallos -join ', ')" -ForegroundColor Yellow
    Write-Host "Revisa que el pod 'db-<servicio>' este Running para cada uno." -ForegroundColor Yellow
}