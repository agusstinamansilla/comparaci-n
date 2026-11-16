"""Conectores para bajar precios automáticamente.

Cada instrumento del catálogo puede tener un campo ``fuente`` que dice de dónde se actualiza:

    {"tipo": "yahoo", "ticker": "AAPL"}                       acciones, ETFs, CEDEARs (AAPL.BA)
    {"tipo": "bcra", "variable": "badlar", "como": "tasa"}    BADLAR (tasa -> índice acumulado)
    {"tipo": "bcra", "variable": "cer", "como": "indice"}     CER
    {"tipo": "mep"}                                           dólar MEP (ArgentinaDatos)
    {"tipo": "ppi", "ticker": "YM34D", "tipo_ppi": "ON"}      ON y bonos (precio ajustado por pagos)
    {"tipo": "fci", "nombre": "Galileo Income - Clase A"}     fondos (planilla diaria de CAFCI)

Todas las funciones devuelven una serie de pandas: índice = fecha, valor = precio o índice.
"""

from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd
import requests

TIMEOUT = 60
BCRA = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
ARGENTINADATOS = "https://api.argentinadatos.com/v1/cotizaciones/dolares"

# Cómo reconocer cada variable en el listado del BCRA (por descripción, así no dependemos de IDs)
VARIABLES_BCRA = {
    "badlar": [r"badlar", r"privad", r"n\.\s*a"],
    "cer": [r"\bcer\b", r"base"],
    "tamar": [r"tamar", r"privad", r"n\.\s*a"],
    "uva": [r"\buva\b"],
}


class FuenteNoDisponible(RuntimeError):
    pass


def _get(url: str, **kw) -> requests.Response:
    verificar = os.environ.get("BCRA_VERIFY_SSL", "1") != "0" if "bcra" in url else True
    r = requests.get(url, timeout=TIMEOUT, verify=verificar, headers={"Accept": "application/json"}, **kw)
    if r.status_code >= 400:
        raise FuenteNoDisponible(f"{url} respondió {r.status_code}: {r.text[:200]}")
    return r


# --- Yahoo Finance ----------------------------------------------------------------------------


def yahoo(ticker: str, desde: str = "1990-01-01") -> pd.Series:
    """Precio ajustado por dividendos y splits. CEDEARs: sufijo .BA (en pesos)."""
    import yfinance as yf

    df = yf.download(ticker, start=desde, auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        raise FuenteNoDisponible(f"Yahoo no devolvió datos para {ticker}.")
    cierre = df["Close"]
    if isinstance(cierre, pd.DataFrame):  # yfinance nuevo devuelve columnas por ticker
        cierre = cierre.iloc[:, 0]
    cierre.index = pd.to_datetime(cierre.index).tz_localize(None)
    return cierre.dropna().astype(float).rename("precio")


# --- BCRA -------------------------------------------------------------------------------------


def _buscar_variable_bcra(clave: str) -> int:
    patrones = VARIABLES_BCRA[clave]
    datos = _get(BCRA, params={"limit": 3000}).json()
    for v in datos.get("results", []):
        desc = str(v.get("descripcion") or v.get("Descripcion") or "").lower()
        if all(re.search(p, desc) for p in patrones):
            return int(v.get("idVariable") or v.get("IdVariable"))
    raise FuenteNoDisponible(f"No encontré la variable '{clave}' en el listado del BCRA.")


def bcra_serie(id_variable: int, desde: str = "2000-01-01") -> pd.Series:
    filas, offset = [], 0
    while True:
        datos = _get(f"{BCRA}/{id_variable}", params={"desde": desde, "limit": 3000, "offset": offset}).json()
        res = datos.get("results", [])
        detalle = res[0].get("detalle", []) if res and isinstance(res[0], dict) and "detalle" in res[0] else res
        filas += detalle
        total = datos.get("metadata", {}).get("resultset", {}).get("count", len(filas))
        offset += 3000
        if not detalle or offset >= total:
            break
    if not filas:
        raise FuenteNoDisponible(f"El BCRA no devolvió datos para la variable {id_variable}.")
    s = pd.Series({pd.Timestamp(f["fecha"]): float(f["valor"]) for f in filas}).sort_index()
    return s[~s.index.duplicated(keep="last")]


def tasa_a_indice(tasa_na: pd.Series) -> pd.Series:
    """Convierte una tasa nominal anual (%) en un índice que capitaliza día a día (base 100).

    Cada día corrido rinde tasa/365 de la tasa vigente el día anterior: es lo que ganaría un
    plazo fijo o una cuenta remunerada a esa tasa, y sirve para comparar contra un fondo.
    """
    t = tasa_na.dropna().sort_index()
    dias = t.index.to_series().diff().dt.days.fillna(0).to_numpy()
    factor = 1 + t.shift(1).fillna(0).to_numpy() / 100 * dias / 365
    return pd.Series(100 * np.cumprod(factor), index=t.index, name="precio")


def bcra(variable: str, como: str = "indice", desde: str = "2000-01-01") -> pd.Series:
    s = bcra_serie(_buscar_variable_bcra(variable), desde)
    return tasa_a_indice(s) if como == "tasa" else s.rename("precio")


# --- Dólar MEP --------------------------------------------------------------------------------


def mep() -> pd.Series:
    """Dólar MEP (bolsa), punta vendedora, histórico diario desde ArgentinaDatos."""
    try:
        filas = _get(f"{ARGENTINADATOS}/bolsa").json()
    except FuenteNoDisponible:
        filas = [f for f in _get(ARGENTINADATOS).json() if str(f.get("casa", "")).lower() == "bolsa"]
    if not filas:
        raise FuenteNoDisponible("ArgentinaDatos no devolvió cotizaciones del MEP.")
    s = pd.Series(
        {pd.Timestamp(f["fecha"]): float(f.get("venta") or f.get("compra")) for f in filas if f.get("venta") or f.get("compra")}
    ).sort_index()
    return s[~s.index.duplicated(keep="last")].rename("precio")


# --- PPI (Portfolio Personal Inversiones) -----------------------------------------------------
# Valores de la librería oficial ppi-client (ppi_client/ppi_api_client.py). Se pueden pisar con
# variables de entorno si PPI los cambia.
PPI_BASE = os.environ.get("PPI_BASE_URL", "https://clientapi.portfoliopersonal.com/api/1.0")
PPI_AUTH_CLIENT = os.environ.get("PPI_AUTHORIZED_CLIENT", "API_CLI_PYTHON")
PPI_CLIENT_KEY = os.environ.get("PPI_CLIENT_KEY", "pp19PythonApp12")
_ppi_token: str | None = None


def _ppi_headers() -> dict:
    return {"AuthorizedClient": PPI_AUTH_CLIENT, "ClientKey": PPI_CLIENT_KEY, "Content-Type": "application/json"}


def ppi_login(forzar: bool = False) -> str:
    global _ppi_token
    if _ppi_token and not forzar:
        return _ppi_token
    clave, secreto = os.environ.get("PPI_PUBLIC_KEY"), os.environ.get("PPI_PRIVATE_KEY")
    if not clave or not secreto:
        raise FuenteNoDisponible("Faltan PPI_PUBLIC_KEY y PPI_PRIVATE_KEY (variables de entorno / secrets).")
    r = requests.post(
        f"{PPI_BASE}/Account/LoginApi", headers={**_ppi_headers(), "ApiKey": clave, "ApiSecret": secreto}, timeout=TIMEOUT
    )
    if r.status_code >= 400:
        raise FuenteNoDisponible(f"Login PPI falló ({r.status_code}): {r.text[:200]}")
    _ppi_token = r.json().get("accessToken")
    if not _ppi_token:
        raise FuenteNoDisponible("PPI no devolvió accessToken.")
    return _ppi_token


def ppi(ticker: str, tipo: str = "ON", settlement: str = "A-24HS", desde: str = "2015-01-01", hasta: str | None = None, **_kw) -> pd.Series:
    """Precio de cierre diario histórico (sin ajustar) de PPI MarketData/Search."""
    hasta = hasta or f"{pd.Timestamp.today() + pd.Timedelta(days=1):%Y-%m-%d}"
    params = {"ticker": ticker, "type": tipo, "dateFrom": desde, "dateTo": hasta, "settlement": settlement}
    for intento in range(2):
        r = requests.get(
            f"{PPI_BASE}/MarketData/Search",
            params=params,
            headers={**_ppi_headers(), "Authorization": f"Bearer {ppi_login(forzar=intento > 0)}"},
            timeout=TIMEOUT,
        )
        if r.status_code != 401:
            break
    if r.status_code >= 400:
        raise FuenteNoDisponible(f"PPI {ticker} ({tipo}, {settlement}) respondió {r.status_code}: {r.text[:200]}")
    filas = r.json() or []
    s = pd.Series(
        {pd.Timestamp(str(f["date"])[:10]): float(f["price"]) for f in filas if f.get("price") not in (None, 0)}
    ).sort_index()
    if s.empty:
        raise FuenteNoDisponible(f"PPI no devolvió precios para {ticker} ({tipo}, {settlement}).")
    return s[~s.index.duplicated(keep="last")].rename("precio")


def excluir_pagos(precios: pd.Series, fechas_pago: list, habiles_antes: int = 3, habiles_despues: int = 1) -> tuple[pd.Series, list]:
    """Rendimientos diarios sin el salto por pago de cupón/amortización.

    Igual que el Excel de series de ON: entre los últimos 3 días hábiles hasta la fecha de pago
    (y el siguiente) se busca la caída más grande, que es el día en que el precio deja de incluir
    el cupón, y su variación se toma como 0. Devuelve (rendimientos, fechas excluidas).
    """
    r = precios.pct_change().dropna()
    excluidas = []
    for pago in pd.to_datetime(fechas_pago):
        if r.empty or r.index[-1] < pago:  # el pago todavía no llegó: se decide en la próxima corrida
            continue
        antes = r[(r.index <= pago) & (r.index >= pago - pd.Timedelta(days=7))].iloc[-habiles_antes:]
        despues = r[r.index > pago].iloc[:habiles_despues]
        ventana = pd.concat([antes, despues])
        if not ventana.empty and ventana.min() < 0:
            dia = ventana.idxmin()
            r.loc[dia] = 0.0
            excluidas.append(dia)
    return r, excluidas


# --- Fondos (planilla diaria pública de CAFCI) ------------------------------------------------
# Misma lógica que actualizar_historico_cafci.py del dashboard de fondos.
CAFCI_URL = os.environ.get("CAFCI_PLANILLA_URL", "https://api.pub.cafci.org.ar/pb_get")
CAFCI_COL_NOMBRE, CAFCI_COL_FECHA, CAFCI_COL_VCP, CAFCI_COL_VCP_ANT = 0, 4, 5, 6
CAFCI_FILA_FECHA_ANT = 8  # fila (0-indexada) con la fecha de la columna de comparación
CAFCI_DIVISOR = 1000  # la planilla informa el valor de 1000 cuotapartes
_cafci_planilla: pd.DataFrame | None = None


def _normalizar(texto) -> str:
    import unicodedata

    if not isinstance(texto, str):
        return ""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", texto.lower().strip())


def cafci_planilla(forzar: bool = False) -> pd.DataFrame:
    """Baja la planilla una sola vez por corrida (trae todos los fondos del mercado)."""
    global _cafci_planilla
    if _cafci_planilla is None or forzar:
        from io import BytesIO

        r = requests.get(CAFCI_URL, timeout=TIMEOUT)
        if r.status_code >= 400:
            raise FuenteNoDisponible(f"CAFCI respondió {r.status_code}.")
        _cafci_planilla = pd.read_excel(BytesIO(r.content), header=None)
    return _cafci_planilla


def _fecha(valor):
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return pd.NaT
    if isinstance(valor, str):
        v = valor.strip()
        for fmt in ("%d/%m/%y", "%d/%m/%Y"):
            f = pd.to_datetime(v, format=fmt, errors="coerce")
            if pd.notna(f):
                return f
        return pd.to_datetime(v, dayfirst=True, errors="coerce")
    return pd.to_datetime(valor, errors="coerce")


def fci(nombre: str, alias: list[str] | None = None, **_kw) -> pd.Series:
    """VCP del último día hábil y del anterior (este puede venir corregido por la administradora).

    Devuelve hasta 2 datos; el actualizador los suma a la serie guardada y, si una fecha ya
    existía, manda el valor nuevo (así se aplican las correcciones retroactivas).
    """
    df = cafci_planilla()
    buscados = {_normalizar(nombre), *(_normalizar(a) for a in (alias or []))}
    filas = df[df[CAFCI_COL_NOMBRE].map(_normalizar).isin(buscados)]
    if filas.empty:
        raise FuenteNoDisponible(
            f"'{nombre}' no aparece en la planilla de CAFCI de hoy (¿no operó o cambió de nombre?)."
        )
    fila = filas.iloc[0]
    datos = {}
    f_act, v_act = _fecha(fila[CAFCI_COL_FECHA]), fila[CAFCI_COL_VCP]
    if pd.notna(f_act) and pd.notna(v_act):
        datos[f_act.normalize()] = float(v_act) / CAFCI_DIVISOR
    f_ant, v_ant = _fecha(df.iloc[CAFCI_FILA_FECHA_ANT, CAFCI_COL_VCP_ANT]), fila[CAFCI_COL_VCP_ANT]
    if pd.notna(f_ant) and pd.notna(v_ant):
        datos.setdefault(f_ant.normalize(), float(v_ant) / CAFCI_DIVISOR)
    if not datos:
        raise FuenteNoDisponible(f"'{nombre}' está en la planilla pero sin valor de cuotaparte.")
    return pd.Series(datos, name="precio").sort_index()


def bajar(fuente: dict) -> pd.Series:
    tipo = fuente.get("tipo")
    if tipo == "yahoo":
        return yahoo(fuente["ticker"], fuente.get("desde", "1990-01-01"))
    if tipo == "bcra":
        return bcra(fuente["variable"], fuente.get("como", "indice"), fuente.get("desde", "2000-01-01"))
    if tipo == "mep":
        return mep()
    if tipo == "ppi":
        return ppi(
            fuente["ticker"], fuente.get("tipo_ppi", "ON"), fuente.get("settlement", "A-24HS"),
            fuente.get("desde", "2015-01-01"),
        )
    if tipo == "fci":
        return fci(fuente["nombre"], fuente.get("alias"))
    raise FuenteNoDisponible(f"Fuente desconocida: {tipo}")
