"""Clima vía Open-Meteo: sin clave, sin registro."""

import httpx

from herramientas import herramienta

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@herramienta
def consultar_clima(ciudad: str) -> str:
    """Consulta la temperatura actual y la probabilidad de lluvia de una ciudad."""
    geo = httpx.get(
        _GEOCODING_URL, params={"name": ciudad, "count": 1, "language": "es"}, timeout=5.0
    ).json()
    resultados = geo.get("results")
    if not resultados:
        return f"No encontré ninguna ciudad llamada {ciudad!r}."
    lugar = resultados[0]

    pronostico = httpx.get(
        _FORECAST_URL,
        params={
            "latitude": lugar["latitude"],
            "longitude": lugar["longitude"],
            "current": "temperature_2m",
            "hourly": "precipitation_probability",
            "forecast_days": 1,
            "timezone": "auto",
        },
        timeout=5.0,
    ).json()

    temperatura = pronostico["current"]["temperature_2m"]
    probabilidades = pronostico["hourly"]["precipitation_probability"]
    hora_actual = int(pronostico["current"]["time"][11:13])
    proximas = probabilidades[hora_actual : hora_actual + 6]
    max_prob = max(proximas, default=0)

    return (
        f"En {lugar['name']} la temperatura actual es {temperatura}°C. "
        f"Probabilidad de lluvia en las próximas horas: hasta {max_prob}%."
    )
