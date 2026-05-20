# Entregable Semana 01 - Motor LLM local (Tailo)

Configuracion del motor de inferencia local **Ollama + Llama 3.1 8B Q4_K_M** con el Modelfile del asistente Tailo. Esta capa es la base sobre la que se construye el RAG en la [semana 02](../entregable-semana-02/).

## Contenido

| Archivo | Descripcion |
|---|---|
| `Modelfile` | Configuracion del modelo personalizado `tailo` (parametros + system prompt). |
| `demo-profesor.txt` | Guion paso a paso para la demostracion en vivo (8 pasos). |

## Setup rapido

```powershell
ollama pull llama3.1:8b
ollama create tailo -f Modelfile
ollama list                              # debe aparecer "tailo"
ollama run tailo "Hola"                  # prueba interactiva
```

## Prueba del endpoint OpenAI-compatible con streaming

```powershell
# Crear request.json
'{"model": "tailo", "messages": [{"role": "user", "content": "Recomiendame 3 juguetes para un cachorro labrador"}], "stream": true}' | Out-File -Encoding ascii request.json

# Llamada con CURL
curl.exe http://localhost:11434/v1/chat/completions -H "Content-Type: application/json" -d "@request.json"
```

## Metricas cumplidas (sobre RTX 5060 + Ryzen 7 5700X + 32 GB RAM)

| Criterio | Medido | Umbral rubrica | Estado |
|---|---|---|---|
| TTFT (Time to First Token) | 98.75 ms | < 500 ms | APROBADO |
| Throughput | 45.27 tokens/s | > 15 TPS | APROBADO |
| Uso de VRAM | 88 % (7.2 / 8.1 GiB) | < 90 % | APROBADO |
| Cuantizacion | Q4_K_M | Q4_K_M o Q8_0 | APROBADO |
| Estandar de API | OpenAI-compatible + streaming SSE | Compatible | APROBADO |
| Seguridad de Red | Localhost 127.0.0.1:11434 | No exponer publico | APROBADO |

El informe completo en docx esta en OneDrive (no en este repo por ser entregable academico).
