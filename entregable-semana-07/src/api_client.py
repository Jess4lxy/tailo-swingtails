"""Cliente HTTP para la API publica de SwingTails.

Centraliza:
  - login (POST /api/auth/login) y manejo del JWT,
  - extraccion del user_id desde el propio token (el agente NO tiene un
    usuario propio: adopta la identidad del usuario que esta conversando),
  - cabeceras de autorizacion (Bearer),
  - manejo uniforme de errores (red, 4xx, 5xx, JSON invalido),
  - desempaquetado de las respuestas envueltas ({"data": ...}, {"items": ...}).

Las funciones que el LLM llama (tools.py) reutilizan este cliente; no se
encargan de autenticar ni de parsear errores HTTP, y NUNCA reciben el
user_id desde el modelo: lo toman de la sesion autenticada (current_user_id).

Nota arquitectonica: si una llamada falla, devolvemos un dict con
{"error": "..."} en vez de levantar excepcion. Asi tools.py puede inyectar
ese resultado al historial con role="tool" sin romper el ciclo del LLM
(rubrica fase 2: "el error no debe ensuciar la memoria del agente").
"""
from __future__ import annotations

import base64
import contextvars
import json
import time
from typing import Any

import requests

from config import (
    API_BASE,
    API_EMAIL,
    API_JWT,
    API_PASSWORD,
    API_TIMEOUT,
)


def _decode_jwt_user_id(token: str) -> int | None:
    """Extrae el `id` del usuario del payload de un JWT, sin validar firma.

    El access token de SwingTails embebe el id del usuario en su payload
    (p.ej. {"id": 29, "name": ..., "email": ...}). Lo usamos para auto-rellenar
    user_id en las operaciones de escritura: el modelo nunca lo inventa.

    No verificamos la firma (no tenemos el secreto y no hace falta: el backend
    revalida el token en cada request). Solo leemos el payload.
    """
    try:
        payload_b64 = token.split(".")[1]
        # base64url -> base64 + padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (IndexError, ValueError, json.JSONDecodeError):
        return None
    uid = payload.get("id") or payload.get("userId") or payload.get("user_id")
    try:
        return int(uid) if uid is not None else None
    except (TypeError, ValueError):
        return None


def token_is_expired(token: str, skew: int = 30) -> bool:
    """True si el JWT trae un `exp` ya vencido (con `skew` seg de margen).

    Los access tokens de SwingTails duran ~30 min. Detectamos aqui la expiracion
    para devolver un 401 limpio en lugar de reenviar un token muerto y que el
    modelo termine relatando un confuso \"su sesion ha expirado\". Si el token no
    trae `exp` legible, devolvemos False (no bloqueamos tokens sin expiracion)."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (IndexError, ValueError, json.JSONDecodeError):
        return False
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return time.time() > (exp + skew)


class SwingTailsClient:
    """Cliente delgado para la API de SwingTails con autenticacion JWT."""

    def __init__(self, base_url: str = API_BASE, timeout: int = API_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token: str | None = API_JWT  # JWT directo desde .env si existe
        self._user_id: int | None = (
            _decode_jwt_user_id(API_JWT) if API_JWT else None
        )

    def set_token(self, token: str | None) -> None:
        """Fija el JWT (y recalcula el user_id). Usado por el modo HTTP, donde
        cada peticion trae el token del usuario que conversa."""
        self._token = token or None
        self._user_id = _decode_jwt_user_id(token) if token else None

    def set_user_id(self, user_id: int | None) -> None:
        """Fija el user_id SIN token (sesion local para pruebas/evaluacion).

        Lo usa evaluar_agente.py para abrir una sesion en proceso contra las
        tools LOCALES (BD de estres) sin necesitar el login remoto: las tools
        de la agenda local solo requieren current_user_id, no el Bearer."""
        self._user_id = int(user_id) if user_id is not None else None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def login(self, email: str | None = None, password: str | None = None) -> dict:
        """Hace login y guarda el JWT (y el user_id) en memoria.

        Si no se pasan credenciales, usa SWINGTAILS_EMAIL y SWINGTAILS_PASSWORD
        del .env. Devuelve el cuerpo de la respuesta (o {"error": ...}).

        La API responde {"status": "success", "data": {"accessToken": "...",
        "refreshToken": "..."}}, asi que el token vive en data.accessToken.
        """
        email = email or API_EMAIL
        password = password or API_PASSWORD
        if not email or not password:
            return {"error": "Faltan credenciales (SWINGTAILS_EMAIL / SWINGTAILS_PASSWORD o login interactivo)"}

        resp = self._request(
            "POST",
            "/api/auth/login",
            json_body={"email": email, "password": password},
            auth=False,
        )
        if isinstance(resp, dict) and not resp.get("error"):
            data = resp.get("data") or {}
            token = (
                data.get("accessToken")
                or resp.get("accessToken")
                or data.get("token")
                or resp.get("token")
            )
            if token:
                self._token = token
                self._user_id = _decode_jwt_user_id(token)
        return resp

    def ensure_authenticated(self) -> None:
        """Si no hay JWT en memoria, intenta login con credenciales del .env."""
        if self._token:
            return
        if API_EMAIL and API_PASSWORD:
            self.login()

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    @property
    def current_user_id(self) -> int | None:
        """ID del usuario autenticado (extraido del JWT). None si no hay sesion."""
        return self._user_id

    # ------------------------------------------------------------------
    # Helpers HTTP genericos (usados por tools.py)
    # ------------------------------------------------------------------
    def get(self, path: str, params: dict | None = None) -> Any:
        return self._unwrap(self._request("GET", path, params=params))

    def post(self, path: str, json_body: dict | None = None) -> Any:
        return self._unwrap(self._request("POST", path, json_body=json_body))

    def put(self, path: str, json_body: dict | None = None) -> Any:
        return self._unwrap(self._request("PUT", path, json_body=json_body))

    def delete(self, path: str) -> Any:
        return self._unwrap(self._request("DELETE", path))

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------
    @staticmethod
    def _unwrap(body: Any) -> Any:
        """Desempaqueta las respuestas envueltas de la API.

        - {"data": [...], "total": N}                 -> [...]
        - {"items": [...], "totalItems": N, ...}      -> [...]  (paginacion)
        - {"status": "success", "data": {...}}        -> {...}

        Deja intactos los objetos sin envoltura y los dicts de error
        ({"error": ...}), que no traen "data" ni "items".
        """
        if isinstance(body, dict):
            if "data" in body and "error" not in body:
                return body["data"]
            if "items" in body and ("totalItems" in body or "totalPages" in body):
                return body["items"]
        return body

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        auth: bool = True,
        _retried: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            resp = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return {"error": f"Fallo de red: {exc.__class__.__name__}: {exc}"}

        # Cuerpo: intentamos JSON; si no es JSON devolvemos texto recortado.
        body: Any
        try:
            body = resp.json()
        except json.JSONDecodeError:
            body = resp.text[:500]

        if 200 <= resp.status_code < 300:
            return body

        # Token expirado (los access tokens duran ~30 min): reintentamos una
        # sola vez tras re-loguear con las credenciales del .env, si existen.
        if (
            resp.status_code == 401
            and auth
            and not _retried
            and API_EMAIL
            and API_PASSWORD
        ):
            self._token = None
            login_resp = self.login()
            if isinstance(login_resp, dict) and not login_resp.get("error") and self._token:
                return self._request(
                    method, path, params=params, json_body=json_body,
                    auth=auth, _retried=True,
                )

        # Error del SERVIDOR (5xx): la API existe pero fallo (p.ej. su BD en
        # Render caida). Damos un mensaje claro y accionable para que el modelo
        # NO lo confunda con un problema de la conexion del usuario.
        if resp.status_code >= 500:
            return {
                "error": (
                    "El servicio de SwingTails no esta disponible temporalmente "
                    "(error del servidor de la API). Dile al usuario que lo "
                    "intente mas tarde; NO es un problema de su conexion a internet."
                ),
                "http_status": resp.status_code,
            }

        # Otros errores HTTP (4xx): empaquetamos para que el LLM lo vea.
        return {
            "error": f"HTTP {resp.status_code}",
            "detalle": body if isinstance(body, (dict, list)) else str(body)[:300],
        }


# ---------------------------------------------------------------------------
# Resolucion del cliente activo
# ---------------------------------------------------------------------------
# Hay dos modos de uso:
#
#   - CLI (chat.py): un solo usuario por proceso. Se usa un singleton global
#     que se autentica con las credenciales del .env (o el login interactivo).
#
#   - Servicio HTTP (server.py): muchos usuarios concurrentes. Cada peticion
#     trae el JWT del usuario que conversa en su header Authorization. NO
#     podemos compartir un token global (se pisarian entre peticiones), asi
#     que cada request fija su propio cliente en un ContextVar aislado.
#     tools.py llama a get_client() sin saber en que modo esta: si hay un
#     cliente de request en el contexto, lo usa; si no, cae al singleton.
_default_client: SwingTailsClient | None = None
_request_client: contextvars.ContextVar[SwingTailsClient | None] = (
    contextvars.ContextVar("swingtails_request_client", default=None)
)


def get_client() -> SwingTailsClient:
    """Devuelve el cliente activo: el de la peticion actual (HTTP) o el
    singleton del proceso (CLI)."""
    scoped = _request_client.get()
    if scoped is not None:
        return scoped
    global _default_client
    if _default_client is None:
        _default_client = SwingTailsClient()
        _default_client.ensure_authenticated()
    return _default_client


def client_from_token(token: str) -> SwingTailsClient:
    """Crea un cliente ligado al JWT de un usuario (modo HTTP)."""
    client = SwingTailsClient()
    client.set_token(token)
    return client


def use_request_client(client: SwingTailsClient) -> contextvars.Token:
    """Activa `client` solo para el contexto actual. Devuelve un token que
    debe pasarse a reset_request_client() al terminar la peticion."""
    return _request_client.set(client)


def reset_request_client(token: contextvars.Token) -> None:
    _request_client.reset(token)
