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
    """Obtiene la ficha completa de UNA mascota por su id.

    Args:
        pet_id: Identificador numerico de la mascota.

    Llama a GET /api/pets/{id}. Util para ver edad, raza, sexo, peso
    antes de agendar o recomendar producto.
    """
    return get_client().get(f"/api/pets/{int(pet_id)}")


def register_pet(
    name: str,
    sex: str,
    age: str,
    height: str,
    specie: str | None = None,
    breed: str | None = None,
    weight: float | None = None,
) -> dict:
    """Registra una nueva mascota para el usuario autenticado.

    Args:
        name: Nombre de la mascota.
        sex: 'Macho' o 'Hembra' (enum estricto del API).
        age: 'Cachorro', 'Joven', 'Adulto' o 'Senior'.
        height: '<30', '30-40', '41-50', '51-60' o '>60' (cm).
        specie: Especie (perro, gato, ...). Opcional.
        breed: Raza. Opcional.
        weight: Peso en kg. Opcional.

    Llama a POST /api/pets. El propietario (user_id) se toma de la sesion
    autenticada, NO se pide al usuario. Si falta algun campo obligatorio,
    el LLM debe pedirselo en vez de inventarlo.
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    body: dict[str, Any] = {
        "name": name,
        "sex": sex,
        "age": age,
        "height": height,
        "user_id": uid,
    }
    if specie is not None:
        body["specie"] = specie
    if breed is not None:
        body["breed"] = breed
    if weight is not None:
        body["weight"] = float(weight)
    return get_client().post("/api/pets", json_body=body)


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


def list_appointments(limit: int = 10, page: int = 1) -> dict:
    """Lista las citas (proximas e historicas) del usuario.

    Args:
        limit: Cantidad maxima por pagina.
        page: Numero de pagina.

    Llama a GET /api/appointments. El backend filtra por usuario segun
    el JWT.
    """
    return get_client().get(
        "/api/appointments",
        params={"limit": int(limit), "page": int(page)},
    )


def book_appointment(
    pet_id: int,
    veterinary_id: int,
    date: str,
    reason: str,
) -> dict:
    """Agenda una cita veterinaria para el usuario autenticado.

    Args:
        pet_id: ID de la mascota (obtenlo de list_my_pets si no lo tienes).
        veterinary_id: ID de la clinica (obtenlo de list_clinics).
        date: Fecha y hora en formato ISO 8601 (YYYY-MM-DDTHH:MM:SS).
        reason: Motivo breve de la cita (consulta, vacunacion, control...).

    Llama a POST /api/appointments. El user_id se toma de la sesion
    autenticada. Antes de invocar esta funcion el LLM debe asegurarse de
    tener pet_id, veterinary_id, date y reason; si falta alguno, debe
    preguntarle al usuario (no inventar ids ni fechas).
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    return get_client().post(
        "/api/appointments",
        json_body={
            "user_id": uid,
            "pet_id": int(pet_id),
            "veterinary_id": int(veterinary_id),
            "date": date,
            "reason": reason,
        },
    )


def reschedule_appointment(
    appointment_id: int,
    pet_id: int,
    veterinary_id: int,
    date: str,
    reason: str,
) -> dict:
    """Reagenda una cita existente (cambia fecha u otros datos).

    Args:
        appointment_id: ID de la cita a modificar.
        pet_id: ID de la mascota.
        veterinary_id: ID de la clinica.
        date: Nueva fecha y hora ISO 8601.
        reason: Motivo de la cita.

    Llama a PUT /api/appointments/{id}. El user_id se toma de la sesion
    autenticada.
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    return get_client().put(
        f"/api/appointments/{int(appointment_id)}",
        json_body={
            "user_id": uid,
            "pet_id": int(pet_id),
            "veterinary_id": int(veterinary_id),
            "date": date,
            "reason": reason,
        },
    )


def cancel_appointment(appointment_id: int) -> dict:
    """Cancela (elimina) una cita por su id.

    Args:
        appointment_id: ID de la cita.

    Llama a DELETE /api/appointments/{id}.
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


def add_to_cart(product_id: int, quantity: int = 1) -> dict:
    """Agrega un producto al carrito del usuario autenticado.

    Args:
        product_id: ID del producto.
        quantity: Cantidad (default 1).

    Llama a POST /api/cart. El user_id se toma de la sesion autenticada.
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    return get_client().post(
        "/api/cart",
        json_body={
            "user_id": uid,
            "product_id": int(product_id),
            "quantity": int(quantity),
        },
    )


def view_cart() -> dict:
    """Muestra los productos actualmente en el carrito del usuario.

    Llama a GET /api/cart.
    """
    return get_client().get("/api/cart")


def purchase_history(limit: int = 10, page: int = 1) -> dict:
    """Historial de compras del usuario.

    Args:
        limit: Resultados por pagina.
        page: Numero de pagina.

    Llama a GET /api/purchase-history.
    """
    return get_client().get(
        "/api/purchase-history",
        params={"limit": int(limit), "page": int(page)},
    )


# ===========================================================================
# Registro central de tools (lo lee chat.py)
# ===========================================================================

# Mapa name -> callable. Es lo que usamos para ejecutar localmente cuando
# el LLM devuelve un tool_call con un name dado.
TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "list_my_pets": list_my_pets,
    "get_pet": get_pet,
    "register_pet": register_pet,
    "list_clinics": list_clinics,
    "list_appointments": list_appointments,
    "book_appointment": book_appointment,
    "reschedule_appointment": reschedule_appointment,
    "cancel_appointment": cancel_appointment,
    "list_products": list_products,
    "add_to_cart": add_to_cart,
    "view_cart": view_cart,
    "purchase_history": purchase_history,
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
            "description": "Registra una nueva mascota para el usuario autenticado (el propietario se infiere de la sesion).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "sex": {"type": "string", "enum": ["Macho", "Hembra"]},
                    "age": {
                        "type": "string",
                        "enum": ["Cachorro", "Joven", "Adulto", "Senior"],
                    },
                    "height": {
                        "type": "string",
                        "enum": ["<30", "30-40", "41-50", "51-60", ">60"],
                        "description": "Rango de altura en cm.",
                    },
                    "specie": {"type": "string"},
                    "breed": {"type": "string"},
                    "weight": {"type": "number", "description": "Peso en kg."},
                },
                "required": ["name", "sex", "age", "height"],
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
            "description": "Agenda una nueva cita veterinaria para el usuario autenticado (el user_id se infiere de la sesion).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pet_id": {"type": "integer"},
                    "veterinary_id": {"type": "integer"},
                    "date": {
                        "type": "string",
                        "description": "Fecha y hora ISO 8601 (YYYY-MM-DDTHH:MM:SS).",
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "pet_id",
                    "veterinary_id",
                    "date",
                    "reason",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": "Reagenda una cita existente (el user_id se infiere de la sesion).",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "integer"},
                    "pet_id": {"type": "integer"},
                    "veterinary_id": {"type": "integer"},
                    "date": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "appointment_id",
                    "pet_id",
                    "veterinary_id",
                    "date",
                    "reason",
                ],
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
            "name": "add_to_cart",
            "description": "Agrega un producto al carrito del usuario autenticado (el user_id se infiere de la sesion).",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "quantity": {"type": "integer", "default": 1},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_cart",
            "description": "Muestra los productos en el carrito del usuario.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "purchase_history",
            "description": "Historial de compras del usuario.",
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

    try:
        result = TOOL_REGISTRY[name](**arguments)  # type: ignore[arg-type]
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
