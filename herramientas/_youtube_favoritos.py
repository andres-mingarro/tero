"""Canales de YouTube que Tero va aprendiendo por uso, en vez de una
lista fija en el código.

Por qué: la primera versión de esta herramienta tenía un `Literal[...]`
con siete canales fijos -- el usuario lo marcó como un antipatrón, con
razón: pedir un canal que no está en esa lista ni siquiera es una llamada
válida para el modelo (el enum lo rechaza), así que no generaliza a nada
nuevo. La solución no es agrandar la lista a mano cada vez -- es no tener
lista fija. `reproducir_canal_youtube` (herramientas/youtube.py) acepta
texto libre; esta memoria es lo que hace que "poné Olga" siga siendo
rápido y preciso sin necesitar una lista cerrada: se busca primero acá
(sin red, por parecido fonético) y solo se sale a buscar en vivo a
YouTube si no hay nada parecido -- mismo patrón que ya usa
`reproducir_musica` contra Spotify (búsqueda libre, no un catálogo).

Aprende solo: cada vez que `reproducir_canal_youtube` resuelve un canal
(ya sea porque estaba acá o porque lo encontró buscando), lo guarda o le
suma una vez más -- así "Vorter" o una transcripción rota como
"Borteroc" apuntan a Vorterix la próxima vez, si ya es un canal
conocido, en vez de tratarse como un canal nuevo cada vez. Guardado en
`~/.config/tero/youtube_canales.json` (mismo patrón que la posición del
soul-connector o el token de Spotify).
"""

import difflib
import json
import re
import unicodedata
from pathlib import Path

import httpx

from herramientas import _pantalla_youtube

_RUTA = Path.home() / ".config" / "tero" / "youtube_canales.json"

# Punto de partida, no un techo: los siete canales que el usuario ya
# había pedido seguido el 2026-09-14 (resueltos a mano vía <link
# rel="canonical">, ver BITACORA.html). La memoria crece sola desde acá.
_SEMILLA = {
    "olga": {"handle": "olgaenvivo_", "id": "UC7mJ2EDXFomeDIRFu5FtEbA", "nombre": "Olga", "veces": 1},
    "mitre": {"handle": "Radiomitre", "id": "UCYvINPByAdCcpA0sWrF3I_w", "nombre": "Radio Mitre", "veces": 1},
    "urbana play": {"handle": "UrbanaPlayFM", "id": "UCC1kfsMJko54AqxtcFECt-A", "nombre": "Urbana Play", "veces": 1},
    "paren la mano": {"handle": "Parenlamano", "id": "UCulzKEqyE73gXCUqTimbP4A", "nombre": "Parén la Mano", "veces": 1},
    "aislados": {"handle": "AisladosElPodcast", "id": "UCXjNLHGKB83V7FxRvEeWJ5A", "nombre": "Aislados", "veces": 1},
    "vorterix": {"handle": "VorterixOficial", "id": "UCvCTWHCbBC0b9UIeLeNs8ug", "nombre": "Vorterix", "veces": 1},
    "midu": {"handle": "midudev", "id": "UC8LeXCWOalN8SxlrPcG-PaQ", "nombre": "Midu", "veces": 1},
}

# Medido en vivo contra las transcripciones reales que motivaron esto
# ("Bortegui"/"Portelix"/"Bordelix" para "Vorterix" dieron 0.62-0.75;
# palabras sin relación como "Olga" contra "Vorterix" dan ~0.15-0.17) --
# 0.55 separa bien un mal transcripto de un canal realmente distinto.
_UMBRAL_PARECIDO = 0.55


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", texto).strip()


def _cargar() -> dict:
    if not _RUTA.exists():
        return dict(_SEMILLA)
    try:
        return json.loads(_RUTA.read_text())
    except Exception:
        return dict(_SEMILLA)


def _guardar(datos: dict) -> None:
    _RUTA.parent.mkdir(parents=True, exist_ok=True)
    _RUTA.write_text(json.dumps(datos, ensure_ascii=False, indent=2))


def buscar_aprendido(texto: str) -> dict | None:
    """El canal ya conocido más parecido a `texto` (por nombre, no id),
    o None si ninguno se acerca lo suficiente -- ahí es cuando
    reproducir_canal_youtube tiene que salir a buscar en vivo.

    A propósito compara la frase entera, no palabra por palabra: se
    probó separar en palabras para tolerar muletillas ("Bueno,
    Bortelix" en vez de solo "Bortelix") y se descartó -- "hola" contra
    "olga" da 0.75 de parecido (mismas cuatro letras), idéntico al de
    "bortelix" contra "vorterix", así que esa vía abría el canal de
    Olga cada vez que alguien decía "hola, ¿cómo estás?". Con umbrales
    de texto simple, más permisivo no es más general, es más frágil."""
    objetivo = _normalizar(texto)
    mejor, mejor_ratio = None, 0.0
    for clave, info in _cargar().items():
        ratio = difflib.SequenceMatcher(None, objetivo, clave).ratio()
        if ratio > mejor_ratio:
            mejor, mejor_ratio = info, ratio
    return mejor if mejor_ratio >= _UMBRAL_PARECIDO else None


def abrir_si_conocido(texto: str) -> str | None:
    """Si `texto` se parece a un canal ya aprendido, lo abre y devuelve
    la frase de confirmación; None si no hay nada parecido (no hace
    nada en ese caso). Un solo lugar para esta lógica -- la usan tanto
    `reproducir_musica` (herramientas/musica.py, "poné X" es ambiguo
    entre canción y canal) como `cerebro/router.py` (red de seguridad
    para cuando la transcripción salió tan mal que ni el verbo del
    pedido sobrevivió -- ver BITACORA.html, 2026-09-14: pedirle al
    modelo que reconstruya el verbo por prompt desestabilizó el tool
    calling, así que esto se resuelve en código, no en el prompt)."""
    canal = buscar_aprendido(texto)
    if canal is None:
        return None
    recordar(canal["handle"], canal["id"], canal["nombre"])
    _pantalla_youtube.mostrar(f"https://www.youtube.com/@{canal['handle']}/live")
    return f"Abrí {canal['nombre']} en vivo."


def recordar(handle: str, id_canal: str, nombre: str) -> None:
    """Guarda o refuerza un canal -- se llama cada vez que
    reproducir_canal_youtube abre algo, tanto si ya estaba acá (para
    llevar la cuenta de veces) como si lo acaba de resolver buscando.

    Clave por `nombre` (el nombre real del canal en YouTube), NUNCA por
    lo que dijo el usuario esta vez -- si se guardara por el texto
    transcripto, cada mala transcripción distinta de "Vorterix"
    ("Bortegui", "Portelix", "Bordelix"...) crearía una entrada nueva en
    vez de reforzar la misma (bug real, encontrado probando esto mismo:
    quedaban cuatro entradas para un solo canal, cada una con veces=1)."""
    datos = _cargar()
    clave = _normalizar(nombre)
    veces_previas = datos.get(clave, {}).get("veces", 0)
    datos[clave] = {"handle": handle, "id": id_canal, "nombre": nombre, "veces": veces_previas + 1}
    _guardar(datos)


def todos() -> list[dict]:
    """Para sugerir_canales_youtube: todo lo aprendido hasta ahora."""
    return list(_cargar().values())


def resolver_por_busqueda(texto: str) -> dict | None:
    """Busca `texto` en YouTube y devuelve el primer canal real que
    encuentra, o None si no hay nada -- sin API key: se scrapea la
    página de resultados filtrada a canales (parámetro `sp=EgIQAg==`, el
    mismo que aplica el filtro "Canal" de la búsqueda real de YouTube).
    Solo se llega acá cuando buscar_aprendido() no encontró nada
    parecido, así que es aceptable que tarde un poco más (una request)."""
    try:
        respuesta = httpx.get(
            "https://www.youtube.com/results",
            params={"search_query": texto, "sp": "EgIQAg=="},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8.0,
        )
        respuesta.raise_for_status()
    except Exception:
        return None
    match = re.search(
        r'"channelRenderer":\{"channelId":"([^"]+)".*?'
        r'"title":\{"simpleText":"([^"]+)".*?'
        r'"canonicalBaseUrl":"/@([^"]+)"',
        respuesta.text,
    )
    if not match:
        return None
    id_canal, nombre, handle = match.groups()
    return {"id": id_canal, "nombre": nombre, "handle": handle}
