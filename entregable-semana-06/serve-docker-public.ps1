# =============================================================================
# serve-docker-public.ps1  -  Levanta la app DOCKERIZADA y la publica con un
# tunel rapido de Cloudflare (trycloudflare, sin registro).  [Semana 06]
#
#   [ movil ] -> https://<algo>.trycloudflare.com -> (tunel) -> localhost:8080
#                                                              (contenedor nginx)
#                                                                    |
#                                                        reverse proxy a backend
#
# A diferencia del script de la semana 05, este NO arranca uvicorn: de eso se
# encarga docker compose. El tunel apunta al PUERTO DEL FRONTEND (8080), que ya
# sirve la web y hace de proxy a la API -> una sola URL publica para todo.
#
# Requisitos:
#   - Docker Desktop corriendo.
#   - Ollama nativo con el modelo 'tailo-agent' (ollama list).
#   - cloudflared instalado (winget install Cloudflare.cloudflared).
#
# Uso:   .\serve-docker-public.ps1        (Ctrl+C detiene el tunel; los
#                                           contenedores siguen vivos)
# =============================================================================
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# Localiza cloudflared (permite override con $env:CLOUDFLARED_PATH).
$cf = $env:CLOUDFLARED_PATH
if (-not $cf) { $cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source }
if (-not $cf) {
    foreach ($p in @("C:\Program Files (x86)\cloudflared\cloudflared.exe",
                     "C:\Program Files\cloudflared\cloudflared.exe")) {
        if (Test-Path $p) { $cf = $p; break }
    }
}
if (-not $cf) { throw "No encontre cloudflared. Instala con: winget install Cloudflare.cloudflared" }

# 1) Levanta los contenedores (build la primera vez) en segundo plano.
Write-Host "==> docker compose up -d --build ..." -ForegroundColor Cyan
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "Fallo 'docker compose up'." }

# 2) Espera a que la app responda a traves de nginx (/health -> proxy al backend).
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
    Write-Host "La app no respondio en /health. Revisa los logs con:" -ForegroundColor Red
    Write-Host "    docker compose logs -f backend" -ForegroundColor Yellow
    throw "Los contenedores no estan sanos (revisa que Ollama este corriendo)."
}
Write-Host "==> App OK (modelo: $($h.model))." -ForegroundColor Green

# 3) Tunel publico (primer plano; imprime la URL trycloudflare y bloquea).
Write-Host "==> Abriendo tunel Cloudflare hacia el frontend (localhost:8080)..." -ForegroundColor Cyan
Write-Host "    Busca la linea 'https://<...>.trycloudflare.com' y abrela en el movil." -ForegroundColor Yellow
Write-Host "    (Ctrl+C detiene el tunel; los contenedores quedan corriendo)`n" -ForegroundColor Yellow
& $cf tunnel --url http://localhost:8080
