"""Modelo de conteo para goles, tarjetas y corners.

Para cada estadística el valor esperado de cada equipo en un partido es

    λ_local  = base_local  · fuerza[local]  · concede[visita] · árbitro
    λ_visita = base_visita · fuerza[visita] · concede[local]  · árbitro

- goles:    fuerza = ataque, concede = debilidad defensiva
- corners:  fuerza = corners que genera, concede = corners que permite
- tarjetas: fuerza = tarjetas que recibe, concede = tarjetas que provoca en el rival
  (el factor árbitro solo se usa en tarjetas y solo si la liga publica el árbitro)

Los partidos pesan menos cuanto más antiguos (vida media configurable) y los
equipos o árbitros con pocos partidos se encogen hacia el promedio.

El total del partido se modela con Poisson; si los datos muestran más
variabilidad que Poisson (habitual en tarjetas y corners), con binomial negativa.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from . import config


def _valores(df: pd.DataFrame, mercado: str):
    if mercado == "goles":
        return df["gl"].to_numpy(float), df["gv"].to_numpy(float)
    if mercado == "corners":
        return df["cl"].to_numpy(float), df["cv"].to_numpy(float)
    if mercado == "tarjetas":
        h = df["al"].to_numpy(float) + config.PESO_ROJA * df["rl"].fillna(0).to_numpy(float)
        a = df["av"].to_numpy(float) + config.PESO_ROJA * df["rv"].fillna(0).to_numpy(float)
        return h, a
    raise ValueError(mercado)


def valor_real(fila, mercado: str) -> float | None:
    """Total real de un partido para un mercado (None si la liga no lo publica)."""
    def v(k):
        x = fila[k]
        return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)
    if mercado == "goles":
        a, b = v("gl"), v("gv")
        return None if a is None or b is None else a + b
    if mercado == "corners":
        a, b = v("cl"), v("cv")
        return None if a is None or b is None else a + b
    if mercado == "tarjetas":
        a, b = v("al"), v("av")
        if a is None or b is None:
            return None
        return a + b + config.PESO_ROJA * ((v("rl") or 0) + (v("rv") or 0))
    raise ValueError(mercado)


@dataclass
class ModeloMercado:
    mercado: str
    base_local: float
    base_visita: float
    fuerza: dict
    concede: dict
    partidos_equipo: dict
    arbitro: dict = field(default_factory=dict)
    dispersion: float = 1.0           # varianza / media del total
    n_partidos: int = 0

    def esperado(self, local: str, visita: str, arbitro: str | None = None):
        if local not in self.fuerza or visita not in self.fuerza:
            return None
        f_arb = self.arbitro.get(arbitro, 1.0) if arbitro else 1.0
        lh = self.base_local * self.fuerza[local] * self.concede[visita] * f_arb
        la = self.base_visita * self.fuerza[visita] * self.concede[local] * f_arb
        return lh, la

    def prob_over(self, media_total: float, linea: float) -> float:
        k = int(np.floor(linea))  # over 2.5 = P(X >= 3) = 1 - P(X <= 2)
        if self.dispersion > 1.05:
            r = media_total / (self.dispersion - 1.0)
            p = r / (r + media_total)
            return float(stats.nbinom.sf(k, r, p))
        return float(stats.poisson.sf(k, media_total))


def _ajustar(mercado, df, pesos):
    h, a = _valores(df, mercado)
    ok = ~(np.isnan(h) | np.isnan(a))
    if ok.sum() < 30:
        return None
    df, h, a, w = df[ok], h[ok], a[ok], pesos[ok]

    equipos = pd.Index(sorted(set(df["local"]) | set(df["visita"])))
    il = equipos.get_indexer(df["local"])
    iv = equipos.get_indexer(df["visita"])
    n = len(equipos)

    base_l = np.sum(w * h) / np.sum(w)
    base_v = np.sum(w * a) / np.sum(w)
    fuerza = np.ones(n)
    concede = np.ones(n)
    arb_idx = None
    f_arb = np.ones(len(df))
    usar_arbitro = mercado == "tarjetas" and df["arbitro"].notna().mean() > 0.8
    if usar_arbitro:
        arbitros = pd.Index(sorted(df["arbitro"].dropna().unique()))
        arb_idx = arbitros.get_indexer(df["arbitro"].fillna("__sin__"))
        arb = np.ones(len(arbitros))

    k = config.PRIOR_PARTIDOS
    for _ in range(config.ITERACIONES):
        # fuerza (lo que hace / recibe el propio equipo)
        exp_h = base_l * concede[iv] * f_arb
        exp_a = base_v * concede[il] * f_arb
        num = np.bincount(il, w * h, n) + np.bincount(iv, w * a, n)
        den = np.bincount(il, w * exp_h, n) + np.bincount(iv, w * exp_a, n)
        prior = k * (base_l + base_v) / 2
        fuerza = (num + prior) / (den + prior)
        fuerza /= np.average(fuerza)
        # concede (lo que provoca / permite al rival)
        exp_h = base_l * fuerza[il] * f_arb
        exp_a = base_v * fuerza[iv] * f_arb
        num = np.bincount(iv, w * h, n) + np.bincount(il, w * a, n)
        den = np.bincount(iv, w * exp_h, n) + np.bincount(il, w * exp_a, n)
        concede = (num + prior) / (den + prior)
        concede /= np.average(concede)
        # árbitro
        if usar_arbitro:
            sin_arb = arb_idx < 0
            exp_tot = base_l * fuerza[il] * concede[iv] + base_v * fuerza[iv] * concede[il]
            idx = np.where(sin_arb, 0, arb_idx)
            m = len(arb)
            num = np.bincount(idx, np.where(sin_arb, 0, w * (h + a)), m)
            den = np.bincount(idx, np.where(sin_arb, 0, w * exp_tot), m)
            kp = config.PRIOR_PARTIDOS_ARBITRO * (base_l + base_v)
            arb = (num + kp) / (den + kp)
            f_arb = np.where(sin_arb, 1.0, arb[idx])

    lam = base_l * fuerza[il] * concede[iv] * f_arb + base_v * fuerza[iv] * concede[il] * f_arb
    tot = h + a
    dispersion = float(np.sum(w * (tot - lam) ** 2 / lam) / np.sum(w))
    if mercado == "goles":
        dispersion = 1.0  # los goles se ajustan bien a Poisson

    partidos = (pd.Series(1, index=df["local"]).groupby(level=0).sum()
                .add(pd.Series(1, index=df["visita"]).groupby(level=0).sum(), fill_value=0))
    dic_arb = {}
    if usar_arbitro:
        conteo = df["arbitro"].value_counts()
        dic_arb = {r: float(arb[i]) for i, r in enumerate(arbitros)
                   if conteo.get(r, 0) >= config.MIN_PARTIDOS_ARBITRO}
    return ModeloMercado(
        mercado=mercado, base_local=float(base_l), base_visita=float(base_v),
        fuerza=dict(zip(equipos, fuerza.astype(float))),
        concede=dict(zip(equipos, concede.astype(float))),
        partidos_equipo={e: int(partidos.get(e, 0)) for e in equipos},
        arbitro=dic_arb, dispersion=max(dispersion, 1.0), n_partidos=int(len(df)))


@dataclass
class ModeloLiga:
    liga: str
    fecha_corte: str
    mercados: dict

    def proyectar(self, local: str, visita: str, arbitro: str | None = None) -> dict:
        """{mercado: {'media', 'local', 'visita', 'lineas': {linea: prob_over}}} o motivo de omisión."""
        salida = {}
        for mercado, m in self.mercados.items():
            if m is None:
                salida[mercado] = {"omitido": "la liga no publica este dato"}
                continue
            for eq in (local, visita):
                if m.partidos_equipo.get(eq, 0) < config.MIN_PARTIDOS_EQUIPO:
                    salida[mercado] = {"omitido": f"{eq} tiene muy pocos partidos recientes en la liga"}
                    break
            else:
                lh, la = m.esperado(local, visita, arbitro)
                media = lh + la
                salida[mercado] = {
                    "media": media, "local": lh, "visita": la,
                    "arbitro_usado": bool(arbitro and arbitro in m.arbitro),
                    "dispersion": m.dispersion,
                    "lineas": {l: m.prob_over(media, l) for l in config.LINEAS[mercado]},
                }
        return salida


def ajustar_liga(partidos: pd.DataFrame, liga: str, fecha_corte: str) -> ModeloLiga:
    """Ajusta los tres mercados con partidos ANTERIORES a fecha_corte (sin mirar el futuro)."""
    corte = pd.Timestamp(fecha_corte)
    df = partidos[(partidos["liga"] == liga)].copy()
    f = pd.to_datetime(df["fecha"])
    df = df[(f < corte) & (f >= corte - pd.Timedelta(days=config.VENTANA_DIAS))]
    dias = (corte - pd.to_datetime(df["fecha"])).dt.days.to_numpy(float)
    pesos = 0.5 ** (dias / config.VIDA_MEDIA_DIAS)
    return ModeloLiga(liga=liga, fecha_corte=fecha_corte,
                      mercados={m: _ajustar(m, df, pesos) for m in config.MERCADOS})


def cuota_justa(p: float) -> float:
    return float("inf") if p <= 0 else 1.0 / p


def prob_sin_margen(cuota_a: float, cuota_b: float) -> float:
    """Probabilidad implícita del lado A quitando el margen de la casa."""
    ia, ib = 1 / cuota_a, 1 / cuota_b
    return ia / (ia + ib)
