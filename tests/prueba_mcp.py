"""Levanta el MCP contra la base de prueba y llama sus herramientas con un cliente real."""
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DATOS = Path(sys.argv[1]).resolve()
TOKEN = secrets.token_hex(32)
PUERTO = 8019
URL = f"http://127.0.0.1:{PUERTO}/mcp"
env = {**os.environ, "LIGASTATS_DATA": str(DATOS), "LIGASTATS_MCP_TOKEN": TOKEN,
       "LIGASTATS_MCP_PORT": str(PUERTO), "LIGASTATS_MCP_HOSTS": f"127.0.0.1:{PUERTO}",
       "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
raiz = Path(__file__).resolve().parent.parent
srv = subprocess.Popen([sys.executable, "-m", "ligastats.mcp_server"], cwd=raiz, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
fallas = []


def chequear(cond, msg):
    print(("OK   " if cond else "FALLA"), msg)
    if not cond:
        fallas.append(msg)


async def main():
    for _ in range(30):
        try:
            if httpx.get(f"http://127.0.0.1:{PUERTO}/salud", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    chequear(httpx.post(URL, json={}, headers={"Authorization": "Bearer malo"}).status_code == 401,
             "rechaza clave incorrecta")
    async with streamablehttp_client(URL, headers={"Authorization": f"Bearer {TOKEN}"}) as (rd, wr, _):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            nombres = {t.name for t in (await s.list_tools()).tools}
            esperadas = {"estado_sistema", "senales_proximas", "proyeccion_partido", "rendimiento_modelo",
                         "registrar_apuesta", "ver_parametros", "ajustar_parametro", "ejecutar_actualizacion"}
            chequear(esperadas <= nombres, f"herramientas publicadas ({len(nombres)})")

            async def llamar(nombre, args=None):
                r = await s.call_tool(nombre, args or {})
                return r.isError, json.loads(r.content[0].text) if r.content else None
            err, est = await llamar("estado_sistema")
            chequear(not err and est["parametros"]["CUOTA_PISO"] >= 1.2, "estado_sistema")
            err, r = await llamar("ajustar_parametro", {"nombre": "CUOTA_PISO", "valor": "9"})
            chequear(not err and "error" in r, "rechaza parámetro fuera de rango")
            err, r = await llamar("ajustar_parametro", {"nombre": "PROB_CANDIDATA", "valor": "0.56", "motivo": "ci"})
            chequear(not err and r.get("nuevo") == 0.56, "ajusta parámetro válido")
            err, r = await llamar("ver_parametros")
            chequear(not err and r["historial"][0]["motivo"] == "ci", "historial de parámetros")
            err, r = await llamar("proyeccion_partido", {"liga": "E0", "local": "Arsenal", "visita": "Chelsea"})
            chequear(not err and "tarjetas" in r, "proyeccion_partido")
            err, r = await llamar("rendimiento_modelo", {"mercado": "tarjetas"})
            chequear(not err and "proyecciones" in r, "rendimiento_modelo")


try:
    asyncio.run(main())
finally:
    srv.terminate()
    salida = srv.communicate(timeout=10)[0].decode(errors="replace")
if fallas:
    print(salida[-3000:])
print("RESULTADO MCP:", "TODO OK" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
