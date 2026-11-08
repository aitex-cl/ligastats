"""Carga de los CSV descargados a SQLite.

Solo se guardan las columnas que usa el modelo. Además se registra qué datos
trae cada liga y temporada (tabla `cobertura`), porque no todas las ligas
publican corners, tarjetas o árbitro.
"""
import io
import logging

import pandas as pd

from . import config, db

log = logging.getLogger(__name__)

COLUMNAS = {
    "FTHG": "gl", "FTAG": "gv",
    "HS": "tl", "AS": "tv",
    "HST": "tal", "AST": "tav",
    "HF": "fl", "AF": "fv",
    "HC": "cl", "AC": "cv",
    "HY": "al", "AY": "av",
    "HR": "rl", "AR": "rv",
}
# cuota previa: primero Pinnacle, luego promedio de mercado, luego Bet365
CUOTA_PREVIA = [("P>2.5", "P<2.5", "pinnacle"), ("Avg>2.5", "Avg<2.5", "promedio"),
                ("BbAv>2.5", "BbAv<2.5", "promedio"), ("B365>2.5", "B365<2.5", "bet365")]
CUOTA_CIERRE = [("PC>2.5", "PC<2.5", "pinnacle"), ("AvgC>2.5", "AvgC<2.5", "promedio"),
                ("B365C>2.5", "B365C<2.5", "bet365")]
CAMPOS_PARTIDO = ["id", "liga", "temporada", "fecha", "hora", "local", "visita", "arbitro",
                  *COLUMNAS.values(), "c_o25", "c_u25", "cc_o25", "cc_u25",
                  "fuente_cuota", "fuente_cierre", "actualizado"]


def leer_csv(ruta) -> pd.DataFrame:
    crudo = ruta.read_bytes()
    for cod in ("utf-8-sig", "latin-1"):
        try:
            texto = crudo.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    df = pd.read_csv(io.StringIO(texto), on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    return df.dropna(how="all")


def _fechas(serie: pd.Series) -> pd.Series:
    s = serie.astype(str).str.strip()
    f = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    faltan = f.isna()
    f[faltan] = pd.to_datetime(s[faltan], format="%d/%m/%y", errors="coerce")
    faltan = f.isna()
    f[faltan] = pd.to_datetime(s[faltan], format="%Y-%m-%d", errors="coerce")
    return f.dt.strftime("%Y-%m-%d")


def _cuotas(df: pd.DataFrame, opciones):
    over = pd.Series(float("nan"), index=df.index)
    under = pd.Series(float("nan"), index=df.index)
    fuente = pd.Series(None, index=df.index, dtype=object)
    for c_over, c_under, nombre in opciones:
        if c_over in df.columns and c_under in df.columns:
            o = pd.to_numeric(df[c_over], errors="coerce")
            u = pd.to_numeric(df[c_under], errors="coerce")
            usar = over.isna() & o.notna() & u.notna() & (o > 1) & (u > 1)
            over[usar], under[usar], fuente[usar] = o[usar], u[usar], nombre
    return over, under, fuente


def normalizar(df: pd.DataFrame, liga: str, temporada: str | None) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["liga"] = liga
    out["temporada"] = temporada
    out["fecha"] = _fechas(df["Date"])
    out["hora"] = df["Time"].astype(str).str.strip() if "Time" in df.columns else None
    out["local"] = df["HomeTeam"].astype(str).str.strip()
    out["visita"] = df["AwayTeam"].astype(str).str.strip()
    if "Referee" in df.columns:
        arb = df["Referee"].astype(str).str.strip()
        out["arbitro"] = arb.where(~arb.isin(["", "nan", "None"]), None)
    else:
        out["arbitro"] = None
    for origen, destino in COLUMNAS.items():
        out[destino] = pd.to_numeric(df[origen], errors="coerce") if origen in df.columns else None
    out["c_o25"], out["c_u25"], out["fuente_cuota"] = _cuotas(df, CUOTA_PREVIA)
    out["cc_o25"], out["cc_u25"], out["fuente_cierre"] = _cuotas(df, CUOTA_CIERRE)
    out = out[out["fecha"].notna() & (out["local"] != "nan") & (out["visita"] != "nan")]
    out["id"] = out["liga"] + "|" + out["fecha"] + "|" + out["local"] + "|" + out["visita"]
    out["actualizado"] = db.ahora()
    return out


def _filas(df: pd.DataFrame, campos):
    df = df[campos].astype(object).where(pd.notna(df[campos]), None)
    return [tuple(None if v is None else (int(v) if isinstance(v, float) and v.is_integer()
                                          and c not in ("c_o25", "c_u25", "cc_o25", "cc_u25") else v)
                  for c, v in zip(campos, fila)) for fila in df.itertuples(index=False)]


def cargar() -> dict:
    db.inicializar()
    resumen = {"partidos": 0, "proximos": 0, "archivos": 0}
    with db.conexion() as con:
        for liga in config.LIGAS:
            for temp in config.temporadas():
                ruta = config.RAW_DIR / f"{liga}_{temp}.csv"
                if not ruta.exists():
                    continue
                df = normalizar(leer_csv(ruta), liga, temp)
                # solo partidos jugados (con goles)
                df = df[df["gl"].notna() & df["gv"].notna()]
                if df.empty:
                    continue
                marcadores = ",".join("?" * len(CAMPOS_PARTIDO))
                con.executemany(
                    f"INSERT OR REPLACE INTO partidos ({','.join(CAMPOS_PARTIDO)}) VALUES ({marcadores})",
                    _filas(df, CAMPOS_PARTIDO))
                con.execute(
                    "INSERT OR REPLACE INTO cobertura VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (liga, temp, len(df),
                     int(df["cl"].notna().sum()), int(df["al"].notna().sum()),
                     int(df["fl"].notna().sum()), int(df["arbitro"].notna().sum()),
                     int(df["c_o25"].notna().sum()), int(df["cc_o25"].notna().sum()), db.ahora()))
                resumen["partidos"] += len(df)
                resumen["archivos"] += 1

        ruta = config.RAW_DIR / "fixtures.csv"
        if ruta.exists():
            fx = leer_csv(ruta)
            if "Div" in fx.columns:
                fx = fx[fx["Div"].astype(str).str.strip().isin(config.LIGAS)]
                partes = [normalizar(g, liga.strip(), None) for liga, g in fx.groupby(fx["Div"].astype(str))]
                if partes:
                    fx = pd.concat(partes)
                    campos = ["id", "liga", "fecha", "hora", "local", "visita", "arbitro",
                              "c_o25", "c_u25", "fuente_cuota", "actualizado"]
                    con.execute("DELETE FROM proximos")
                    con.executemany(
                        f"INSERT OR REPLACE INTO proximos ({','.join(campos)}) VALUES ({','.join('?' * len(campos))})",
                        _filas(fx, campos))
                    resumen["proximos"] = len(fx)
    return resumen


def cobertura() -> list[dict]:
    with db.conexion() as con:
        return [dict(r) for r in con.execute("SELECT * FROM cobertura ORDER BY liga, temporada")]
