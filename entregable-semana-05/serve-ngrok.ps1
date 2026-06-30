# =============================================================================
# serve-ngrok.ps1  -  Publica el backend de Tailo con ngrok (URL FIJA).
#
# Igual que serve-public.ps1 pero usando ngrok con tu DOMINIO ESTATICO, asi la
# URL no cambia entre arranques (no hay que reconfigurar el front cada vez).
#
#   front  ->  https://<tu-dominio>.ngrok-free.app  ->  (ngrok)  ->  localhost:8000
#
# Requisitos (una sola vez):
#   1. Crear el dominio estatico en https://dashboard.ngrok.com -> Domains.
#   2. ngrok config add-authtoken <TU_TOKEN>      (token del dashboard)
#
# Uso:
#   .\serve-ngrok.ps1 -Domain tu-dominio.ngrok-free.app
#   # o fija el dominio en una variable de entorno y omite el parametro:
#   $env:NGROK_DOMAIN = "tu-dominio.ngrok-free.app"; .\serve-ngrok.ps1
#
#   Ctrl+C detiene backend y tunel.
#
# NOTA para el FRONT: en el plan gratis de ngrok, las peticiones deben incluir
# el header  'ngrok-skip-browser-warning: true'  (cualquier valor) para saltar
# la pagina de aviso de ngrok. Agregalo en cada fetch ademas del Authorization.
# =============================================================================
param(
    [string]$Domain = $env:NGROK_DOMAIN
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"

if (-not $Domain) {
    throw "Falta el dominio. Usa: .\serve-ngrok.ps1 -Domain tu-dominio.ngrok-free.app  (o define `$env:NGROK_DOMAIN)"
}
$ngrok = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
if (-not $ngrok) { $ngrok = "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ngrok.exe" }
if (-not (Test-Path $ngrok)) { throw "No encontre ngrok. Instala con: winget install Ngrok.Ngrok" }
if (-not (Test-Path $py)) { throw "No existe el venv. Corre primero: .\setup.ps1" }

# 1) Backend (uvicorn) en segundo plano, escuchando en 0.0.0.0:8000.
Write-Host "==> Arrancando backend en http://localhost:8000 ..." -ForegroundColor Cyan
$server = Start-Job -ScriptBlock {
    param($py, $src)
    Set-Location $src
    & $py -m uvicorn server:app --host 0.0.0.0 --port 8000
} -ArgumentList $py, (Join-Path $here "src")

$ok = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2
        if ($h.status -eq "ok") { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    Write-Host "El backend no respondio en /health. Log:" -ForegroundColor Red
    Receive-Job $server
    Stop-Job $server; Remove-Job $server
    throw "Backend no arranco (revisa que Ollama este corriendo)."
}
Write-Host "==> Backend OK (modelo cargado)." -ForegroundColor Green

# 2) Tunel ngrok con dominio fijo. Primer plano: bloquea hasta Ctrl+C.
Write-Host "==> Abriendo tunel ngrok en https://$Domain ..." -ForegroundColor Cyan
Write-Host "    Esa es la URL FIJA para el front. (Ctrl+C para detener todo)`n" -ForegroundColor Yellow
try {
    & $ngrok http "--domain=$Domain" 8000
}
finally {
    Write-Host "`n==> Deteniendo backend..." -ForegroundColor Cyan
    Stop-Job $server -ErrorAction SilentlyContinue
    Remove-Job $server -ErrorAction SilentlyContinue
}
