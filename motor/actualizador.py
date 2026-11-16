"""Actualiza los instrumentos que tienen ``fuente`` y guarda todo en un solo commit.

Se usa desde el botón del dashboard y desde scripts/actualizar.py (GitHub Actions, todos los días).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from . import fuentes
from .almacen import INDICE, indice_a_texto, serie_a_csv
from .catalogo import cargar_calendario, cargar_indice, cargar_precios, ruta_precios
from .datos import combinar, limpiar_id

# Estas fuentes devuelven la historia completa: se reemplaza la serie entera (en Yahoo los precios
# ajustados viejos cambian cada vez que hay un dividendo, así que no conviene pegar pedazos).
REEMPLAZA = {"yahoo", "bcra", "mep"}
RECALCULAR_DIAS = 15  # ventana que se recalcula en cada actualización de PPI


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


SALTO_MAXIMO = 0.25  # variación diaria máxima aceptada al sumar datos nuevos (fondos)


def _controlar_salto(vieja: pd.Series, nueva: pd.Series) -> None:
    """Evita guardar un dato con otra escala o mal informado (ej. VCP por 1 en vez de por 1000)."""
    if vieja.empty or nueva.empty:
        return
    previos = vieja[vieja.index < nueva.index[0]]
    if previos.empty:
        return
    cambio = float(nueva.iloc[0]) / float(previos.iloc[-1]) - 1
    if abs(cambio) > SALTO_MAXIMO:
        raise fuentes.FuenteNoDisponible(
            f"salto sospechoso de {cambio:+.0%} contra el último dato guardado "
            f"({previos.iloc[-1]:.6g} el {previos.index[-1]:%d/%m/%Y} → {nueva.iloc[0]:.6g}); no se guardó."
        )


def fechas_pago(calendario: dict, ticker: str) -> list:
    """Fechas de cupón/amortización de una ON o bono (clave = 4 primeros caracteres del ticker)."""
    info = calendario.get(ticker[:4]) or {}
    fechas = list(info.get("cupones") or [])
    if info.get("vencimiento"):
        fechas.append(info["vencimiento"])
    return sorted(set(fechas))


def _ppi_ajustado(inst: dict, vieja: pd.Series, calendario: dict) -> pd.Series:
    """Baja de PPI y extiende el índice ajustado por pagos (como el Excel de series de ON).

    Si ya hay serie, baja desde unos días antes del último dato y encadena los rendimientos
    nuevos sobre el último valor del índice. Si no hay, arma el índice desde cero (base 100).
    """
    f = inst["fuente"]
    desde = f"{vieja.index[-1] - pd.Timedelta(days=RECALCULAR_DIAS + 20):%Y-%m-%d}" if not vieja.empty else f.get("desde", "2015-01-01")
    crudo = fuentes.ppi(f["ticker"], f.get("tipo_ppi", "ON"), f.get("settlement", "A-24HS"), desde).dropna()
    crudo = crudo[crudo > 0]
    pagos = fechas_pago(calendario, inst["id"])
    rend, _ = fuentes.excluir_pagos(crudo, pagos)
    if vieja.empty:
        indice = 100 * (1 + rend).cumprod()
        return pd.concat([pd.Series([100.0], index=[crudo.index[0]]), indice]).rename("precio")
    # Se recalculan las últimas semanas desde un "ancla" para que un pago que todavía no había
    # ocurrido en la corrida anterior se ajuste bien cuando ya pasó.
    ancla_max = vieja.index[-1] - pd.Timedelta(days=RECALCULAR_DIAS)
    comunes = vieja.index[(vieja.index <= vieja.index[-1]) & vieja.index.isin(crudo.index)]
    anclas = comunes[comunes <= ancla_max]
    ancla = anclas[-1] if len(anclas) else (comunes[0] if len(comunes) else None)
    if ancla is None:
        raise fuentes.FuenteNoDisponible(
            f"PPI no trae precios que se superpongan con la serie guardada (último dato {vieja.index[-1]:%d/%m/%Y})."
        )
    nuevos = rend[rend.index > ancla]
    if nuevos.empty:
        return vieja
    extension = float(vieja.loc[ancla]) * (1 + nuevos).cumprod()
    return pd.concat([vieja[vieja.index <= ancla], extension]).rename("precio")


def actualizar(alm, ids: list[str] | None = None, progreso=None) -> tuple[list[dict], bool]:
    """Baja datos nuevos. Devuelve (informe, hubo_cambios). Un error en un instrumento no frena al resto."""
    version = alm.version()
    indice = cargar_indice(alm, version)
    candidatos = [i for i in indice if i.get("fuente") and (ids is None or i["id"] in ids)]
    calendario = cargar_calendario(alm, version)
    archivos: dict[str, str] = {}
    informe: list[dict] = []
    for n, inst in enumerate(candidatos, 1):
        if progreso:
            progreso(n / max(len(candidatos), 1), inst["id"])
        fuente = inst["fuente"]
        try:
            vieja = cargar_precios(alm, inst, version) if inst.get("archivo") else pd.Series(dtype=float)
            if fuente["tipo"] == "ppi":
                serie = _ppi_ajustado(inst, vieja, calendario)
            else:
                nueva = fuentes.bajar(fuente).dropna()
                nueva = nueva[nueva > 0]
                if len(nueva) < 2:
                    raise fuentes.FuenteNoDisponible("la fuente devolvió menos de 2 datos")
                if fuente["tipo"] not in REEMPLAZA:
                    _controlar_salto(vieja, nueva)
                serie = nueva if fuente["tipo"] in REEMPLAZA else combinar(vieja, nueva)
            ultima_vieja = vieja.index.max() if not vieja.empty else None
            inst["archivo"] = ruta_precios(inst["id"])
            archivos[inst["archivo"]] = serie_a_csv(serie, "precio")
            inst.update(
                desde=f"{serie.index[0]:%Y-%m-%d}", hasta=f"{serie.index[-1]:%Y-%m-%d}", actualizado=_ahora()
            )
            nuevos = int((serie.index > ultima_vieja).sum()) if ultima_vieja is not None else len(serie)
            informe.append({"id": inst["id"], "estado": "ok", "detalle": f"hasta {serie.index[-1]:%d/%m/%Y} (+{nuevos} días)"})
        except Exception as e:  # noqa: BLE001 - se informa y se sigue con el resto
            informe.append({"id": inst["id"], "estado": "error", "detalle": str(e)[:200]})
    if archivos:
        archivos[INDICE] = indice_a_texto(indice)
        alm.guardar(archivos, [], f"Datos: actualización automática ({len(archivos) - 1} series)")
    return informe, bool(archivos)


def alta_desde_fuente(
    alm, iid: str, nombre: str, categoria: str, moneda: str, benchmark: str | None, precio: str, fuente: dict
) -> str:
    """Crea (o pisa) un instrumento bajando su historia de la fuente indicada."""
    version = alm.version()
    indice = cargar_indice(alm, version)
    iid = limpiar_id(iid)
    serie = fuentes.bajar(fuente).dropna()
    serie = serie[serie > 0]
    if len(serie) < 2:
        raise fuentes.FuenteNoDisponible(f"{iid}: la fuente devolvió menos de 2 datos")
    por_id = {i["id"]: i for i in indice}
    inst = por_id.get(iid, {"id": iid, "creado": _ahora(), "pagos": None})
    inst.update(
        nombre=nombre or iid, categoria=categoria, moneda=moneda, benchmark=benchmark or None, precio=precio,
        fuente=fuente, archivo=ruta_precios(iid), desde=f"{serie.index[0]:%Y-%m-%d}",
        hasta=f"{serie.index[-1]:%Y-%m-%d}", actualizado=_ahora(),
    )
    por_id[iid] = inst
    nuevo = sorted(por_id.values(), key=lambda i: (i["categoria"], i["id"]))
    alm.guardar(
        {ruta_precios(iid): serie_a_csv(serie, "precio"), INDICE: indice_a_texto(nuevo)}, [], f"Datos: alta de {iid}"
    )
    return f"{iid}: {len(serie)} datos, del {serie.index[0]:%d/%m/%Y} al {serie.index[-1]:%d/%m/%Y}."
