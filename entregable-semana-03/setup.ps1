# Setup del entregable de la semana 03 (Tailo Agent). Autonomo: no depende de
# la carpeta de la semana 02.

$ErrorActionPreference = "Stop"

Write-Host "==> Creando modelo Ollama tailo-agent" -ForegroundColor Cyan
ollama create tailo-agent -f Modelfile.tailo-agent

Write-Host "==> Verificando modelos necesarios" -ForegroundColor Cyan
ollama list

if (-not (Test-Path ".env")) {
    Write-Host "==> Copiando .env.example -> .env (editalo con tus credenciales)" -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

# En Windows "python" suele ser un stub de la Microsoft Store; preferimos el
# lanzador "py" si existe.
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PyVenv = { py -3 -m venv .venv }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PyVenv = { python -m venv .venv }
} else {
    throw "No encontre Python (ni 'py' ni 'python'). Instala Python 3.11+ y reintenta."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "==> Creando entorno virtual .venv" -ForegroundColor Cyan
    & $PyVenv
}

Write-Host "==> Instalando dependencias" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

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
