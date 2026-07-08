# =============================================================================
# serve-public.ps1  -  Publica el backend de Tailo en internet (semana 05).
#
# Esquema "todo local + tunel": el backend (FastAPI) y Ollama corren en ESTE
# equipo (localhost, sin exponer Ollama); cloudflared abre un tunel HTTPS publico
# hacia el puerto 8000 para que el frontend lo consuma desde donde sea.
#
#   front  ->  https://<algo>.trycloudflare.com  ->  (tunel)  ->  localhost:8000
#
# Requisitos:
#   - Ollama corriendo con el modelo 'tailo-agent' (ollama list para verificar).
#   - El venv ya instalado (.\setup.ps1 o pip install -r requirements.txt).
#   - cloudflared instalado (winget install Cloudflare.cloudflared).
#
# Uso:
#   .\serve-public.ps1                 # URL publica efimera (trycloudflare)
#   Ctrl+C                             # detiene backend y tunel
#
# Nota: la URL de trycloudflare CAMBIA en cada arranque. Para una URL fija hace
# falta una cuenta de Cloudflare + un dominio (named tunnel); ver README.
# =============================================================================
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"

# Permite sobreescribir la ruta de cloudflared con $env:CLOUDFLARED_PATH.
$cf = $env:CLOUDFLARED_PATH
if (-not $cf) {
    $cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
}
if (-not $cf) {
    foreach ($p in @("C:\Program Files (x86)\cloudflared\cloudflared.exe",
                     "C:\Program Files\cloudflared\cloudflared.exe")) {
        if (Test-Path $p) { $cf = $p; break }
    }
}
if (-not $cf) { throw "No encontre cloudflared. Instala con: winget install Cloudflare.cloudflared" }
if (-not (Test-Path $py)) { throw "No existe el venv. Corre primero: .\setup.ps1" }

# 1) Backend (uvicorn) como job en segundo plano, escuchando en 0.0.0.0:8000.
Write-Host "==> Arrancando backend en http://localhost:8000 ..." -ForegroundColor Cyan
$server = Start-Job -ScriptBlock {
    param($py, $src)
    Set-Location $src
    & $py -m uvicorn server:app --host 0.0.0.0 --port 8000
} -ArgumentList $py, (Join-Path $here "src")

# Espera a que /health responda (Ollama puede tardar en cargar el modelo).
$ok = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2
        if ($h.status -eq "ok") { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    Write-Host "El backend no respondio en /health. Log del servidor:" -ForegroundColor Red
    Receive-Job $server
    Stop-Job $server; Remove-Job $server
    throw "Backend no arranco (revisa que Ollama este corriendo)."
}
Write-Host "==> Backend OK (modelo cargado)." -ForegroundColor Green

# 2) Tunel publico (cloudflared). Corre en primer plano: imprime la URL y bloquea.
Write-Host "==> Abriendo tunel publico con cloudflared..." -ForegroundColor Cyan
Write-Host "    Busca la linea 'https://<...>.trycloudflare.com' abajo y pasasela al front." -ForegroundColor Yellow
Write-Host "    (Ctrl+C para detener todo)`n" -ForegroundColor Yellow
try {
    & $cf tunnel --url http://localhost:8000
}
finally {
    Write-Host "`n==> Deteniendo backend..." -ForegroundColor Cyan
    Stop-Job $server -ErrorAction SilentlyContinue
    Remove-Job $server -ErrorAction SilentlyContinue
}
