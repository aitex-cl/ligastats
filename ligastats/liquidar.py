"""Cierre automático de proyecciones y apuestas con los resultados reales."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import config, db
from .modelo import prob_sin_margen, valor_real

log = logging.getLogger(__name__)


def _buscar_partido(con, liga, local, visita, fecha):
    """El partido puede haberse reprogramado: se busca ±7 días."""
    d = date.fromisoformat(fecha)
    return con.execute(
        "SELECT * FROM partidos WHERE liga=? AND local=? AND visita=? AND fecha BETWEEN ? AND ? "
        "ORDER BY ABS(julianday(fecha) - julianday(?)) LIMIT 1",
        (liga, local, visita, (d - timedelta(days=7)).isoformat(),
         (d + timedelta(days=7)).isoformat(), fecha)).fetchone()


def _gana(lado, linea, real):
    return real > linea if lado == "over" else real < linea


def liquidar(hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    resumen = {"proyecciones_liquidadas": 0, "acertadas": 0, "falladas": 0, "nulas": 0,
               "apuestas_liquidadas": 0}
    with db.conexion() as con:
        pendientes = con.execute(
            "SELECT * FROM proyecciones WHERE estado='pendiente' AND fecha <= ?",
            (hoy.isoformat(),)).fetchall()
        cache = {}
        for p in pendientes:
            clave = (p["liga"], p["local"], p["visita"], p["fecha"])
            if clave not in cache:
                cache[clave] = _buscar_partido(con, *clave)
            partido = cache[clave]
            if partido is None:
                if date.fromisoformat(p["fecha"]) < hoy - timedelta(days=config.DIAS_PARA_ANULAR):
                    con.execute("UPDATE proyecciones SET estado='nula', liquidado_en=? WHERE id=?",
                                (db.ahora(), p["id"]))
                    resumen["nulas"] += 1
                    resumen["proyecciones_liquidadas"] += 1
                continue
            real = valor_real(partido, p["mercado"])
            if real is None:
                estado = "nula"
            else:
                estado = "acertada" if _gana(p["lado"], p["linea"], real) else "fallada"
            con.execute(
                "UPDATE proyecciones SET estado=?, valor_real=?, partido_real_id=?, liquidado_en=? WHERE id=?",
                (estado, real, partido["id"], db.ahora(), p["id"]))
            resumen["proyecciones_liquidadas"] += 1
            resumen[{"acertada": "acertadas", "fallada": "falladas", "nula": "nulas"}[estado]] += 1

        for a in con.execute("SELECT * FROM apuestas WHERE estado='pendiente' AND fecha <= ?",
                             (hoy.isoformat(),)).fetchall():
            partido = _buscar_partido(con, a["liga"], a["local"], a["visita"], a["fecha"])
            if partido is None:
                continue
            real = valor_real(partido, a["mercado"])
            cierre = a["cuota_cierre"]
            clv = a["clv"]
            # en goles 2.5 el cierre de Pinnacle viene en los datos
            if cierre is None and a["mercado"] == "goles" and a["linea"] == 2.5 \
                    and partido["cc_o25"] and partido["cc_u25"]:
                p_over = prob_sin_margen(partido["cc_o25"], partido["cc_u25"])
                p_lado = p_over if a["lado"] == "over" else 1 - p_over
                cierre = round(1 / p_lado, 3)
            if cierre and clv is None:
                clv = round(a["cuota"] / cierre - 1, 4)
            if real is None:
                estado, ganancia = "nula", 0.0
            elif _gana(a["lado"], a["linea"], real):
                estado, ganancia = "ganada", round(a["monto"] * (a["cuota"] - 1), 2)
            else:
                estado, ganancia = "perdida", -a["monto"]
            con.execute(
                "UPDATE apuestas SET estado=?, ganancia=?, cuota_cierre=?, clv=?, liquidado_en=?, "
                "liquidado_por='auto' WHERE id=? AND estado='pendiente'",
                (estado, ganancia, cierre, clv, db.ahora(), a["id"]))
            resumen["apuestas_liquidadas"] += 1
    return resumen
