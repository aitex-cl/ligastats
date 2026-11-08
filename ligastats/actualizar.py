"""Punto de entrada de las tareas programadas.

    python -m ligastats.actualizar diario     # descargar + cargar + liquidar + señales
    python -m ligastats.actualizar backtest   # backtest completo (semanal)
    python -m ligastats.actualizar <tarea>    # descargar | cargar | liquidar | senales | cobertura
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import sys
import traceback

from . import backtest, cargar, config, db, descargar, liquidar, senales

TAREAS = ("diario", "descargar", "cargar", "liquidar", "senales", "backtest", "cobertura")

log = logging.getLogger("ligastats")


def _correr(tarea: str) -> dict:
    if tarea == "descargar":
        return descargar.descargar()
    if tarea == "cargar":
        return cargar.cargar()
    if tarea == "liquidar":
        return liquidar.liquidar()
    if tarea == "senales":
        return senales.generar()
    if tarea == "cobertura":
        return {"cobertura": cargar.cobertura()}
    if tarea == "backtest":
        r = backtest.ejecutar()
        return {liga: {"goles_2_5": v.get("goles_2_5_apuestas"), "desde": v.get("desde"), "hasta": v.get("hasta")}
                for liga, v in r["ligas"].items()}
    if tarea == "diario":
        out = {"descargar": descargar.descargar(), "cargar": cargar.cargar()}
        out["liquidar"] = liquidar.liquidar()
        out["senales"] = senales.generar()
        return out
    raise ValueError(f"tarea desconocida: {tarea}")


def ejecutar(tarea: str) -> dict:
    config.asegurar_directorios()
    db.inicializar()
    with open(config.LOCK_PATH, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": False, "mensaje": "ya hay una tarea en ejecución"}
        with db.conexion() as con:
            eid = con.execute("INSERT INTO ejecuciones (tarea, inicio) VALUES (?, ?)",
                              (tarea, db.ahora())).lastrowid
        try:
            res = _correr(tarea)
            ok, msg = True, json.dumps(res, default=str, ensure_ascii=False)[:20000]
        except Exception as e:  # se registra y se reporta
            res = {"error": str(e)}
            ok, msg = False, traceback.format_exc()[-5000:]
            log.exception("Falló la tarea %s", tarea)
        with db.conexion() as con:
            con.execute("UPDATE ejecuciones SET fin=?, ok=?, mensaje=? WHERE id=?",
                        (db.ahora(), int(ok), msg, eid))
        return {"ok": ok, "resultado": res}


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Tareas de LigaStats")
    p.add_argument("tarea", choices=TAREAS)
    args = p.parse_args(argv)
    out = ejecutar(args.tarea)
    print(json.dumps(out, default=str, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
