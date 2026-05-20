# Entregable Semana 02 - Base de Datos Vectorial y RAG (Tailo / SwingTails)

**Materia:** Desarrollo Web Integral - Cuatrimestre 9
**Equipo:** 2 - IDGS 9B
**Prototipo:** Tailo (asistente IA de SwingTails) + RAG sobre ChromaDB

Esta fase parte del motor configurado en la semana 01 (Ollama + Llama 3.1 8B con el Modelfile `tailo`) y le agrega una capa de Recuperacion Aumentada por Generacion (RAG) sobre una base de datos vectorial persistente.

---

## 1. Arquitectura

```
        +-------------------+
Usuario ->| Pregunta (texto) |
        +---------+---------+
                  |
                  v
       +----------------------+        +-------------------------+
       | Embedding (nomic-    |------->| ChromaDB persistente    |
       | embed-text via Ollama)|       | coseno + HNSW           |
       | 768 dim              |        | colección tailo_swingtails|
       +----------------------+        +-----------+-------------+
                                                   |
                                       top-K=5 chunks relevantes
                                                   |
                  +--------------------------------v--------------+
                  | Prompt = system + contexto + pregunta         |
                  |        |                                       |
                  |        v                                       |
                  | Llama 3.1 8B (modelo "tailo-rag", temp 0.2)    |
                  +--------------------+--------------------------+
                                       |
                                       v
                              Respuesta fundamentada
```

### Decisiones arquitectonicas

| Componente | Eleccion | Justificacion |
| --- | --- | --- |
| Motor LLM | Ollama + Llama 3.1 8B Q4_K_M | Continuidad con semana 01, OpenAI-compatible, localhost-only. |
| Base vectorial | **ChromaDB** persistente | Filosofia "llegar y usar" requerida por la rubrica para prototipo. Persiste en disco (no se borra al reiniciar) y soporta HNSW + coseno. |
| Embeddings | **nomic-embed-text** via Ollama | 768 dim, multilingue, corre en el mismo motor (sin nuevas dependencias pesadas). |
| Chunking | RecursiveCharacterTextSplitter (markdown) + un chunk por registro (JSON) | Preserva fronteras semanticas: parrafos en markdown y fichas completas en JSON. Overlap 80/500 (~16 %). |
| Metrica de distancia | Coseno | Estandar para embeddings normalizados como nomic-embed-text. |
| Evaluacion | RAGAS con juez local (Llama 3.1) | Las 4 metricas exigidas por la rubrica (faithfulness, answer_relevancy, context_precision, context_recall), todo local. |
| Seguridad | Ollama bindeado a 127.0.0.1 | Heredado de semana 01. Chroma corre embebido, sin puerto abierto. |

---

## 2. Corpus (base de conocimiento)

| Archivo | Tipo | Contenido |
| --- | --- | --- |
| `corpus/productos.json` | JSON, 20 registros | Catalogo sintetico de productos: alimento, antiparasitarios, suplementos, higiene, juguetes, primeros auxilios. |
| `corpus/veterinarias.json` | JSON, 8 registros | Fichas de clinicas en Merida con especialidades, horarios, urgencias 24h. |
| `corpus/guias_cuidado.md` | Markdown | Calendarios de vacunacion, desparasitacion, esterilizacion, senales de alarma, orientacion ante sintomas, alimentos toxicos, primeros auxilios. |
| `corpus/politicas_swingtails.md` | Markdown | Politicas de la app: envios, devoluciones, agendado, PawPoints, soporte, alcance de Tailo. |

---

## 3. Rol de Tailo (actualizado para esta fase)

Tailo apoya a DOS perfiles: tutores Y veterinarios.

Lo que SI puede hacer (cuando la informacion esta en la base vectorial):
- Recomendar productos, clinicas, horarios y precios concretos.
- Orientar sobre signos clinicos y nivel de urgencia (urgente / pronto / observar).
- Citar calendarios de vacunacion y desparasitacion estandar.
- Sugerir primeros auxilios.

Lo que NO hace nunca:
- Diagnosticos definitivos.
- Prescripcion de medicamentos o dosis para un paciente concreto.
- Sustituir la valoracion presencial de un veterinario titulado.

Ante cualquier decision terapeutica, Tailo recomienda redirigir a un medico veterinario humano. Ante senales de alarma, indica acudir a urgencias 24h.

El `SYSTEM` prompt completo esta en `Modelfile.tailo-rag`.

## 4. Como reproducirlo (Windows / PowerShell)

```powershell
# 1. Crear modelo personalizado en Ollama (una sola vez)
ollama pull llama3.1:8b
ollama pull nomic-embed-text
ollama create tailo-rag -f .\Modelfile.tailo-rag

# 2. Entorno Python
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Ingesta (chunking + embeddings + persistencia en chroma_db/)
python .\src\ingest.py

# 4. Probar recuperacion sola
python .\src\retrieve.py "Mi gato tiene problemas renales, que producto recomiendan?"

# 5. Probar el chat RAG end-to-end (streaming activado)
python .\src\chat.py
# o one-shot:
python .\src\chat.py "Recomiendame croquetas para cachorro labrador"

# 6. Benchmark de latencia p95 (objetivo < 100ms)
python .\src\retrieve.py --bench

# 7. Evaluacion RAGAS completa (las 4 metricas)
python .\src\evaluate.py
# muestreo rapido durante desarrollo:
python .\src\evaluate.py --sample 5
```

---

## 5. Metricas objetivo

| Metrica | Umbral rubrica | Donde se evidencia |
| --- | --- | --- |
| Recall del contexto | 90% - 95% | `evaluacion/ragas_results.json` campo `context_recall` |
| Latencia p95 | < 100 ms | `python src/retrieve.py --bench` |
| Faithfulness | sin alucinaciones | `evaluacion/ragas_results.json` campo `faithfulness` |
| Answer Relevancy | respuesta al punto | `evaluacion/ragas_results.json` campo `answer_relevancy` |
| Context Precision | top-k bien rankeado | `evaluacion/ragas_results.json` campo `context_precision` |

---

## 6. Continuidad respecto a la semana 01

| Semana 01 | Semana 02 |
| --- | --- |
| Motor Ollama + Llama 3.1 8B configurado | Se reutiliza. Mismo binding a localhost. |
| Modelfile `tailo` con system prompt restrictivo | Evoluciona a `tailo-rag`, anade reglas RAG y permite apoyo veterinario. |
| Endpoint OpenAI-compatible + streaming | Reutilizado por `chat.py`. |
| Benchmark de TTFT y throughput | Se mantiene. Se agrega latencia p95 de recuperacion. |
