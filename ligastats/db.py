"""Base de datos SQLite: esquema, conexión y reglas de inmutabilidad."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS partidos (
    id TEXT PRIMARY KEY,               -- liga|fecha|local|visita
    liga TEXT NOT NULL,
    temporada TEXT NOT NULL,
    fecha TEXT NOT NULL,               -- AAAA-MM-DD
    hora TEXT,
    local TEXT NOT NULL,
    visita TEXT NOT NULL,
    arbitro TEXT,
    gl INTEGER, gv INTEGER,            -- goles
    tl INTEGER, tv INTEGER,            -- tiros
    tal INTEGER, tav INTEGER,          -- tiros al arco
    fl INTEGER, fv INTEGER,            -- faltas
    cl INTEGER, cv INTEGER,            -- corners
    al INTEGER, av INTEGER,            -- amarillas
    rl INTEGER, rv INTEGER,            -- rojas
    c_o25 REAL, c_u25 REAL,            -- cuota previa más/menos 2.5 goles
    cc_o25 REAL, cc_u25 REAL,          -- cuota de cierre más/menos 2.5 goles
    fuente_cuota TEXT,
    fuente_cierre TEXT,
    actualizado TEXT
);
CREATE INDEX IF NOT EXISTS ix_partidos_liga_fecha ON partidos(liga, fecha);
CREATE INDEX IF NOT EXISTS ix_partidos_equipos ON partidos(liga, local, visita);

CREATE TABLE IF NOT EXISTS proximos (
    id TEXT PRIMARY KEY,
    liga TEXT NOT NULL,
    fecha TEXT NOT NULL,
    hora TEXT,
    local TEXT NOT NULL,
    visita TEXT NOT NULL,
    arbitro TEXT,
    c_o25 REAL, c_u25 REAL,
    fuente_cuota TEXT,
    actualizado TEXT
);

CREATE TABLE IF NOT EXISTS cobertura (
    liga TEXT, temporada TEXT,
    partidos INTEGER, con_corners INTEGER, con_tarjetas INTEGER,
    con_faltas INTEGER, con_arbitro INTEGER, con_cuotas INTEGER, con_cierre INTEGER,
    actualizado TEXT,
    PRIMARY KEY (liga, temporada)
);

-- Cada proyección se congela al crearse. Solo se completan los campos de liquidación.
CREATE TABLE IF NOT EXISTS proyecciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partido_id TEXT NOT NULL,
    liga TEXT NOT NULL,
    fecha TEXT NOT NULL,
    hora TEXT,
    local TEXT NOT NULL,
    visita TEXT NOT NULL,
    arbitro TEXT,
    mercado TEXT NOT NULL,             -- goles | tarjetas | corners
    linea REAL NOT NULL,
    lado TEXT NOT NULL,                -- over | under
    media_esperada REAL NOT NULL,      -- total esperado del partido
    prob REAL NOT NULL,
    cuota_justa REAL NOT NULL,
    cuota_minima REAL NOT NULL,
    cuota_mercado REAL,                -- solo si había cuota al generar
    prob_implicita REAL,               -- sin margen de la casa
    valor REAL,                        -- prob - prob_implicita
    tipo TEXT NOT NULL,                -- senal | candidata | proyeccion
    version_modelo TEXT NOT NULL,
    creado_en TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente | acertada | fallada | nula
    valor_real REAL,
    partido_real_id TEXT,
    liquidado_en TEXT,
    UNIQUE (partido_id, mercado, linea, lado, version_modelo)
);
CREATE INDEX IF NOT EXISTS ix_proy_estado ON proyecciones(estado, fecha);
CREATE INDEX IF NOT EXISTS ix_proy_tipo ON proyecciones(tipo, fecha);

CREATE TRIGGER IF NOT EXISTS proyecciones_inmutables
BEFORE UPDATE OF partido_id, liga, fecha, hora, local, visita, arbitro, mercado, linea, lado,
    media_esperada, prob, cuota_justa, cuota_minima, cuota_mercado, prob_implicita, valor,
    tipo, version_modelo, creado_en ON proyecciones
BEGIN
    SELECT RAISE(ABORT, 'las proyecciones no se pueden modificar');
END;

CREATE TRIGGER IF NOT EXISTS proyecciones_sin_borrar
BEFORE DELETE ON proyecciones
BEGIN
    SELECT RAISE(ABORT, 'las proyecciones no se pueden borrar');
END;

CREATE TRIGGER IF NOT EXISTS proyecciones_liquidar_una_vez
BEFORE UPDATE OF estado ON proyecciones
WHEN OLD.estado <> 'pendiente'
BEGIN
    SELECT RAISE(ABORT, 'la proyección ya estaba liquidada');
END;

CREATE TABLE IF NOT EXISTS apuestas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proyeccion_id INTEGER REFERENCES proyecciones(id),
    partido_id TEXT NOT NULL,
    liga TEXT, fecha TEXT, local TEXT, visita TEXT,
    mercado TEXT NOT NULL,
    linea REAL NOT NULL,
    lado TEXT NOT NULL,
    cuota REAL NOT NULL,
    monto REAL NOT NULL,
    casa TEXT,
    nota TEXT,
    creado_en TEXT NOT NULL,
    cuota_cierre REAL,
    clv REAL,
    estado TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente | ganada | perdida | nula
    ganancia REAL,
    liquidado_en TEXT
);

CREATE TRIGGER IF NOT EXISTS apuestas_inmutables
BEFORE UPDATE OF proyeccion_id, partido_id, mercado, linea, lado, cuota, monto, creado_en ON apuestas
BEGIN
    SELECT RAISE(ABORT, 'los datos de una apuesta registrada no se pueden modificar');
END;

CREATE TABLE IF NOT EXISTS backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_modelo TEXT,
    creado_en TEXT,
    parametros TEXT,
    resultado TEXT                     -- JSON
);

CREATE TABLE IF NOT EXISTS parametros (
    nombre TEXT PRIMARY KEY,
    valor TEXT NOT NULL,               -- JSON
    actualizado TEXT
);

CREATE TABLE IF NOT EXISTS historial_parametros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT, valor_anterior TEXT, valor_nuevo TEXT, motivo TEXT, creado_en TEXT
);

CREATE TABLE IF NOT EXISTS ejecuciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea TEXT, inicio TEXT, fin TEXT, ok INTEGER, mensaje TEXT
);
"""


def ahora() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def conectar(ruta=None) -> sqlite3.Connection:
    config.asegurar_directorios()
    con = sqlite3.connect(ruta or config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


MIGRACIONES = [
    ("apuestas", "liquidado_por", "ALTER TABLE apuestas ADD COLUMN liquidado_por TEXT"),  # auto | manual
]


def inicializar(ruta=None) -> None:
    with conectar(ruta) as con:
        con.executescript(ESQUEMA)
        for tabla, columna, sql in MIGRACIONES:
            cols = {r["name"] for r in con.execute(f"PRAGMA table_info({tabla})")}
            if columna not in cols:
                con.execute(sql)


@contextmanager
def conexion(ruta=None):
    con = conectar(ruta)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def parametros_actuales() -> dict:
    return {k: (list(v) if isinstance(v, tuple) else v)
            for k in config.PARAMETROS_AJUSTABLES for v in [getattr(config, k)]}


def aplicar_parametros(con) -> dict:
    """Carga los parámetros ajustados desde el MCP (si los hay) sobre la configuración."""
    try:
        filas = con.execute("SELECT nombre, valor FROM parametros").fetchall()
    except sqlite3.OperationalError:
        filas = []
    for f in filas:
        if f["nombre"] in config.PARAMETROS_AJUSTABLES:
            v = json.loads(f["valor"])
            setattr(config, f["nombre"], tuple(v) if isinstance(v, list) else v)
    return parametros_actuales()


def guardar_parametro(con, nombre: str, valor, motivo: str | None = None) -> dict:
    regla = config.PARAMETROS_AJUSTABLES.get(nombre)
    if regla is None:
        raise ValueError(f"parámetro no ajustable: {nombre}")
    if regla in ("lista", "ligas", "lados"):
        if isinstance(valor, str):
            valor = [x.strip() for x in valor.split(",") if x.strip()]
        validos = {"lista": config.MERCADOS, "ligas": tuple(config.LIGAS), "lados": ("over", "under")}[regla]
        if any(m not in validos for m in valor) or (regla != "ligas" and not valor):
            raise ValueError(f"valores válidos: {', '.join(validos)}")
        valor = list(dict.fromkeys(valor))
    elif regla == "mercado":
        if valor not in config.MERCADOS:
            raise ValueError(f"mercados válidos: {', '.join(config.MERCADOS)}")
    else:
        valor = float(valor)
        lo, hi = regla
        if not lo <= valor <= hi:
            raise ValueError(f"{nombre} debe estar entre {lo} y {hi}")
    aplicar_parametros(con)
    anterior = parametros_actuales()[nombre]
    con.execute("INSERT OR REPLACE INTO parametros (nombre, valor, actualizado) VALUES (?,?,?)",
                (nombre, json.dumps(valor), ahora()))
    con.execute("INSERT INTO historial_parametros (nombre, valor_anterior, valor_nuevo, motivo, creado_en) "
                "VALUES (?,?,?,?,?)", (nombre, json.dumps(anterior), json.dumps(valor), motivo, ahora()))
    aplicar_parametros(con)
    return {"nombre": nombre, "anterior": anterior, "nuevo": valor}
