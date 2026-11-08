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
import json
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
from . import cuotas as reg_cuotas
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


def filtro_foco(filas: list[dict], todas: bool, mercado: str | None = None) -> list[dict]:
    """Filtra por mercado elegido y, salvo 'todas', por lados y ligas del foco."""
    mercado = mercado or config.MERCADO_PRINCIPAL
    filas = [f for f in filas if f["mercado"] == mercado]
    if todas:
        return filas
    return [f for f in filas if f["lado"] in config.LADOS_FOCO and f["liga"] not in config.LIGAS_EXCLUIDAS]


def mercado_pedido(request) -> str:
    m = request.query_params.get("mercado", "")
    return m if m in config.MERCADOS else config.MERCADO_PRINCIPAL


def selector_mercado(ruta: str, mercado: str, todas: bool, extra: str = "") -> str:
    chips = []
    for m in ("tarjetas", "corners", "goles"):
        url = f"{ruta}?mercado={m}" + ("&todas=1" if todas else "") + extra
        etiqueta = MERCADO[m] + (" ★" if m == config.MERCADO_PRINCIPAL else "")
        chips.append(f'<a href="{url}" class="{"on" if m == mercado else ""}">{etiqueta}</a>')
    aviso = ""
    if mercado != config.MERCADO_PRINCIPAL:
        aviso = ('<p class="nota"><b>En observación:</b> este mercado se mide para saber si el modelo acierta, '
                 'pero no es tu foco de apuesta.</p>')
    return f'<nav style="margin:4px 0 8px">{"".join(chips)}</nav>{aviso}'


def descripcion_foco(mercado: str | None = None) -> str:
    mercado = mercado or config.MERCADO_PRINCIPAL
    lados = " y ".join(LADO[l].lower() for l in config.LADOS_FOCO)
    excl = ", ".join(NOMBRE_LIGA.get(l, l) for l in config.LIGAS_EXCLUIDAS) or "ninguna"
    return f"{MERCADO[mercado]} · {lados} · ligas excluidas: {excl} · piso de cuota {num(config.CUOTA_PISO)}"


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
.cuo{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
.lado{border:1px solid var(--b);border-radius:10px;padding:8px;background:var(--f);display:flex;flex-direction:column;gap:6px}
.lado input,.pie input{width:100%;font:inherit;font-size:16px;padding:7px 8px;border:1px solid var(--b);border-radius:8px;background:var(--c);color:var(--t)}
.lado.cand{border-color:var(--a)}
.pie{display:flex;gap:8px;align-items:center;margin-top:8px}.pie input{width:110px;flex:0 0 auto}.pie button{flex:1}
.mk{font-size:12px;color:var(--s);margin-top:4px}
.mb{margin-top:12px;padding-top:10px;border-top:1px dashed var(--b)}
.mb .cab{display:flex;justify-content:space-between;align-items:center;gap:8px}
.mb select{font:inherit;font-size:15px;padding:5px 8px;border:1px solid var(--b);border-radius:8px;background:var(--f);color:var(--t)}
.badge{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;background:var(--a);color:#fff;margin:4px 4px 0 0}
"""


JS = """
function num(v){v=(v||'').toString().replace(',','.').trim();return v===''?NaN:Number(v)}
function fmt(x){return x.toFixed(2).replace('.',',')}
function pc(x){return Math.round(100*x)+' %'}
function lineaActual(mb){const L=JSON.parse(mb.dataset.lineas);return L[mb.querySelector('select').value]}
function cambiarLinea(sel){const mb=sel.closest('.mb'),d=lineaActual(mb);mb.dataset.id=d.id;
 mb.querySelectorAll('.lado').forEach(l=>{const u=l.dataset.lado==='under';l.dataset.min=u?d.mu:d.mo;
  l.querySelector('.pl').textContent=(u?'Menos ':'Más ')+d.t;l.querySelector('.pm').textContent='modelo '+pc(u?d.pu:1-d.pu);
  l.querySelector('input').value=(u?d.cu:d.co)||''});actualizar(mb)}
function actualizar(mb){let cm=NaN,cx=NaN;
 mb.querySelectorAll('.lado').forEach(l=>{const v=num(l.querySelector('input').value),min=Number(l.dataset.min),o=l.querySelector('.ver');
  if(l.dataset.lado==='under')cm=v;else cx=v;
  if(isNaN(v)){o.textContent='mín. '+fmt(min);o.className='ver m'}
  else if(v>=min){o.textContent='✓ conviene';o.className='ver ok'}else{o.textContent='✗ bajo el mínimo';o.className='ver no'}});
 const mk=mb.querySelector('.mk');
 if(!isNaN(cm)&&!isNaN(cx)){const a=1/cm,b=1/cx,q=a/(a+b);
  mk.textContent='Casa sin margen: menos '+pc(q)+' · más '+pc(1-q)+' (margen '+Math.round(100*(a+b-1))+' %)'}else mk.textContent=''}
function items(card){const r=[];card.querySelectorAll('.mb').forEach(mb=>{
  const u=mb.querySelector('.lado[data-lado=under] input').value,o=mb.querySelector('.lado[data-lado=over] input').value;
  if(u||o)r.push({proyeccion_id:Number(mb.dataset.id),cuota_menos:u,cuota_mas:o})});return r}
function guardarCuotas(b){const card=b.closest('.card'),it=items(card);
 if(!it.length){card.querySelector('.err').textContent='Ingresa al menos una cuota';return}
 enviar('/api/cuotas',{items:it},b)}
function apostar(b,lado){const card=b.closest('.card'),mb=b.closest('.mb'),l=mb.querySelector('.lado[data-lado='+lado+']');
 const cuota=l.querySelector('input').value,v=num(cuota);
 if(isNaN(v)){card.querySelector('.err').textContent='Ingresa la cuota de '+(lado==='under'?'menos':'más');return}
 if(v<Number(l.dataset.min)&&!confirm('La cuota está bajo el mínimo. ¿Registrar igual?'))return;
 enviar('/api/apostar',{proyeccion_id:Number(mb.dataset.id),lado:lado,cuota:cuota,
  monto:card.querySelector('[name=monto]').value,items:items(card)},b)}
async function enviar(url,datos,boton){boton.disabled=true;const card=boton.closest('.card');
 const e=card?card.querySelector('.err'):null;if(e)e.textContent='';
 try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Senales':'1'},
   body:JSON.stringify(datos)});const j=await r.json();if(!r.ok||j.error)throw new Error(j.error||'error');location.reload()}
 catch(x){boton.disabled=false;if(e)e.textContent=x.message}}
function resultado(b,id,estado){enviar('/api/resultado',{apuesta_id:id,estado:estado},b)}
document.addEventListener('DOMContentLoaded',()=>document.querySelectorAll('.mb').forEach(actualizar));
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


def botones_resultado(a: dict) -> str:
    sel = a["estado"] if a.get("liquidado_por") == "manual" or a["estado"] != "pendiente" else ""
    def b(estado, txt):
        clase = f' class="sel-{estado}"' if sel == estado else ""
        return f'<button{clase} onclick="resultado(this,{a["id"]},\'{estado}\')">{txt}</button>'
    return (f'<div class="btns">{b("ganada", "Ganada")}{b("perdida", "Perdida")}{b("nula", "Anulada")}</div>'
            f'<div class="err"></div>')


def partidos_ventana(desde: date, hasta: date) -> dict[str, dict]:
    # Todas las proyecciones (ambas caras, todas las líneas) agrupadas por partido.
    with db.conexion() as con:
        db.aplicar_parametros(con)
        filas = [dict(r) for r in con.execute(
            "SELECT * FROM proyecciones WHERE fecha BETWEEN ? AND ? ORDER BY id",
            (desde.isoformat(), hasta.isoformat()))]
        guardadas = reg_cuotas.ultimas(con, filas)
        ids = sorted({f["partido_id"] for f in filas})
        apuestas_l = {}
        if ids:
            for r in con.execute(f"SELECT * FROM apuestas WHERE partido_id IN ({','.join('?' * len(ids))})", ids):
                apuestas_l[(r["partido_id"], r["mercado"])] = dict(r)
    partidos = {}
    for f in filas:
        pt = partidos.setdefault(f["partido_id"], {
            "id": f["partido_id"], "liga": f["liga"], "local": f["local"], "visita": f["visita"],
            "arbitro": f["arbitro"], "inicio": hora_chile(f["fecha"], f["hora"]), "pendiente": False,
            "mercados": {}, "candidatas": []})
        pt["pendiente"] |= f["estado"] == "pendiente"
        m = pt["mercados"].setdefault(f["mercado"], {})
        ln = m.setdefault(f["linea"], {})
        ln["id_" + f["lado"]] = f["id"]
        if f["lado"] == "under":
            ln["pu"] = f["prob"]
        if f["tipo"] in ("candidata", "senal"):
            pt["candidatas"].append(f)
    for pt in partidos.values():
        for merc, lineas in pt["mercados"].items():
            for linea, ln in lineas.items():
                if "pu" not in ln:  # solo existiera la cara 'over'
                    continue
                c = guardadas.get((pt["id"], merc, linea)) or {}
                ln.update(cu=c.get("cuota_menos"), co=c.get("cuota_mas"))
            pt.setdefault("apuestas", {})[merc] = apuestas_l.get((pt["id"], merc))
    return partidos


def linea_inicial(pt: dict, merc: str) -> float:
    lineas = {l: d for l, d in pt["mercados"][merc].items() if "pu" in d}
    ap = pt["apuestas"].get(merc)
    if ap and ap["linea"] in lineas:
        return ap["linea"]
    cands = [c for c in pt["candidatas"] if c["mercado"] == merc]
    foco = [c for c in cands if c["lado"] in config.LADOS_FOCO]
    if foco or cands:
        return max(foco or cands, key=lambda c: c["prob"])["linea"]
    return min(lineas, key=lambda l: abs(lineas[l]["pu"] - 0.5))


def bloque_mercado(pt: dict, merc: str) -> str:
    lineas = {l: d for l, d in sorted(pt["mercados"][merc].items()) if "pu" in d}
    if not lineas:
        return ""
    sel = linea_inicial(pt, merc)
    cand_lineas = {c["linea"] for c in pt["candidatas"] if c["mercado"] == merc}
    datos = {}
    opciones = []
    for l, d in lineas.items():
        clave_l = f"{l:g}"
        datos[clave_l] = {"id": d.get("id_under") or d.get("id_over"), "t": num(l, 1), "pu": round(d["pu"], 4),
                          "mu": round(config.cuota_minima(d["pu"]), 3), "mo": round(config.cuota_minima(1 - d["pu"]), 3),
                          "cu": num(d["cu"]) if d.get("cu") else "", "co": num(d["co"]) if d.get("co") else ""}
        estrella = " ★" if l in cand_lineas else ""
        opciones.append(f'<option value="{clave_l}"{" selected" if l == sel else ""}>{num(l, 1)}{estrella}</option>')
    d = datos[f"{sel:g}"]
    ap = pt["apuestas"].get(merc)
    lados = []
    for lado in ("under", "over"):
        u = lado == "under"
        boton = "" if ap else ('<button onclick="apostar(this,' + "'" + lado + "'" + f')">Apostar {LADO[lado].lower()}</button>')
        lados.append(
            f'<div class="lado" data-lado="{lado}" data-min="{d["mu"] if u else d["mo"]}">'
            f'<div class="m"><b class="pl">{LADO[lado]} {d["t"]}</b> · <span class="pm">modelo {pct(d["pu"] if u else 1 - d["pu"])}</span></div>'
            f'<input inputmode="decimal" placeholder="cuota" value="{d["cu"] if u else d["co"]}" autocomplete="off" '
            f'oninput="actualizar(this.closest(' + "'.mb'" + '))">'
            f'<div class="ver m"></div>{boton}</div>')
    apuesta_html = ""
    if ap:
        est = {"ganada": '<span class="ok">ganada</span>', "perdida": '<span class="no">perdida</span>',
               "nula": '<span class="m">anulada</span>'}.get(ap["estado"], '<span class="m">pendiente</span>')
        apuesta_html = (f'<div style="margin-top:8px" class="m">Apostada: <b>{LADO[ap["lado"]]} {num(ap["linea"], 1)}</b> '
                        f'a <b>{num(ap["cuota"])}</b> · {pesos(ap["monto"]).replace("+", "")} · {est}</div>'
                        f'<div style="margin-top:6px">{botones_resultado(ap)}</div>')
    principal = " ★" if merc == config.MERCADO_PRINCIPAL else ""
    lineas_json = escape(json.dumps(datos, ensure_ascii=False))
    return (f'<div class="mb" data-mercado="{merc}" data-id="{d["id"]}" data-lineas="{lineas_json}">'
            f'<div class="cab"><b>{MERCADO[merc]}{principal}</b><label class="m">línea '
            f'<select onchange="cambiarLinea(this)">{"".join(opciones)}</select></label></div>'
            f'<div class="cuo">{"".join(lados)}</div><div class="mk"></div>{apuesta_html}</div>')


def tarjeta_partido(pt: dict, monto_defecto: float) -> str:
    arb = f' · árb. {escape(pt["arbitro"])}' if pt.get("arbitro") else ""
    badges = "".join(
        f'<span class="badge">★ {LADO[c["lado"]].lower()} {num(c["linea"], 1)} {MERCADO[c["mercado"]].lower()} · {pct(c["prob"])}</span>'
        for c in sorted(pt["candidatas"], key=lambda c: -c["prob"])
        if c["mercado"] == config.MERCADO_PRINCIPAL and c["lado"] in config.LADOS_FOCO)
    bloques = "".join(bloque_mercado(pt, m) for m in ("tarjetas", "corners", "goles") if m in pt["mercados"])
    return (f'<div class="card"><div class="p">{escape(pt["local"])} – {escape(pt["visita"])}</div>'
            f'<div class="m">{pt["inicio"]:%d-%m %H:%M} · {NOMBRE_LIGA.get(pt["liga"], pt["liga"])}{arb}</div>'
            f'<div>{badges}</div>{bloques}'
            f'<div class="pie"><input name="monto" inputmode="numeric" value="{monto_defecto:.0f}" aria-label="monto">'
            f'<button class="pri" onclick="guardarCuotas(this)">Guardar cuotas</button></div><div class="err"></div></div>')


def apuestas_por_linea(filas_p: list[dict]) -> dict[tuple, dict]:
    if not filas_p:
        return {}
    ids = sorted({f["partido_id"] for f in filas_p})
    with db.conexion() as con:
        filas = con.execute(f"SELECT * FROM apuestas WHERE partido_id IN ({','.join('?' * len(ids))})", ids)
        return {(r["partido_id"], r["mercado"], r["linea"]): dict(r) for r in filas}


def clave(f: dict) -> tuple:
    return (f["partido_id"], f["mercado"], f["linea"])


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
    partidos = partidos_ventana(ahora.date() - timedelta(days=1), ahora.date() + timedelta(days=2))
    lista = []
    for pt in partidos.values():
        if not todas and pt["liga"] in config.LIGAS_EXCLUIDAS:
            continue
        if pt["inicio"].date() > ahora.date() + timedelta(days=1):
            continue
        con_apuesta = any(pt["apuestas"].values())
        vigente = pt["pendiente"] and pt["inicio"] >= ahora - timedelta(minutes=15)
        if vigente or (con_apuesta and pt["inicio"].date() == ahora.date()):
            lista.append(pt)
    # primero los que tienen candidatas de tu foco, luego por hora
    lista.sort(key=lambda pt: pt["inicio"])
    monto_defecto = reg_apuestas.ultimo_monto()
    excl = ", ".join(NOMBRE_LIGA.get(l, l) for l in config.LIGAS_EXCLUIDAS) or "ninguna"
    cambiar = ('<a class="sw" href="/">Ocultar ligas excluidas</a>' if todas
               else f'<a class="sw" href="/?todas=1">Ver también {excl}</a>')
    cuerpo = [f'<p class="nota">Horas de Chile. ★ = candidata del modelo (tu foco: {MERCADO[config.MERCADO_PRINCIPAL].lower()}, '
              f'{" y ".join(LADO[l].lower() for l in config.LADOS_FOCO)}). Piso de cuota {num(config.CUOTA_PISO)}. {cambiar}</p>']
    if not lista:
        cuerpo.append('<div class="card">No hay partidos pendientes para hoy ni mañana.</div>')
    dia_actual = None
    for pt in lista:
        if pt["inicio"].date() != dia_actual:
            dia_actual = pt["inicio"].date()
            nombre = "Hoy" if dia_actual == ahora.date() else "Mañana"
            cuerpo.append(f"<h2>{nombre} · {dia_actual:%d-%m}</h2>")
        cuerpo.append(tarjeta_partido(pt, monto_defecto))
    cuerpo.append('<p class="nota">Elige la línea que ofrece la casa en cada mercado y escribe sus cuotas de '
                  '"menos" y "más": te dice en qué lado conviene y cuánto cree la casa sin su margen. '
                  '"Guardar cuotas" registra todo el partido aunque no apuestes (así se mide si el modelo le gana '
                  'al mercado). "Apostar" registra la apuesta en ese lado con el monto de abajo. El resultado se '
                  'cierra solo; si lo marcas a mano, manda el tuyo.</p>')
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


def bloque_cuotas_reales(desde: date, hasta: date, mercado: str, todas: bool) -> str:
    # Simula 1 unidad a la última cuota guardada de cada línea jugada: (a) los lados que convenían,
    # (b) siempre el lado del foco. Incluye todas las líneas con cuotas, sean o no candidatas.
    with db.conexion() as con:
        db.aplicar_parametros(con)
        filas = [dict(r) for r in con.execute(
            "SELECT p.*, c.cuota_menos, c.cuota_mas FROM cuotas_casa c JOIN proyecciones p ON p.id = c.proyeccion_id "
            "WHERE c.id IN (SELECT MAX(id) FROM cuotas_casa GROUP BY partido_id, mercado, linea) "
            "AND p.mercado = ? AND p.fecha BETWEEN ? AND ? AND p.estado IN ('acertada','fallada')",
            (mercado, desde.isoformat(), hasta.isoformat()))]
    if not todas:
        filas = [f for f in filas if f["liga"] not in config.LIGAS_EXCLUIDAS]
    if not filas:
        return ('<h2>A cuotas reales de la casa</h2><p class="nota">Aún no hay partidos jugados con cuotas guardadas '
                'en este mercado. Usa "Guardar cuotas" en Hoy para empezar a medir contra el mercado.</p>')
    conv, foco, brechas = [], [], []
    for f in filas:
        gana_lado = f["estado"] == "acertada"
        sale_menos = gana_lado if f["lado"] == "under" else not gana_lado
        p_menos = reg_cuotas.prob_menos(f)
        ev = reg_cuotas.evaluar(p_menos, f["cuota_menos"], f["cuota_mas"])
        for lado, gana in (("under", sale_menos), ("over", not sale_menos)):
            cuota = ev[lado]["cuota"]
            if cuota and ev[lado]["conviene"]:
                conv.append((gana, cuota))
            if cuota and lado in config.LADOS_FOCO:
                foco.append((gana, cuota))
        if "prob_casa_sin_margen" in ev["under"]:
            brechas.append(p_menos - ev["under"]["prob_casa_sin_margen"])

    def fila(nombre, apuestas):
        if not apuestas:
            return f'<tr><td>{nombre}</td><td class="r">0</td><td class="r">—</td><td class="r">—</td></tr>'
        g = sum((c - 1) if w else -1 for w, c in apuestas)
        ac = sum(w for w, _ in apuestas) / len(apuestas)
        clase = "ok" if g >= 0 else "no"
        return (f'<tr><td>{nombre}</td><td class="r">{len(apuestas)}</td><td class="r">{pct(ac)}</td>'
                f'<td class="r {clase}">{100 * g / len(apuestas):+.1f} %</td></tr>')
    brecha = ""
    if brechas:
        m = sum(brechas) / len(brechas)
        brecha = (f'<p class="nota">En {len(brechas)} líneas con ambas cuotas, el modelo dio en promedio '
                  f'{abs(100 * m):.1f} puntos {"más" if m > 0 else "menos"} de probabilidad al "menos" que la casa.</p>')
    lados_txt = " y ".join(LADO[l].lower() for l in config.LADOS_FOCO)
    return (f'<h2>A cuotas reales de la casa</h2><p class="nota">{len(filas)} líneas jugadas con cuotas guardadas. '
            'Simula 1 unidad por apuesta a la última cuota guardada.</p>'
            '<table><tr><th>Estrategia</th><th class="r">Apuestas</th><th class="r">Acierto</th><th class="r">ROI</th></tr>'
            + fila("Solo los lados que convenían", conv) + fila(f"Siempre {lados_txt}", foco) + "</table>" + brecha)


async def resultados(request):
    todas = request.query_params.get("todas") == "1"
    mercado = mercado_pedido(request)
    try:
        dias = max(1, min(int(request.query_params.get("dias", "14")), 120))
    except ValueError:
        dias = 14
    hoy_cl = datetime.now(TZ_CL).date()
    filas = filtro_foco(candidatas(hoy_cl - timedelta(days=dias), hoy_cl, ("acertada", "fallada", "nula")), todas, mercado)
    pend = [f for f in filtro_foco(candidatas(hoy_cl - timedelta(days=dias), hoy_cl, ("pendiente",)), todas, mercado)
            if f["inicio"] < datetime.now(TZ_CL)]
    filas.sort(key=lambda f: f["inicio"], reverse=True)
    cambiar = (f'<a class="sw" href="/resultados?mercado={mercado}&dias={dias}">Ver solo mi foco</a>' if todas
               else f'<a class="sw" href="/resultados?mercado={mercado}&dias={dias}&todas=1">Ver todas</a>')
    cuerpo = [selector_mercado("/resultados", mercado, todas, f"&dias={dias}"),
              f'<p class="nota">{"Todas las candidatas de " + MERCADO[mercado].lower() if todas else "Foco: " + descripcion_foco(mercado)}. '
              f'Últimos {dias} días. {cambiar} · '
              + " ".join(f'<a class="sw" href="/resultados?mercado={mercado}&dias={d}{"&todas=1" if todas else ""}">{d} d</a>'
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
    cuerpo.append(bloque_cuotas_reales(hoy_cl - timedelta(days=dias), hoy_cl, mercado, todas))
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


def _guardar_items(d: dict) -> list[dict]:
    items = d.get("items")
    if items is None and d.get("proyeccion_id") is not None:
        items = [d]
    salida = []
    for it in items or []:
        if it.get("cuota_menos") or it.get("cuota_mas"):
            salida.append(reg_cuotas.guardar(int(it["proyeccion_id"]), it.get("cuota_menos"), it.get("cuota_mas"),
                                             casa="panel"))
    return salida


async def api_apostar(request: Request):
    if not _origen_valido(request):
        return JSONResponse({"error": "solicitud no permitida"}, status_code=403)
    try:
        d = await request.json()
        _guardar_items({"items": d.get("items") or []})
        r = reg_apuestas.registrar(d.get("cuota"), d.get("monto"), int(d.get("proyeccion_id")), casa="panel",
                                   lado=d.get("lado"))
    except (ValueError, TypeError, KeyError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(r)


async def api_cuotas(request: Request):
    if not _origen_valido(request):
        return JSONResponse({"error": "solicitud no permitida"}, status_code=403)
    try:
        r = _guardar_items(await request.json())
        if not r:
            raise ValueError("ingresa al menos una cuota")
    except (ValueError, TypeError, KeyError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "guardadas": len(r), "detalle": r})


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
                             Route("/api/cuotas", api_cuotas, methods=["POST"]),
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
