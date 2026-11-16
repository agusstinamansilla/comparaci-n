"""Catálogo de instrumentos: data/instrumentos.json + un CSV de precios (y de pagos) por instrumento."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from .almacen import INDICE, csv_a_serie, indice_a_texto, leer_indice, serie_a_csv
from .datos import combinar, limpiar_id


CALENDARIO_ON = "data/on_calendario.json"


def ruta_precios(iid: str) -> str:
    return f"data/precios/{iid}.csv"


def ruta_pagos(iid: str) -> str:
    return f"data/pagos/{iid}.csv"


def cargar_indice(alm, version: str) -> list[dict]:
    return leer_indice(alm.leer_texto(INDICE, version))


def cargar_precios(alm, inst: dict, version: str) -> pd.Series:
    if inst.get("tasa_anual") is not None:  # benchmark de rendimiento objetivo: no tiene archivo
        from .calculo import indice_objetivo

        return indice_objetivo(float(inst["tasa_anual"]))
    if not inst.get("archivo"):  # todavía no se bajó por primera vez
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return csv_a_serie(alm.leer_texto(inst["archivo"], version))


def disponible(inst: dict) -> bool:
    """Tiene datos para mostrar (los que esperan su primera descarga quedan ocultos)."""
    return inst.get("tasa_anual") is not None or bool(inst.get("archivo"))


def cargar_calendario(alm, version: str) -> dict:
    texto = alm.leer_texto(CALENDARIO_ON, version)
    return json.loads(texto) if texto else {}


def calendario_a_texto(cal: dict) -> str:
    return json.dumps(cal, ensure_ascii=False, indent=2) + "\n"


def preparar_objetivo(indice: list[dict], iid: str, nombre: str, tasa_anual: float, moneda: str):
    """Agrega un benchmark de rendimiento objetivo (ej. 'Meta 4,5% anual en USD')."""
    por_id = {i["id"]: dict(i) for i in indice}
    iid = limpiar_id(iid)
    por_id[iid] = {
        "id": iid,
        "nombre": nombre.strip() or iid,
        "categoria": "benchmark",
        "moneda": moneda,
        "benchmark": None,
        "precio": "objetivo",
        "tasa_anual": float(tasa_anual),
        "archivo": None,
        "pagos": None,
        "actualizado": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    nuevo = sorted(por_id.values(), key=lambda i: (i["categoria"], i["id"]))
    return {INDICE: indice_a_texto(nuevo)}, nuevo


def cargar_pagos(alm, inst: dict, version: str) -> pd.Series | None:
    if not inst.get("pagos"):
        return None
    s = csv_a_serie(alm.leer_texto(inst["pagos"], version))
    return s if not s.empty else None


def preparar_alta(
    alm,
    version: str,
    indice: list[dict],
    filas: list[dict],
    precios: pd.DataFrame,
    pagos: pd.DataFrame,
) -> tuple[dict[str, str], list[dict], list[str]]:
    """Arma los archivos a guardar para los instrumentos nuevos o actualizados.

    filas: una por columna del archivo subido, con columna, id, nombre, categoria, moneda,
    benchmark y precio. Si el id ya existe, se combinan los precios (mandan los nuevos)
    y se actualizan los datos descriptivos.
    """
    por_id = {i["id"]: dict(i) for i in indice}
    archivos: dict[str, str] = {}
    resumen: list[str] = []
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for f in filas:
        iid = limpiar_id(f["id"] or f["columna"])
        nueva = precios[f["columna"]].dropna()
        previo = por_id.get(iid)
        vieja = cargar_precios(alm, previo, version) if previo else pd.Series(dtype=float)
        serie = combinar(vieja, nueva)
        archivos[ruta_precios(iid)] = serie_a_csv(serie, "precio")

        inst = previo or {"id": iid, "creado": ahora}
        inst.update(
            {
                "nombre": (f.get("nombre") or iid).strip(),
                "categoria": f["categoria"],
                "moneda": f["moneda"],
                "benchmark": f.get("benchmark") or None,
                "precio": f.get("precio") or "cierre",
                "archivo": ruta_precios(iid),
                "actualizado": ahora,
                "desde": f"{serie.index[0]:%Y-%m-%d}",
                "hasta": f"{serie.index[-1]:%Y-%m-%d}",
            }
        )
        pg = pagos[pagos["instrumento"].map(limpiar_id).isin({iid, limpiar_id(f["columna"])})]
        if not pg.empty:
            nuevos_pagos = pg.groupby("fecha")["monto"].sum()
            viejos = cargar_pagos(alm, inst, version) if inst.get("pagos") else None
            pagos_final = combinar(viejos if viejos is not None else pd.Series(dtype=float), nuevos_pagos)
            archivos[ruta_pagos(iid)] = serie_a_csv(pagos_final, "monto")
            inst["pagos"] = ruta_pagos(iid)
            resumen.append(f"{iid}: {len(serie)} precios y {len(pagos_final)} pagos.")
        else:
            inst.setdefault("pagos", None)
            resumen.append(f"{iid}: {len(serie)} precios ({'actualizado' if previo else 'nuevo'}).")
        por_id[iid] = inst
    nuevo_indice = sorted(por_id.values(), key=lambda i: (i["categoria"], i["id"]))
    archivos[INDICE] = indice_a_texto(nuevo_indice)
    return archivos, nuevo_indice, resumen


def preparar_edicion(indice: list[dict], editados: list[dict], borrar_ids: set[str]):
    """Cambios de datos descriptivos y bajas desde la pestaña Administrar."""
    por_id = {i["id"]: dict(i) for i in indice}
    for e in editados:
        if e["id"] in por_id:
            por_id[e["id"]].update(
                {k: (e[k] or None) if k == "benchmark" else e[k] for k in ("nombre", "categoria", "moneda", "benchmark", "precio")}
            )
    borrar: list[str] = []
    for iid in borrar_ids:
        inst = por_id.pop(iid, None)
        if inst:
            if inst.get("archivo"):
                borrar.append(inst["archivo"])
            if inst.get("pagos"):
                borrar.append(inst["pagos"])
    for inst in por_id.values():  # nadie puede quedar apuntando a un benchmark borrado
        if inst.get("benchmark") in borrar_ids:
            inst["benchmark"] = None
    nuevo = sorted(por_id.values(), key=lambda i: (i["categoria"], i["id"]))
    return {INDICE: indice_a_texto(nuevo)}, borrar, nuevo
