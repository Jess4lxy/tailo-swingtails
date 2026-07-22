"""Lectura de enlaces que comparte el usuario (entregable semana 07).

Cuando el usuario pega una URL en el chat (p.ej. una pagina sobre una raza de
perro), el BACKEND la descarga, extrae el texto legible y lo inyecta al contexto
para que el agente lo relacione con la conversacion. Asi el modelo NO tiene que
"leer" nada por su cuenta (antes alucinaba e incluso ofrecia escribir un script
de Python para abrir el enlace).

Seguridad (importante: descargamos URLs ARBITRARIAS que manda el usuario):
  - Solo esquemas http/https.
  - Anti-SSRF: se resuelve el host y se RECHAZA si apunta a IP privada, loopback,
    link-local o reservada (evita que alguien haga que el server pegue a
    169.254.x, 127.0.0.1, 10.x, metadata de la nube, etc.).
  - Limite de tamaño (no descargar archivos enormes) y timeout corto.
  - Solo se procesa contenido de texto (text/html, text/plain); otros tipos
    (pdf, imagenes, binarios) se reportan como no legibles.

No agrega dependencias: usa requests (ya presente) + html.parser (stdlib).
"""
from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

from config import (
    WEB_READ_ENABLED,
    WEB_READ_MAX_BYTES,
    WEB_READ_MAX_CHARS,
    WEB_READ_MAX_URLS,
    WEB_READ_TIMEOUT,
)

# Detecta URLs http/https en el texto del usuario.
_URL_RX = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

# User-Agent de navegador: muchos sitios responden 403 a clientes "raros".
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}


def extract_urls(text: str) -> list[str]:
    """Devuelve las URLs http/https del texto, sin duplicados y en orden."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RX.finditer(text):
        url = m.group(0).rstrip(".,;:)]}")  # limpia puntuacion final pegada
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


# ---------------------------------------------------------------------------
# Extraccion de texto legible de HTML (sin dependencias externas).
# ---------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Acumula el texto visible, ignorando script/style, y captura el <title>."""

    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title: str = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        text = data.strip()
        if text:
            self.parts.append(text + " ")

    def get_text(self) -> str:
        raw = "".join(self.parts)
        # Colapsa espacios y limita saltos de linea consecutivos.
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


# ---------------------------------------------------------------------------
# Anti-SSRF: valida que el host no sea una direccion interna.
# ---------------------------------------------------------------------------
def _host_is_safe(host: str) -> bool:
    """False si el host resuelve a una IP privada/loopback/reservada."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return False
    return True


def _fetch_one(url: str) -> dict:
    """Descarga y extrae UNA url. Devuelve {url, ok, title?, text?, error?}."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"url": url, "ok": False, "error": "esquema no soportado (solo http/https)"}
    if not _host_is_safe(parsed.hostname):
        return {"url": url, "ok": False, "error": "el enlace apunta a una direccion no permitida"}

    try:
        resp = requests.get(
            url, headers=_HEADERS, timeout=WEB_READ_TIMEOUT,
            stream=True, allow_redirects=True,
        )
    except requests.RequestException as exc:
        return {"url": url, "ok": False, "error": f"no se pudo abrir ({exc.__class__.__name__})"}

    with resp:
        if resp.status_code >= 400:
            return {"url": url, "ok": False, "error": f"el sitio respondio HTTP {resp.status_code}"}

        # Si hubo redireccion, revalida el destino final (anti-SSRF).
        final_host = urlparse(str(resp.url)).hostname
        if final_host and not _host_is_safe(final_host):
            return {"url": url, "ok": False, "error": "la redireccion apunta a una direccion no permitida"}

        ctype = (resp.headers.get("Content-Type") or "").lower()
        if not ("text/html" in ctype or "text/plain" in ctype or "xml" in ctype or ctype == ""):
            return {"url": url, "ok": False,
                    "error": f"el enlace no es una pagina de texto ({ctype.split(';')[0] or 'desconocido'})"}

        # Lee con tope de bytes (no descargar archivos enormes).
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=16384):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= WEB_READ_MAX_BYTES:
                break
        raw = b"".join(chunks)

    encoding = resp.encoding or "utf-8"
    try:
        html = raw.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        html = raw.decode("utf-8", errors="replace")

    if "html" in ctype or "<html" in html[:2000].lower():
        parser = _TextExtractor()
        try:
            parser.feed(html)
        except Exception:  # noqa: BLE001 - HTML malformado no debe romper el turno
            pass
        title = parser.title.strip()
        text = parser.get_text()
    else:
        title = ""
        text = html.strip()

    if not text:
        return {"url": url, "ok": False, "error": "la pagina no tiene texto legible"}

    if len(text) > WEB_READ_MAX_CHARS:
        text = text[:WEB_READ_MAX_CHARS].rstrip() + " […contenido recortado]"
    return {"url": url, "ok": True, "title": title, "text": text}


def read_urls(urls: list[str]) -> tuple[str, list[dict]]:
    """Lee hasta WEB_READ_MAX_URLS enlaces y arma el bloque a inyectar al LLM.

    Devuelve (bloque_texto, resultados). Si el bloque es "" no hay nada que
    inyectar (por ejemplo si la lectura esta deshabilitada). El bloque explica al
    modelo que el contenido YA fue leido por el sistema, y para los enlaces que
    fallaron le dice que NO invente su contenido.
    """
    if not WEB_READ_ENABLED or not urls:
        return "", []

    resultados = [_fetch_one(u) for u in urls[:WEB_READ_MAX_URLS]]

    secciones: list[str] = []
    for r in resultados:
        if r["ok"]:
            encabezado = f"### {r['url']}"
            if r.get("title"):
                encabezado += f" — {r['title']}"
            secciones.append(f"{encabezado}\n{r['text']}")
        else:
            secciones.append(
                f"### {r['url']}\n[NO se pudo leer este enlace: {r['error']}. "
                f"Dile al usuario que no pudiste abrir esa pagina y ayudalo con lo "
                f"que sepas; NO inventes ni afirmes su contenido.]"
            )

    bloque = (
        "[Contenido de los enlaces que el usuario compartio - el sistema YA los "
        "leyo por ti; usalos para responder su mensaje. NO digas que no puedes "
        "abrir enlaces ni ofrezcas escribir codigo para leerlos.]\n\n"
        + "\n\n".join(secciones)
        + "\n[Fin del contenido de los enlaces]"
    )
    return bloque, resultados
