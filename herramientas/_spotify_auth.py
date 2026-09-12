"""Login OAuth de Spotify (PKCE) para la Web API.

No es una herramienta (no se decora con @herramienta): es un helper interno
que usa herramientas/musica.py. Solo hace falta loguearse una vez — abre el
navegador, el usuario autoriza, y el refresh token queda guardado en
~/.config/tero/spotify_token.json (fuera del repo, nunca en git). Las
llamadas siguientes piden un access token nuevo con ese refresh token sin
volver a mostrar el navegador.

PKCE en vez de Authorization Code clásico: es una app de escritorio, no hay
forma de guardar un client secret con seguridad, y Spotify pide PKCE para
apps públicas como esta (no hace falta secret en absoluto).
"""

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

_AUTH_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_REDIRECT_URI = "http://127.0.0.1:8942/callback"
_SCOPE = "user-modify-playback-state user-read-playback-state"
_RUTA_TOKEN = Path.home() / ".config" / "tero" / "spotify_token.json"


def _code_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class _HandlerCallback(http.server.BaseHTTPRequestHandler):
    codigo: str | None = None

    def do_GET(self) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _HandlerCallback.codigo = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<p>Listo, ya podés cerrar esta pestaña.</p>".encode())

    def log_message(self, *args) -> None:
        pass  # no ensuciar stdout del daemon con logs http


def _esperar_callback() -> str:
    servidor = http.server.HTTPServer(("127.0.0.1", 8942), _HandlerCallback)
    hilo = threading.Thread(target=servidor.handle_request, daemon=True)
    hilo.start()
    hilo.join(timeout=120)
    servidor.server_close()
    if _HandlerCallback.codigo is None:
        raise RuntimeError("No llegó el código de autorización (¿se canceló el login o pasaron 2 min?)")
    return _HandlerCallback.codigo


def _login(client_id: str) -> dict:
    verifier = _code_verifier()
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": _REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": _code_challenge(verifier),
        "scope": _SCOPE,
    }
    webbrowser.open(f"{_AUTH_URL}?{urllib.parse.urlencode(params)}")
    codigo = _esperar_callback()
    respuesta = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": codigo,
            "redirect_uri": _REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=10.0,
    )
    respuesta.raise_for_status()
    return respuesta.json()


def _refrescar(client_id: str, refresh_token: str) -> dict:
    respuesta = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        timeout=10.0,
    )
    respuesta.raise_for_status()
    return respuesta.json()


def _guardar(tokens: dict) -> None:
    _RUTA_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    datos = {
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens["access_token"],
        "vence": time.time() + tokens["expires_in"] - 60,
    }
    _RUTA_TOKEN.write_text(json.dumps(datos))
    _RUTA_TOKEN.chmod(0o600)


def _client_id() -> str:
    import tomllib

    ruta_config = Path(__file__).parent.parent / "config.toml"
    with open(ruta_config, "rb") as f:
        config = tomllib.load(f)
    return config["spotify"]["client_id"]


def obtener_token() -> str:
    """Devuelve un access token válido, logueándose o refrescando si hace falta."""
    client_id = _client_id()
    if _RUTA_TOKEN.exists():
        datos = json.loads(_RUTA_TOKEN.read_text())
        if time.time() < datos["vence"]:
            return datos["access_token"]
        tokens = _refrescar(client_id, datos["refresh_token"])
        tokens.setdefault("refresh_token", datos["refresh_token"])  # el refresh no siempre lo repite
    else:
        tokens = _login(client_id)
    _guardar(tokens)
    return tokens["access_token"]


if __name__ == "__main__":
    obtener_token()
    print(f"Login OK, token guardado en {_RUTA_TOKEN}")
