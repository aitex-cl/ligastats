"""Descarga de CSV desde football-data.co.uk.

Las temporadas pasadas se descargan una sola vez; la temporada en curso y la
lista de próximos partidos se vuelven a descargar en cada actualización.
"""
import logging
import time

import requests

from . import config

log = logging.getLogger(__name__)


def _bajar(url: str, destino, intentos: int = 3) -> bool:
    for intento in range(1, intentos + 1):
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": config.USER_AGENT})
            if r.status_code == 404:
                log.info("No existe (404): %s", url)
                return False
            r.raise_for_status()
            if len(r.content) < 100:
                log.warning("Archivo demasiado pequeño, se ignora: %s", url)
                return False
            tmp = destino.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(destino)
            return True
        except requests.RequestException as e:
            log.warning("Intento %d/%d falló para %s: %s", intento, intentos, url, e)
            time.sleep(3 * intento)
    raise RuntimeError(f"No se pudo descargar {url}")


def descargar(forzar: bool = False) -> dict:
    """Devuelve un resumen {archivo: 'nuevo'|'actualizado'|'en caché'|'no existe'}."""
    config.asegurar_directorios()
    resumen = {}
    actual = config.temporada_actual()
    for liga in config.LIGAS:
        for temp in config.temporadas():
            destino = config.RAW_DIR / f"{liga}_{temp}.csv"
            if destino.exists() and temp != actual and not forzar:
                resumen[destino.name] = "en caché"
                continue
            existia = destino.exists()
            ok = _bajar(config.URL_TEMPORADA.format(temporada=temp, liga=liga), destino)
            resumen[destino.name] = ("actualizado" if existia else "nuevo") if ok else "no existe"
            time.sleep(0.5)  # no saturar el sitio
    destino = config.RAW_DIR / "fixtures.csv"
    resumen["fixtures.csv"] = "actualizado" if _bajar(config.URL_FIXTURES, destino) else "no existe"
    return resumen
