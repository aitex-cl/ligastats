"""Panel web de LigaStats, protegido con usuario y clave.

Pantallas:
  /            Hoy: candidatas de hoy y mañana (foco por defecto, ?todas=1 para ver todo)
  /resultados  Candidatas ya jugadas con su resultado y el acierto contra el mínimo exigido
  /apuestas    Apuestas reales registradas (con botones de resultado)
  POST /api/apostar, /api/resultado   registran apuesta y resultado manual

Escucha en 127.0.0.1:8011; nginx lo publica en https://stat.aitex.cl/.
"""
from __future__ import annotations

import base64
import binascii
import hmac
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.requests import Request
from starlette.routing import Route

from . import apuestas as reg_apuestas
from . import config, db
from .rendimiento import acierto_vs_minimo, rendimiento_apuestas

TZ_CL = ZoneInfo(config.ZONA_HORARIA)
TZ_DATOS = ZoneInfo(config.ZONA_DATOS)
NOMBRE_LIGA = {"E0": "Premier", "SP1": "LaLiga", "I1": "Serie A", "D1": "Bundesliga",
               "F1": "Ligue 1", "P1": "Portugal", "N1": "Holanda"}
MERCADO = {"tarjetas": "Tarjetas", "corners": "Corners", "goles": "Goles"}
LADO = {"under": "Menos", "over": "Más"}


# ------------------------------------------------------------------ utilidades
def hora_chile(fecha: str, hora: str | None) -> datetime:
    try:
        h, m = (hora or "15:00").split(":")[:2]
        dt = datetime.fromisoformat(fecha).replace(hour=int(h), minute=int(m), tzinfo=TZ_DATOS)
    except ValueError:
        dt = datetime.fromisoformat(fecha).replace(hour=15, tzinfo=TZ_DATOS)
    return dt.astimezone(TZ_CL)


def apostar_desde(cuota_minima: float) -> float:
    return round(max(cuota_minima, config.CUOTA_PISO), 2)


def pct(x) -> str:
    return "—" if x is None else f"{100 * x:.0f} %"


def num(x, d=2) -> str:
    return "—" if x is None else f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pesos(x) -> str:
    return "—" if x is None else ("−" if x < 0 else "+") + "$" + f"{abs(x):,.0f}".replace(",", ".")


def filtro_foco(filas: list[dict], todas: bool) -> list[dict]:
    if todas:
        return filas
    return [f for f in filas if f["mercado"] in config.MERCADOS_APUESTA
            and f["mercado"] == config.MERCADO_PRINCIPAL
            and f["lado"] in config.LADOS_FOCO and f["liga"] not in config.LIGAS_EXCLUIDAS]


def candidatas(desde: date, hasta: date, estados: tuple[str, ...]) -> list[dict]:
    with db.conexion() as con:
        db.aplicar_parametros(con)
        q = ",".join("?" * len(estados))
        filas = [dict(r) for r in con.execute(
            f"SELECT * FROM proyecciones WHERE tipo IN ('candidata','senal') AND estado IN ({q}) "
            f"AND fecha BETWEEN ? AND ?", (*estados, desde.isoformat(), hasta.isoformat()))]
    for f in filas:
        f["inicio"] = hora_chile(f["fecha"], f["hora"])
        f["desde"] = apostar_desde(f["cuota_minima"])
    return filas


# ------------------------------------------------------------------ HTML
CSS = """
:root{--f:#fff;--t:#16181d;--s:#5b6270;--b:#e3e6ea;--c:#f6f7f9;--ok:#0f7b3f;--no:#b42318;--a:#1d4ed8}
@media(prefers-color-scheme:dark){:root{--f:#111317;--t:#e8eaee;--s:#9aa3b2;--b:#2a2e36;--c:#181b21;--ok:#4ade80;--no:#f87171;--a:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--f);color:var(--t)}
header{position:sticky;top:0;background:var(--f);border-bottom:1px solid var(--b);padding:10px 14px;z-index:2}
header b{font-size:16px}nav{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
nav a{padding:6px 12px;border-radius:999px;border:1px solid var(--b);color:var(--t);text-decoration:none;font-size:14px}
nav a.on{background:var(--t);color:var(--f);border-color:var(--t)}
main{max-width:760px;margin:0 auto;padding:12px 14px 40px}
h2{font-size:15px;margin:22px 0 8px;color:var(--s);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.card{border:1px solid var(--b);border-radius:12px;padding:12px;margin:8px 0;background:var(--c)}
.fila{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.p{font-weight:600}.m{color:var(--s);font-size:13px}.big{font-size:20px;font-weight:700}
.tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--b);color:var(--s)}
.ok{color:var(--ok)}.no{color:var(--no)}.cuota{font-size:18px;font-weight:700;color:var(--a)}
.res{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.res>div{border:1px solid var(--b);border-radius:12px;padding:10px;background:var(--c)}
.nota{color:var(--s);font-size:13px;margin:6px 0 14px}a.sw{color:var(--a);font-size:14px}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:7px 6px;border-bottom:1px solid var(--b);text-align:left}
th{color:var(--s);font-weight:600;font-size:12px;text-transform:uppercase}td.r,th.r{text-align:right}
.ap{display:flex;flex-direction:column;gap:6px;align-items:stretch;min-width:132px}
.ap input{width:100%;font:inherit;font-size:16px;padding:7px 8px;border:1px solid var(--b);border-radius:8px;background:var(--f);color:var(--t)}
.ap .dos{display:flex;gap:6px}.ap .dos input:first-child{flex:1.1}.ap .dos input:last-child{flex:1}
button{font:inherit;font-size:14px;padding:8px 10px;border-radius:8px;border:1px solid var(--b);background:var(--f);color:var(--t);cursor:pointer}
button.pri{background:var(--a);border-color:var(--a);color:#fff;font-weight:600}
.btns{display:flex;gap:4px;width:100%}.btns button{flex:1 1 0;min-width:0;padding:7px 2px;font-size:12.5px}
.btns button.sel-ganada{background:var(--ok);border-color:var(--ok);color:#fff}
.btns button.sel-perdida{background:var(--no);border-color:var(--no);color:#fff}
.btns button.sel-nula{background:var(--s);border-color:var(--s);color:#fff}
.ver{font-size:12px;min-height:16px}.err{color:var(--no);font-size:12px}
"""


JS = """
function num(v){v=(v||'').toString().replace(',','.').trim();return v===''?NaN:Number(v)}
function veredicto(inp){const c=inp.closest('.ap'),min=Number(c.dataset.min),v=num(inp.value),o=c.querySelector('.ver');
 if(isNaN(v)){o.textContent='mín. '+min.toFixed(2).replace('.',',');o.className='ver m';return}
 if(v>=min){o.textContent='✓ conviene';o.className='ver ok'}else{o.textContent='✗ bajo el mínimo';o.className='ver no'}}
async function enviar(url,datos,boton){boton.disabled=true;const caja=boton.closest('.ap,.btns').parentElement;
 const e=caja.querySelector('.err');if(e)e.textContent='';
 try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Senales':'1'},
   body:JSON.stringify(datos)});const j=await r.json();if(!r.ok||j.error)throw new Error(j.error||'error');location.reload()}
 catch(x){boton.disabled=false;if(e)e.textContent=x.message}}
function apostar(b){const c=b.closest('.ap');const cuota=c.querySelector('[name=cuota]').value,monto=c.querySelector('[name=monto]').value;
 const v=num(cuota);if(isNaN(v)){c.querySelector('.err').textContent='Ingresa la cuota';return}
 if(v<Number(c.dataset.min)&&!confirm('La cuota está bajo el mínimo. ¿Registrar igual?'))return;
 enviar('/api/apostar',{proyeccion_id:Number(c.dataset.id),cuota:cuota,monto:monto},b)}
function resultado(b,id,estado){enviar('/api/resultado',{apuesta_id:id,estado:estado},b)}
"""


def pagina(titulo: str, activa: str, cuerpo: str) -> HTMLResponse:
    enlaces = "".join(
        f'<a href="{u}" class="{"on" if k == activa else ""}">{t}</a>'
        for k, u, t in (("hoy", "/", "Hoy"), ("res", "/resultados", "Resultados"), ("ap", "/apuestas", "Mis apuestas")))
    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>{escape(titulo)}</title><style>{CSS}</style></head><body>
<header><b>Señales</b> <span class="m">· {datetime.now(TZ_CL):%d-%m %H:%M}</span><nav>{enlaces}</nav></header>
<main>{cuerpo}</main><script>{JS}</script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def descripcion_foco() -> str:
    lados = " y ".join(LADO[l].lower() for l in config.LADOS_FOCO)
    excl = ", ".join(NOMBRE_LIGA.get(l, l) for l in config.LIGAS_EXCLUIDAS) or "ninguna"
    return (f"{MERCADO[config.MERCADO_PRINCIPAL]} · {lados} · ligas excluidas: {excl} · "
            f"piso de cuota {num(config.CUOTA_PISO)}")


def botones_resultado(a: dict) -> str:
    sel = a["estado"] if a.get("liquidado_por") == "manual" or a["estado"] != "pendiente" else ""
    def b(estado, txt):
        clase = f' class="sel-{estado}"' if sel == estado else ""
        return f'<button{clase} onclick="resultado(this,{a["id"]},\'{estado}\')">{txt}</button>'
    return (f'<div class="btns">{b("ganada", "Ganada")}{b("perdida", "Perdida")}{b("nula", "Anulada")}</div>'
            f'<div class="err"></div>')


def caja_apuesta(f: dict, apuesta: dict | None, monto_defecto: float) -> str:
    if apuesta:
        est = {"ganada": '<span class="ok">ganada</span>', "perdida": '<span class="no">perdida</span>',
               "nula": '<span class="m">anulada</span>'}.get(apuesta["estado"], '<span class="m">pendiente</span>')
        return (f'<div class="ap"><div class="m">Apostada a <b>{num(apuesta["cuota"])}</b> · '
                f'{pesos(apuesta["monto"]).replace("+", "")}</div><div class="m">Resultado: {est}</div>'
                f'<div>{botones_resultado(apuesta)}</div></div>')
    return (f'<div class="ap" data-id="{f["id"]}" data-min="{f["desde"]:.2f}">'
            f'<div class="m">apostar si paga <b class="cuota" style="font-size:15px">≥ {num(f["desde"])}</b></div>'
            f'<div class="dos"><input name="cuota" inputmode="decimal" placeholder="cuota" autocomplete="off" '
            f'oninput="veredicto(this)"><input name="monto" inputmode="numeric" value="{monto_defecto:.0f}" '
            f'aria-label="monto"></div><div class="ver m">mín. {num(f["desde"])}</div>'
            f'<button class="pri" onclick="apostar(this)">Apostar</button><div class="err"></div></div>')


def apuestas_por_proyeccion(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    with db.conexion() as con:
        filas = con.execute(f"SELECT * FROM apuestas WHERE proyeccion_id IN ({','.join('?' * len(ids))})", ids)
        return {r["proyeccion_id"]: dict(r) for r in filas}


def tarjeta_candidata(f: dict, con_resultado: bool = False, apuesta: dict | None = None,
                      monto_defecto: float = 5000, con_apuesta: bool = False) -> str:
    arb = f' · árb. {escape(f["arbitro"])}' if f.get("arbitro") else ""
    linea = f'{LADO[f["lado"]]} {num(f["linea"], 1)} {MERCADO[f["mercado"]].lower()}'
    if con_resultado:
        if f["estado"] == "acertada":
            der = f'<span class="big ok">✓</span><div class="m">hubo {num(f["valor_real"], 0)}</div>'
        elif f["estado"] == "fallada":
            der = f'<span class="big no">✗</span><div class="m">hubo {num(f["valor_real"], 0)}</div>'
        else:
            der = '<span class="tag">anulada</span>'
    elif con_apuesta:
        der = caja_apuesta(f, apuesta, monto_defecto)
    else:
        der = f'<div class="m">apostar si paga</div><div class="cuota">≥ {num(f["desde"])}</div>'
    return f"""<div class="card"><div class="fila"><div>
<div class="p">{escape(f["local"])} – {escape(f["visita"])}</div>
<div class="m">{f["inicio"]:%d-%m %H:%M} · {NOMBRE_LIGA.get(f["liga"], f["liga"])}{arb}</div>
<div style="margin-top:6px">{linea} <span class="tag">modelo {pct(f["prob"])}</span></div>
</div><div style="text-align:right;min-width:92px;width:{'172px' if con_apuesta else 'auto'};flex-shrink:0">{der}</div></div></div>"""


# ------------------------------------------------------------------ pantallas
async def hoy(request):
    todas = request.query_params.get("todas") == "1"
    ahora = datetime.now(TZ_CL)
    filas = candidatas(ahora.date() - timedelta(days=1), ahora.date() + timedelta(days=2),
                       ("pendiente", "acertada", "fallada", "nula"))
    apostadas = apuestas_por_proyeccion([f["id"] for f in filas])
    filas = [f for f in filas if f["id"] in apostadas or f in filtro_foco([f], todas)]
    filas = [f for f in filas if f["inicio"].date() <= ahora.date() + timedelta(days=1) and (
        (f["estado"] == "pendiente" and f["inicio"] >= ahora - timedelta(minutes=15))
        or (f["id"] in apostadas and f["inicio"].date() == ahora.date()))]
    filas.sort(key=lambda f: (f["inicio"], -f["prob"]))
    monto_defecto = reg_apuestas.ultimo_monto()
    cambiar = ('<a class="sw" href="/">Ver solo mi foco</a>' if todas
               else '<a class="sw" href="/?todas=1">Ver todas las candidatas</a>')
    cuerpo = [f'<p class="nota">{"Todas las candidatas" if todas else "Foco: " + descripcion_foco()}. '
              f'Horas de Chile. {cambiar}</p>']
    if not filas:
        cuerpo.append('<div class="card">No hay candidatas pendientes para hoy ni mañana.</div>')
    dia_actual = None
    for f in filas:
        if f["inicio"].date() != dia_actual:
            dia_actual = f["inicio"].date()
            nombre = "Hoy" if dia_actual == ahora.date() else "Mañana"
            cuerpo.append(f"<h2>{nombre} · {dia_actual:%d-%m}</h2>")
        cuerpo.append(tarjeta_candidata(f, apuesta=apostadas.get(f["id"]), monto_defecto=monto_defecto,
                                        con_apuesta=True))
    cuerpo.append('<p class="nota">Escribe la cuota que ofrece la casa: te dice al tiro si conviene. '
                  '"Apostar" la registra con ese monto. El resultado se cierra solo con los datos oficiales; '
                  'si lo marcas a mano, manda el tuyo. Si la casa ofrece otra línea, pídele el cálculo a Claude.</p>')
    return pagina("Hoy", "hoy", "".join(cuerpo))


def bloque_resumen(filas: list[dict], titulo: str) -> str:
    dec = [f for f in filas if f["estado"] in ("acertada", "fallada")]
    if not dec:
        return ""
    ac = sum(f["estado"] == "acertada" for f in dec)
    minimo = sum(1 / f["desde"] for f in dec) / len(dec)
    r = acierto_vs_minimo(ac, len(dec), minimo)
    clase = "ok" if r["diferencia_puntos"] > 0 else "no"
    return f"""<h2>{escape(titulo)}</h2><div class="res">
<div><div class="m">Acertadas</div><div class="big">{ac} de {len(dec)}</div></div>
<div><div class="m">Acierto</div><div class="big">{pct(r["acierto"])}</div></div>
<div><div class="m">Mínimo para no perder</div><div class="big">{pct(r["minimo_para_no_perder"])}</div></div>
<div><div class="m">Diferencia</div><div class="big {clase}">{r["diferencia_puntos"]:+.1f} pts</div></div>
<div><div class="m">Prob. de ser suerte</div><div class="big">{pct(r["prob_de_ser_suerte"])}</div></div>
</div>"""


async def resultados(request):
    todas = request.query_params.get("todas") == "1"
    try:
        dias = max(1, min(int(request.query_params.get("dias", "14")), 120))
    except ValueError:
        dias = 14
    hoy_cl = datetime.now(TZ_CL).date()
    filas = filtro_foco(candidatas(hoy_cl - timedelta(days=dias), hoy_cl, ("acertada", "fallada", "nula")), todas)
    pend = [f for f in filtro_foco(candidatas(hoy_cl - timedelta(days=dias), hoy_cl, ("pendiente",)), todas)
            if f["inicio"] < datetime.now(TZ_CL)]
    filas.sort(key=lambda f: f["inicio"], reverse=True)
    cambiar = ('<a class="sw" href="/resultados">Ver solo mi foco</a>' if todas
               else '<a class="sw" href="/resultados?todas=1">Ver todas</a>')
    cuerpo = [f'<p class="nota">{"Todas las candidatas" if todas else "Foco: " + descripcion_foco()}. '
              f'Últimos {dias} días. {cambiar} · '
              + " ".join(f'<a class="sw" href="/resultados?dias={d}{"&todas=1" if todas else ""}">{d} d</a>'
                         for d in (7, 14, 30, 90)) + "</p>"]
    if not filas:
        cuerpo.append('<div class="card">Todavía no hay candidatas con resultado en este período. '
                      'Los resultados llegan entre 1 y 3 días después del partido.</div>')
    else:
        cuerpo.append(bloque_resumen(filas, "Resumen"))
        cuerpo.append('<p class="nota">Supone apostar justo a la cuota mínima indicada '
                      '("apostar si paga ≥"). Si la casa pagó más, el resultado real es mejor.</p>')
        por_liga = {}
        for f in filas:
            por_liga.setdefault(f["liga"], []).append(f)
        if len(por_liga) > 1:
            filas_liga = []
            for liga, g in sorted(por_liga.items()):
                dec = [f for f in g if f["estado"] in ("acertada", "fallada")]
                if not dec:
                    continue
                ac = sum(f["estado"] == "acertada" for f in dec)
                mn = sum(1 / f["desde"] for f in dec) / len(dec)
                filas_liga.append(f'<tr><td>{NOMBRE_LIGA.get(liga, liga)}</td><td class="r">{ac}/{len(dec)}</td>'
                                  f'<td class="r">{pct(ac / len(dec))}</td><td class="r">{pct(mn)}</td></tr>')
            cuerpo.append('<h2>Por liga</h2><table><tr><th>Liga</th><th class="r">Aciertos</th>'
                          '<th class="r">Acierto</th><th class="r">Mínimo</th></tr>' + "".join(filas_liga) + "</table>")
    if pend:
        cuerpo.append(f'<p class="nota">{len(pend)} candidatas ya jugadas esperan el resultado oficial.</p>')
    if filas:
        cuerpo.append("<h2>Detalle</h2>" + "".join(tarjeta_candidata(f, con_resultado=True) for f in filas[:200]))
    return pagina("Resultados", "res", "".join(cuerpo))


async def apuestas(request):
    r = rendimiento_apuestas()
    with db.conexion() as con:
        lista = [dict(x) for x in con.execute("SELECT * FROM apuestas ORDER BY fecha DESC, id DESC LIMIT 100")]
    cuerpo = []
    if r.get("apuestas_liquidadas"):
        t = r["total"]
        clase = "ok" if t["ganancia"] >= 0 else "no"
        cuerpo.append(f"""<h2>Resumen</h2><div class="res">
<div><div class="m">Apuestas cerradas</div><div class="big">{t["n"]}</div></div>
<div><div class="m">Resultado</div><div class="big {clase}">{pesos(t["ganancia"])}</div></div>
<div><div class="m">Acierto / mínimo</div><div class="big">{pct(t.get("acierto"))} / {pct(t.get("minimo_para_no_perder"))}</div></div>
<div><div class="m">Prob. de ser suerte</div><div class="big">{pct(t.get("prob_de_ser_suerte"))}</div></div>
<div><div class="m">CLV medio</div><div class="big">{pct(t["clv_medio"]) if t["clv_medio"] is not None else "—"}</div></div>
</div>""")
    if r.get("pendientes"):
        n = r["pendientes"]
        cuerpo.append(f'<p class="nota">{n} {"apuesta espera" if n == 1 else "apuestas esperan"} resultado.</p>')
    if not lista:
        cuerpo.append('<div class="card">Aún no hay apuestas registradas. Pídele a Claude que registre '
                      'cada apuesta con su cuota y monto.</div>')
    if lista:
        cuerpo.append("<h2>Detalle</h2>")
    for a in lista:
        est = {"ganada": '<span class="ok">ganada</span>', "perdida": '<span class="no">perdida</span>',
               "nula": '<span class="m">anulada</span>'}.get(a["estado"], '<span class="m">pendiente</span>')
        origen = " · a mano" if a.get("liquidado_por") == "manual" else ""
        linea = f'{LADO.get(a["lado"], a["lado"])} {num(a["linea"], 1)} {MERCADO.get(a["mercado"], a["mercado"]).lower()}'
        gan = f' · {pesos(a["ganancia"])}' if a["ganancia"] is not None and a["estado"] != "pendiente" else ""
        cuerpo.append(f"""<div class="card"><div class="fila"><div>
<div class="p">{escape(a["local"] or "")} – {escape(a["visita"] or "")}</div>
<div class="m">{a["fecha"][8:10]}-{a["fecha"][5:7]} · {NOMBRE_LIGA.get(a["liga"], a["liga"] or "")}</div>
<div style="margin-top:6px">{linea}</div>
<div class="m">cuota {num(a["cuota"])} · {pesos(a["monto"]).replace("+", "")}{gan}</div>
</div><div style="text-align:right;width:172px;flex-shrink:0"><div class="m">{est}{origen}</div>
<div style="margin-top:6px">{botones_resultado(a)}</div></div></div></div>""")
    return pagina("Mis apuestas", "ap", "".join(cuerpo))


def _origen_valido(request: Request) -> bool:
    """Protección contra envíos desde otros sitios: exige el encabezado propio del panel y,
    si el navegador manda Origin, que sea este mismo sitio."""
    if request.headers.get("x-senales") != "1":
        return False
    origen = request.headers.get("origin")
    if origen:
        host = request.headers.get("host", "")
        return origen in (f"https://{host}", f"http://{host}")
    return True


async def api_apostar(request: Request):
    if not _origen_valido(request):
        return JSONResponse({"error": "solicitud no permitida"}, status_code=403)
    try:
        d = await request.json()
        r = reg_apuestas.registrar(d.get("cuota"), d.get("monto"), int(d.get("proyeccion_id")), casa="panel")
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(r)


async def api_resultado(request: Request):
    if not _origen_valido(request):
        return JSONResponse({"error": "solicitud no permitida"}, status_code=403)
    try:
        d = await request.json()
        r = reg_apuestas.marcar_resultado(int(d.get("apuesta_id")), str(d.get("estado")))
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(r)


async def salud(request):
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ autenticación
class Autenticacion(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path == "/salud":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.lower().startswith("basic ") and config.WEB_CLAVE:
            try:
                usuario, _, clave = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                ok = (hmac.compare_digest(usuario.encode(), config.WEB_USUARIO.encode())
                      & hmac.compare_digest(clave.encode(), config.WEB_CLAVE.encode()))
            except (binascii.Error, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response("Acceso restringido", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Senales", charset="UTF-8"'})
        resp = await call_next(request)
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp


def crear_app() -> Starlette:
    return Starlette(routes=[Route("/", hoy), Route("/resultados", resultados), Route("/apuestas", apuestas),
                             Route("/api/apostar", api_apostar, methods=["POST"]),
                             Route("/api/resultado", api_resultado, methods=["POST"]),
                             Route("/salud", salud)],
                     middleware=[Middleware(Autenticacion)])


def main():
    import uvicorn
    if len(config.WEB_CLAVE) < 12:
        raise SystemExit("Falta LIGASTATS_WEB_CLAVE (mínimo 12 caracteres)")
    db.inicializar()
    uvicorn.run(crear_app(), host=config.WEB_HOST, port=config.WEB_PORT, log_level="info",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
