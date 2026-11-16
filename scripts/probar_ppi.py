"""Prueba rápida de la conexión con PPI, antes de actualizar todo.

    PPI_PUBLIC_KEY=... PPI_PRIVATE_KEY=... python scripts/probar_ppi.py RCCRD
    python scripts/probar_ppi.py GD30D BONOS

Imprime si el login funcionó y los últimos precios que devuelve PPI.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from motor import fuentes  # noqa: E402


def main() -> int:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RCCRD"
    tipo = sys.argv[2] if len(sys.argv) > 2 else "ON"
    try:
        fuentes.ppi_login()
        print("Login PPI: OK")
        desde = f"{pd.Timestamp.today() - pd.Timedelta(days=30):%Y-%m-%d}"
        for settlement in ("A-24HS", "INMEDIATA"):
            try:
                s = fuentes.ppi(ticker, tipo, settlement, desde)
                print(f"{ticker} ({tipo}, {settlement}): {len(s)} precios en 30 días")
                print(s.tail(5).to_string())
            except fuentes.FuenteNoDisponible as e:
                print(f"{ticker} ({tipo}, {settlement}): {e}")
    except fuentes.FuenteNoDisponible as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
