"""Datos extra de acciones para la pestaña Valuación y tendencia: cierre + volumen y EPS.

Se guardan aparte de los precios ajustados:
  data/tecnico/<ID>.csv   fecha, cierre, volumen   (cierre sin ajustar por dividendos)
  data/eps/<ID>.csv       fecha, eps               (ganancia por acción trimestral reportada)
  data/valuacion.json     P/E actual y a futuro que publica Yahoo, por ID
"""

from __future__ import annotations

import json
from io import StringIO

import pandas as pd

from .fuentes import FuenteNoDisponible

VALUACION = "data/valuacion.json"


def ruta_tecnico(iid: str) -> str:
    return f"data/tecnico/{iid}.csv"


def ruta_eps(iid: str) -> str:
    return f"data/eps/{iid}.csv"


def aplica(inst: dict) -> bool:
    """Acciones y ETFs que vienen de Yahoo (los ETFs tienen volumen pero no P/E)."""
    f = inst.get("fuente") or {}
    return f.get("tipo") == "yahoo" and inst["categoria"] in {"acciones", "benchmark"}


# --- bajar ---------------------------------------------------------------------------------------


def bajar_tecnico(ticker: str, desde: str = "1990-01-01") -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(ticker, start=desde, auto_adjust=False, progress=False, threads=False)
    if df is None or df.empty:
        raise FuenteNoDisponible(f"Yahoo no devolvió cierre/volumen para {ticker}.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    out = pd.DataFrame({"cierre": df["Close"], "volumen": df["Volume"]})
    out.index = pd.to_datetime(out.index).tz_localize(None)
    out.index.name = "fecha"
    return out.dropna(subset=["cierre"])


def bajar_eps(ticker: str) -> tuple[pd.Series, dict]:
    """EPS trimestral reportada (~10 años) y P/E actual/forward de Yahoo."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    eps = pd.Series(dtype=float)
    try:
        ed = t.get_earnings_dates(limit=60)
        if ed is not None and not ed.empty:
            col = next((c for c in ed.columns if "reported" in c.lower()), None)
            if col:
                s = ed[col].dropna()
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                eps = s.astype(float).sort_index()
    except Exception:  # noqa: BLE001 - se intenta con el estado de resultados
        pass
    if eps.size < 4:
        try:
            q = t.quarterly_income_stmt
            fila = next((i for i in q.index if "diluted eps" in str(i).lower()), None)
            if fila is not None:
                s = q.loc[fila].dropna()
                s.index = pd.to_datetime(s.index).tz_localize(None) + pd.Timedelta(days=30)
                eps = s.astype(float).sort_index()
        except Exception:  # noqa: BLE001
            pass
    meta: dict = {}
    try:
        info = t.info or {}
        meta = {k: info.get(k) for k in ("trailingPE", "forwardPE", "shortName")}
    except Exception:  # noqa: BLE001
        pass
    return eps, meta


# --- leer/escribir ------------------------------------------------------------------------------


def _csv(df: pd.DataFrame) -> str:
    df = df.copy()
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    df.index.name = "fecha"
    return df.to_csv(float_format="%.10g")


def leer_tecnico(alm, iid: str, version: str) -> pd.DataFrame | None:
    t = alm.leer_texto(ruta_tecnico(iid), version)
    if not t:
        return None
    return pd.read_csv(StringIO(t), parse_dates=["fecha"]).set_index("fecha").sort_index()


def leer_eps(alm, iid: str, version: str) -> pd.Series | None:
    t = alm.leer_texto(ruta_eps(iid), version)
    if not t:
        return None
    return pd.read_csv(StringIO(t), parse_dates=["fecha"]).set_index("fecha")["eps"].sort_index()


def leer_meta(alm, version: str) -> dict:
    t = alm.leer_texto(VALUACION, version)
    return json.loads(t) if t else {}


def actualizar_extras(alm, version: str, instrumentos: list[dict], archivos: dict, informe: list) -> None:
    """Agrega a `archivos` los CSV de cierre/volumen y EPS. Un error no frena el resto."""
    meta = leer_meta(alm, version)
    tocado = False
    for inst in instrumentos:
        if not aplica(inst):
            continue
        ticker = inst["fuente"]["ticker"]
        try:
            archivos[ruta_tecnico(inst["id"])] = _csv(bajar_tecnico(ticker))
            tocado = True
        except Exception as e:  # noqa: BLE001
            informe.append({"id": f"{inst['id']} (volumen)", "estado": "error", "detalle": str(e)[:200]})
            continue
        if inst["categoria"] != "acciones":
            continue
        try:
            eps, m = bajar_eps(ticker)
            if eps.size >= 4:
                previo = leer_eps(alm, inst["id"], version)
                if previo is not None:  # Yahoo da ~60 trimestres: se conserva lo viejo que ya no trae
                    eps = eps.combine_first(previo).sort_index()
                archivos[ruta_eps(inst["id"])] = _csv(eps.rename("eps").to_frame())
            meta[inst["id"]] = {**m, "actualizado": f"{pd.Timestamp.today():%Y-%m-%d}"}
        except Exception as e:  # noqa: BLE001
            informe.append({"id": f"{inst['id']} (P/E)", "estado": "error", "detalle": str(e)[:200]})
    if tocado:
        archivos[VALUACION] = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
