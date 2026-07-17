# =============================================================================
# serve-docker-ngrok.ps1  -  Levanta la app DOCKERIZADA y la publica con ngrok
# usando tu DOMINIO ESTATICO (URL fija entre arranques).  [Semana 06]
#
#   [ movil ] -> https://<tu-dominio>.ngrok-free.dev -> (ngrok) -> localhost:8080
#                                                                 (contenedor nginx)
#
# El tunel apunta al PUERTO DEL FRONTEND (8080), que sirve la web y hace de proxy
# a la API. docker compose se encarga de backend + frontend (este script NO
# arranca uvicorn).
#
# Requisitos (una sola vez):
#   1. Docker Desktop corriendo + Ollama nativo con 'tailo-agent'.
#   2. Crear el dominio estatico en https://dashboard.ngrok.com -> Domains.
#   3. ngrok config add-authtoken <TU_TOKEN>
#
# Uso:
#   .\serve-docker-ngrok.ps1 -Domain tu-dominio.ngrok-free.dev
#   $env:NGROK_DOMAIN = "tu-dominio.ngrok-free.dev"; .\serve-docker-ngrok.ps1
#
# NOTA (plan gratis de ngrok): el front ya manda el header
# 'ngrok-skip-browser-warning' en sus peticiones para saltar la pagina de aviso.
# =============================================================================
param(
    [string]$Domain = $env:NGROK_DOMAIN
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not $Domain) {
    throw "Falta el dominio. Usa: .\serve-docker-ngrok.ps1 -Domain tu-dominio.ngrok-free.dev  (o define `$env:NGROK_DOMAIN)"
}
$ngrok = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
if (-not $ngrok) { $ngrok = "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ngrok.exe" }
if (-not (Test-Path $ngrok)) { throw "No encontre ngrok. Instala con: winget install Ngrok.Ngrok" }

# 1) Levanta los contenedores (build la primera vez).
Write-Host "==> docker compose up -d --build ..." -ForegroundColor Cyan
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "Fallo 'docker compose up'." }

# 2) Espera a que la app responda a traves de nginx.
Write-Host "==> Esperando a que la app responda en http://localhost:8080/health ..." -ForegroundColor Cyan
$ok = $false
foreach ($i in 1..60) {
    Start-Sleep -Seconds 2
    try {
        $h = Invoke-RestMethod -Uri "http://localhost:8080/health" -TimeoutSec 3
        if ($h.status -eq "ok") { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    Write-Host "La app no respondio en /health. Revisa: docker compose logs -f backend" -ForegroundColor Red
    throw "Los contenedores no estan sanos (revisa que Ollama este corriendo)."
}
Write-Host "==> App OK (modelo: $($h.model))." -ForegroundColor Green

# 3) Tunel ngrok con dominio fijo (primer plano; bloquea hasta Ctrl+C).
Write-Host "==> Abriendo tunel ngrok en https://$Domain -> localhost:8080 ..." -ForegroundColor Cyan
Write-Host "    URL FIJA para el movil. (Ctrl+C detiene el tunel; los contenedores quedan vivos)`n" -ForegroundColor Yellow
& $ngrok http "--domain=$Domain" 8080
