"""Servidor MCP de LigaStats (Streamable HTTP, protegido con clave).

Escucha solo en 127.0.0.1; nginx lo publica con HTTPS en stat.aitex.cl/mcp.
La clave se envía como 'Authorization: Bearer <clave>' o 'X-API-Key: <clave>'.
"""
from __future__ import annotations

import hmac
import json
import subprocess
import sys
from datetime import date, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from . import backtest, cargar, config, db, rendimiento, senales

LECTURA = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
ESCRITURA = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
TAREAS_PERMITIDAS = ("diario", "descargar", "cargar", "liquidar", "senales", "backtest")

mcp = FastMCP(
    "LigaStats",
    instructions=(
        "Proyecciones de goles, tarjetas y corners para 7 ligas europeas (E0 Premier, SP1 LaLiga, "
        "I1 Serie A, D1 Bundesliga, F1 Ligue 1, P1 Portugal, N1 Holanda). Tipos de proyección: "
        "'senal' (hay cuota de mercado y ventaja), 'candidata' (sin cuota en los datos: apostar solo si "
        "la casa paga al menos cuota_minima, que ya incluye el piso de cuota), 'proyeccion' (resto, se guarda "
        "para medir calibración). Foco actual: tarjetas (sobre todo 'menos'); corners en observación; goles y "
        "jugadores no se apuestan. Al evaluar resultados, comparar siempre el acierto con el mínimo que exige "
        "la cuota (1/cuota). "
        "Las proyecciones se congelan al crearse y se liquidan solas con el resultado. Tarjetas: roja = 2. "
        "Los nombres de equipo son los de football-data.co.uk (p. ej. 'Man City', 'Ath Bilbao')."),
    host=config.MCP_HOST, port=config.MCP_PORT, stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=config.MCP_HOSTS_PERMITIDOS,
        allowed_origins=["https://claude.ai", "https://claude.com"]),
)


def _filas(sql, args=()):
    with db.conexion() as con:
        return [dict(r) for r in con.execute(sql, args)]


# ------------------------------------------------------------------ consulta
@mcp.tool(annotations=LECTURA)
def estado_sistema() -> dict:
    """Estado general: últimas ejecuciones, datos cargados por liga, señales pendientes y último backtest."""
    ej = _filas("SELECT tarea, inicio, fin, ok, substr(mensaje, 1, 400) AS mensaje FROM ejecuciones "
                "ORDER BY id DESC LIMIT 8")
    datos = _filas("SELECT liga, COUNT(*) AS partidos, MIN(fecha) AS desde, MAX(fecha) AS hasta, "
                   "SUM(cl IS NOT NULL) AS con_corners, SUM(al IS NOT NULL) AS con_tarjetas, "
                   "SUM(arbitro IS NOT NULL) AS con_arbitro FROM partidos GROUP BY liga")
    proy = _filas("SELECT tipo, estado, COUNT(*) AS n FROM proyecciones GROUP BY tipo, estado")
    prox = _filas("SELECT liga, COUNT(*) AS n, MIN(fecha) AS desde, MAX(fecha) AS hasta FROM proximos GROUP BY liga")
    bt = backtest.ultimo()
    with db.conexion() as con:
        params = db.aplicar_parametros(con)
    return {"version_modelo": config.VERSION_MODELO, "hoy": date.today().isoformat(), "parametros": params,
            "ultimas_ejecuciones": ej, "datos_por_liga": datos, "proximos_partidos": prox,
            "proyecciones": proy,
            "ultimo_backtest": bt and {"id": bt["id"], "creado_en": bt["creado_en"]}}


@mcp.tool(annotations=LECTURA)
def senales_proximas(dias: int = 3, liga: str | None = None,
                     mercado: Literal["goles", "tarjetas", "corners"] | None = None,
                     incluir: Literal["senales", "senales_y_candidatas", "todo"] = "senales_y_candidatas") -> dict:
    """Señales y candidatas de los próximos días en los mercados de apuesta (por defecto tarjetas y corners,
    tarjetas primero). Regla para apostar una candidata: la casa debe pagar al menos 'apostar_si_cuota_desde'
    (incluye el piso de cuota de Eduardo)."""
    with db.conexion() as con:
        params = db.aplicar_parametros(con)
    tipos = {"senales": ("senal",), "senales_y_candidatas": ("senal", "candidata"),
             "todo": ("senal", "candidata", "proyeccion")}[incluir]
    mercados = (mercado,) if mercado else tuple(config.MERCADOS_APUESTA)
    sql = (f"SELECT id, fecha, hora, liga, local, visita, arbitro, mercado, linea, lado, tipo, "
           f"media_esperada, prob, cuota_justa, cuota_minima AS apostar_si_cuota_desde, cuota_mercado, valor "
           f"FROM proyecciones WHERE estado='pendiente' AND fecha >= ? AND fecha <= ? "
           f"AND tipo IN ({','.join('?' * len(tipos))}) AND mercado IN ({','.join('?' * len(mercados))})")
    args = [date.today().isoformat(), (date.today() + timedelta(days=dias)).isoformat(), *tipos, *mercados]
    if liga:
        sql += " AND liga = ?"; args.append(liga)
    sql += (" ORDER BY CASE WHEN mercado = ? THEN 0 ELSE 1 END, fecha, hora, "
            "CASE lado WHEN 'under' THEN 0 ELSE 1 END, prob DESC LIMIT 300")
    args.append(config.MERCADO_PRINCIPAL)
    return {"parametros": params, "senales": _filas(sql, args)}


@mcp.tool(annotations=LECTURA)
def proximos_partidos(dias: int = 7, liga: str | None = None) -> list[dict]:
    """Partidos por jugar publicados por football-data (con cuota de goles 2.5 si existe)."""
    sql = "SELECT * FROM proximos WHERE fecha >= ? AND fecha <= ?"
    args = [date.today().isoformat(), (date.today() + timedelta(days=dias)).isoformat()]
    if liga:
        sql += " AND liga = ?"; args.append(liga)
    return _filas(sql + " ORDER BY fecha, hora", args)


@mcp.tool(annotations=LECTURA)
def proyeccion_partido(liga: str, local: str, visita: str, arbitro: str | None = None) -> dict:
    """Proyección en vivo de cualquier cruce (no se guarda): totales esperados y probabilidad de cada línea."""
    return senales.proyectar_partido(liga, local, visita, arbitro)


@mcp.tool(annotations=LECTURA)
def stats_equipo(equipo: str, ultimos: int = 10) -> dict:
    """Últimos partidos de un equipo con goles, corners, tarjetas y faltas, más promedios."""
    filas = _filas("SELECT fecha, liga, local, visita, arbitro, gl, gv, cl, cv, al, av, rl, rv, fl, fv "
                   "FROM partidos WHERE local = ? OR visita = ? ORDER BY fecha DESC LIMIT ?",
                   (equipo, equipo, ultimos))
    if not filas:
        parecidos = _filas("SELECT DISTINCT local FROM partidos WHERE local LIKE ? LIMIT 10", (f"%{equipo}%",))
        return {"error": "equipo no encontrado", "sugerencias": [p["local"] for p in parecidos]}

    def prom(clave_propia, clave_rival):
        vals = [(f[clave_propia[0]] if f["local"] == equipo else f[clave_propia[1]]) for f in filas]
        riv = [(f[clave_rival[0]] if f["local"] == equipo else f[clave_rival[1]]) for f in filas]
        v = [x for x in vals if x is not None]
        r = [x for x in riv if x is not None]
        return {"propio": round(sum(v) / len(v), 2) if v else None, "rival": round(sum(r) / len(r), 2) if r else None}
    return {"equipo": equipo, "partidos": filas,
            "promedios": {"goles": prom(("gl", "gv"), ("gv", "gl")), "corners": prom(("cl", "cv"), ("cv", "cl")),
                          "amarillas": prom(("al", "av"), ("av", "al")), "faltas": prom(("fl", "fv"), ("fv", "fl"))}}


@mcp.tool(annotations=LECTURA)
def stats_arbitro(arbitro: str, ultimos: int = 20) -> dict:
    """Tarjetas por partido de un árbitro (solo ligas que publican árbitro, hoy la Premier)."""
    filas = _filas("SELECT fecha, liga, local, visita, al, av, rl, rv FROM partidos WHERE arbitro = ? "
                   "ORDER BY fecha DESC LIMIT ?", (arbitro, ultimos))
    if not filas:
        parecidos = _filas("SELECT DISTINCT arbitro FROM partidos WHERE arbitro LIKE ? LIMIT 10", (f"%{arbitro}%",))
        return {"error": "árbitro no encontrado", "sugerencias": [p["arbitro"] for p in parecidos]}
    tot = [(f["al"] or 0) + (f["av"] or 0) + config.PESO_ROJA * ((f["rl"] or 0) + (f["rv"] or 0)) for f in filas]
    liga = filas[0]["liga"]
    prom_liga = _filas("SELECT AVG(al + av + ? * (COALESCE(rl,0) + COALESCE(rv,0))) AS p FROM partidos "
                       "WHERE liga = ? AND al IS NOT NULL AND fecha >= date('now', '-730 days')",
                       (config.PESO_ROJA, liga))[0]["p"]
    return {"arbitro": arbitro, "partidos": filas, "tarjetas_por_partido": round(sum(tot) / len(tot), 2),
            "promedio_liga_2_anios": round(prom_liga, 2) if prom_liga else None}


@mcp.tool(annotations=LECTURA)
def revisar_senales(desde: str | None = None, hasta: str | None = None, liga: str | None = None,
                    mercado: Literal["goles", "tarjetas", "corners"] | None = None,
                    tipo: Literal["senal", "candidata", "proyeccion"] | None = None,
                    estado: Literal["pendiente", "acertada", "fallada", "nula"] | None = None,
                    limite: int = 100) -> list[dict]:
    """Historial de proyecciones con su resultado real. Fechas en formato AAAA-MM-DD."""
    sql = ("SELECT id, fecha, liga, local, visita, mercado, linea, lado, tipo, prob, cuota_minima, "
           "cuota_mercado, valor, estado, valor_real, version_modelo FROM proyecciones WHERE 1=1")
    args = []
    for col, val, op in (("fecha", desde, ">="), ("fecha", hasta, "<="), ("liga", liga, "="),
                         ("mercado", mercado, "="), ("tipo", tipo, "="), ("estado", estado, "=")):
        if val:
            sql += f" AND {col} {op} ?"; args.append(val)
    return _filas(sql + " ORDER BY fecha DESC, id DESC LIMIT ?", (*args, min(limite, 500)))


@mcp.tool(annotations=LECTURA)
def rendimiento_modelo(desde: str | None = None, hasta: str | None = None, liga: str | None = None,
                       mercado: Literal["goles", "tarjetas", "corners"] | None = None,
                       tipo: Literal["senal", "candidata", "proyeccion"] | None = None,
                       version: str | None = None) -> dict:
    """Calibración real de las proyecciones liquidadas: prob. media vs frecuencia real, sesgo y Brier,
    por mercado, tipo y liga. También el rendimiento de las apuestas registradas (ROI y CLV)."""
    return {"proyecciones": rendimiento.rendimiento_proyecciones(desde, hasta, liga, mercado, tipo, version),
            "apuestas": rendimiento.rendimiento_apuestas(desde, liga, mercado)}


@mcp.tool(annotations=LECTURA)
def resultados_backtest(liga: str | None = None, detalle: bool = False) -> dict:
    """Resultado del último backtest sin mirar el futuro. Sin detalle omite las tablas de calibración."""
    bt = backtest.ultimo()
    if not bt:
        return {"nota": "Aún no se ha corrido un backtest. Usa ejecutar_actualizacion('backtest')."}
    ligas = bt["resultado"]["ligas"]
    if liga:
        ligas = {liga: ligas.get(liga, {"nota": "liga sin resultados"})}
    if not detalle:
        ligas = json.loads(json.dumps(ligas))
        for v in ligas.values():
            for m in v.get("mercados", {}).values():
                m.pop("calibracion", None)
            v.get("goles_2_5_apuestas", {}).pop("por_temporada", None)
    return {"creado_en": bt["creado_en"], "version_modelo": bt["version_modelo"],
            "parametros": bt["parametros"], "ligas": ligas}


@mcp.tool(annotations=LECTURA)
def cobertura_datos() -> list[dict]:
    """Qué datos publica cada liga y temporada (corners, tarjetas, faltas, árbitro, cuotas, cierre)."""
    return cargar.cobertura()


@mcp.tool(annotations=LECTURA)
def mis_apuestas(estado: Literal["pendiente", "ganada", "perdida", "nula"] | None = None, limite: int = 50) -> list[dict]:
    """Apuestas registradas con su resultado y CLV."""
    sql, args = "SELECT * FROM apuestas", []
    if estado:
        sql += " WHERE estado = ?"; args.append(estado)
    return _filas(sql + " ORDER BY fecha DESC, id DESC LIMIT ?", (*args, min(limite, 500)))


# ------------------------------------------------------------------ escritura acotada
@mcp.tool(annotations=ESCRITURA)
def registrar_apuesta(cuota: float, monto: float, proyeccion_id: int | None = None,
                      liga: str | None = None, fecha: str | None = None, local: str | None = None,
                      visita: str | None = None, mercado: Literal["goles", "tarjetas", "corners"] | None = None,
                      linea: float | None = None, lado: Literal["over", "under"] | None = None,
                      casa: str | None = None, nota: str | None = None) -> dict:
    """Registra una apuesta hecha. Lo normal es pasar proyeccion_id (de senales_proximas); si la apuesta
    no viene de una proyección, indicar liga, fecha, local, visita, mercado, linea y lado.
    Una vez registrada no se puede modificar."""
    if cuota <= 1 or monto <= 0:
        return {"error": "cuota debe ser > 1 y monto > 0"}
    if proyeccion_id is not None:
        p = _filas("SELECT * FROM proyecciones WHERE id = ?", (proyeccion_id,))
        if not p:
            return {"error": f"no existe la proyección {proyeccion_id}"}
        p = p[0]
        datos = {k: p[k] for k in ("partido_id", "liga", "fecha", "local", "visita", "mercado", "linea", "lado")}
    else:
        faltan = [k for k, v in dict(liga=liga, fecha=fecha, local=local, visita=visita, mercado=mercado,
                                     linea=linea, lado=lado).items() if v is None]
        if faltan:
            return {"error": "faltan datos", "campos": faltan}
        datos = dict(partido_id=f"{liga}|{fecha}|{local}|{visita}", liga=liga, fecha=fecha, local=local,
                     visita=visita, mercado=mercado, linea=linea, lado=lado)
    with db.conexion() as con:
        aid = con.execute(
            "INSERT INTO apuestas (proyeccion_id, partido_id, liga, fecha, local, visita, mercado, linea, lado, "
            "cuota, monto, casa, nota, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (proyeccion_id, datos["partido_id"], datos["liga"], datos["fecha"], datos["local"], datos["visita"],
             datos["mercado"], datos["linea"], datos["lado"], cuota, monto, casa, nota, db.ahora())).lastrowid
    return {"ok": True, "apuesta_id": aid, **datos, "cuota": cuota, "monto": monto}


@mcp.tool(annotations=ESCRITURA)
def registrar_cierre(apuesta_id: int, cuota_cierre: float) -> dict:
    """Registra la cuota de cierre (justo antes del partido) de una apuesta para calcular su CLV.
    En goles 2.5 no hace falta: se toma el cierre de Pinnacle de los datos."""
    if cuota_cierre <= 1:
        return {"error": "cuota_cierre debe ser > 1"}
    with db.conexion() as con:
        a = con.execute("SELECT cuota, cuota_cierre FROM apuestas WHERE id = ?", (apuesta_id,)).fetchone()
        if not a:
            return {"error": f"no existe la apuesta {apuesta_id}"}
        if a["cuota_cierre"] is not None:
            return {"error": "esa apuesta ya tiene cuota de cierre", "cuota_cierre": a["cuota_cierre"]}
        clv = round(a["cuota"] / cuota_cierre - 1, 4)
        con.execute("UPDATE apuestas SET cuota_cierre = ?, clv = ? WHERE id = ?", (cuota_cierre, clv, apuesta_id))
    return {"ok": True, "apuesta_id": apuesta_id, "clv": clv}


@mcp.tool(annotations=LECTURA)
def ver_parametros() -> dict:
    """Parámetros actuales de las señales, rangos permitidos e historial de cambios."""
    with db.conexion() as con:
        actuales = db.aplicar_parametros(con)
        historial = [dict(r) for r in con.execute(
            "SELECT nombre, valor_anterior, valor_nuevo, motivo, creado_en FROM historial_parametros "
            "ORDER BY id DESC LIMIT 30")]
    rangos = {k: (v if isinstance(v, str) else {"min": v[0], "max": v[1]})
              for k, v in config.PARAMETROS_AJUSTABLES.items()}
    return {"actuales": actuales, "rangos": rangos, "mercados_validos": list(config.MERCADOS),
            "historial": historial}


@mcp.tool(annotations=ESCRITURA)
def ajustar_parametro(nombre: Literal["MERCADOS_APUESTA", "MERCADO_PRINCIPAL", "MARGEN_VALOR",
                                      "MARGEN_CUOTA_MINIMA", "CUOTA_PISO", "PROB_CANDIDATA",
                                      "CUOTA_MAX_CANDIDATA"],
                      valor: str, motivo: str | None = None) -> dict:
    """Ajusta un parámetro de las señales dentro de su rango permitido (ver_parametros). Rige para las
    proyecciones que se generen desde ahora; las ya creadas no cambian. MERCADOS_APUESTA se pasa como
    lista separada por comas, p. ej. 'tarjetas,corners'. Queda registrado en el historial."""
    try:
        with db.conexion() as con:
            v = valor if config.PARAMETROS_AJUSTABLES[nombre] in ("lista", "mercado") else float(valor)
            res = db.guardar_parametro(con, nombre, v, motivo)
    except (ValueError, KeyError) as e:
        return {"error": str(e)}
    return {"ok": True, **res}


@mcp.tool(annotations=ESCRITURA)
def ejecutar_actualizacion(tarea: Literal["diario", "descargar", "cargar", "liquidar", "senales", "backtest"]) -> dict:
    """Lanza en segundo plano una de las tareas definidas. El resultado se ve luego en estado_sistema."""
    if tarea not in TAREAS_PERMITIDAS:
        return {"error": "tarea no permitida"}
    subprocess.Popen([sys.executable, "-m", "ligastats.actualizar", tarea], cwd=str(config.BASE_DIR),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"ok": True, "lanzada": tarea, "nota": "Revisa estado_sistema en unos minutos."}


# ------------------------------------------------------------------ autenticación y arranque
class ClaveRequerida(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.rstrip("/") == "/salud":
            return JSONResponse({"ok": True})
        token = config.MCP_TOKEN
        dado = request.headers.get("x-api-key", "")
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            dado = auth[7:].strip()
        if not token or not hmac.compare_digest(dado.encode(), token.encode()):
            return JSONResponse({"error": "no autorizado"}, status_code=401)
        return await call_next(request)


def crear_app():
    app = mcp.streamable_http_app()
    app.add_middleware(ClaveRequerida)
    return app


def main():
    import uvicorn
    if len(config.MCP_TOKEN) < 32:
        raise SystemExit("Falta LIGASTATS_MCP_TOKEN (mínimo 32 caracteres)")
    db.inicializar()
    uvicorn.run(crear_app(), host=config.MCP_HOST, port=config.MCP_PORT, log_level="info",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
