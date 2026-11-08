"""Arma un sitio falso con el formato real de football-data.co.uk para pruebas.

Usa estadísticas reales (espejo en GitHub de football-data) y agrega cuotas
sintéticas de más/menos 2.5 goles (previa y cierre). Crea además P1 y N1
solo con goles y cuotas, para probar la detección de cobertura.

Los partidos de la última fecha (desde CORTE) se sacan del CSV de la
temporada y se publican en fixtures.csv, para simular partidos por jugar.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ESPEJO = Path(sys.argv[1])
SITIO = Path(sys.argv[2])
CORTE = sys.argv[3] if len(sys.argv) > 3 else "2025-05-17"
MAPA = {"premier-league": "E0", "la-liga": "SP1", "serie-a": "I1", "bundesliga": "D1", "ligue-1": "F1"}
rng = np.random.default_rng(7)


def cuotas(df):
    # probabilidad "verdadera" ruidosa a partir de un promedio móvil de goles
    lam = np.clip(rng.normal(2.7, 0.35, len(df)), 1.6, 4.0)
    p = stats.poisson.sf(2, lam)
    ruido_pre = np.clip(p + rng.normal(0, 0.04, len(df)), 0.2, 0.85)
    ruido_cierre = np.clip(p + rng.normal(0, 0.02, len(df)), 0.2, 0.85)
    m = 1.05
    df["P>2.5"] = np.round(1 / (ruido_pre * m), 2)
    df["P<2.5"] = np.round(1 / ((1 - ruido_pre) * m), 2)
    df["PC>2.5"] = np.round(1 / (ruido_cierre * 1.03), 2)
    df["PC<2.5"] = np.round(1 / ((1 - ruido_cierre) * 1.03), 2)
    df["B365>2.5"] = np.round(df["P>2.5"] * 0.97, 2)
    df["B365<2.5"] = np.round(df["P<2.5"] * 0.97, 2)
    return df


def formato_real(df, div):
    df = df.copy()
    f = pd.to_datetime(df["Date"])
    df.insert(0, "Div", div)
    df["Date"] = f.dt.strftime("%d/%m/%Y")
    df.insert(2, "Time", "15:00")
    if div != "E0":
        df = df.drop(columns=["Referee"])
    return cuotas(df)


def main():
    (SITIO / "mmz4281").mkdir(parents=True, exist_ok=True)
    fixtures = []
    for f in sorted(ESPEJO.glob("*.csv")):
        nombre, temp = f.stem.rsplit("_", 1)
        div = MAPA[nombre]
        df = pd.read_csv(f)
        if temp == "2425":
            jugados = df[df["Date"] < CORTE]
            futuros = df[df["Date"] >= CORTE]
            (SITIO / "reservados").mkdir(exist_ok=True)
            formato_real(df, div).to_csv(SITIO / "reservados" / f"{div}_{temp}.csv", index=False)
            df = jugados
            fx = formato_real(futuros, div)
            fixtures.append(fx.drop(columns=[c for c in fx.columns if c in (
                "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR", "HS", "AS", "HST", "AST",
                "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR", "PC>2.5", "PC<2.5")]))
        d = SITIO / "mmz4281" / temp
        d.mkdir(exist_ok=True)
        formato_real(df, div).to_csv(d / f"{div}.csv", index=False)
    # P1 y N1: solo goles y cuotas (sin corners, tarjetas ni árbitro), reutilizando equipos de F1
    for div in ("P1", "N1"):
        for temp_dir in (SITIO / "mmz4281").iterdir():
            src = temp_dir / "F1.csv"
            if src.exists():
                d = pd.read_csv(src)
                d["Div"] = div
                d = d[["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
                       "P>2.5", "P<2.5", "PC>2.5", "PC<2.5"]]
                d.to_csv(temp_dir / f"{div}.csv", index=False)
    pd.concat(fixtures).to_csv(SITIO / "fixtures.csv", index=False)
    print("sitio listo en", SITIO, "| partidos por jugar:", sum(len(x) for x in fixtures))


if __name__ == "__main__":
    main()
