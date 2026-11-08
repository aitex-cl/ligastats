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
    ("E0|x|Arsenal|Chelsea", "E0", hoy.isoformat(), "23:59", "Arsenal", "Chelsea", "M Oliver", "tarjetas", 4.5,
     "over", 3.9, 0.35, 2.86, 3.0, "proyeccion", "v1.1", "t"),
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
    import re
    r = httpx.get(base + "/", headers=auth)
    chequear(r.status_code == 200 and "Arsenal" in r.text, "Hoy muestra el partido de la Premier")
    chequear("Ajax" not in r.text, "Hoy oculta Holanda (liga excluida)")
    chequear("Ajax" in httpx.get(base + "/?todas=1", headers=auth).text, "Ver también ligas excluidas")
    chequear("Lyon" in r.text and "Roma" in r.text, "partidos con candidatas de corners y goles también aparecen")
    chequear('data-min="1.7"' in r.text, "aplica el piso de cuota 1,70")
    chequear(r.text.count('class="mb"') >= 3 and "<select" in r.text, "bloques por mercado con selector de línea")
    chequear("Guardar cuotas" in r.text and "apostar(this,'under')" in r.text and "apostar(this,'over')" in r.text,
             "botones presentes")
    chequear('class="tres"' in r.text and 'class="ancho"' in r.text, "tres columnas y pantalla ancha")
    m = re.search(r'<div class="p">Arsenal.*?class="mb" data-mercado="tarjetas" data-id="(\d+)"', r.text, re.S)
    pid = int(m.group(1))
    api = {**auth, "Content-Type": "application/json"}
    ok_h = {**api, "X-Senales": "1"}
    chequear(httpx.post(base + "/api/cuotas", headers=api, json={"items": [{"proyeccion_id": pid, "cuota_menos": "1,5"}]})
             .status_code == 403, "API sin encabezado del panel: 403")
    chequear(httpx.post(base + "/api/cuotas", headers={**ok_h, "Origin": "https://malo.com"},
                        json={"items": [{"proyeccion_id": pid, "cuota_menos": "1,5"}]}).status_code == 403,
             "API desde otro sitio: 403")
    chequear(httpx.post(base + "/api/cuotas", headers={"X-Senales": "1"},
                        json={"items": [{"proyeccion_id": pid, "cuota_menos": "1,5"}]}).status_code == 401, "API sin clave: 401")
    r = httpx.post(base + "/api/cuotas", headers=ok_h, json={"items": [{"proyeccion_id": pid, "cuota_menos": "1,36", "cuota_mas": "3,10"}]})
    chequear(r.status_code == 200 and r.json()["guardadas"] == 1, "guarda cuotas de menos y más")
    ev = r.json()["detalle"][0]["evaluacion"]
    chequear(ev["under"]["conviene"] is False and "prob_casa_sin_margen" in ev["under"], "evalúa lados y quita el margen")
    chequear(httpx.post(base + "/api/cuotas", headers=ok_h, json={"items": [{"proyeccion_id": pid, "cuota_menos": "0,5"}]})
             .status_code == 400, "rechaza cuota inválida")
    chequear('value="1,36"' in httpx.get(base + "/", headers=auth).text, "Hoy muestra las cuotas guardadas")
    r = httpx.post(base + "/api/apostar", headers=ok_h, json={"proyeccion_id": pid, "lado": "over", "cuota": "3,10",
                                                               "monto": "5000", "items": []})
    chequear(r.status_code == 200 and r.json()["lado"] == "over", "apuesta al lado contrario (más)")
    aid = r.json()["apuesta_id"]
    r = httpx.post(base + "/api/apostar", headers=ok_h, json={"proyeccion_id": pid, "cuota": "1,9", "monto": "5000"})
    chequear(r.status_code == 400 and "ya tiene" in r.json()["error"], "una sola apuesta por línea")
    chequear("Apostada" in httpx.get(base + "/", headers=auth).text, "Hoy muestra la apuesta registrada")
    r = httpx.post(base + "/api/resultado", headers=ok_h, json={"apuesta_id": aid, "estado": "ganada"})
    chequear(r.status_code == 200 and r.json()["ganancia"] == 10500.0, "resultado ganada: +$10.500")
    r = httpx.get(base + "/apuestas", headers=auth)
    chequear(r.status_code == 200 and "a mano" in r.text and "+$10.500" in r.text, "Mis apuestas: ganada a mano")
    # resultados a cuotas reales: guardar cuotas para la candidata ya liquidada (Everton, menos 5,5 acertada)
    import sqlite3 as _sq
    _c = _sq.connect(DATOS / "ligastats.db")
    ev_id = _c.execute("SELECT id FROM proyecciones WHERE local='Everton' AND fecha=?", (ayer.isoformat(),)).fetchone()[0]
    _c.close()
    httpx.post(base + "/api/cuotas", headers=ok_h, json={"items": [{"proyeccion_id": ev_id, "cuota_menos": "1,36", "cuota_mas": "3,20"}]})
    r = httpx.get(base + "/resultados", headers=auth)
    chequear(r.status_code == 200 and "1 de 2" in r.text, "Resultados: 1 de 2 acertadas")
    chequear("59 %" in r.text, "mínimo para no perder con cuota 1,70 (59 %)")
    chequear("A cuotas reales de la casa" in r.text and "+36.0 %" in r.text, "simulación a cuota real (+36 %)")
    r2 = httpx.get(base + "/resultados?mercado=corners", headers=auth)
    chequear(r2.status_code == 200 and "En observación" in r2.text, "Resultados por mercado (corners en observación)")
    chequear(r.headers.get("x-robots-tag", "").startswith("noindex"), "no indexable")
finally:
    srv.terminate()
    salida = srv.communicate(timeout=10)[0].decode(errors="replace")
    shutil.rmtree(DATOS, ignore_errors=True)
if fallas:
    print(salida[-3000:])
print("RESULTADO WEB:", "TODO OK" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
