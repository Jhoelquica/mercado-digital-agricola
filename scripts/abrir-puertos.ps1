<#
.SYNOPSIS
  Abre en 127.0.0.1, con kubectl port-forward, los puertos del clúster local que ya no se publican en el host.

.DESCRIPTION
  En el clúster local solo se publican el gateway (http://localhost:8000: el sitio y las APIs, con el JWT y el límite de
  peticiones de Traefik) y la API de MinIO (9000: las fotos). Lo demás es ClusterIP: este script lo abre mientras lo
  necesites, solo en 127.0.0.1 (no se ve desde otras máquinas de la red). Grupos:

    monitoreo       Grafana  http://127.0.0.1:3000   y  Prometheus  http://127.0.0.1:9090
    minio           consola de MinIO  http://127.0.0.1:9001
    servicios       los 8 servicios en 127.0.0.1:8001..8008 (saltan el gateway: solo para depurar, o para el seed con
                    SEED_PUERTOS_DIRECTOS=1). generar-esquemas.mjs NO los necesita: lee dentro de cada pod.
    base-productos  la base de Productos en 127.0.0.1:15432 (la usa seed_imagenes.py)
    traefik         el panel de Traefik  http://127.0.0.1:18080/dashboard/ (el 8080 suele estar ocupado en Windows)
    todo            todos los anteriores

  Cada port-forward es un proceso kubectl en segundo plano; sus PID quedan en %TEMP%\chakrashop-puertos.json para poder
  cerrarlos. Si un pod se reinicia (o se apaga Docker Desktop), su port-forward se corta: vuelve a correr el script.

.EXAMPLE
  .\scripts\abrir-puertos.ps1                     # monitoreo y consola de MinIO (lo de siempre)
  .\scripts\abrir-puertos.ps1 -Solo servicios     # los 8 servicios
  .\scripts\abrir-puertos.ps1 -Solo todo
  .\scripts\abrir-puertos.ps1 -Estado             # qué está abierto
  .\scripts\abrir-puertos.ps1 -Cerrar             # cierra todo lo que abrió este script

  Si Windows no deja correr scripts:  powershell -ExecutionPolicy Bypass -File scripts\abrir-puertos.ps1 -Solo todo
#>
param(
  [string[]]$Solo = @('monitoreo', 'minio'),
  [switch]$Cerrar,
  [switch]$Estado
)
$ErrorActionPreference = 'Stop'
$Contexto = 'docker-desktop'
$Registro = Join-Path $env:TEMP 'chakrashop-puertos.json'

function Nuevo($nombre, $recurso, $local, $remoto, $url) {
  [pscustomobject]@{ nombre = $nombre; recurso = $recurso; local = $local; remoto = $remoto; url = $url }
}
$Grupos = [ordered]@{
  'monitoreo'      = @((Nuevo 'Grafana' 'svc/grafana' 3000 3000 'http://127.0.0.1:3000'),
                       (Nuevo 'Prometheus' 'svc/prometheus' 9090 9090 'http://127.0.0.1:9090'))
  'minio'          = @((Nuevo 'Consola de MinIO' 'deploy/minio' 9001 9001 'http://127.0.0.1:9001'))
  'servicios'      = @((Nuevo 'productores' 'svc/productores' 8001 8001 'http://127.0.0.1:8001/salud'),
                       (Nuevo 'productos' 'svc/productos' 8002 8002 'http://127.0.0.1:8002/salud'),
                       (Nuevo 'pedidos' 'svc/pedidos' 8003 8003 'http://127.0.0.1:8003/salud'),
                       (Nuevo 'transporte' 'svc/transporte' 8004 8004 'http://127.0.0.1:8004/salud'),
                       (Nuevo 'notificaciones' 'svc/notificaciones' 8005 8005 'http://127.0.0.1:8005/salud'),
                       (Nuevo 'usuarios' 'svc/usuarios' 8006 8006 'http://127.0.0.1:8006/salud'),
                       (Nuevo 'pagos' 'svc/pagos' 8007 8007 'http://127.0.0.1:8007/salud'),
                       (Nuevo 'certificacion' 'svc/certificacion' 8008 8008 'http://127.0.0.1:8008/salud'))
  'base-productos' = @((Nuevo 'Base de Productos' 'svc/db-productos' 15432 5432 '127.0.0.1:15432 (PostgreSQL)'))
  'traefik'        = @((Nuevo 'Panel de Traefik' 'deploy/traefik-gateway' 18080 8080 'http://127.0.0.1:18080/dashboard/'))
}

function Leer-Registro {
  if (-not (Test-Path -LiteralPath $Registro)) { return @() }
  # Windows PowerShell 5.1 devuelve el arreglo de ConvertFrom-Json como UN solo objeto: se recorre para separarlo.
  try { $datos = Get-Content -LiteralPath $Registro -Raw | ConvertFrom-Json } catch { return @() }
  $lista = @()
  foreach ($d in $datos) { $lista += $d }
  return $lista
}
function Guardar-Registro($entradas) {
  if (@($entradas).Count -eq 0) { Remove-Item -LiteralPath $Registro -ErrorAction SilentlyContinue; return }
  ConvertTo-Json -InputObject @($entradas) -Depth 3 | Set-Content -LiteralPath $Registro -Encoding UTF8
}
function Responde($puerto) {
  $cliente = New-Object System.Net.Sockets.TcpClient
  try {
    $intento = $cliente.BeginConnect('127.0.0.1', $puerto, $null, $null)
    return ($intento.AsyncWaitHandle.WaitOne(700) -and $cliente.Connected)
  } catch { return $false } finally { $cliente.Close() }
}
function Vive($id) { return [bool](Get-Process -Id $id -ErrorAction SilentlyContinue) }

# -Solo acepta 'servicios,monitoreo' (así llega con powershell -File) o una lista de PowerShell.
$Validos = @($Grupos.Keys) + 'todo'
$Solo = @($Solo | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Malos = @($Solo | Where-Object { $Validos -notcontains $_ })
if ($Malos.Count -gt 0) { throw "Grupo desconocido: $($Malos -join ', '). Usa: $($Validos -join ', ')" }

if ($Cerrar) {
  $entradas = Leer-Registro
  $cerrados = 0
  foreach ($e in $entradas) {
    if (Vive $e.pid) { Stop-Process -Id $e.pid -Force -ErrorAction SilentlyContinue; $cerrados++ }
  }
  Guardar-Registro @()
  Write-Host "Cerrados $cerrados port-forward (de $(@($entradas).Count) registrados)."
  Write-Host 'Si abriste alguno a mano en otra ventana, ciérralo con Ctrl+C en esa ventana.'
  return
}

if ($Estado) {
  $entradas = @(Leer-Registro)
  if ($entradas.Count -eq 0) { Write-Host 'No hay port-forward abiertos por este script.'; return }
  foreach ($e in $entradas) {
    if ((Vive $e.pid) -and (Responde $e.local)) { $s = 'abierto' } else { $s = 'cortado (vuelve a correr el script)' }
    Write-Host ('{0,-18} {1,-36} {2}' -f $e.nombre, $e.url, $s)
  }
  return
}

$kubectl = (Get-Command kubectl -ErrorAction SilentlyContinue)
if (-not $kubectl) { throw 'No encuentro kubectl en el PATH.' }
& $kubectl.Source --context $Contexto get svc traefik-gateway -o name 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw "El clúster '$Contexto' no responde: enciende Docker Desktop y espera a que Kubernetes esté listo." }

if ($Solo -contains 'todo') { $Solo = @($Grupos.Keys | Where-Object { $_ -ne 'todo' }) }
$entradas = @(Leer-Registro | Where-Object { Vive $_.pid })
foreach ($grupo in $Solo) {
  foreach ($p in $Grupos[$grupo]) {
    if (Responde $p.local) {
      Write-Host ('{0,-18} {1,-36} ya estaba abierto (o el puerto lo usa otro programa)' -f $p.nombre, $p.url)
      continue
    }
    $proceso = Start-Process -FilePath $kubectl.Source -WindowStyle Hidden -PassThru -ArgumentList @(
      '--context', $Contexto, 'port-forward', '--address', '127.0.0.1', $p.recurso, "$($p.local):$($p.remoto)")
    $listo = $false
    foreach ($i in 1..20) { if (Responde $p.local) { $listo = $true; break }; Start-Sleep -Milliseconds 400 }
    if ($listo) {
      $entradas += [pscustomobject]@{ pid = $proceso.Id; nombre = $p.nombre; local = $p.local; url = $p.url }
      Write-Host ('{0,-18} {1,-36} abierto' -f $p.nombre, $p.url)
    } else {
      Stop-Process -Id $proceso.Id -Force -ErrorAction SilentlyContinue
      Write-Host ('{0,-18} {1,-36} NO se pudo abrir (¿el pod está listo?)' -f $p.nombre, $p.url)
    }
  }
}
Guardar-Registro $entradas
Write-Host ''
Write-Host 'Solo escuchan en 127.0.0.1. Para cerrarlos:  .\scripts\abrir-puertos.ps1 -Cerrar'
