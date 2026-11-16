"""Indicadores técnicos y P/E histórico (cálculos puros, sin bajar datos)."""

from __future__ import annotations

import numpy as np
import pandas as pd

MEDIA_CORTA, MEDIA_LARGA = 50, 200
RSI_N = 14
MACD_RAPIDA, MACD_LENTA, MACD_SENAL = 12, 26, 9
VOL_N = 20


def rsi(precio: pd.Series, n: int = RSI_N) -> pd.Series:
    """RSI de Wilder (el estándar de TradingView/Investing)."""
    d = precio.diff()
    suba = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    baja = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = suba / baja.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(baja != 0, 100.0).where(suba.notna())


def macd(precio: pd.Series) -> pd.DataFrame:
    rapida = precio.ewm(span=MACD_RAPIDA, adjust=False).mean()
    lenta = precio.ewm(span=MACD_LENTA, adjust=False).mean()
    linea = rapida - lenta
    senal = linea.ewm(span=MACD_SENAL, adjust=False).mean()
    return pd.DataFrame({"macd": linea, "senal": senal, "histograma": linea - senal})


def ultimo_cruce(a: pd.Series, b: pd.Series) -> tuple[str | None, pd.Timestamp | None]:
    """Si a está arriba o abajo de b, y desde qué fecha."""
    dif = (a - b).dropna()
    if dif.empty:
        return None, None
    signo = np.sign(dif)
    estado = "arriba" if signo.iloc[-1] > 0 else "abajo"
    cambios = signo[signo != signo.shift()]
    desde = cambios.index[-1] if len(cambios) > 1 else None
    return estado, desde


def eps_ttm(eps_trimestral: pd.Series) -> pd.Series:
    """Ganancia por acción de los últimos 4 trimestres, vigente desde cada fecha de reporte."""
    e = eps_trimestral.dropna().sort_index()
    e = e[~e.index.duplicated(keep="last")]
    return e.rolling(4).sum().dropna()


def pe_historico(precio: pd.Series, eps_trimestral: pd.Series | None) -> pd.Series:
    """P/E diario = precio / EPS de los últimos 12 meses. Sin dato si la ganancia es <= 0."""
    if eps_trimestral is None or eps_trimestral.dropna().size < 4:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    ttm = eps_ttm(eps_trimestral)
    vigente = ttm.reindex(ttm.index.union(precio.index)).ffill().reindex(precio.index)
    pe = precio / vigente
    return pe.where(vigente > 0).dropna()


def preparar(precio: pd.Series, volumen: pd.Series | None, eps: pd.Series | None) -> pd.DataFrame:
    """Todo lo que necesitan la tabla y los gráficos, en un solo DataFrame diario."""
    p = precio.dropna().sort_index()
    df = pd.DataFrame({"precio": p})
    df["media_corta"] = p.rolling(MEDIA_CORTA).mean()
    df["media_larga"] = p.rolling(MEDIA_LARGA).mean()
    df["rsi"] = rsi(p)
    df = df.join(macd(p))
    if volumen is not None and not volumen.dropna().empty:
        v = volumen.reindex(p.index)
        df["volumen"] = v
        df["volumen_prom"] = v.rolling(VOL_N, min_periods=10).mean()
    df["pe"] = pe_historico(p, eps)
    return df


def resumen(df: pd.DataFrame, anios_pe: int = 5) -> dict:
    """Una fila de la tabla: el estado de hoy de cada indicador."""
    u = df.iloc[-1]
    r: dict = {"fecha": df.index[-1], "precio": u["precio"]}
    for k in ("media_corta", "media_larga"):
        r[f"dist_{k}"] = u["precio"] / u[k] - 1 if pd.notna(u[k]) else None
    r["cruce"], r["cruce_desde"] = ultimo_cruce(df["media_corta"], df["media_larga"])
    r["rsi"] = u["rsi"] if pd.notna(u["rsi"]) else None
    r["macd_estado"], r["macd_desde"] = ultimo_cruce(df["macd"], df["senal"])
    r["macd_hist"] = u["histograma"]
    if "volumen" in df and pd.notna(u.get("volumen_prom")) and u.get("volumen_prom"):
        r["vol_relativo"] = u["volumen"] / u["volumen_prom"] if pd.notna(u["volumen"]) else None
    else:
        r["vol_relativo"] = None
    pe = df["pe"].dropna()
    r["pe"] = r["pe_mediana"] = r["pe_percentil"] = r["pe_min"] = r["pe_max"] = None
    if not pe.empty and pe.index[-1] >= df.index[-1] - pd.Timedelta(days=10):
        ventana = pe[pe.index >= pe.index[-1] - pd.DateOffset(years=anios_pe)]
        r.update(
            pe=float(pe.iloc[-1]),
            pe_mediana=float(ventana.median()),
            pe_percentil=float((ventana < pe.iloc[-1]).mean()),
            pe_min=float(ventana.min()),
            pe_max=float(ventana.max()),
        )
    return r
