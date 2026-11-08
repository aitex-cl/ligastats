"""Métricas de rendimiento: calibración, Brier, ROI y CLV.

Se usan tanto para las señales reales (tabla proyecciones / apuestas) como
para el backtest.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from . import db

TRAMOS = [0, 0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.0001]


def calibracion(prob: np.ndarray, resultado: np.ndarray) -> list[dict]:
    """Para cada tramo de probabilidad: cuántas, prob. media del modelo y frecuencia real."""
    df = pd.DataFrame({"p": prob, "y": resultado})
    df["tramo"] = pd.cut(df["p"], TRAMOS, right=False)
    salida = []
    for tramo, g in df.groupby("tramo", observed=True):
        salida.append({
            "tramo": f"{tramo.left:.0%}-{min(tramo.right, 1):.0%}",
            "n": int(len(g)),
            "prob_modelo": round(float(g["p"].mean()), 3),
            "frecuencia_real": round(float(g["y"].mean()), 3),
        })
    return salida


def resumen_prob(prob, resultado, base=None) -> dict:
    prob, resultado = np.asarray(prob, float), np.asarray(resultado, float)
    if len(prob) == 0:
        return {"n": 0}
    brier = float(np.mean((prob - resultado) ** 2))
    out = {
        "n": int(len(prob)),
        "prob_media": round(float(prob.mean()), 3),
        "frecuencia_real": round(float(resultado.mean()), 3),
        "sesgo": round(float(prob.mean() - resultado.mean()), 3),
        "brier": round(brier, 4),
    }
    if base is not None:
        base = np.asarray(base, float)
        b = float(np.mean((base - resultado) ** 2))
        out["brier_referencia"] = round(b, 4)
        out["mejora_vs_promedio"] = round(1 - brier / b, 4) if b > 0 else None
    return out


def acierto_vs_minimo(aciertos: int, n: int, minimo: float) -> dict:
    """Acierto real contra el mínimo que exigían las cuotas, y probabilidad de lograrlo por pura suerte."""
    if n == 0:
        return {}
    return {
        "acierto": round(aciertos / n, 3),
        "minimo_para_no_perder": round(minimo, 3),
        "diferencia_puntos": round(100 * (aciertos / n - minimo), 1),
        "prob_de_ser_suerte": round(float(stats.binom.sf(aciertos - 1, n, minimo)), 3),
    }


def rendimiento_proyecciones(desde=None, hasta=None, liga=None, mercado=None, tipo=None,
                             version=None) -> dict:
    filtros, args = ["estado IN ('acertada','fallada')"], []
    for col, val in (("liga", liga), ("mercado", mercado), ("tipo", tipo), ("version_modelo", version)):
        if val:
            filtros.append(f"{col} = ?")
            args.append(val)
    if desde:
        filtros.append("fecha >= ?"); args.append(desde)
    if hasta:
        filtros.append("fecha <= ?"); args.append(hasta)
    with db.conexion() as con:
        df = pd.read_sql_query(f"SELECT * FROM proyecciones WHERE {' AND '.join(filtros)}", con, params=args)
        pend = con.execute("SELECT COUNT(*) FROM proyecciones WHERE estado='pendiente'").fetchone()[0]
    if df.empty:
        return {"liquidadas": 0, "pendientes": pend,
                "nota": "Aún no hay proyecciones liquidadas con esos filtros."}
    df["y"] = (df["estado"] == "acertada").astype(float)
    # para calibración se usa solo el lado 'over' (el 'under' es el complemento exacto)
    over = df[df["lado"] == "over"]
    salida = {"liquidadas": int(len(df)), "pendientes": pend, "por_segmento": [], "calibracion": {}}
    for (merc, tip, lado), g in df.groupby(["mercado", "tipo", "lado"]):
        r = resumen_prob(g["prob"], g["y"])
        r.update({"mercado": merc, "tipo": tip, "lado": lado})
        if tip == "candidata":
            # si se hubiera apostado justo a la cuota mínima
            r["si_se_apuesta_a_cuota_minima"] = acierto_vs_minimo(
                int(g["y"].sum()), len(g), float((1 / g["cuota_minima"]).mean()))
        salida["por_segmento"].append(r)
    for merc, g in over.groupby("mercado"):
        salida["calibracion"][merc] = calibracion(g["prob"].to_numpy(), g["y"].to_numpy())
    salida["por_liga"] = [dict(liga=l, mercado=m, **resumen_prob(g["prob"], g["y"]))
                          for (l, m), g in df[df["tipo"] != "proyeccion"].groupby(["liga", "mercado"])]
    return salida


def rendimiento_apuestas(desde=None, liga=None, mercado=None) -> dict:
    filtros, args = ["estado IN ('ganada','perdida')"], []
    if desde:
        filtros.append("fecha >= ?"); args.append(desde)
    if liga:
        filtros.append("liga = ?"); args.append(liga)
    if mercado:
        filtros.append("mercado = ?"); args.append(mercado)
    with db.conexion() as con:
        df = pd.read_sql_query(f"SELECT * FROM apuestas WHERE {' AND '.join(filtros)}", con, params=args)
        pend = con.execute("SELECT COUNT(*) FROM apuestas WHERE estado='pendiente'").fetchone()[0]
    if df.empty:
        return {"apuestas_liquidadas": 0, "pendientes": pend}

    def bloque(g):
        con_clv = g["clv"].dropna()
        return {
            **acierto_vs_minimo(int((g["estado"] == "ganada").sum()), len(g), float((1 / g["cuota"]).mean())),
            "n": int(len(g)), "apostado": round(float(g["monto"].sum()), 2),
            "ganancia": round(float(g["ganancia"].sum()), 2),
            "roi": round(float(g["ganancia"].sum() / g["monto"].sum()), 4),
            "cuota_media": round(float(g["cuota"].mean()), 3),
            "n_con_clv": int(len(con_clv)),
            "clv_medio": round(float(con_clv.mean()), 4) if len(con_clv) else None,
            "clv_positivo": round(float((con_clv > 0).mean()), 3) if len(con_clv) else None,
        }
    salida = {"apuestas_liquidadas": int(len(df)), "pendientes": pend, "total": bloque(df)}
    salida["por_mercado"] = {m: bloque(g) for m, g in df.groupby("mercado")}
    salida["por_mercado_y_lado"] = {f"{m} {l}": bloque(g) for (m, l), g in df.groupby(["mercado", "lado"])}
    salida["por_liga"] = {l: bloque(g) for l, g in df.groupby("liga")}
    return salida
