"""Prueba de punta a punta contra el sitio falso (ver preparar_datos.py).

Uso: python tests/e2e.py <dir_sitio> <dir_datos>
"""
import functools
import http.server
import json
import os
import shutil
import sqlite3
import sys
import threading
from datetime import date
from pathlib import Path

SITIO, DATOS = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
shutil.rmtree(DATOS, ignore_errors=True)
os.environ.update(LIGASTATS_DATA=str(DATOS), LIGASTATS_URL_BASE="http://127.0.0.1:8765",
                  NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ligastats import config, db  # noqa: E402

config.temporada_actual = lambda hoy=None: "2425"
from ligastats import cargar, descargar, liquidar, rendimiento, senales  # noqa: E402

class Silencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), functools.partial(Silencioso, directory=str(SITIO)))
threading.Thread(target=srv.serve_forever, daemon=True).start()

fallas = []


def chequear(cond, msg):
    print(("OK   " if cond else "FALLA"), msg)
    if not cond:
        fallas.append(msg)


# 1. descarga y carga
r = descargar.descargar()
chequear(sum(v in ("nuevo", "actualizado") for v in r.values()) == 36, f"36 archivos descargados ({len(r)})")
r = cargar.cargar()
chequear(r["partidos"] > 8000 and r["proximos"] == 76, f"carga: {r}")
cob = {(c["liga"], c["temporada"]): c for c in cargar.cobertura()}
chequear(cob[("E0", "2324")]["con_arbitro"] == 380, "Premier trae árbitro")
chequear(cob[("SP1", "2324")]["con_arbitro"] == 0, "LaLiga sin árbitro (detectado)")
chequear(cob[("P1", "2324")]["con_corners"] == 0 and cob[("P1", "2324")]["con_cuotas"] > 0,
         "Portugal sin corners pero con cuotas (detectado)")

# 2. señales
r = senales.generar(hoy=date(2025, 5, 16))
print("   señales:", {k: v for k, v in r.items() if k != "omitidos"}, "| omitidos:", len(r["omitidos"]))
chequear(r["nuevas"] > 0, "se generaron proyecciones")
r2 = senales.generar(hoy=date(2025, 5, 16))
chequear(r2["nuevas"] == 0, "regenerar no duplica ni pisa (congeladas)")
con = sqlite3.connect(DATOS / "ligastats.db")
con.row_factory = sqlite3.Row
tipos = dict(con.execute("SELECT tipo, COUNT(*) FROM proyecciones GROUP BY tipo").fetchall())
print("   por tipo:", tipos)
chequear(con.execute("SELECT COUNT(*) FROM proyecciones WHERE liga='P1' AND mercado='corners'").fetchone()[0] == 0,
         "sin proyecciones de corners en Portugal")
# Inmutabilidad
for sql, desc in (("UPDATE proyecciones SET prob=0.99 WHERE id=1", "no se puede cambiar la probabilidad"),
                  ("DELETE FROM proyecciones WHERE id=1", "no se puede borrar")):
    try:
        con.execute(sql); con.commit(); chequear(False, desc)
    except sqlite3.DatabaseError:
        con.rollback()
        chequear(True, desc)

# 3. apuesta registrada sobre una candidata
cand = con.execute("SELECT * FROM proyecciones WHERE tipo='candidata' ORDER BY prob DESC LIMIT 1").fetchone()
senal = con.execute("SELECT * FROM proyecciones WHERE tipo='senal' LIMIT 1").fetchone()
for p, cuota in ((cand, cand["cuota_minima"] + 0.05), (senal, senal["cuota_mercado"] if senal else None)):
    if p is None:
        continue
    con.execute("INSERT INTO apuestas (proyeccion_id, partido_id, liga, fecha, local, visita, mercado, linea, lado,"
                " cuota, monto, casa, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (p["id"], p["partido_id"], p["liga"], p["fecha"], p["local"], p["visita"], p["mercado"],
                 p["linea"], p["lado"], cuota, 10000, "prueba", db.ahora()))
con.commit()
try:
    con.execute("UPDATE apuestas SET cuota=9.9 WHERE id=1"); con.commit(); chequear(False, "apuesta inmutable")
except sqlite3.DatabaseError:
    con.rollback()
    chequear(True, "apuesta inmutable")

# 4. llegan los resultados
for f in (SITIO / "reservados").glob("*.csv"):
    div, temp = f.stem.split("_")
    shutil.copy(f, SITIO / "mmz4281" / temp / f"{div}.csv")
descargar.descargar(); cargar.cargar()
r = liquidar.liquidar(hoy=date(2025, 6, 10))
print("   liquidación:", r)
pend = con.execute("SELECT COUNT(*) FROM proyecciones WHERE estado='pendiente'").fetchone()[0]
chequear(pend == 0 and r["acertadas"] + r["falladas"] > 0, "todas las proyecciones liquidadas")
try:
    con.execute("UPDATE proyecciones SET estado='acertada' WHERE estado='fallada'"); con.commit()
    chequear(False, "no se puede re-liquidar")
except sqlite3.DatabaseError:
    con.rollback()
    chequear(True, "no se puede re-liquidar")
ap = [dict(a) for a in con.execute("SELECT mercado, cuota, cuota_cierre, clv, estado, ganancia FROM apuestas")]
print("   apuestas:", ap)
chequear(all(a["estado"] != "pendiente" for a in ap), "apuestas liquidadas")

# 5. rendimiento
rp = rendimiento.rendimiento_proyecciones()
print("   rendimiento segmentos:", json.dumps(rp["por_segmento"][:3], ensure_ascii=False))
chequear(rp["liquidadas"] > 0, "rendimiento de proyecciones")
ra = rendimiento.rendimiento_apuestas()
chequear(ra["apuestas_liquidadas"] == len(ap), "rendimiento de apuestas")

# 6. proyección en vivo
pv = senales.proyectar_partido("E0", "Arsenal", "Chelsea", fecha="2025-05-16")
print("   Arsenal-Chelsea:", {m: v.get("total_esperado", v) for m, v in pv.items()})
chequear("total_esperado" in pv["tarjetas"], "proyección en vivo")

srv.shutdown()
print("\nRESULTADO:", "TODO OK" if not fallas else f"{len(fallas)} FALLAS: {fallas}")
sys.exit(1 if fallas else 0)
