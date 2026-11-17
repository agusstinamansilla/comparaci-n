"""Rendimientos, monedas, períodos y métricas (calculadas con OpenFactsheet)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import openfactsheet as ofs

# Las métricas que muestra el dashboard: (id en OpenFactsheet, etiqueta, formato, explicación)
METRICAS: list[tuple[str, str, str, str]] = [
    ("cagr", "Rendimiento anual (CAGR)", "pct", "Cuánto ganó por año, en promedio compuesto."),
    ("cumulative_return", "Rendimiento acumulado", "pct", "Ganancia total en todo el período."),
    ("worst_calendar_year", "Peor año calendario", "pct", "El peor año completo (enero a diciembre)."),
    ("positive_period_pct", "% de períodos positivos", "pct", "Qué tan seguido gana (días o meses, según la frecuencia)."),
    ("annualized_volatility", "Volatilidad anual", "pct", "Cuánto se mueve el precio. Más alto = más movido."),
    ("max_drawdown", "Caída máxima", "pct", "Lo peor que podías perder comprando en el pico."),
    ("longest_drawdown_duration", "Duración de la peor caída", "dur", "Cuánto tiempo estuvo debajo de un máximo anterior."),
    ("expected_shortfall_historical_95", "CVaR 95% (por período)", "pct", "Pérdida promedio en el peor 5% de los días/meses."),
    ("sharpe_ratio", "Sharpe", "num", "Rendimiento por cada unidad de volatilidad. Más alto = mejor."),
    ("sortino_ratio", "Sortino", "num", "Como el Sharpe pero solo castiga las bajas."),
    ("calmar_ratio", "Calmar", "num", "Rendimiento anual / caída máxima."),
    ("beta", "Beta vs. benchmark", "num", "Si el benchmark se mueve 1%, cuánto se mueve esto."),
    ("correlation", "Correlación con benchmark", "num", "1 = se mueve igual que el benchmark; 0 = nada que ver."),
    ("active_return", "Diferencia vs. benchmark (anual)", "pct", "Cuánto rindió por año por encima (+) o por debajo (−) de su benchmark."),
]

CATEGORIAS = {
    "acciones": "Acciones",
    "fci": "Fondos (FCI)",
    "on": "Obligaciones negociables",
    "bonos": "Bonos del Tesoro",
}
CATEGORIAS_AUX = {"benchmark": "Benchmark", "tipo_cambio": "Tipo de cambio (ARS por USD)"}
TODAS_CATEGORIAS = {**CATEGORIAS, **CATEGORIAS_AUX}

PERIODOS = {"1 año": 1, "3 años": 3, "5 años": 5, "10 años": 10, "Todo": None, "Elegir fechas": "fechas"}


def indice_objetivo(tasa_anual: float, desde: str = "2000-01-03", hasta=None) -> pd.Series:
    """Benchmark de 'rendimiento objetivo': crece a una tasa anual fija, en días hábiles."""
    hasta = pd.Timestamp(hasta) if hasta is not None else pd.Timestamp.today().normalize()
    fechas = pd.bdate_range(desde, hasta)
    anios = (fechas - fechas[0]).days / 365.25
    return pd.Series((1 + tasa_anual) ** anios, index=fechas, name="precio")


# --- construcción de la serie de rendimiento total --------------------------------------


def _asof(fx: pd.Series, fechas: pd.DatetimeIndex) -> pd.Series:
    """Tipo de cambio vigente en cada fecha (el último conocido)."""
    fx = fx.dropna().sort_index()
    return pd.Series(fx.reindex(fx.index.union(fechas)).ffill().reindex(fechas).values, index=fechas)


def indice_total(
    precios: pd.Series,
    pagos: pd.Series | None = None,
    fx: pd.Series | None = None,
    conversion: str = "ninguna",
) -> pd.Series:
    """Índice de rendimiento total (base 1) sumando los pagos y convirtiendo de moneda.

    conversion: "ninguna", "a_usd" (precio en pesos / tipo de cambio) o "a_ars" (precio × tc).
    Un pago de cupón/amortización cuenta como ganancia el día que se cobra:
        r_t = (P_t + pago_t) / P_{t-1} - 1
    """
    p = precios.dropna().sort_index()
    p = p[~p.index.duplicated(keep="last")]
    c = pd.Series(0.0, index=p.index)
    if pagos is not None and not pagos.empty:
        pg = pagos.groupby(level=0).sum().sort_index()
        # un pago en una fecha sin precio se imputa al siguiente día con precio
        pos = p.index.searchsorted(pg.index)
        for i, monto in zip(pos, pg.values):
            if i < len(p):
                c.iloc[i] += float(monto)
    if conversion != "ninguna":
        if fx is None or fx.dropna().empty:
            raise ValueError("Falta cargar el tipo de cambio para convertir monedas.")
        tc = _asof(fx, p.index)
        valido = tc.notna()
        p, c, tc = p[valido], c[valido], tc[valido]
        if conversion == "a_usd":
            p, c = p / tc, c / tc
        else:
            p, c = p * tc, c * tc
    if len(p) < 2:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    r = (p + c) / p.shift(1) - 1
    r.iloc[0] = 0.0
    return (1 + r).cumprod()


def a_frecuencia(indice: pd.Series, frecuencia: str) -> pd.Series:
    if frecuencia == "Mensual":
        return indice.resample("ME").last().dropna()
    return indice


def recortar(
    indices: dict[str, pd.Series], anios, solo_comun: bool, fecha_desde=None, fecha_hasta=None
) -> tuple[dict[str, pd.Series], pd.Timestamp, pd.Timestamp, list[str]]:
    """Corta todas las series a la misma ventana. Devuelve (series, desde, hasta, avisos)."""
    avisos: list[str] = []
    indices = {k: v for k, v in indices.items() if len(v) >= 2}
    if not indices:
        return {}, pd.NaT, pd.NaT, avisos
    hasta = min(v.index[-1] for v in indices.values())
    desde = max(v.index[0] for v in indices.values()) if solo_comun else min(v.index[0] for v in indices.values())
    if fecha_hasta is not None:
        hasta = min(hasta, pd.Timestamp(fecha_hasta))
    if fecha_desde is not None:
        desde = max(desde, pd.Timestamp(fecha_desde))
    if isinstance(anios, (int, float)):
        desde = max(desde, hasta - pd.DateOffset(years=anios))
    out = {}
    for k, v in indices.items():
        s = v[(v.index >= desde) & (v.index <= hasta)]
        if len(s) >= 2:
            if s.index[0] > desde + pd.Timedelta(days=10):
                avisos.append(f"{k}: su historia empieza recién el {s.index[0]:%d/%m/%Y}.")
            out[k] = s / s.iloc[0]
        else:
            avisos.append(f"{k}: no tiene datos en el período elegido.")
    return out, desde, hasta, avisos


# --- métricas -----------------------------------------------------------------------------


@dataclass
class Resultado:
    nombre: str
    valores: dict[str, float | None] = field(default_factory=dict)
    baja_confianza: set[str] = field(default_factory=set)
    periodos_por_anio: float = np.nan
    anios: float = np.nan
    observaciones: int = 0
    suavizado: str = "none"
    avisos: list[str] = field(default_factory=list)
    detalle: dict[str, tuple[str, ...]] = field(default_factory=dict)
    error: str | None = None


def calcular(
    nombre: str, indice: pd.Series, indice_bench: pd.Series | None, tasa_libre_anual: float = 0.0
) -> Resultado:
    res = Resultado(nombre)
    ret = indice.pct_change().dropna()
    bench = None
    if indice_bench is not None and len(indice_bench) > 2:
        bench = indice_bench.pct_change().dropna().rename("Benchmark")
        if bench.index.intersection(ret.index).size < 12:
            res.avisos.append("El benchmark casi no se superpone en fechas; no se usó.")
            bench = None
    try:
        rep = ofs.analyze(ret.rename(nombre), benchmark=bench, return_scale="decimal", name=nombre)
    except Exception as e:  # noqa: BLE001 - se muestra el motivo en pantalla
        res.error = str(e)
        return res
    ppy = float(rep.input_summary.periods_per_year or np.nan)
    res.periodos_por_anio = ppy
    res.observaciones = int(rep.input_summary.observations)
    res.anios = float(rep.diagnostics.years)
    res.suavizado = rep.diagnostics.smoothing_risk

    metricas = {mid: rep.metrics[mid] for mid, *_ in METRICAS}
    if tasa_libre_anual and np.isfinite(ppy):
        rf = (1 + tasa_libre_anual) ** (1 / ppy) - 1
        try:
            rep_ex = ofs.analyze((ret - rf).rename(nombre), return_scale="decimal", name=nombre)
            metricas["sharpe_ratio"] = rep_ex.metrics["sharpe_ratio"]
            metricas["sortino_ratio"] = rep_ex.metrics["sortino_ratio"]
        except Exception:  # noqa: BLE001
            res.avisos.append("No se pudo aplicar la tasa libre de riesgo al Sharpe/Sortino.")

    for mid, m in metricas.items():
        v = m.value
        res.valores[mid] = None if v is None or not np.isfinite(v) else float(v)
        if m.confidence == "low":
            res.baja_confianza.add(mid)
        if m.warnings:
            res.detalle[mid] = tuple(m.warnings)

    if res.anios < 3:
        res.avisos.append(f"Historia corta ({res.anios:.1f} años): las métricas son poco confiables.")
    if res.suavizado in {"high", "moderate"}:
        res.avisos.append(
            "Rendimientos 'suavizados' (cotiza poco o el precio se actualiza poco): la volatilidad "
            "real probablemente es mayor y el Sharpe está inflado. Probá en frecuencia mensual."
        )
    zeros = rep.diagnostics.zero_return_pct
    if zeros and zeros > 0.2:
        res.avisos.append(f"{zeros:.0%} de los períodos tienen rendimiento cero (días sin operar).")
    return res


def correlaciones(indices: dict[str, pd.Series]) -> pd.DataFrame:
    # solo fechas en que cotizan todos (feriados distintos en Argentina y EE.UU.)
    precios = pd.DataFrame(indices).dropna()
    return precios.pct_change().dropna().corr(min_periods=12)


def drawdowns(indice: pd.Series) -> pd.Series:
    return indice / indice.cummax() - 1
