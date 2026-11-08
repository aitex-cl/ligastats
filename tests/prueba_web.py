"""Levanta el panel web contra una copia de la base de prueba con candidatas de hoy y de ayer."""
import base64
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

ORIGEN = Path(sys.argv[1]).resolve()
DATOS = Path(tempfile.mkdtemp())
shutil.copy(ORIGEN / "ligastats.db", DATOS / "ligastats.db")
con = sqlite3.connect(DATOS / "ligastats.db")
hoy, ayer = date.today(), date.today() - timedelta(days=1)
campos = ("partido_id, liga, fecha, hora, local, visita, arbitro, mercado, linea, lado, media_esperada, prob, "
          "cuota_justa, cuota_minima, tipo, version_modelo, creado_en")
filas = [
    ("E0|x|Arsenal|Chelsea", "E0", hoy.isoformat(), "23:59", "Arsenal", "Chelsea", "M Oliver", "tarjetas", 4.5,
     "under", 3.9, 0.65, 1.54, 1.62, "candidata", "v1.1", "t"),
    ("N1|x|Ajax|PSV", "N1", hoy.isoformat(), "23:59", "Ajax", "PSV", None, "tarjetas", 4.5,
     "under", 3.9, 0.66, 1.52, 1.60, "candidata", "v1.1", "t"),
    ("E0|y|Everton|Wolves", "E0", ayer.isoformat(), "15:00", "Everton", "Wolves", None, "tarjetas", 5.5,
     "under", 4.1, 0.70, 1.43, 1.50, "candidata", "v1.1", "t"),
    ("D1|y|Bayern|Mainz", "D1", ayer.isoformat(), "15:00", "Bayern", "Mainz", None, "tarjetas", 4.5,
     "under", 3.6, 0.68, 1.47, 1.54, "candidata", "v1.1", "t"),
    ("F1|x|Lyon|Nantes", "F1", hoy.isoformat(), "23:59", "Lyon", "Nantes", None, "corners", 9.5,
     "under", 8.9, 0.61, 1.64, 1.72, "candidata", "v1.1", "t"),
    ("I1|x|Roma|Torino", "I1", hoy.isoformat(), "23:59", "Roma", "Torino", None, "goles", 2.5,
     "under", 2.3, 0.60, 1.67, 1.75, "candidata", "v1.1", "t"),
]
for f in filas:
    con.execute(f"INSERT INTO proyecciones ({campos}) VALUES ({','.join('?' * 17)})", f)
con.execute(f"UPDATE proyecciones SET estado='acertada', valor_real=3 WHERE local='Everton' AND fecha='{ayer.isoformat()}'")
con.execute(f"UPDATE proyecciones SET estado='fallada', valor_real=6 WHERE local='Bayern' AND fecha='{ayer.isoformat()}'")
con.execute("DELETE FROM parametros")  # valores por defecto
con.commit()
con.close()

CLAVE = "clave-de-prueba-123"
PUERTO = 8021
env = {**os.environ, "LIGASTATS_DATA": str(DATOS), "LIGASTATS_WEB_CLAVE": CLAVE,
       "LIGASTATS_WEB_PORT": str(PUERTO), "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
srv = subprocess.Popen([sys.executable, "-m", "ligastats.web"], cwd=Path(__file__).resolve().parent.parent,
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{PUERTO}"
fallas = []


def chequear(cond, msg):
    print(("OK   " if cond else "FALLA"), msg)
    if not cond:
        fallas.append(msg)


try:
    for _ in range(40):
        try:
            if httpx.get(base + "/salud", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)
    auth = {"Authorization": "Basic " + base64.b64encode(f"eduardo:{CLAVE}".encode()).decode()}
    malo = {"Authorization": "Basic " + base64.b64encode(b"eduardo:otra").decode()}
    chequear(httpx.get(base + "/").status_code == 401, "sin clave: 401")
    chequear(httpx.get(base + "/", headers=malo).status_code == 401, "clave incorrecta: 401")
    r = httpx.get(base + "/", headers=auth)
    chequear(r.status_code == 200 and "Arsenal" in r.text, "Hoy muestra la candidata de la Premier")
    chequear("Ajax" not in r.text, "Hoy oculta Holanda (fuera del foco)")
    chequear("≥ 1,70" in r.text, "aplica el piso de cuota 1,70")
    chequear("Ajax" in httpx.get(base + "/?todas=1", headers=auth).text, "Ver todas incluye Holanda")
    chequear("Lyon" not in r.text and "Roma" not in r.text, "Hoy (tarjetas) no mezcla corners ni goles")
    r2 = httpx.get(base + "/?mercado=corners", headers=auth)
    chequear("Lyon" in r2.text and "En observación" in r2.text, "pestaña Corners muestra corners en observación")
    chequear("Roma" in httpx.get(base + "/?mercado=goles", headers=auth).text, "pestaña Goles muestra goles")
    r = httpx.get(base + "/resultados", headers=auth)
    chequear(r.status_code == 200 and "1 de 2" in r.text, "Resultados: 1 de 2 acertadas")
    chequear("59 %" in r.text, "mínimo para no perder con cuota 1,70 (59 %)")
    r = httpx.get(base + "/", headers=auth)
    chequear('name="cuota"' in r.text and "Apostar" in r.text, "Hoy tiene campo de cuota y botón Apostar")
    import re
    pid = int(re.search(r'data-id="(\d+)"', r.text).group(1))
    api = {**auth, "Content-Type": "application/json"}
    chequear(httpx.post(base + "/api/apostar", headers=api, json={"proyeccion_id": pid, "cuota": "1,85", "monto": "5000"})
             .status_code == 403, "API sin encabezado del panel: 403")
    chequear(httpx.post(base + "/api/apostar", headers={**api, "X-Senales": "1", "Origin": "https://malo.com"},
                        json={"proyeccion_id": pid, "cuota": "1,85", "monto": "5000"}).status_code == 403,
             "API desde otro sitio: 403")
    chequear(httpx.post(base + "/api/apostar", json={"proyeccion_id": pid, "cuota": "1,85", "monto": "5000"},
                        headers={"X-Senales": "1"}).status_code == 401, "API sin clave: 401")
    ok_h = {**api, "X-Senales": "1"}
    r = httpx.post(base + "/api/apostar", headers=ok_h, json={"proyeccion_id": pid, "cuota": "1,85", "monto": "5000"})
    chequear(r.status_code == 200 and r.json()["cuota"] == 1.85, "registra apuesta con cuota 1,85")
    aid = r.json()["apuesta_id"]
    r = httpx.post(base + "/api/apostar", headers=ok_h, json={"proyeccion_id": pid, "cuota": "1,9", "monto": "5000"})
    chequear(r.status_code == 400 and "ya tiene" in r.json()["error"], "no permite apostar dos veces la misma")
    r = httpx.post(base + "/api/apostar", headers=ok_h, json={"proyeccion_id": pid, "cuota": "0,5", "monto": "5000"})
    chequear(r.status_code == 400, "rechaza cuota inválida")
    chequear("Apostada a" in httpx.get(base + "/", headers=auth).text, "Hoy muestra la apuesta registrada")
    r = httpx.post(base + "/api/resultado", headers=ok_h, json={"apuesta_id": aid, "estado": "ganada"})
    chequear(r.status_code == 200 and r.json()["ganancia"] == 4250.0, "resultado ganada: +$4.250")
    r = httpx.post(base + "/api/resultado", headers=ok_h, json={"apuesta_id": aid, "estado": "anulada"})
    chequear(r.status_code == 200 and r.json()["estado"] == "nula", "se puede corregir a anulada")
    httpx.post(base + "/api/resultado", headers=ok_h, json={"apuesta_id": aid, "estado": "ganada"})
    r = httpx.get(base + "/apuestas", headers=auth)
    chequear(r.status_code == 200 and "a mano" in r.text and "+$4.250" in r.text, "Mis apuestas: ganada a mano")
    chequear("sel-ganada" in r.text, "botón Ganada marcado")
    chequear(r.headers.get("x-robots-tag", "").startswith("noindex"), "no indexable")
finally:
    srv.terminate()
    salida = srv.communicate(timeout=10)[0].decode(errors="replace")
    shutil.rmtree(DATOS, ignore_errors=True)
if fallas:
    print(salida[-3000:])
print("RESULTADO WEB:", "TODO OK" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
