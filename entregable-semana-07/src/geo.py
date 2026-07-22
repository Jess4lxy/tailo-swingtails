"""Geolocalizacion para "veterinarias mas cercanas" (entregable semana 07).

Permite responder consultas del tipo "que veterinarias tengo cerca" usando la
ubicacion REAL del usuario (que el frontend obtiene con la Geolocation API del
navegador y envia en el cuerpo del chat).

LIMITACION DEL PROTOTIPO (documentada a proposito): ni la API de SwingTails ni
el corpus traen coordenadas de las clinicas (son datos ficticios de faker:
direcciones inventadas, estado "Hawaii", etc.). Por eso asignamos a cada clinica
una coordenada SINTETICA pero DETERMINISTA (derivada de su id) dispersa dentro de
un radio alrededor de una ciudad base (Merida por defecto, la del corpus). Asi:
  - la distancia se calcula de verdad (formula de Haversine) desde el usuario,
  - el orden "mas cercana primero" es estable entre corridas,
  - si la API algun dia entrega latitude/longitude reales, basta con leerlos en
    `clinic_coords()` y el resto del pipeline no cambia.

El user_id ya se toma de la sesion (api_client); la UBICACION se maneja igual:
un ContextVar por peticion que el server fija con lo que mando el frontend, y las
tools la leen sin que el modelo la invente.
"""
from __future__ import annotations

import contextvars
import hashlib
import math

from config import GEO_BASE_LAT, GEO_BASE_LON, GEO_SPREAD_DEG


# ---------------------------------------------------------------------------
# Ubicacion del usuario para la peticion en curso (ContextVar, por-hilo).
# ---------------------------------------------------------------------------
_request_location: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "swingtails_request_location", default=None
)


def use_request_location(location: dict | None) -> contextvars.Token:
    """Activa la ubicacion {lat, lon} solo para el contexto actual."""
    return _request_location.set(location)


def reset_request_location(token: contextvars.Token) -> None:
    _request_location.reset(token)


def get_location() -> dict | None:
    """Ubicacion del usuario en la peticion actual, o None si no la compartio."""
    loc = _request_location.get()
    if not loc:
        return None
    lat, lon = loc.get("lat"), loc.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return {"lat": float(lat), "lon": float(lon)}
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Distancia geodesica (Haversine).
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en kilometros entre dos puntos (lat/lon en grados)."""
    r = 6371.0  # radio medio de la Tierra en km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------------------
# Coordenadas de una clinica (sinteticas y deterministas; ver docstring arriba).
# ---------------------------------------------------------------------------
def clinic_coords(clinic: dict) -> tuple[float, float]:
    """Devuelve (lat, lon) de una clinica.

    Si el registro trae latitude/longitude reales (por si la API los agrega en el
    futuro), se usan. Si no, se generan de forma DETERMINISTA a partir del id: un
    hash estable -> dos offsets en [-spread, +spread] grados alrededor de la
    ciudad base. Mismo id => misma coordenada siempre.
    """
    lat = clinic.get("latitude") or clinic.get("lat")
    lon = clinic.get("longitude") or clinic.get("lon") or clinic.get("lng")
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            pass

    key = str(clinic.get("id") or clinic.get("name") or "")
    h = hashlib.md5(key.encode("utf-8")).digest()
    # Dos enteros de 16 bits -> [0,1) -> [-1,1) -> escala por spread.
    u = int.from_bytes(h[0:2], "big") / 65535.0
    v = int.from_bytes(h[2:4], "big") / 65535.0
    d_lat = (u * 2 - 1) * GEO_SPREAD_DEG
    d_lon = (v * 2 - 1) * GEO_SPREAD_DEG
    return GEO_BASE_LAT + d_lat, GEO_BASE_LON + d_lon
