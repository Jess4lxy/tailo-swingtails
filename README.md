# Tailo - Asistente IA local de SwingTails

Prototipo academico de un asistente conversacional con IA local (sin nube) para la app movil **SwingTails** (productos y servicios veterinarios para mascotas).

- **Materia:** Desarrollo Web Integral
- **Carrera:** Desarrollo y Gestion de Software
- **Profesor:** Chuc Uc Joel Ivan
- **Equipo (9-B):**
  - Buenfil Yunes Julian Nahim
  - Lopez Uicab Rossana Sofia
  - Martinez Rivero Allan Alexis
  - Sima Moo Jafet de Jesus

---

## Estructura del repositorio

```
desarrolloWeb/
├── entregable-semana-01/   <- Motor de inferencia local (Ollama + Llama 3.1 8B)
└── entregable-semana-02/   <- Capa RAG (ChromaDB + nomic-embed-text + RAGAS)
```

Cada carpeta es un entregable independiente con su propio `README.md` y guion de demo.

| Entregable | Descripcion | Artefactos clave |
|---|---|---|
| [Semana 01](./entregable-semana-01/) | Configuracion del motor LLM local, Modelfile de Tailo, benchmark de TTFT/throughput, prueba de endpoint OpenAI-compatible con streaming. | `Modelfile`, `demo-profesor.txt` |
| [Semana 02](./entregable-semana-02/) | Pipeline RAG completo: ingesta + chunking + embeddings + ChromaDB persistente + recuperacion + evaluacion RAGAS. | `Modelfile.tailo-rag`, `src/`, `corpus/`, `INFORME-semana-02.md` |

---

## Setup rapido (para colaboradores que clonan este repo)

> El sistema corre **100 % en local**. No hay claves de API ni servicios en la nube.

### 1. Prerrequisitos

| Software | Version recomendada | Para que |
|---|---|---|
| [Ollama](https://ollama.com/download) | >= 0.3 | Motor de inferencia LLM y embeddings |
| Python | **3.12** (NO usar 3.14, RAGAS rompe con `dill`) | Pipeline RAG y evaluacion |
| Hardware sugerido | GPU NVIDIA con >= 6 GB VRAM | Inferencia rapida del modelo 8B |

En equipos sin GPU dedicada el sistema sigue funcionando, pero la latencia sube considerablemente (Ollama cae a CPU automaticamente).

### 2. Setup del motor LLM (semana 01)

```powershell
# Descargar el modelo base
ollama pull llama3.1:8b

# Construir el modelo personalizado Tailo (sin RAG)
cd entregable-semana-01
ollama create tailo -f Modelfile
ollama list   # debe aparecer "tailo"

# Probar
ollama run tailo "Hola, busco croquetas para un cachorro labrador"
```

### 3. Setup del pipeline RAG (semana 02)

```powershell
cd ..\entregable-semana-02

# Descargar el modelo de embeddings
ollama pull nomic-embed-text

# Construir el modelo Tailo-RAG (con system prompt restrictivo)
ollama create tailo-rag -f Modelfile.tailo-rag

# Entorno virtual de Python (usar 3.12)
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1    # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

# Ingestar el corpus a ChromaDB (genera chroma_db/, ~60 chunks)
python src\ingest.py

# Probar
python src\chat.py
# escribe: "Mi gato lleva 36 horas sin comer"
# para salir: "salir"
```

O usa el script all-in-one (solo Windows):

```powershell
cd entregable-semana-02
.\setup.ps1
```

### 4. Comandos utiles

```powershell
# Benchmark de latencia (objetivo p95 < 100 ms)
python src\retrieve.py --bench

# Evaluacion RAGAS (Faithfulness, Answer Relevancy, Context Precision/Recall)
python src\evaluate.py --sample 3    # muestra rapida
python src\evaluate.py               # completa, ~30-60 min con juez local

# Buscar sin LLM (solo recuperacion vectorial)
python src\retrieve.py "Que clinica atiende oncologia en Merida?"

# Regenerar la BD desde cero
Remove-Item -Recurse -Force chroma_db
python src\ingest.py
```

---

## Arquitectura

```
Usuario --> Embedding (nomic-embed-text) --> ChromaDB (HNSW + coseno)
                                                      |
                                            top-K=5 fragmentos
                                                      |
                                                      v
                            Llama 3.1 8B (modelo tailo-rag, temp 0.2)
                                                      |
                                                      v
                                          Respuesta fundamentada
```

Detalles, decisiones de diseno y metricas en [`entregable-semana-02/INFORME-semana-02.md`](./entregable-semana-02/INFORME-semana-02.md).

---

## Troubleshooting

| Sintoma | Causa | Solucion |
|---|---|---|
| `Python was not found` al ejecutar `python` | Alias de la Microsoft Store sin instalar Python real | Instalar Python 3.12 desde python.org marcando "Add to PATH" o usar `py -3.12` |
| `TypeError: Pickler._batch_setitems() takes 2 positional arguments but 3 were given` | Estas en Python 3.14, `dill` no es compatible | Recrear venv con Python 3.12 |
| `Failed to send telemetry event...` al usar ChromaDB | Bug cosmetico de chromadb 0.5.5 con posthog | Ignorar, no afecta nada |
| RAGAS devuelve `NaN` | Timeout del juez LLM local | Ya viene corregido con `RunConfig(timeout=600, max_workers=1)` en `evaluate.py` |
| `ollama: command not found` | Ollama no en PATH | Reinstalar Ollama y reiniciar la terminal |

---

## Licencia y uso

Proyecto academico sin fines comerciales. El catalogo de productos, fichas de clinicas y guias de cuidado en `entregable-semana-02/corpus/` son **sinteticos** y no representan datos reales de la app SwingTails.
