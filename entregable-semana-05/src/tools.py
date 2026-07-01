"""Tools (Function Calling) que el LLM Tailo puede invocar.

Cada funcion:
  - es codigo Python normal (no la ejecuta la IA, la ejecutamos nosotros);
  - lleva type hints + docstring porque Ollama los lee para construir el
    schema que ve el modelo (rubrica fase 2 - Paso 1);
  - delega en el cliente HTTP de api_client.py para hablar con
    https://swingtails-api-yz02.onrender.com;
  - devuelve siempre algo serializable a JSON (dict, list, str). Si la API
    devuelve error, lo propagamos como {"error": "..."} para que el LLM lo
    vea y pueda explicarselo al usuario sin crashear.

Las 10 funciones expuestas cubren el caso de uso de un tutor de mascotas:
listar mascotas, registrar una nueva, ver clinicas, agendar / reagendar /
cancelar citas, ver catalogo, manejar carrito e historial.

Se eligieron deliberadamente lecturas + escrituras representativas (no las
33 del API) para mantener el espacio de decision del modelo manejable.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable

from api_client import get_client


# El user_id de las operaciones de escritura NUNCA lo provee el modelo: se
# toma de la sesion autenticada (el agente adopta la identidad del usuario que
# esta conversando). Asi evitamos que el LLM invente ids o que un usuario opere
# a nombre de otro.
def _current_user_id() -> int | None:
    return get_client().current_user_id


_NO_SESSION_ERROR = {
    "error": "No hay una sesion iniciada. El usuario debe autenticarse "
    "(login con su email y contrasena) antes de hacer esta operacion."
}


def _match_by_name(items: Any, name: str, key: str = "name") -> dict | None:
    """Busca un item por nombre (exacto y luego por substring, sin distinguir
    mayusculas). Usado para resolver mascota/clinica/servicio a partir del
    nombre que da el usuario, en vez de confiar en ids que el modelo inventa."""
    if not isinstance(items, list):
        return None
    n = (name or "").strip().lower()
    if not n:
        return None
    for it in items:
        if str(it.get(key, "")).strip().lower() == n:
            return it
    for it in items:
        if n in str(it.get(key, "")).strip().lower():
            return it
    return None


# ===========================================================================
# Funciones - PERFIL TUTOR
# ===========================================================================

def list_my_pets() -> dict:
    """Lista las mascotas del usuario autenticado.

    Llama a GET /api/user/pets. Util cuando el usuario pregunta
    "que mascotas tengo registradas" o necesita el id de una mascota
    para agendar una cita.
    """
    return get_client().get("/api/user/pets")


def get_pet(pet_id: int) -> dict:
    """Obtiene la ficha completa de UNA mascota del usuario por su id.

    Args:
        pet_id: Identificador numerico de la mascota.

    El endpoint GET /api/pets/{id} es solo para admin/recepcionista (un tutor
    recibe 403), asi que resolvemos la ficha filtrando entre las mascotas del
    propio usuario (GET /api/user/pets). Util para ver edad, raza, sexo o peso
    antes de agendar o recomendar un producto.
    """
    pets = get_client().get("/api/user/pets")
    if isinstance(pets, dict) and pets.get("error"):
        return pets
    if isinstance(pets, list):
        for p in pets:
            if str(p.get("id")) == str(pet_id):
                return p
        return {"error": f"No tienes una mascota con id {pet_id}."}
    return pets


# Valores cerrados que exige la API para una mascota. Se usan como red de
# seguridad DETERMINISTICA: si el modelo no manda un valor valido y explicito
# (o lo deja vacio), register_pet NO llama a la API: devuelve
# 'preguntar_al_usuario' para que el asistente pida el dato real en vez de
# inventarlo. Es el mismo patron de book_appointment (nombres -> preguntar).
VALID_SEX = {"Macho", "Hembra"}
VALID_AGE = {"Cachorro", "Joven", "Adulto", "Senior"}
VALID_HEIGHT = {"<30", "30-40", "41-50", "51-60", ">60"}


def register_pet(
    name: str = "",
    specie: str = "",
    sex: str = "",
    age: str = "",
    height: str = "",
    breed: str | None = None,
    weight: float | None = None,
) -> dict:
    """Registra una nueva mascota para el usuario autenticado.

    Args:
        name: Nombre de la mascota.
        specie: Especie (perro, gato, ...). OBLIGATORIO en la API real.
        sex: 'Macho' o 'Hembra' (enum estricto del API).
        age: 'Cachorro', 'Joven', 'Adulto' o 'Senior'.
        height: '<30', '30-40', '41-50', '51-60' o '>60' (cm). Debe venir del
            usuario; NO se deduce de la raza/peso/edad. Si el usuario no la dijo,
            deja este campo como cadena vacia "".
        breed: Raza. Opcional.
        weight: Peso en kg. Opcional.

    Llama a POST /api/pets. El propietario (user_id) se toma de la sesion
    autenticada, NO se pide al usuario. Si falta algun campo obligatorio (o el
    valor no es uno de los validos), NO se llama a la API: se devuelve
    'preguntar_al_usuario' para que el asistente lo pida (no lo invente).
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)

    # Red de seguridad: valida los obligatorios ANTES de tocar la API. Asi, si
    # el modelo alucino un valor invalido o dejo algo vacio, se le pide al
    # usuario en vez de registrar datos inventados.
    faltan: list[str] = []
    if not (name or "").strip():
        faltan.append("el nombre")
    if not (specie or "").strip():
        faltan.append("la especie (perro, gato, ...)")
    if sex not in VALID_SEX:
        faltan.append("el sexo (Macho o Hembra)")
    if age not in VALID_AGE:
        faltan.append("la edad (Cachorro, Joven, Adulto o Senior)")
    if height not in VALID_HEIGHT:
        faltan.append("la altura en cm (<30, 30-40, 41-50, 51-60 o >60)")
    if faltan:
        return {"preguntar_al_usuario":
                f"Antes de registrar a {name or 'la mascota'} necesito que el "
                f"usuario indique: {', '.join(faltan)}. Preguntaselo ofreciendo "
                f"las opciones; NO inventes estos valores (la altura NO se "
                f"deduce de la raza, el peso ni la edad)."}

    body: dict[str, Any] = {
        "name": name,
        "specie": specie,
        "sex": sex,
        "age": age,
        "height": height,
        "user_id": uid,
    }
    if breed is not None:
        body["breed"] = breed
    if weight is not None:
        body["weight"] = float(weight)
    return get_client().post("/api/pets", json_body=body)


def update_pet(
    pet_id: int,
    name: str,
    specie: str,
    sex: str,
    age: str,
    height: str,
    breed: str | None = None,
    weight: float | None = None,
) -> dict:
    """Actualiza los datos de una mascota existente del usuario.

    IMPORTANTE: la API hace REEMPLAZO COMPLETO, no parcial. Debes enviar
    TODOS los campos (no solo el que cambia) o los omitidos se pierden. Para
    cambiar un solo dato: primero obten la ficha actual con list_my_pets,
    luego llama update_pet repitiendo los valores actuales y modificando solo
    lo que el usuario pidio.

    Args:
        pet_id: ID de la mascota a actualizar.
        name: Nombre.
        specie: Especie (perro, gato, ...).
        sex: 'Macho' o 'Hembra'.
        age: 'Cachorro', 'Joven', 'Adulto' o 'Senior'.
        height: '<30', '30-40', '41-50', '51-60' o '>60'.
        breed: Raza. Opcional.
        weight: Peso en kg. Opcional.

    Llama a PUT /api/pets/{id}. El user_id se toma de la sesion autenticada
    (es obligatorio en el body: la API lo usa para validar la propiedad).
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    body: dict[str, Any] = {
        "name": name,
        "specie": specie,
        "sex": sex,
        "age": age,
        "height": height,
        "user_id": uid,
    }
    if breed is not None:
        body["breed"] = breed
    if weight is not None:
        body["weight"] = float(weight)
    return get_client().put(f"/api/pets/{int(pet_id)}", json_body=body)


def delete_pet(pet_id: int) -> dict:
    """Elimina una mascota del usuario por su id.

    Args:
        pet_id: Identificador numerico de la mascota a eliminar.

    Llama a DELETE /api/pets/{id}. Operacion destructiva: el LLM debe
    confirmar con el usuario antes de invocarla y asegurarse de que el id
    corresponde a la mascota correcta (usa list_my_pets si hay duda).
    """
    return get_client().delete(f"/api/pets/{int(pet_id)}")


def list_clinics(limit: int = 10, page: int = 1) -> dict:
    """Lista clinicas veterinarias registradas en SwingTails.

    Args:
        limit: Cantidad maxima por pagina (default 10).
        page: Numero de pagina (default 1).

    Llama a GET /api/veterinary. Util cuando el usuario quiere ver
    opciones para agendar o buscar urgencias.
    """
    return get_client().get(
        "/api/veterinary",
        params={"limit": int(limit), "page": int(page)},
    )


def list_appointments(limit: int = 100, page: int = 1) -> dict:
    """Lista las citas del usuario autenticado.

    Args:
        limit: Cantidad maxima por pagina.
        page: Numero de pagina.

    Llama a GET /api/appointments/user (el backend filtra por el JWT, asi
    que solo devuelve las citas del usuario que conversa).
    """
    return get_client().get(
        "/api/appointments/user",
        params={"limit": int(limit), "page": int(page)},
    )


def book_appointment(
    pet_name: str,
    clinic_name: str,
    service_name: str,
    appointment_date: str,
    hour: str,
    notes: str | None = None,
) -> dict:
    """Agenda una cita veterinaria para el usuario autenticado.

    Recibe NOMBRES (no ids): la funcion resuelve por si misma el id de la
    mascota, de la clinica y del servicio consultando la API. Asi el modelo
    nunca inventa ids. Si un nombre no coincide, devuelve la lista de opciones
    disponibles para que el asistente se las ofrezca al usuario.

    Args:
        pet_name: Nombre de la mascota del usuario (p.ej. "Firulais").
        clinic_name: Nombre de la clinica (p.ej. "Toy Inc").
        service_name: Nombre del servicio (p.ej. "Consulta General").
        appointment_date: Fecha en formato YYYY-MM-DD.
        hour: Hora en formato HH:MM:SS (24h), p.ej. "10:00:00".
        notes: Nota opcional.

    Llama a POST /api/appointments (user_id de la sesion). Si el horario esta
    ocupado la API responde 409.
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)

    client = get_client()

    pets = client.get("/api/user/pets")
    if isinstance(pets, dict) and pets.get("error"):
        return pets
    pet = _match_by_name(pets, pet_name)
    if not pet:
        nombres = [p.get("name") for p in pets] if isinstance(pets, list) else []
        return {"preguntar_al_usuario":
                f"No encontre una mascota llamada '{pet_name}'. Tus mascotas registradas son: "
                f"{', '.join(nombres) if nombres else 'ninguna'}. Preguntale para cual es la cita."}

    clinics = client.get("/api/veterinary")
    if isinstance(clinics, dict) and clinics.get("error"):
        return clinics
    clinic = _match_by_name(clinics, clinic_name)
    if not clinic:
        nombres = [c.get("name") for c in clinics if c.get("name")][:6] if isinstance(clinics, list) else []
        listado = "  ".join(f"{i}) {n}" for i, n in enumerate(nombres, 1))
        return {"preguntar_al_usuario":
                f"Para agendar falta elegir la clinica. Muestrale al usuario ESTA lista numerada "
                f"(son las unicas clinicas validas) y pidele que elija por nombre o numero: {listado}"}

    servicios = clinic.get("services") or []
    svc = _match_by_name(servicios, service_name)
    if not svc:
        opts = [f"{s.get('name')} (${s.get('price')})" for s in servicios]
        return {"preguntar_al_usuario":
                f"Falta el servicio para la cita en {clinic.get('name')}. Muestrale al usuario "
                f"estos servicios con su precio y pregunta cual quiere: {'; '.join(opts) if opts else 'ninguno'}."}

    body: dict[str, Any] = {
        "pet_id": pet["id"],
        "pet_name": pet["name"],
        "veterinary_id": clinic["id"],
        "appointment_date": appointment_date,
        "hour": hour,
        "services": [{"offering_id": svc["id"], "price": svc["price"]}],
    }
    if notes is not None:
        body["notes"] = notes
    return client.post("/api/appointments", json_body=body)


def reschedule_appointment(
    appointment_id: int,
    appointment_date: str,
    hour: str,
    services: list,
    notes: str | None = None,
) -> dict:
    """Reagenda una cita existente (cambia fecha/hora).

    Args:
        appointment_id: ID de la cita a modificar.
        appointment_date: Nueva fecha YYYY-MM-DD.
        hour: Nueva hora HH:MM:SS.
        services: Lista de servicios [{"offering_id": ..., "price": ...}].
            La API hace reemplazo completo: reenvia los servicios actuales de
            la cita (los ves en list_appointments) o se pierden.
        notes: Nota opcional.

    Llama a PUT /api/appointments/{id}.
    """
    body: dict[str, Any] = {
        "appointment_date": appointment_date,
        "hour": hour,
        "status": "Pendiente",
        "pickup_requested": False,
        "services": services,
    }
    if notes is not None:
        body["notes"] = notes
    return get_client().put(
        f"/api/appointments/{int(appointment_id)}", json_body=body
    )


def cancel_appointment(appointment_id: int) -> dict:
    """Cancela (elimina) una cita por su id.

    Args:
        appointment_id: ID de la cita.

    Llama a DELETE /api/appointments/{id}. Operacion destructiva: confirma
    con el usuario antes de invocarla.
    """
    return get_client().delete(f"/api/appointments/{int(appointment_id)}")


def list_products(limit: int = 10, page: int = 1) -> dict:
    """Lista productos del catalogo de SwingTails (alimentos, accesorios, etc.).

    Args:
        limit: Resultados por pagina.
        page: Numero de pagina.

    Llama a GET /api/products. Para sugerencias profundas con motivo
    clinico, complementar con el RAG local (catalogo enriquecido).
    """
    return get_client().get(
        "/api/products",
        params={"limit": int(limit), "page": int(page)},
    )


def get_product(product_id: int) -> dict:
    """Obtiene el detalle completo de UN producto por su id.

    Args:
        product_id: Identificador numerico del producto.

    Llama a GET /api/products/{id}. Util cuando el usuario quiere precio,
    stock o descripcion de un producto especifico que vio en el catalogo.
    """
    return get_client().get(f"/api/products/{int(product_id)}")


def list_clinic_reviews(veterinary_id: int) -> dict:
    """Lista las reseñas de una clinica veterinaria.

    Args:
        veterinary_id: ID de la clinica (de list_clinics).

    Llama a GET /api/veterinary-reviews/{veterinary_id}.
    """
    return get_client().get(f"/api/veterinary-reviews/{int(veterinary_id)}")


def get_clinic_rating(veterinary_id: int) -> dict:
    """Obtiene la calificacion promedio y total de reseñas de una clinica.

    Args:
        veterinary_id: ID de la clinica.

    Llama a GET /api/veterinary-reviews/{veterinary_id}/average.
    """
    return get_client().get(
        f"/api/veterinary-reviews/{int(veterinary_id)}/average"
    )


def review_clinic(veterinary_id: int, rating: int, comment: str | None = None) -> dict:
    """Publica una reseña de una clinica a nombre del usuario autenticado.

    Args:
        veterinary_id: ID de la clinica a reseñar.
        rating: Calificacion entera de 1 a 5.
        comment: Comentario opcional.

    Llama a POST /api/veterinary-reviews. El user_id se toma de la sesion.
    """
    body: dict[str, Any] = {
        "veterinary_id": int(veterinary_id),
        "rating": int(rating),
    }
    if comment is not None:
        body["comment"] = comment
    return get_client().post("/api/veterinary-reviews", json_body=body)


# ===========================================================================
# Registro central de tools (lo lee chat.py)
# ===========================================================================

# Mapa name -> callable. Es lo que usamos para ejecutar localmente cuando
# el LLM devuelve un tool_call con un name dado.
TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "list_my_pets": list_my_pets,
    "get_pet": get_pet,
    "register_pet": register_pet,
    "update_pet": update_pet,
    "delete_pet": delete_pet,
    "list_clinics": list_clinics,
    "list_appointments": list_appointments,
    "book_appointment": book_appointment,
    "reschedule_appointment": reschedule_appointment,
    "cancel_appointment": cancel_appointment,
    "list_products": list_products,
    "get_product": get_product,
    "list_clinic_reviews": list_clinic_reviews,
    "get_clinic_rating": get_clinic_rating,
    "review_clinic": review_clinic,
}


# Schemas JSON que se envian al modelo via parametro `tools`.
# Ollama (>=0.3) acepta el formato OpenAI-compatible: type=function + parameters
# como JSON Schema. Los enums se exponen al modelo para que no aluciene valores.
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_my_pets",
            "description": "Lista las mascotas del usuario autenticado.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pet",
            "description": "Obtiene la ficha completa de una mascota por su id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pet_id": {"type": "integer", "description": "ID de la mascota."}
                },
                "required": ["pet_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_pet",
            "description": "Registra una nueva mascota. El propietario se infiere de la sesion. Pasa SOLO los datos que el usuario dijo explicitamente; para un obligatorio que el usuario no menciono, pasa cadena vacia \"\" (la funcion te dira que preguntar). NO inventes valores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre de la mascota."},
                    "specie": {"type": "string", "description": "Especie: perro, gato, etc."},
                    "sex": {"type": "string", "description": "'Macho' o 'Hembra'. Si el usuario no lo dijo, pasa cadena vacia \"\"."},
                    "age": {"type": "string", "description": "'Cachorro', 'Joven', 'Adulto' o 'Senior'. Si el usuario no lo dijo, pasa \"\"."},
                    "height": {"type": "string", "description": "Altura en cm: '<30', '30-40', '41-50', '51-60' o '>60'. Es una medida fisica que SOLO da el usuario; NUNCA la deduzcas de la raza, el peso ni la edad. Si el usuario no la dijo, pasa cadena vacia \"\"."},
                    "breed": {"type": "string", "description": "Raza (opcional)."},
                    "weight": {"type": "number", "description": "Peso en kg (opcional)."},
                },
                "required": ["name", "specie"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_pet",
            "description": "Actualiza una mascota existente. REEMPLAZO COMPLETO: envia todos los campos (usa list_my_pets para los valores actuales y cambia solo lo pedido). El user_id se infiere de la sesion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pet_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "specie": {"type": "string", "description": "perro, gato, etc."},
                    "sex": {"type": "string", "enum": ["Macho", "Hembra"]},
                    "age": {
                        "type": "string",
                        "enum": ["Cachorro", "Joven", "Adulto", "Senior"],
                    },
                    "height": {
                        "type": "string",
                        "enum": ["<30", "30-40", "41-50", "51-60", ">60"],
                    },
                    "breed": {"type": "string"},
                    "weight": {"type": "number", "description": "Peso en kg."},
                },
                "required": ["pet_id", "name", "specie", "sex", "age", "height"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_pet",
            "description": "Elimina una mascota del usuario por su id (operacion destructiva; confirma antes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pet_id": {"type": "integer", "description": "ID de la mascota a eliminar."}
                },
                "required": ["pet_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_clinics",
            "description": "Lista clinicas veterinarias registradas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                    "page": {"type": "integer", "default": 1},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_appointments",
            "description": "Lista las citas del usuario (proximas e historicas).",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                    "page": {"type": "integer", "default": 1},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Agenda una cita. Recibe NOMBRES (mascota, clinica, servicio); la funcion resuelve los ids. Si un nombre no existe, devuelve las opciones disponibles. No inventes nombres: usa list_my_pets y list_clinics si no los sabes, o pregunta al usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pet_name": {"type": "string", "description": "Nombre de la mascota."},
                    "clinic_name": {"type": "string", "description": "Nombre de la clinica."},
                    "service_name": {"type": "string", "description": "Nombre del servicio (p.ej. Consulta General)."},
                    "appointment_date": {"type": "string", "description": "Fecha YYYY-MM-DD."},
                    "hour": {"type": "string", "description": "Hora HH:MM:SS (24h)."},
                    "notes": {"type": "string"},
                },
                "required": [
                    "pet_name",
                    "clinic_name",
                    "service_name",
                    "appointment_date",
                    "hour",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": "Reagenda una cita existente (cambia fecha/hora). Reenvia los servicios actuales (de list_appointments) o se pierden.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "integer"},
                    "appointment_date": {"type": "string", "description": "Fecha YYYY-MM-DD."},
                    "hour": {"type": "string", "description": "Hora HH:MM:SS."},
                    "services": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "offering_id": {"type": "integer"},
                                "price": {"type": "number"},
                            },
                            "required": ["offering_id", "price"],
                        },
                    },
                    "notes": {"type": "string"},
                },
                "required": ["appointment_id", "appointment_date", "hour", "services"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancela (elimina) una cita por su id.",
            "parameters": {
                "type": "object",
                "properties": {"appointment_id": {"type": "integer"}},
                "required": ["appointment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_products",
            "description": "Lista productos del catalogo de SwingTails.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                    "page": {"type": "integer", "default": 1},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "Obtiene el detalle (precio, stock, descripcion) de un producto por su id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "ID del producto."}
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_clinic_reviews",
            "description": "Lista las reseñas de una clinica veterinaria por su id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "veterinary_id": {"type": "integer", "description": "ID de la clinica."}
                },
                "required": ["veterinary_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clinic_rating",
            "description": "Calificacion promedio y total de reseñas de una clinica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "veterinary_id": {"type": "integer", "description": "ID de la clinica."}
                },
                "required": ["veterinary_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_clinic",
            "description": "Publica una reseña de una clinica a nombre del usuario autenticado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "veterinary_id": {"type": "integer"},
                    "rating": {"type": "integer", "description": "Entero de 1 a 5."},
                    "comment": {"type": "string"},
                },
                "required": ["veterinary_id", "rating"],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict | str | None) -> str:
    """Ejecuta una tool por nombre con los argumentos del LLM.

    Devuelve SIEMPRE un string (lo que se inserta como `content` de un
    mensaje role=tool). Si la tool no existe o los argumentos son invalidos,
    devolvemos un error serializado en JSON - no levantamos excepcion -
    para no romper el ciclo conversacional del LLM.
    """
    if name not in TOOL_REGISTRY:
        return json.dumps({"error": f"tool desconocida: {name}"}, ensure_ascii=False)

    # Ollama puede devolver arguments como dict (preferido) o como str JSON.
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            return json.dumps(
                {"error": f"arguments no es JSON valido: {exc}"},
                ensure_ascii=False,
            )
    if arguments is None:
        arguments = {}

    # El modelo a veces inventa parametros que la funcion no acepta (p.ej.
    # limit/page en list_my_pets). En vez de fallar, descartamos los que no
    # esten en la firma (salvo que la funcion acepte **kwargs).
    func = TOOL_REGISTRY[name]
    sig = inspect.signature(func)
    accepts_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if not accepts_var_kw and isinstance(arguments, dict):
        arguments = {k: v for k, v in arguments.items() if k in sig.parameters}

    try:
        result = func(**arguments)  # type: ignore[arg-type]
    except TypeError as exc:
        # Argumentos faltantes o sobrantes: el modelo extrajo mal los params.
        return json.dumps(
            {"error": f"argumentos invalidos para {name}: {exc}"},
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"error": f"{exc.__class__.__name__}: {exc}"},
            ensure_ascii=False,
        )

    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)
