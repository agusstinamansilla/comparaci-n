"""Actualiza todas las series con fuente automática (Yahoo, BCRA, MEP; PPI y fondos cuando estén).

Lo corre GitHub Actions todos los días hábiles (.github/workflows/actualizar.yml), y también
se puede correr a mano:
    python scripts/actualizar.py              # todo
    python scripts/actualizar.py AAPL MEP     # solo algunos

Con GITHUB_TOKEN y GITHUB_REPO definidos guarda en el repo; si no, en la carpeta data/ local.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.actualizador import actualizar  # noqa: E402
from motor.almacen import crear_almacen  # noqa: E402


def main() -> int:
    ids = sys.argv[1:] or None
    alm = crear_almacen()
    print(f"Guardando en: {alm.modo}")
    informe, cambios = actualizar(alm, ids, progreso=lambda f, iid: print(f"  {f:4.0%}  {iid}", flush=True))
    for x in informe:
        print(f"{'OK ' if x['estado'] == 'ok' else 'ERR'} {x['id']:28s} {x['detalle']}")
    errores = [x for x in informe if x["estado"] != "ok"]
    print(f"\n{len(informe) - len(errores)} actualizados, {len(errores)} con error, {'con' if cambios else 'sin'} cambios guardados.")
    # Falla el job solo si no se pudo actualizar nada (así GitHub te avisa por mail)
    return 1 if informe and len(errores) == len(informe) else 0


if __name__ == "__main__":
    raise SystemExit(main())
