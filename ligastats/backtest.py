"""Backtest sin mirar el futuro (walk-forward).

Cada semana se ajusta el modelo solo con partidos anteriores y se proyectan
los partidos de esa semana. Se mide:

- calibración y Brier de goles, tarjetas y corners en todas las líneas,
  contra una referencia ingenua (frecuencia histórica de la liga);
- cómo habrían rendido las 'candidatas' (tarjetas/corners sin cuota);
- en goles 2.5, apuestas simuladas a la cuota previa con ROI y CLV contra
  el cierre de Pinnacle sin margen.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from . import config, db
from .modelo import ajustar_liga, prob_sin_margen
from .rendimiento import calibracion, resumen_prob

log = logging.getLogger(__name__)


def _registros_liga(partidos: pd.DataFrame, liga: str, dias_calentamiento: int = 365) -> pd.DataFrame:
    df = partidos[partidos["liga"] == liga].copy()
    if df.empty:
        return pd.DataFrame()
    df["f"] = pd.to_datetime(df["fecha"])
    inicio = df["f"].min() + pd.Timedelta(days=dias_calentamiento)
    df["semana"] = df["f"] - pd.to_timedelta(df["f"].dt.weekday, unit="D")
    totales = {
        "goles": df["gl"] + df["gv"],
        "corners": df["cl"] + df["cv"],
        "tarjetas": df["al"] + df["av"] + config.PESO_ROJA * (df["rl"].fillna(0) + df["rv"].fillna(0)),
    }
    for mercado, serie in totales.items():
        df["tot_" + mercado] = pd.to_numeric(serie, errors="coerce")
    filas = []
    for semana, g in df[df["f"] >= inicio].groupby("semana"):
        modelo = ajustar_liga(df, liga, semana.strftime("%Y-%m-%d"))
        previos = df[(df["f"] < semana) & (df["f"] >= semana - pd.Timedelta(days=config.VENTANA_DIAS))]
        base = {}
        for mercado in config.MERCADOS:
            tot = previos["tot_" + mercado].dropna()
            base[mercado] = {l: (float((tot > l).mean()) if len(tot) else np.nan) for l in config.LINEAS[mercado]}
        for _, p in g.iterrows():
            proy = modelo.proyectar(p["local"], p["visita"], p["arbitro"])
            for mercado, res in proy.items():
                if "omitido" in res:
                    continue
                real = p["tot_" + mercado]
                if pd.isna(real):
                    continue
                for linea, prob in res["lineas"].items():
                    filas.append({
                        "liga": liga, "fecha": p["fecha"], "temporada": p["temporada"],
                        "mercado": mercado, "linea": linea, "prob_over": prob,
                        "base_over": base[mercado][linea], "over": float(real > linea),
                        "media": res["media"], "real": real,
                        "c_o25": p["c_o25"], "c_u25": p["c_u25"],
                        "cc_o25": p["cc_o25"], "cc_u25": p["cc_u25"],
                    })
    return pd.DataFrame(filas)


def _simular_goles(r: pd.DataFrame) -> dict:
    g = r[(r["mercado"] == "goles") & (r["linea"] == 2.5)].dropna(subset=["c_o25", "c_u25"])
    if g.empty:
        return {"n_apuestas": 0, "nota": "sin cuotas de goles en los datos"}
    apuestas = []
    for _, x in g.iterrows():
        p_over_mkt = prob_sin_margen(x["c_o25"], x["c_u25"])
        for lado, p, cuota, imp in (("over", x["prob_over"], x["c_o25"], p_over_mkt),
                                    ("under", 1 - x["prob_over"], x["c_u25"], 1 - p_over_mkt)):
            minima = config.cuota_minima(p)
            if p - imp >= config.MARGEN_VALOR and cuota >= minima:
                gana = x["over"] == 1.0 if lado == "over" else x["over"] == 0.0
                clv = None
                if pd.notna(x["cc_o25"]) and pd.notna(x["cc_u25"]):
                    pc = prob_sin_margen(x["cc_o25"], x["cc_u25"])
                    pc = pc if lado == "over" else 1 - pc
                    clv = cuota * pc - 1  # = cuota / cuota_cierre_justa - 1
                apuestas.append({"temporada": x["temporada"], "lado": lado, "cuota": cuota,
                                 "ganancia": cuota - 1 if gana else -1.0, "clv": clv})
    if not apuestas:
        return {"n_apuestas": 0, "partidos_con_cuota": int(len(g))}
    a = pd.DataFrame(apuestas)
    clv = a["clv"].dropna()

    def bloque(x):
        c = x["clv"].dropna()
        return {"n": int(len(x)), "roi": round(float(x["ganancia"].mean()), 4),
                "clv_medio": round(float(c.mean()), 4) if len(c) else None,
                "clv_positivo": round(float((c > 0).mean()), 3) if len(c) else None}
    return {
        "partidos_con_cuota": int(len(g)), "n_apuestas": int(len(a)),
        "roi": round(float(a["ganancia"].mean()), 4),
        "cuota_media": round(float(a["cuota"].mean()), 3),
        "clv_medio": round(float(clv.mean()), 4) if len(clv) else None,
        "clv_positivo": round(float((clv > 0).mean()), 3) if len(clv) else None,
        "por_lado": {l: bloque(x) for l, x in a.groupby("lado")},
        "por_temporada": {t: bloque(x) for t, x in a.groupby("temporada")},
    }


def _candidatas(r: pd.DataFrame) -> dict:
    """Cómo habrían rendido las candidatas (prob ≥ PROB_CANDIDATA) en tarjetas y corners."""
    out = {}
    for mercado in ("tarjetas", "corners"):
        m = r[r["mercado"] == mercado]
        if m.empty:
            continue
        lados = pd.concat([
            pd.DataFrame({"prob": m["prob_over"], "y": m["over"], "linea": m["linea"], "lado": "over"}),
            pd.DataFrame({"prob": 1 - m["prob_over"], "y": 1 - m["over"], "linea": m["linea"], "lado": "under"})])
        c = lados[lados["prob"].apply(config.es_candidata)]
        out[mercado] = resumen_prob(c["prob"], c["y"])
        out[mercado]["por_lado"] = {l: resumen_prob(x["prob"], x["y"]) for l, x in c.groupby("lado")}
    return out


def ejecutar(ligas=None, guardar: bool = True) -> dict:
    ligas = ligas or list(config.LIGAS)
    with db.conexion() as con:
        db.aplicar_parametros(con)
        partidos = pd.read_sql_query("SELECT * FROM partidos", con)
    resultado = {"version_modelo": config.VERSION_MODELO, "ligas": {}}
    for liga in ligas:
        log.info("Backtest %s", liga)
        r = _registros_liga(partidos, liga)
        if r.empty:
            resultado["ligas"][liga] = {"nota": "sin datos suficientes"}
            continue
        info = {"desde": r["fecha"].min(), "hasta": r["fecha"].max(), "mercados": {}}
        for mercado, m in r.groupby("mercado"):
            ok = m.dropna(subset=["base_over"])
            info["mercados"][mercado] = {
                "partidos": int(m["fecha"].count() / len(config.LINEAS[mercado])),
                "lineas": {str(l): resumen_prob(x["prob_over"], x["over"], x["base_over"])
                           for l, x in ok.groupby("linea")},
                "calibracion": calibracion(m["prob_over"].to_numpy(), m["over"].to_numpy()),
                "error_medio_total": round(float((m["media"] - m["real"]).mean()), 3),
            }
        info["goles_2_5_apuestas"] = _simular_goles(r)
        info["candidatas"] = _candidatas(r)
        resultado["ligas"][liga] = info
    if guardar:
        with db.conexion() as con:
            con.execute("INSERT INTO backtests (version_modelo, creado_en, parametros, resultado) VALUES (?,?,?,?)",
                        (config.VERSION_MODELO, db.ahora(), json.dumps({
                            "vida_media": config.VIDA_MEDIA_DIAS, "ventana": config.VENTANA_DIAS,
                            "margen_valor": config.MARGEN_VALOR, "prob_candidata": config.PROB_CANDIDATA,
                            "cuota_piso": config.CUOTA_PISO, "cuota_max_candidata": config.CUOTA_MAX_CANDIDATA,
                            "peso_roja": config.PESO_ROJA}),
                         json.dumps(resultado, default=float)))
    return resultado


def ultimo() -> dict | None:
    with db.conexion() as con:
        r = con.execute("SELECT * FROM backtests ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    return {"id": r["id"], "creado_en": r["creado_en"], "version_modelo": r["version_modelo"],
            "parametros": json.loads(r["parametros"]), "resultado": json.loads(r["resultado"])}
