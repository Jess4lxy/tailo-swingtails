# Setup del entregable de la semana 05 (Tailo Agent: streaming SSE + voz +
# guardrails + observabilidad). Autonomo: las BD de runtime (data\sessions.db y
# data\observability.db) se crean solas al arrancar el servidor / primer turno.

$ErrorActionPreference = "Stop"

Write-Host "==> Creando modelo Ollama tailo-agent" -ForegroundColor Cyan
ollama create tailo-agent -f Modelfile.tailo-agent

Write-Host "==> Verificando modelos necesarios" -ForegroundColor Cyan
ollama list

if (-not (Test-Path ".env")) {
    Write-Host "==> Copiando .env.example -> .env (editalo con tus credenciales)" -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

# La VOZ (faster-whisper -> av/PyAV) necesita Python 3.11/3.12: no hay wheel de
# 'av' para 3.14. Por eso preferimos 3.12 para el venv; si no esta, caemos a
# cualquier Python (el backend corre, pero /transcribe dara 503 sin la voz).
if (py -3.12 --version 2>$null) {
    $PyVenv = { py -3.12 -m venv .venv }
    Write-Host "==> Usando Python 3.12 (soporta la voz)" -ForegroundColor Green
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PyVenv = { py -3 -m venv .venv }
    Write-Host "==> Python 3.12 no encontrado; uso 'py -3' (la voz puede no instalar)" -ForegroundColor Yellow
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PyVenv = { python -m venv .venv }
} else {
    throw "No encontre Python. Instala Python 3.12 (winget install Python.Python.3.12) y reintenta."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "==> Creando entorno virtual .venv" -ForegroundColor Cyan
    & $PyVenv
}

Write-Host "==> Instalando dependencias (core)" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "==> Instalando dependencias de VOZ (Whisper)" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "    [aviso] No se pudo instalar la voz (probablemente Python != 3.11/3.12)." -ForegroundColor Yellow
    Write-Host "    El backend funciona sin voz; /transcribe devolvera 503." -ForegroundColor Yellow
}

if (-not (Test-Path "chroma_db\chroma.sqlite3")) {
    Write-Host "==> No hay base vectorial; generandola con ingest.py" -ForegroundColor Yellow
    Write-Host "    (requiere Ollama corriendo con nomic-embed-text)" -ForegroundColor Yellow
    & .\.venv\Scripts\python.exe src\ingest.py
} else {
    Write-Host "==> ChromaDB local detectado (chroma_db\)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Listo. Edita .env con tus credenciales de SwingTails y luego:" -ForegroundColor Green
Write-Host "    .\.venv\Scripts\python.exe src\chat.py                 # REPL (CLI)" -ForegroundColor Green
Write-Host "    cd src; ..\.venv\Scripts\python.exe -m uvicorn server:app --port 8000   # servicio HTTP" -ForegroundColor Green
