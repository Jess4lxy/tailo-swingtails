# =============================================================================
# serve-ngrok.ps1  -  Publica el backend de Tailo con ngrok (URL FIJA).
#
# Esquema "todo local + tunel": el backend (FastAPI) y Ollama corren en ESTE
# equipo (localhost); ngrok abre un tunel HTTPS con tu DOMINIO ESTATICO, asi la
# URL no cambia entre arranques.
#
#   front  ->  https://<tu-dominio>.ngrok-free.dev  ->  (ngrok)  ->  localhost:8000
#
# Requisitos (una sola vez):
#   1. Crear el dominio estatico en https://dashboard.ngrok.com -> Domains.
#   2. ngrok config add-authtoken <TU_TOKEN>      (token del dashboard)
#
# Uso:
#   .\serve-ngrok.ps1 -Domain tu-dominio.ngrok-free.dev
#   $env:NGROK_DOMAIN = "tu-dominio.ngrok-free.dev"; .\serve-ngrok.ps1
#   Ctrl+C detiene backend y tunel.
#
# NOTA para el FRONT (plan gratis de ngrok): cada peticion debe incluir el
# header  'ngrok-skip-browser-warning: true'  (cualquier valor) para saltar la
# pagina de aviso de ngrok. Agregalo ademas del Authorization.
# =============================================================================
param(
    [string]$Domain = $env:NGROK_DOMAIN
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"
$srcDir = Join-Path $here "src"

if (-not $Domain) {
    throw "Falta el dominio. Usa: .\serve-ngrok.ps1 -Domain tu-dominio.ngrok-free.dev  (o define `$env:NGROK_DOMAIN)"
}
$ngrok = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
if (-not $ngrok) { $ngrok = "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ngrok.exe" }
if (-not (Test-Path $ngrok)) { throw "No encontre ngrok. Instala con: winget install Ngrok.Ngrok" }
if (-not (Test-Path $py)) { throw "No existe el venv. Corre primero: .\setup.ps1" }

# --- 0) Liberar el puerto 8000 -------------------------------------------------
# Mata cualquier proceso que aun escuche en :8000 (p.ej. un uvicorn zombi de una
# corrida anterior). Sin esto, el nuevo backend choca con 'error 10048'.
$busy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $pids = $busy.OwningProcess | Select-Object -Unique
    Write-Host "==> Puerto 8000 ocupado (PID $($pids -join ', ')); liberandolo..." -ForegroundColor Yellow
    $pids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
}

# --- 1) Backend (uvicorn) como proceso hijo con PID rastreable -----------------
# Usamos Start-Process (NO Start-Job): asi tenemos el PID real de uvicorn y lo
# podemos matar limpio al salir (Start-Job dejaba el python huerfano).
Write-Host "==> Arrancando backend en http://localhost:8000 ..." -ForegroundColor Cyan
$server = Start-Process -FilePath $py `
    -ArgumentList @("-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000") `
    -WorkingDirectory $srcDir -PassThru -NoNewWindow

# --- 2) Esperar a que /health responda (hasta ~60s por el import de chromadb) --
$ok = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 2
    if ($server.HasExited) { break }
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2
        if ($h.status -eq "ok") { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    Write-Host "El backend no respondio en /health." -ForegroundColor Red
    if ($server.HasExited) {
        Write-Host "uvicorn termino (exit $($server.ExitCode)). Causas tipicas:" -ForegroundColor Red
        Write-Host "  - Ollama no esta corriendo, o" -ForegroundColor Red
        Write-Host "  - el venv no tiene las dependencias (corre .\setup.ps1)." -ForegroundColor Red
    } else {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
    throw "Backend no arranco."
}
Write-Host "==> Backend OK (modelo: $($h.model))." -ForegroundColor Green

# --- 3) Tunel ngrok con dominio fijo (primer plano; bloquea hasta Ctrl+C) ------
Write-Host "==> Abriendo tunel ngrok en https://$Domain ..." -ForegroundColor Cyan
Write-Host "    URL FIJA para el front. (Ctrl+C para detener todo)`n" -ForegroundColor Yellow
try {
    & $ngrok http "--domain=$Domain" 8000
}
finally {
    Write-Host "`n==> Deteniendo backend (PID $($server.Id))..." -ForegroundColor Cyan
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
}
