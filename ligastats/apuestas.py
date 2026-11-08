"""Registro de apuestas reales y resultado manual (lo usan el panel web y el MCP)."""
from __future__ import annotations

from . import db

ESTADOS_MANUALES = ("ganada", "perdida", "nula")


def _numero(valor, nombre: str, minimo: float, maximo: float) -> float:
    try:
        v = float(str(valor).replace(",", ".").strip())
    except ValueError:
        raise ValueError(f"{nombre} no es un número válido")
    if not minimo <= v <= maximo:
        raise ValueError(f"{nombre} debe estar entre {minimo:g} y {maximo:g}")
    return v


def registrar(cuota, monto, proyeccion_id: int | None = None, casa: str | None = None, nota: str | None = None,
              **manual) -> dict:
    """Registra una apuesta. Con proyeccion_id toma los datos del partido de la proyección;
    sin ella requiere liga, fecha, local, visita, mercado, linea y lado."""
    cuota = _numero(cuota, "La cuota", 1.01, 100)
    monto = _numero(monto, "El monto", 100, 10_000_000)
    with db.conexion() as con:
        if proyeccion_id is not None:
            p = con.execute("SELECT * FROM proyecciones WHERE id = ?", (int(proyeccion_id),)).fetchone()
            if not p:
                raise ValueError(f"no existe la proyección {proyeccion_id}")
            ya = con.execute("SELECT id FROM apuestas WHERE proyeccion_id = ?", (p["id"],)).fetchone()
            if ya:
                raise ValueError(f"esa candidata ya tiene una apuesta registrada (#{ya['id']})")
            datos = {k: p[k] for k in ("partido_id", "liga", "fecha", "local", "visita", "mercado", "linea", "lado")}
        else:
            faltan = [k for k in ("liga", "fecha", "local", "visita", "mercado", "linea", "lado") if manual.get(k) is None]
            if faltan:
                raise ValueError("faltan datos: " + ", ".join(faltan))
            datos = {k: manual[k] for k in ("liga", "fecha", "local", "visita", "mercado", "linea", "lado")}
            datos["partido_id"] = f"{datos['liga']}|{datos['fecha']}|{datos['local']}|{datos['visita']}"
        aid = con.execute(
            "INSERT INTO apuestas (proyeccion_id, partido_id, liga, fecha, local, visita, mercado, linea, lado, "
            "cuota, monto, casa, nota, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (proyeccion_id, datos["partido_id"], datos["liga"], datos["fecha"], datos["local"], datos["visita"],
             datos["mercado"], datos["linea"], datos["lado"], cuota, monto, casa, nota, db.ahora())).lastrowid
    return {"ok": True, "apuesta_id": aid, **datos, "cuota": cuota, "monto": monto}


def marcar_resultado(apuesta_id: int, estado: str) -> dict:
    """Resultado manual: ganada, perdida o nula (anulada). Manda sobre el cierre automático."""
    if estado == "anulada":
        estado = "nula"
    if estado not in ESTADOS_MANUALES:
        raise ValueError("el resultado debe ser ganada, perdida o anulada")
    with db.conexion() as con:
        a = con.execute("SELECT * FROM apuestas WHERE id = ?", (int(apuesta_id),)).fetchone()
        if not a:
            raise ValueError(f"no existe la apuesta {apuesta_id}")
        ganancia = {"ganada": round(a["monto"] * (a["cuota"] - 1), 2), "perdida": -a["monto"], "nula": 0.0}[estado]
        con.execute("UPDATE apuestas SET estado = ?, ganancia = ?, liquidado_en = ?, liquidado_por = 'manual' "
                    "WHERE id = ?", (estado, ganancia, db.ahora(), a["id"]))
    return {"ok": True, "apuesta_id": a["id"], "estado": estado, "ganancia": ganancia}


def ultimo_monto(defecto: float = 5000) -> float:
    with db.conexion() as con:
        r = con.execute("SELECT monto FROM apuestas ORDER BY id DESC LIMIT 1").fetchone()
    return float(r["monto"]) if r else defecto
