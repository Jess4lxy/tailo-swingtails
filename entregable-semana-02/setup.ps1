# =============================================================================
# setup.ps1 - Provisioning local del entregable semana 02 (Tailo RAG)
# Ejecutar desde la carpeta entregable-semana-02 en PowerShell (no admin):
#     .\setup.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

Write-Host "== 1. Modelos Ollama ==" -ForegroundColor Cyan
ollama pull llama3.1:8b
ollama pull nomic-embed-text
ollama create tailo-rag -f .\Modelfile.tailo-rag

Write-Host "`n== 2. Entorno virtual Python ==" -ForegroundColor Cyan
if (-not (Test-Path .\.venv)) {
    python -m venv .venv
}
. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

Write-Host "`n== 3. Ingesta del corpus a ChromaDB ==" -ForegroundColor Cyan
python .\src\ingest.py

Write-Host "`n== 4. Benchmark de recuperacion (latencia p95) ==" -ForegroundColor Cyan
python .\src\retrieve.py --bench

Write-Host "`n== Setup completado ==" -ForegroundColor Green
Write-Host "Pruebas sugeridas:" -ForegroundColor Yellow
Write-Host "  python .\src\chat.py" -ForegroundColor Yellow
Write-Host "  python .\src\evaluate.py --sample 5    # rapido" -ForegroundColor Yellow
Write-Host "  python .\src\evaluate.py               # completo" -ForegroundColor Yellow
