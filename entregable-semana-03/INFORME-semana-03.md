# Entregable Semana 03 - Tailo Agent (Function Calling)

**Proyecto:** SwingTails / Tailo
**Materia:** Desarrollo Web Integral - Cuatrimestre 9 - UTM
**Equipo:** Equipo 2 - IDGS 9B
**Fecha:** 2026-05-27

---

## 1. Contexto

En la Semana 02 entregamos un asistente con **conocimiento estatico** (RAG):
ChromaDB persistente con catalogo de productos, clinicas, guias de cuidado y
politicas, indexado con `nomic-embed-text` y consultado por similitud coseno.
Tailo podia *leer* informacion pero no podia *actuar*.

Esta semana agregamos **conocimiento dinamico** mediante *Function Calling*:
Tailo ahora puede invocar funciones que ejecutan operaciones reales contra la
API publica de SwingTails (`https://swingtails-api-yz02.onrender.com`):
registrar mascotas, listar clinicas, agendar/cancelar citas, manejar el
carrito, ver historial, etc.

Concepto central que respetamos en toda la arquitectura: **la IA no ejecuta
codigo.** El LLM solo genera una *intencion estructurada* (`tool_calls` con
JSON). Nuestro backend en `src/chat.py` la intercepta, ejecuta la funcion
real, le devuelve el resultado al modelo, y este redacta la respuesta final
al usuario.

---

## 2. Pre-requisitos

| Item | Eleccion | Justificacion |
|------|----------|---------------|
| Modelo LLM | **Llama 3.1 8B** (Q4_K_M) | Recomendado por la rubrica para tool use; soporte nativo en Ollama desde 0.3. |
| Cliente | `ollama==0.3.3` (Python) | Acepta el parametro `tools` y devuelve `tool_calls` estructurados. |
| Persona-base | `tailo-agent` (Modelfile) | Hereda de `llama3.1:8b` + system prompt que define *cuando* usar tools y *cuando* RAG. |
| Vector DB | ChromaDB persistente | Reutilizamos `entregable-semana-02/chroma_db/` sin re-ingerir. |
| HTTP | `requests==2.32.3` | Cliente sincronico, suficiente para 1 turno. |

---

## 3. Arquitectura del agente

```
                 +------------------+
   Usuario  -->  |   chat.py REPL   |
                 +------------------+
                          |
                          | 1. embed + ChromaDB top-k
                          v
                 +------------------+
                 |  Retriever (RAG) |  (reusa semana 02)
                 +------------------+
                          |
                          | 2. Inyecta bloque RAG en mensaje user
                          v
                 +------------------+      tools=TOOL_SCHEMAS
                 |  Ollama / Llama  |  <-----------------------+
                 |     tailo-agent  |                          |
                 +------------------+                          |
                          |                                    |
                  tool_calls?                                  |
                    /         \                                |
                 si           no                               |
                 |             \                               |
                 v              \                              |
        +------------------+     \                             |
        | execute_tool(...)|      \                            |
        | tools.py + API   |       \                           |
        +------------------+        \                          |
                 |                    \                        |
         role=tool result              \                       |
                 +-----------------------> Ollama (reinvoca)---+
                                          (loop hasta sin tools)
                                                |
                                                v
                                        Respuesta final
                                        streaming al usuario
```

---

## 4. Pasos tecnicos seguidos

Siguen el orden exacto de la rubrica (Fase 2, "Pasos para la Implementacion").

### Paso 1. Definir las funciones en codigo tradicional

Archivo: `src/tools.py`.

Cada funcion local que la IA puede invocar:

- Esta escrita en Python **normal**, con type hints obligatorios y docstring
  descriptivo (la IA lee esos metadatos via los `TOOL_SCHEMAS`).
- Delega en `SwingTailsClient` (`src/api_client.py`) que centraliza el JWT
  y el manejo de errores HTTP.
- **Nunca levanta excepciones hacia el orquestador.** Si la API falla,
  devolvemos `{"error": "..."}` para que el resultado se pueda inyectar
  como `role=tool` sin romper el ciclo del LLM.

Ejemplo simplificado:

```python
def book_appointment(
    user_id: int,
    pet_id: int,
    veterinary_id: int,
    date: str,
    reason: str,
) -> dict:
    """Agenda una cita veterinaria.

    Args:
        user_id: ID del usuario que agenda.
        pet_id: ID de la mascota (obtenlo de list_my_pets si no lo tienes).
        veterinary_id: ID de la clinica (obtenlo de list_clinics).
        date: Fecha y hora en formato ISO 8601.
        reason: Motivo breve de la cita.
    """
    return get_client().post("/api/appointments", json_body={
        "user_id": int(user_id),
        "pet_id": int(pet_id),
        "veterinary_id": int(veterinary_id),
        "date": date,
        "reason": reason,
    })
```

### Paso 2. Pasar las herramientas al modelo

En `src/chat.py`, cada llamada a `client.chat(...)` durante la fase de
*decision* incluye `tools=TOOL_SCHEMAS`. `TOOL_SCHEMAS` es una lista de
esquemas JSON Schema (formato compatible con OpenAI/Ollama) construidos a
mano en `tools.py` para exponer al modelo:

- nombre de la funcion,
- descripcion breve (sale literal del docstring),
- propiedades con tipo, descripcion y **enums estrictos** donde aplica
  (sexo, edad, altura para mascotas) - asi el modelo no aluciona valores.

### Paso 3. Interceptar la decision del LLM

`_run_tool_cycle()` en `chat.py` ejecuta:

1. Llama a Ollama con `stream=False` (no se puede streamear cuando se pasa
   `tools`).
2. Lee `response.message.tool_calls`.
3. Si esta vacio: terminamos el ciclo, vamos a la fase 2 (respuesta final
   en streaming).
4. Si trae uno o mas: para cada `tool_call`:
   - extraemos `function.name` y `function.arguments`,
   - ejecutamos via `tools.execute_tool(name, args)`,
   - mostramos el flujo en consola (`--quiet` lo desactiva) para que el
     video demostrativo pueda evidenciar la intercepcion.
5. Repetimos. Maximo 4 iteraciones para evitar loops patologicos (por
   ejemplo si el modelo decide encadenar `list_my_pets -> list_clinics ->
   book_appointment` en tres turnos).

### Paso 4. Devolver el resultado al modelo

Por cada tool ejecutada se anade un mensaje con `role="tool"`, `name=<tool>`
y `content=<json del resultado>`. Cuando ya no hay mas `tool_calls`,
hacemos una segunda invocacion a Ollama **sin** `tools` y **con**
`stream=True` para que la respuesta conversacional final salga token a
token al usuario.

### Nota arquitectonica - Manejo de estado y errores

La rubrica advierte: *"si una funcion falla, el error no se guarde
permanentemente en la memoria del agente"*. Lo cumplimos asi:

- Los mensajes `role=tool` se inyectan **dentro del turno actual** para que
  el LLM redacte la respuesta final, pero **no se guardan en `history`**.
- En `repl()`, al construir el contexto del siguiente turno solo
  conservamos los pares `user / assistant` finales (texto limpio).
- Si un `tool_call` produce `{"error": ...}`, ese error sigue siendo visible
  para el LLM en el turno actual (para que pueda explicarselo al usuario),
  pero no contamina los turnos posteriores.
- `_run_tool_cycle` tiene un techo de 4 iteraciones. Si lo alcanza, inyecta
  un aviso final tipo `"Se alcanzo el limite de llamadas; resume al usuario"`.

---

## 5. Lista de funciones locales expuestas (12)

La rubrica exige entre 7 y 15 funciones. Elegimos **12** que cubren las
operaciones tipicas del tutor de mascotas. Todas requieren JWT (excepto
`auth/login`, que se hace fuera del flujo conversacional).

| # | Funcion | Endpoint | Tipo |
|---|---------|----------|------|
| 1 | `list_my_pets` | `GET /api/user/pets` | lectura |
| 2 | `get_pet` | `GET /api/pets/{id}` | lectura |
| 3 | `register_pet` | `POST /api/pets` | escritura |
| 4 | `list_clinics` | `GET /api/veterinary` | lectura |
| 5 | `list_appointments` | `GET /api/appointments` | lectura |
| 6 | `book_appointment` | `POST /api/appointments` | escritura |
| 7 | `reschedule_appointment` | `PUT /api/appointments/{id}` | escritura |
| 8 | `cancel_appointment` | `DELETE /api/appointments/{id}` | escritura |
| 9 | `list_products` | `GET /api/products` | lectura |
| 10 | `add_to_cart` | `POST /api/cart` | escritura |
| 11 | `view_cart` | `GET /api/cart` | lectura |
| 12 | `purchase_history` | `GET /api/purchase-history` | lectura |

---

## 6. Ejemplo de flujo (para el video)

**Usuario:** *"Quiero agendar una cita para mi perro Firulais manana a las 10am en VetCare por una revision general."*

**Turno 1 - LLM decide tool 1:**
```json
{
  "tool_calls": [
    {"function": {"name": "list_my_pets", "arguments": {}}}
  ]
}
```

**Backend:** ejecuta `GET /api/user/pets` -> `[{"id": 17, "name": "Firulais", "specie": "perro", ...}]` -> inyecta como `role=tool`.

**Turno 2 - LLM decide tool 2:**
```json
{
  "tool_calls": [
    {"function": {"name": "list_clinics", "arguments": {"limit": 10, "page": 1}}}
  ]
}
```

**Backend:** ejecuta `GET /api/veterinary` -> lista clinicas, encuentra
VetCare con `id: 3` -> inyecta resultado.

**Turno 3 - LLM decide tool 3:**
```json
{
  "tool_calls": [
    {"function": {"name": "book_appointment", "arguments": {
      "user_id": 42, "pet_id": 17, "veterinary_id": 3,
      "date": "2026-05-28T10:00:00", "reason": "Revision general"
    }}}
  ]
}
```

**Backend:** ejecuta `POST /api/appointments` -> `{"id": 88, "status": "ok"}` -> inyecta como `role=tool`.

**Turno 4 - Sin tool_calls. Respuesta final streaming:**
> "Listo, agende a Firulais en VetCare manana 28 de mayo a las 10:00 para revision general. Tu numero de cita es 88. Si necesitas reagendar o cancelar, avisame."

---

## 7. Como correrlo

Ver `README.md`. Resumen:

```powershell
cd entregable-semana-03
.\setup.ps1                # crea modelo tailo-agent + venv
copy .env.example .env     # rellena SWINGTAILS_EMAIL / SWINGTAILS_PASSWORD
.venv\Scripts\activate
python src\chat.py         # REPL interactivo
```

---

## 8. Mapeo a la rubrica (autoevaluacion)

| Criterio | Nivel buscado | Evidencia |
|----------|---------------|-----------|
| Precision y extraccion de parametros | 4 - Sobresaliente | Schemas con enums estrictos + temperatura 0.2 |
| Toma de decisiones (When2Call) | 4 - Sobresaliente | System prompt explicito: pedir datos faltantes, no inventar |
| Gestion de intercepcion y errores | 4 - Sobresaliente | Tools devuelven dict de error, nunca crashea; max_iters; sanitizacion del historial |
| Equidad y modularidad | 3-4 | 3 modulos separados (api_client / tools / chat), reutiliza retrieve.py de semana 02 |
| Videos y documentacion | 4 (pendiente del equipo) | Este documento + log verbose en chat.py para el video |
