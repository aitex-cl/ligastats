"""Configuración central de LigaStats.

Todo lo que dependa del servidor (rutas, URL, clave del MCP) se puede
sobrescribir con variables de entorno; el resto son parámetros del modelo.
"""
import os
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------- rutas
BASE_DIR = Path(os.environ.get("LIGASTATS_HOME", Path(__file__).resolve().parent.parent))
DATA_DIR = Path(os.environ.get("LIGASTATS_DATA", BASE_DIR / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.environ.get("LIGASTATS_DB", DATA_DIR / "ligastats.db"))
LOCK_PATH = DATA_DIR / "actualizar.lock"

# ---------------------------------------------------------------- fuentes
URL_BASE = os.environ.get("LIGASTATS_URL_BASE", "https://www.football-data.co.uk")
URL_TEMPORADA = URL_BASE + "/mmz4281/{temporada}/{liga}.csv"
URL_FIXTURES = URL_BASE + "/fixtures.csv"
USER_AGENT = "LigaStats/1.0 (analisis personal)"

LIGAS = {
    "E0": "Premier League",
    "SP1": "LaLiga",
    "I1": "Serie A",
    "D1": "Bundesliga",
    "F1": "Ligue 1",
    "P1": "Primeira Liga",
    "N1": "Eredivisie",
}
TEMPORADAS_HISTORIA = int(os.environ.get("LIGASTATS_TEMPORADAS", "6"))

# ---------------------------------------------------------------- modelo
VERSION_MODELO = "v1.1"
VIDA_MEDIA_DIAS = 180          # un partido de hace 180 días pesa la mitad
VENTANA_DIAS = 730             # solo se usan los últimos 2 años para ajustar
MIN_PARTIDOS_EQUIPO = 5        # menos que esto en la ventana: no se proyecta
PRIOR_PARTIDOS = 3.0           # encoge equipos con pocos datos hacia el promedio
PRIOR_PARTIDOS_ARBITRO = 6.0   # idem para árbitros
MIN_PARTIDOS_ARBITRO = 5
PESO_ROJA = 2                  # regla de tu casa: roja = 2 amarillas
ITERACIONES = 40

MERCADOS = ("goles", "tarjetas", "corners")
LINEAS = {
    "goles": (1.5, 2.5, 3.5),
    "tarjetas": (2.5, 3.5, 4.5, 5.5, 6.5),
    "corners": (7.5, 8.5, 9.5, 10.5, 11.5),
}

# ---------------------------------------------------------------- señales
# Parámetros marcados con (*) se pueden ajustar desde el MCP sin reinstalar.
MERCADOS_APUESTA = ("tarjetas", "corners")  # (*) mercados que generan señales/candidatas
MERCADO_PRINCIPAL = "tarjetas"              # (*) se muestra primero
MARGEN_VALOR = 0.04        # (*) ventaja mínima (prob. modelo - prob. implícita sin margen)
MARGEN_CUOTA_MINIMA = 0.05 # (*) la cuota mínima exige 5 % sobre la cuota justa
CUOTA_PISO = 1.70          # (*) nunca apostar bajo esta cuota (regla de Eduardo)
PROB_CANDIDATA = 0.55      # (*) sin cuota de mercado: candidata si prob ≥ 55 %
CUOTA_MAX_CANDIDATA = 2.20 # (*) ... y la cuota mínima no supera 2.20
LIGAS_EXCLUIDAS = ("N1", "P1")  # (*) ligas fuera del foco (Holanda: tarjetas peor que el promedio; Portugal: sobreestima "menos")
LADOS_FOCO = ("under",)          # (*) lados que se muestran por defecto en el panel
HORAS_ANTES_GENERAR = 48   # la señal se genera (y se congela) 48 h antes
DIAS_PARA_ANULAR = 14      # señal sin resultado tras 14 días: se anula

PARAMETROS_AJUSTABLES = {
    "MERCADOS_APUESTA": "lista",
    "MERCADO_PRINCIPAL": "mercado",
    "MARGEN_VALOR": (0.0, 0.20),
    "MARGEN_CUOTA_MINIMA": (0.0, 0.20),
    "CUOTA_PISO": (1.20, 3.00),
    "PROB_CANDIDATA": (0.50, 0.85),
    "CUOTA_MAX_CANDIDATA": (1.40, 4.00),
    "LIGAS_EXCLUIDAS": "ligas",
    "LADOS_FOCO": "lados",
}


def cuota_minima(prob: float) -> float:
    """Cuota a partir de la cual conviene apostar: justa + margen, y nunca bajo el piso."""
    justa = float("inf") if prob <= 0 else 1.0 / prob
    return max(justa * (1 + MARGEN_CUOTA_MINIMA), CUOTA_PISO)


def es_candidata(prob: float) -> bool:
    return prob >= PROB_CANDIDATA and cuota_minima(prob) <= CUOTA_MAX_CANDIDATA


ZONA_HORARIA = "America/Santiago"

ZONA_DATOS = "Europe/London"      # football-data publica la hora de inicio en hora del Reino Unido

# ---------------------------------------------------------------- panel web
WEB_HOST = os.environ.get("LIGASTATS_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("LIGASTATS_WEB_PORT", "8011"))
WEB_USUARIO = os.environ.get("LIGASTATS_WEB_USUARIO", "eduardo")
WEB_CLAVE = os.environ.get("LIGASTATS_WEB_CLAVE", "")

# ---------------------------------------------------------------- MCP
MCP_HOST = os.environ.get("LIGASTATS_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("LIGASTATS_MCP_PORT", "8010"))
MCP_TOKEN = os.environ.get("LIGASTATS_MCP_TOKEN", "")
MCP_HOSTS_PERMITIDOS = [h for h in os.environ.get(
    "LIGASTATS_MCP_HOSTS", "stat.aitex.cl,127.0.0.1:8010,localhost:8010").split(",") if h]


def temporada_actual(hoy: date | None = None) -> str:
    """Código football-data de la temporada en curso (p. ej. '2627')."""
    hoy = hoy or date.today()
    inicio = hoy.year if hoy.month >= 7 else hoy.year - 1
    return f"{inicio % 100:02d}{(inicio + 1) % 100:02d}"


def temporadas(n: int = TEMPORADAS_HISTORIA, hoy: date | None = None) -> list[str]:
    actual = temporada_actual(hoy)
    inicio = 2000 + int(actual[:2])
    return [f"{(a) % 100:02d}{(a + 1) % 100:02d}" for a in range(inicio - n + 1, inicio + 1)]


def asegurar_directorios() -> None:
    for d in (DATA_DIR, RAW_DIR):
        d.mkdir(parents=True, exist_ok=True)
