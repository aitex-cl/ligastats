"""Generación de proyecciones y señales para los próximos partidos.

Cada proyección se guarda una sola vez (la primera vez que el partido entra
en la ventana de generación) y queda congelada. Tipos:

- senal:      hay cuota de mercado y la ventaja supera MARGEN_VALOR
- candidata:  no hay cuota en los datos (tarjetas, corners); el modelo es
              confiado y entrega la cuota mínima a la que conviene apostar
- proyeccion: el resto; se guarda igual para medir la calibración
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from . import config, db
from .modelo import ajustar_liga, cuota_justa, prob_sin_margen

log = logging.getLogger(__name__)

CAMPOS = ["partido_id", "liga", "fecha", "hora", "local", "visita", "arbitro", "mercado",
          "linea", "lado", "media_esperada", "prob", "cuota_justa", "cuota_minima",
          "cuota_mercado", "prob_implicita", "valor", "tipo", "version_modelo", "creado_en"]


def partidos_df(con) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM partidos", con)


def _filas_partido(fx: dict, proy: dict) -> list[dict]:
    filas = []
    for mercado, res in proy.items():
        if "omitido" in res:
            continue
        for linea, p_over in res["lineas"].items():
            for lado, p in (("over", p_over), ("under", 1 - p_over)):
                justa = cuota_justa(p)
                minima = config.cuota_minima(p)
                cuota = imp = valor = None
                tipo = "proyeccion"
                apostable = mercado in config.MERCADOS_APUESTA
                if mercado == "goles" and linea == 2.5 and fx.get("c_o25") and fx.get("c_u25"):
                    cuota = fx["c_o25"] if lado == "over" else fx["c_u25"]
                    p_over_mkt = prob_sin_margen(fx["c_o25"], fx["c_u25"])
                    imp = p_over_mkt if lado == "over" else 1 - p_over_mkt
                    valor = p - imp
                    if apostable and valor >= config.MARGEN_VALOR and cuota >= minima:
                        tipo = "senal"
                elif apostable and config.es_candidata(p):
                    tipo = "candidata"
                filas.append({
                    "partido_id": fx["id"], "liga": fx["liga"], "fecha": fx["fecha"],
                    "hora": fx.get("hora"), "local": fx["local"], "visita": fx["visita"],
                    "arbitro": fx.get("arbitro"), "mercado": mercado, "linea": linea, "lado": lado,
                    "media_esperada": round(float(res["media"]), 4), "prob": round(float(p), 4),
                    "cuota_justa": round(justa, 3), "cuota_minima": round(minima, 3),
                    "cuota_mercado": cuota, "prob_implicita": None if imp is None else round(imp, 4),
                    "valor": None if valor is None else round(valor, 4), "tipo": tipo,
                    "version_modelo": config.VERSION_MODELO, "creado_en": db.ahora(),
                })
    return filas


def generar(hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    with db.conexion() as con:
        config_aplicada = db.aplicar_parametros(con)
    limite = hoy + timedelta(days=max(1, config.HORAS_ANTES_GENERAR // 24))
    resumen = {"partidos_evaluados": 0, "nuevas": 0, "senales": 0, "candidatas": 0, "omitidos": [],
               "parametros": config_aplicada}
    with db.conexion() as con:
        fixtures = [dict(r) for r in con.execute(
            "SELECT * FROM proximos WHERE fecha >= ? AND fecha <= ? ORDER BY fecha, hora",
            (hoy.isoformat(), limite.isoformat()))]
        if not fixtures:
            return resumen
        partidos = partidos_df(con)
        modelos = {}
        for fx in fixtures:
            liga = fx["liga"]
            if liga not in modelos:
                modelos[liga] = ajustar_liga(partidos, liga, hoy.isoformat())
            proy = modelos[liga].proyectar(fx["local"], fx["visita"], fx.get("arbitro"))
            resumen["partidos_evaluados"] += 1
            for mercado, res in proy.items():
                if "omitido" in res:
                    resumen["omitidos"].append(f"{fx['local']}-{fx['visita']} {mercado}: {res['omitido']}")
            for fila in _filas_partido(fx, proy):
                cur = con.execute(
                    f"INSERT OR IGNORE INTO proyecciones ({','.join(CAMPOS)}) VALUES ({','.join('?' * len(CAMPOS))})",
                    [fila[c] for c in CAMPOS])
                if cur.rowcount:
                    resumen["nuevas"] += 1
                    resumen["senales"] += fila["tipo"] == "senal"
                    resumen["candidatas"] += fila["tipo"] == "candidata"
    return resumen


def proyectar_partido(liga: str, local: str, visita: str, arbitro: str | None = None,
                      fecha: str | None = None) -> dict:
    """Proyección en vivo (no se guarda), útil para consultar cualquier cruce."""
    with db.conexion() as con:
        partidos = partidos_df(con)
    modelo = ajustar_liga(partidos, liga, fecha or date.today().isoformat())
    proy = modelo.proyectar(local, visita, arbitro)
    salida = {}
    for mercado, res in proy.items():
        if "omitido" in res:
            salida[mercado] = res
            continue
        salida[mercado] = {
            "total_esperado": round(float(res["media"]), 2),
            "esperado_local": round(float(res["local"]), 2),
            "esperado_visita": round(float(res["visita"]), 2),
            "arbitro_considerado": res["arbitro_usado"],
            "lineas": {f"{l}": {"over": round(p, 3), "under": round(1 - p, 3),
                                "cuota_justa_over": round(cuota_justa(p), 2),
                                "cuota_justa_under": round(cuota_justa(1 - p), 2)}
                       for l, p in res["lineas"].items()},
        }
    return salida
