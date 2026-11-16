"""Baja precios diarios de Yahoo Finance y arma un Excel listo para subir en ➕ Agregar.

Uso (en tu PC):
    pip install yfinance openpyxl
    python scripts/bajar_acciones.py                       # 7 magníficas + SPY + QQQ
    python scripts/bajar_acciones.py KO PEP --desde 2018-01-01 --salida consumo.xlsx

Los precios salen *ajustados* por dividendos y splits (yfinance auto_adjust=True), o sea
que ya son de rendimiento total: al subirlos elegí tipo de precio "ajustado".
Para acciones argentinas usá el sufijo .BA (ej. GGAL.BA, YPFD.BA) y el índice ^MERV.
"""

from __future__ import annotations

import argparse

import pandas as pd

SIETE_MAGNIFICAS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]
BENCHMARKS = ["SPY", "QQQ"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*", help="Tickers de Yahoo Finance (por defecto, 7 magníficas + SPY + QQQ)")
    ap.add_argument("--desde", default="2015-01-01", help="Fecha inicial AAAA-MM-DD")
    ap.add_argument("--salida", default="precios_acciones.xlsx", help="Nombre del Excel a generar")
    args = ap.parse_args()

    import yfinance as yf  # se importa acá para que el resto del repo no lo necesite

    tickers = args.tickers or SIETE_MAGNIFICAS + BENCHMARKS
    datos = yf.download(tickers, start=args.desde, auto_adjust=True, progress=False)
    if datos.empty:
        raise SystemExit("Yahoo no devolvió datos. Revisá los tickers o tu conexión.")
    cierre = datos["Close"] if isinstance(datos.columns, pd.MultiIndex) else datos[["Close"]]
    if not isinstance(datos.columns, pd.MultiIndex):
        cierre.columns = tickers[:1]
    cierre = cierre.dropna(how="all").round(6)
    cierre.index = pd.to_datetime(cierre.index).tz_localize(None)
    cierre.index.name = "Fecha"
    faltan = [t for t in tickers if t not in cierre.columns or cierre[t].isna().all()]
    with pd.ExcelWriter(args.salida) as w:
        cierre.reset_index().to_excel(w, sheet_name="Precios", index=False)
    print(f"Listo: {args.salida} ({cierre.shape[1]} tickers, {cierre.index[0]:%d/%m/%Y} a {cierre.index[-1]:%d/%m/%Y})")
    if faltan:
        print("Sin datos para: " + ", ".join(faltan))
    print("Al subirlo: SPY y QQQ con categoría 'benchmark'; el resto 'acciones', moneda USD, precio 'ajustado'.")


if __name__ == "__main__":
    main()
