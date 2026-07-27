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
import re
import unicodedata
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

# Registro de mascota EN PROGRESO por usuario (slot-filling entre turnos). Ver
# register_pet: acumula los datos que el usuario va dando para que el modelo no
# tenga que recordarlos. Se limpia al completar el registro o al cambiar de
# mascota. Es estado de proceso, por-usuario; suficiente para el caso de uso.
_pending_pet: dict[int, dict] = {}


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

def _explicit_if_empty(result: Any, mensaje_vacio: str) -> Any:
    """Si `result` es una lista VACIA, la envuelve en un mensaje explicito.

    Un modelo 8B que recibe `[]` a veces ALUCINA elementos plausibles (p.ej.
    invents mascotas 'Luna, Max, Bella') en vez de reportar el vacio. Devolver
    un texto claro ('el usuario NO tiene ...') es una red de seguridad
    deterministica: el modelo ya no puede ignorar el vacio. Los resultados NO
    vacios se devuelven tal cual (el modelo los reporta bien)."""
    if isinstance(result, list) and not result:
        return {"vacio": True, "mensaje": mensaje_vacio}
    return result


def list_my_pets() -> dict:
    """Lista las mascotas del usuario autenticado.

    Llama a GET /api/user/pets. Util cuando el usuario pregunta
    "que mascotas tengo registradas" o necesita el id de una mascota
    para agendar una cita.
    """
    return _explicit_if_empty(
        get_client().get("/api/user/pets"),
        "El usuario NO tiene ninguna mascota registrada. Diselo exactamente asi; "
        "NUNCA inventes ni menciones mascotas que no esten en esta lista.",
    )


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
VALID_HEIGHT = {"<30", "30-40", "41-50", "51-60", ">60"}

# Especies claramente ficticias/miticas: NO se registran tal cual (seria dato
# basura). Se compara sobre el texto normalizado (minusculas, sin acentos) y
# SOLO cuando la especie es EXACTAMENTE una de estas: asi "dragon barbudo" o
# "dragon de komodo", que SI son animales reales, no se bloquean; solo el
# "dragon" a secas y demas criaturas de fantasia.
_FICTIONAL_SPECIES = {
    "dragon", "unicornio", "fenix", "grifo", "hipogrifo", "pegaso", "sirena",
    "minotauro", "quimera", "kraken", "hidra", "basilisco", "cerbero",
    "pokemon", "pikachu", "charizard", "digimon", "tamagotchi", "furia nocturna",
    "dinosaurio", "trex", "t-rex", "velociraptor", "godzilla", "mascota virtual",
}

# Marcadores de especies EXTINTAS o prehistoricas (dinosaurios, homínidos,
# megafauna): reales pero NO se pueden tener de mascota hoy, asi que se tratan
# igual que las ficticias. Se buscan como subcadena del texto normalizado, por
# lo que atrapan "tyrannosaurus rex", "pachycephalosaurus", "australopithecus",
# etc. Los sufijos elegidos (-saurus/-raptor/-pithecus/-ceratops...) practicamente
# no aparecen en ningun animal de compañia actual, asi que casi no hay falsos +.
_EXTINCT_MARKERS = (
    "saurus", "saurio", "raptor", "pithecus", "piteco", "ceratops",
    "pterodactilo", "pterodactylo", "mamut", "mammoth", "mastodonte",
    "mastodon", "megalodon", "smilodon", "trilobite", "neandertal",
    "australopith", "homo erectus", "homo habilis", "dodo",
)


def _normalize_specie(s: Any) -> str:
    """minusculas + sin acentos + espacios colapsados, para comparar especies."""
    txt = str(s or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", txt).strip()


def _is_fictional_specie(specie: Any) -> bool:
    """True si la especie NO es una mascota posible hoy: ficticia/mitica (match
    exacto) o extinta/prehistorica (match por marcador). En ambos casos no se
    registra: se pide el animal real y actual."""
    n = _normalize_specie(specie)
    if n in _FICTIONAL_SPECIES:
        return True
    return any(mark in n for mark in _EXTINCT_MARKERS)


def _coerce_age(age: Any) -> int | None:
    """Convierte la edad a un ENTERO de años (lo que exige la API real).

    La API de SwingTails almacena `age` como numero entero (p.ej. 10), NO como
    categoria. El modelo puede pasar "10", "10 años", " 10 " o 10; extraemos el
    primer numero. Devuelve None si no hay un entero valido (>0) que enviar.
    """
    if age is None:
        return None
    if isinstance(age, bool):  # evita que True/False cuele como 1/0
        return None
    if isinstance(age, (int, float)):
        n = int(age)
        return n if n > 0 else None
    m = re.search(r"\d+", str(age))
    if not m:
        return None
    n = int(m.group())
    return n if 0 < n < 100 else None


# Razas comunes -> especie. Red de seguridad para el registro: el modelo 8B a
# veces NO deduce la especie a partir de la raza (el usuario dice "es un
# dachshund" y el modelo deja specie y breed vacios, y sigue preguntando). Con
# esto, si aparece una raza conocida (en breed O confundida en specie), fijamos
# la especie y la raza automaticamente.
_DOG_BREEDS = {
    "labrador", "golden", "golden retriever", "retriever", "pastor aleman",
    "pastor belga", "bulldog", "bulldog frances", "bulldog ingles", "frances",
    "chihuahua", "poodle", "french poodle", "caniche", "dachshund", "daschund",
    "dachshound", "salchicha", "perro salchicha", "pug", "carlino", "beagle",
    "boxer", "rottweiler", "doberman", "husky", "husky siberiano", "schnauzer",
    "yorkshire", "yorkie", "shih tzu", "maltes", "pomerania", "border collie",
    "collie", "pitbull", "pit bull", "american bully", "dalmata", "san bernardo",
    "gran danes", "akita", "chow chow", "cocker", "cocker spaniel", "basset",
    "basset hound", "galgo", "pointer", "weimaraner", "bull terrier", "terrier",
    "xoloitzcuintle", "xolo", "pekines", "samoyedo", "corgi", "springer",
}
_CAT_BREEDS = {
    "siames", "persa", "angora", "maine coon", "bengali", "sphynx", "esfinge",
    "ragdoll", "british shorthair", "azul ruso", "bombay", "abisinio",
    "siberiano", "scottish fold", "munchkin", "himalayo", "birmano",
}


def _infer_specie_and_breed(specie: str, breed: str | None) -> tuple[str, str | None]:
    """Deduce especie a partir de la raza y corrige campos intercambiados.

    - Si el modelo puso una RAZA en el campo `specie` (p.ej. specie='dachshund'),
      la mueve a `breed` y fija specie a 'perro'/'gato'.
    - Si falta `specie` pero `breed` es una raza conocida, deduce la especie.
    Asi 'es un dachshund' termina como specie='perro', breed='dachshund' sin que
    el modelo tenga que razonarlo."""
    ns = _normalize_specie(specie)
    nb = _normalize_specie(breed)
    if ns in _DOG_BREEDS or ns in _CAT_BREEDS:
        if not (breed or "").strip():
            breed = specie
        specie = "perro" if ns in _DOG_BREEDS else "gato"
        return specie, breed
    if not (specie or "").strip() and (nb in _DOG_BREEDS or nb in _CAT_BREEDS):
        specie = "perro" if nb in _DOG_BREEDS else "gato"
    return specie, breed


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
        age: Edad en AÑOS como numero entero (p.ej. 10). La API la guarda como
            numero, NO como categoria. Si el usuario no la dijo, deja "".
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

    # Red: deduce especie desde la raza (dachshund -> perro) y corrige campos
    # intercambiados, antes de validar los obligatorios.
    specie, breed = _infer_specie_and_breed(specie, breed)

    # --- Acumulador de registro por usuario (slot-filling) -------------------
    # Los resultados de las tools son EFIMEROS (no se guardan en memoria), asi
    # que entre turnos el 8B "olvida" datos que el usuario ya dio (p.ej. la raza
    # del primer mensaje). Guardamos el progreso por usuario y lo FUSIONAMOS:
    # cada llamada solo APORTA los campos no vacios, conservando los anteriores.
    # Es la solucion DETERMINISTA al bug de "vuelve a preguntar el nombre/raza".
    prev = _pending_pet.get(uid)
    if prev and name and prev.get("name") and _normalize_specie(prev["name"]) != _normalize_specie(name):
        prev = None  # nombre distinto -> es OTRA mascota, no arrastres datos viejos
    merged = dict(prev or {})
    for k, v in (
        ("name", name), ("specie", specie), ("sex", sex),
        ("age", age), ("height", height), ("breed", breed), ("weight", weight),
    ):
        if v not in (None, "") and not (isinstance(v, str) and not v.strip()):
            merged[k] = v
    name = merged.get("name", "") or ""
    specie = merged.get("specie", "") or ""
    sex = merged.get("sex", "") or ""
    age = merged.get("age", "")
    height = merged.get("height", "") or ""
    breed = merged.get("breed")
    weight = merged.get("weight")

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
    age_num = _coerce_age(age)
    if age_num is None:
        faltan.append("la edad en años (un numero, por ejemplo 10)")
    if height not in VALID_HEIGHT:
        faltan.append("la altura en cm (<30, 30-40, 41-50, 51-60 o >60)")
    if faltan:
        # Red anti-"vuelve a preguntar lo que ya dije": enumeramos lo que el
        # modelo YA nos paso en esta llamada y le ordenamos NO volver a pedirlo.
        # El bug reportado es que, tras juntar los datos faltantes, el 8B re-
        # preguntaba el nombre/raza que el usuario dio al inicio; poner el
        # recordatorio en el propio resultado de la tool (contexto inmediato) es
        # mucho mas efectivo que solo la regla del system prompt.
        ya = []
        if (name or "").strip():
            ya.append(f"nombre={name}")
        if (specie or "").strip():
            ya.append(f"especie={specie}")
        if breed:
            ya.append(f"raza={breed}")
        if sex in VALID_SEX:
            ya.append(f"sexo={sex}")
        if age_num is not None:
            ya.append(f"edad={age_num}")
        if height in VALID_HEIGHT:
            ya.append(f"altura={height}")
        if weight is not None:
            ya.append(f"peso={weight}")
        resumen = (
            f"Datos que YA tienes y NO debes volver a preguntar: {', '.join(ya)}. "
            if ya else ""
        )
        _pending_pet[uid] = merged  # guarda el progreso para el proximo turno
        return {"preguntar_al_usuario":
                f"{resumen}Falta que el usuario indique: {', '.join(faltan)}. Pregunta SOLO "
                f"eso (ofrece las opciones donde aplique). Cuando el usuario responda, llama "
                f"register_pet OTRA VEZ incluyendo TODOS los datos que ya tienes MAS los nuevos; "
                f"NO vuelvas a preguntar el nombre, la especie ni la raza. NO inventes valores "
                f"(la altura NO se deduce de la raza, el peso ni la edad)."}

    # Red de seguridad anti-dato-basura: no registres una especie de fantasia
    # (dragon, unicornio, pokemon...). El nombre puede ser de fantasia, la
    # especie no. Se pide el animal REAL antes de guardar.
    if _is_fictional_specie(specie):
        # Conserva el resto del progreso pero descarta la especie invalida.
        merged.pop("specie", None)
        _pending_pet[uid] = merged
        return {"preguntar_al_usuario":
                f"«{specie}» no es una mascota que se pueda tener hoy (parece una "
                f"especie de fantasia o extinta), asi que no puedo registrarla asi. "
                f"¿A que animal REAL y actual corresponde {name or 'tu mascota'}? "
                f"Por ejemplo: un perro, gato, conejo, o si es un reptil, un dragon "
                f"barbudo o un gecko. Dime la especie real y lo registro."}

    body: dict[str, Any] = {
        "name": name,
        "specie": specie,
        "sex": sex,
        "age": age_num,
        "height": height,
        "user_id": uid,
    }
    if breed is not None:
        body["breed"] = breed
    if weight is not None:
        body["weight"] = float(weight)
    result = get_client().post("/api/pets", json_body=body)
    # Registro enviado con exito -> limpia el acumulador de este usuario.
    if not (isinstance(result, dict) and result.get("error")):
        _pending_pet.pop(uid, None)
    return result


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
        age: Edad en AÑOS como numero entero (p.ej. 10). NO es categoria.
        height: '<30', '30-40', '41-50', '51-60' o '>60'.
        breed: Raza. Opcional.
        weight: Peso en kg. Opcional.

    Llama a PUT /api/pets/{id}. El user_id se toma de la sesion autenticada
    (es obligatorio en el body: la API lo usa para validar la propiedad).
    """
    uid = _current_user_id()
    if uid is None:
        return dict(_NO_SESSION_ERROR)
    specie, breed = _infer_specie_and_breed(specie, breed)
    age_num = _coerce_age(age)
    if age_num is None:
        return {"preguntar_al_usuario":
                "Necesito la edad de la mascota en años (un numero, por ejemplo "
                "10). Pideselo al usuario; no la inventes."}
    if _is_fictional_specie(specie):
        return {"preguntar_al_usuario":
                f"«{specie}» no es una mascota posible hoy (fantasia o especie "
                f"extinta). ¿A que animal REAL y actual corresponde? (por ejemplo: "
                f"perro, gato, conejo, o un reptil como dragon barbudo o gecko). "
                f"Dime la especie real y actualizo la ficha."}
    body: dict[str, Any] = {
        "name": name,
        "specie": specie,
        "sex": sex,
        "age": age_num,
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


def find_nearest_clinics(limit: int = 3) -> dict:
    """Devuelve las veterinarias MAS CERCANAS a la ubicacion del usuario.

    Uselo cuando el usuario pregunte por clinicas "cerca de mi", "las mas
    cercanas", "cual me queda mas cerca" o similar. La ubicacion (lat/lon) NO la
    provee el modelo: la comparte el usuario desde el navegador y se toma de la
    sesion. NUNCA inventes coordenadas ni distancias.

    Args:
        limit: Cuantas clinicas cercanas devolver (por defecto 3).

    Si el usuario NO ha compartido su ubicacion, devuelve
    {"necesita_ubicacion": true, ...}: en ese caso pidele que active el permiso
    de ubicacion del navegador (la app se lo solicitara) y vuelve a intentar.
    """
    import geo

    loc = geo.get_location()
    if loc is None:
        return {
            "necesita_ubicacion": True,
            "mensaje": "Para decirte las veterinarias mas cercanas necesito tu "
            "ubicacion. Pidele al usuario que ACEPTE el permiso de ubicacion que "
            "le mostrara el navegador y que vuelva a preguntar. No inventes "
            "clinicas ni distancias.",
        }

    clinics = get_client().get("/api/veterinary", params={"limit": 50, "page": 1})
    if isinstance(clinics, dict) and clinics.get("error"):
        return clinics
    if not isinstance(clinics, list) or not clinics:
        return {"vacio": True, "mensaje": "No hay clinicas registradas para comparar."}

    rankeadas = []
    for c in clinics:
        lat, lon = geo.clinic_coords(c)
        dist = geo.haversine_km(loc["lat"], loc["lon"], lat, lon)
        rankeadas.append((dist, c))
    rankeadas.sort(key=lambda t: t[0])

    n = max(1, min(int(limit or 3), 10))
    cercanas = []
    for dist, c in rankeadas[:n]:
        # NOTA: NO devolvemos la direccion. La API entrega direcciones ficticias
        # en formato de EE.UU. (faker) aunque la ciudad diga "Merida", lo que
        # produce respuestas incoherentes ("493 S Market Street en Merida"). Solo
        # el nombre y la distancia son utiles/coherentes aqui.
        cercanas.append({
            "name": c.get("name"),
            "distance_km": round(dist, 1),
        })
    return {
        "total": len(cercanas),
        "clinicas_cercanas": cercanas,
        "nota": "Distancias APROXIMADAS (simuladas), ordenadas de la mas cercana "
        "a la mas lejana. Preséntalas SOLO como 'nombre (distancia km)'; NO "
        "menciones direcciones (no son fiables).",
    }


def list_appointments(limit: int = 100, page: int = 1) -> dict:
    """Lista las citas del usuario autenticado.

    Args:
        limit: Cantidad maxima por pagina.
        page: Numero de pagina.

    Llama a GET /api/appointments/user (el backend filtra por el JWT, asi
    que solo devuelve las citas del usuario que conversa).
    """
    return _explicit_if_empty(
        get_client().get(
            "/api/appointments/user",
            params={"limit": int(limit), "page": int(page)},
        ),
        "El usuario NO tiene ninguna cita agendada. Diselo exactamente asi; "
        "NUNCA inventes citas, fechas ni clinicas que no esten en esta lista.",
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
    "find_nearest_clinics": find_nearest_clinics,
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
                    "specie": {"type": "string", "description": "Especie: perro, gato, etc. IMPORTANTE: si el usuario menciona una RAZA (dachshund, labrador, chihuahua, siames, persa...), esa raza YA indica la especie: una raza de perro -> pon 'perro'; una raza de gato -> pon 'gato'. No dejes este campo vacio si el usuario dio una raza."},
                    "sex": {"type": "string", "description": "'Macho' o 'Hembra'. Si el usuario no lo dijo, pasa cadena vacia \"\"."},
                    "age": {"type": "integer", "description": "Edad en AÑOS como numero entero (p.ej. 10). NO es una categoria. Si el usuario no lo dijo, pasa \"\"."},
                    "height": {"type": "string", "description": "Altura en cm: '<30', '30-40', '41-50', '51-60' o '>60'. Es una medida fisica que SOLO da el usuario; NUNCA la deduzcas de la raza, el peso ni la edad. Si el usuario no la dijo, pasa cadena vacia \"\"."},
                    "breed": {"type": "string", "description": "Raza de la mascota (p.ej. 'dachshund', 'labrador'). SIEMPRE captura la raza si el usuario la menciona; no la dejes vacia en ese caso."},
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
                        "type": "integer",
                        "description": "Edad en años (numero entero, p.ej. 10). NO es categoria.",
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
            "description": "Lista las clinicas veterinarias disponibles/registradas (todas). USA ESTA para 'busca clinicas', 'que veterinarias hay', 'clinicas disponibles'. NO requiere ubicacion.",
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
            "name": "find_nearest_clinics",
            "description": "Devuelve las veterinarias MAS CERCANAS por PROXIMIDAD a la ubicacion del usuario. USA ESTA SOLO cuando el usuario pida cercania explicitamente: 'cerca de mi', 'la mas cercana', 'cual me queda mas cerca', 'por mi ubicacion'. Para un listado general usa list_clinics. La ubicacion se toma de la sesion (el usuario la comparte desde el navegador); NO la pases tu. Si el usuario no compartio ubicacion, devuelve necesita_ubicacion=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Cuantas clinicas cercanas (por defecto 3).", "default": 3}
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


def execute_tool(
    name: str,
    arguments: dict | str | None,
    registry: dict[str, Callable[..., Any]] | None = None,
) -> str:
    """Ejecuta una tool por nombre con los argumentos del LLM.

    Devuelve SIEMPRE un string (lo que se inserta como `content` de un
    mensaje role=tool). Si la tool no existe o los argumentos son invalidos,
    devolvemos un error serializado en JSON - no levantamos excepcion -
    para no romper el ciclo conversacional del LLM.

    `registry` permite pasar un mapa name->callable distinto al global (lo usa
    el agente transaccional de la semana 07 para combinar las tools remotas con
    las locales de la BD de estres). Por defecto usa TOOL_REGISTRY.
    """
    registry = registry if registry is not None else TOOL_REGISTRY
    if name not in registry:
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
    func = registry[name]
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
