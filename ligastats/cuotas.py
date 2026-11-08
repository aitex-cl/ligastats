"""Cuotas reales de la casa de apuestas (menos y más de una línea) y en qué lado hay valor.

Cada guardado es una fila nueva (historial): la primera sirve de cuota de apertura y la
última de cuota más cercana al cierre.
"""
from __future__ import annotations

from . import config, db

ESQUEMA = """
CREATE TABLE IF NOT EXISTS cuotas_casa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proyeccion_id INTEGER NOT NULL REFERENCES proyecciones(id),
    partido_id TEXT NOT NULL,
    mercado TEXT NOT NULL,
    linea REAL NOT NULL,
    cuota_menos REAL,
    cuota_mas REAL,
    casa TEXT,
    creado_en TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cuotas_linea ON cuotas_casa(partido_id, mercado, linea);
"""


def _cuota(v, nombre):
    if v is None or str(v).strip() == "":
        return None
    try:
        c = float(str(v).replace(",", ".").strip())
    except ValueError:
        raise ValueError(f"la cuota de {nombre} no es un número válido")
    if not 1.01 <= c <= 100:
        raise ValueError(f"la cuota de {nombre} debe estar entre 1,01 y 100")
    return c


def prob_menos(p: dict) -> float:
    """Probabilidad del modelo para 'menos' de la línea de una proyección."""
    return p["prob"] if p["lado"] == "under" else 1 - p["prob"]


def evaluar(p_menos: float, cuota_menos: float | None, cuota_mas: float | None) -> dict:
    """Para cada lado: probabilidad del modelo, cuota mínima, si conviene y valor esperado."""
    db_lados = {}
    for lado, p, c in (("under", p_menos, cuota_menos), ("over", 1 - p_menos, cuota_mas)):
        minima = config.cuota_minima(p)
        db_lados[lado] = {
            "prob_modelo": round(p, 4), "cuota_minima": round(minima, 3), "cuota": c,
            "conviene": bool(c and c >= minima),
            "valor_esperado": None if c is None else round(p * c - 1, 4),
        }
    if cuota_menos and cuota_mas:
        im, ix = 1 / cuota_menos, 1 / cuota_mas
        q_menos = im / (im + ix)
        db_lados["under"]["prob_casa_sin_margen"] = round(q_menos, 4)
        db_lados["over"]["prob_casa_sin_margen"] = round(1 - q_menos, 4)
        db_lados["margen_casa"] = round(im + ix - 1, 4)
    elif cuota_menos:
        db_lados["under"]["prob_casa_con_margen"] = round(1 / cuota_menos, 4)
    elif cuota_mas:
        db_lados["over"]["prob_casa_con_margen"] = round(1 / cuota_mas, 4)
    return db_lados


def proyeccion(con, proyeccion_id: int):
    p = con.execute("SELECT * FROM proyecciones WHERE id = ?", (int(proyeccion_id),)).fetchone()
    if not p:
        raise ValueError(f"no existe la proyección {proyeccion_id}")
    return p


def hermana(con, p, lado: str):
    """La proyección del mismo partido, mercado y línea para el lado pedido."""
    if p["lado"] == lado:
        return p
    h = con.execute("SELECT * FROM proyecciones WHERE partido_id=? AND mercado=? AND linea=? AND lado=? "
                    "AND version_modelo=?", (p["partido_id"], p["mercado"], p["linea"], lado,
                                             p["version_modelo"])).fetchone()
    if not h:
        raise ValueError("no se encontró la proyección del otro lado de la línea")
    return h


def guardar(proyeccion_id: int, cuota_menos=None, cuota_mas=None, casa: str | None = None) -> dict:
    cm, cx = _cuota(cuota_menos, "menos"), _cuota(cuota_mas, "más")
    if cm is None and cx is None:
        raise ValueError("ingresa al menos una cuota")
    with db.conexion() as con:
        p = proyeccion(con, proyeccion_id)
        con.execute("INSERT INTO cuotas_casa (proyeccion_id, partido_id, mercado, linea, cuota_menos, cuota_mas, "
                    "casa, creado_en) VALUES (?,?,?,?,?,?,?,?)",
                    (p["id"], p["partido_id"], p["mercado"], p["linea"], cm, cx, casa, db.ahora()))
        return {"ok": True, "partido": f"{p['local']} – {p['visita']}", "mercado": p["mercado"],
                "linea": p["linea"], "evaluacion": evaluar(prob_menos(p), cm, cx)}


def ultimas(con, filas: list[dict]) -> dict[tuple, dict]:
    """Última cuota guardada por (partido_id, mercado, linea)."""
    if not filas:
        return {}
    ids = sorted({f["partido_id"] for f in filas})
    q = ",".join("?" * len(ids))
    salida = {}
    for r in con.execute(f"SELECT * FROM cuotas_casa WHERE partido_id IN ({q}) ORDER BY id", ids):
        salida[(r["partido_id"], r["mercado"], r["linea"])] = dict(r)
    return salida
