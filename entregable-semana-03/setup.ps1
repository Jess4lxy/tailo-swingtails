# Setup rapido para el entregable de la semana 03 (Tailo Agent).
# Asume que ya esta hecho el setup de la semana 02 (chroma_db generado).

$ErrorActionPreference = "Stop"

Write-Host "==> Creando modelo Ollama tailo-agent" -ForegroundColor Cyan
ollama create tailo-agent -f Modelfile.tailo-agent

Write-Host "==> Verificando modelos necesarios" -ForegroundColor Cyan
ollama list

if (-not (Test-Path ".env")) {
    Write-Host "==> Copiando .env.example -> .env (editalo con tus credenciales)" -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

if (-not (Test-Path ".venv")) {
    Write-Host "==> Creando entorno virtual .venv" -ForegroundColor Cyan
    python -m venv .venv
}

Write-Host "==> Instalando dependencias" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

$chroma = Join-Path (Resolve-Path "..").Path "entregable-semana-02\chroma_db"
if (-not (Test-Path $chroma)) {
    Write-Host "ADVERTENCIA: no encontre $chroma" -ForegroundColor Red
    Write-Host "Corre 'python src\ingest.py' en entregable-semana-02 antes de usar Tailo." -ForegroundColor Red
} else {
    Write-Host "==> ChromaDB de la semana 02 detectado: $chroma" -ForegroundColor Green
}

Write-Host ""
Write-Host "Listo. Edita .env con tus credenciales de SwingTails y luego:" -ForegroundColor Green
Write-Host "    .\.venv\Scripts\activate" -ForegroundColor Green
Write-Host "    python src\chat.py" -ForegroundColor Green
