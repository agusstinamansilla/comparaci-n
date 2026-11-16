"""Lectura de los archivos que se suben: precios (y opcionalmente pagos de cupón/amortización).

Formato esperado (ver plantilla/plantilla_carga.xlsx):
  * Hoja "Precios" (o la primera hoja / el CSV): primera columna = fecha, una columna por
    instrumento con su precio, cuotaparte o valor de índice.
  * Hoja "Pagos" (opcional, para ON y bonos): columnas Fecha, Instrumento, Monto. El monto
    va en la misma unidad que el precio (p. ej. por cada 100 de valor nominal) y es lo que
    paga ese día entre cupón y amortización.
"""

from __future__ import annotations

import re
from io import BytesIO

import numpy as np
import pandas as pd


def _a_numero(col: pd.Series) -> pd.Series:
    """Convierte números escritos a la argentina ("1.234,56") o a la inglesa ("1,234.56")."""
    if pd.api.types.is_numeric_dtype(col):
        return col.astype(float)

    def conv(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return np.nan
        if isinstance(v, (int, float, np.number)):
            return float(v)
        t = str(v).strip().replace("$", "").replace("US", "").replace(" ", "")
        if t in {"", "-", "—", "N/A", "n/a", "#N/A"}:
            return np.nan
        if "," in t and "." in t:
            # el separador que aparece último es el decimal
            t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
        elif "," in t:
            t = t.replace(",", ".")
        try:
            return float(t)
        except ValueError:
            return np.nan

    return col.map(conv).astype(float)


def _a_fecha(col: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(col):
        return col
    if pd.api.types.is_numeric_dtype(col):  # fecha serial de Excel
        return pd.to_datetime(col, unit="D", origin="1899-12-30", errors="coerce")
    texto = col.astype(str).str.strip()
    iso = texto.str.match(r"^\d{4}-\d{2}-\d{2}")
    return pd.to_datetime(texto, errors="coerce", dayfirst=not bool(iso.all()), format="mixed")


def limpiar_id(nombre: str) -> str:
    t = re.sub(r"[^A-Za-z0-9.]+", "_", str(nombre).strip()).strip("_").upper()
    return t or "SIN_NOMBRE"


def hojas_excel(nombre_archivo: str, contenido: bytes) -> list[str]:
    if nombre_archivo.lower().rsplit(".", 1)[-1] not in {"xlsx", "xlsm", "xls"}:
        return []
    return list(pd.ExcelFile(BytesIO(contenido)).sheet_names)


def hoja_sugerida(hojas: list[str]) -> str | None:
    """Prefiere una hoja de precios ajustados; si no, 'Precios'; si no, la primera."""
    if not hojas:
        return None
    for h in hojas:
        if "ajust" in h.lower():
            return h
    for h in hojas:
        if h.strip().lower() == "precios":
            return h
    return hojas[0]


def ticker_de_archivo(nombre_archivo: str) -> str:
    """'AAPL_1d.csv' -> 'AAPL'; '0bcffb82-AAPL_1d.csv' -> 'AAPL'."""
    base = nombre_archivo.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    base = re.sub(r"^[0-9a-f]{8}-", "", base)
    base = re.sub(r"_(1d|1wk|1mo|daily|diario)$", "", base, flags=re.I)
    return base


def _formatear(crudo: pd.DataFrame, nombre_archivo: str, avisos: list[str]) -> pd.DataFrame:
    """Reconoce formatos conocidos y los lleva a 'fecha + una columna por instrumento'."""
    cols = {str(c).strip().lower(): c for c in crudo.columns}
    # Descarga de Yahoo Finance: Date, Open, High, Low, Close, Adj Close, Volume...
    if "date" in cols and ("adj close" in cols or "close" in cols):
        col = cols.get("adj close", cols.get("close"))
        if "adj close" in cols:
            avisos.append("Formato Yahoo Finance: se usa 'Adj Close' (ajustado por dividendos y splits).")
        return pd.DataFrame({"Fecha": crudo[cols["date"]], ticker_de_archivo(nombre_archivo): crudo[col]})
    # Formato largo: una fila por instrumento y fecha (ej. fondo, fecha, vcp)
    if crudo.shape[1] == 3:
        fechas = [c for c in crudo.columns if "fecha" in str(c).lower() or "date" in str(c).lower()]
        nombres = [
            c for c in crudo.columns
            if c not in fechas
            and not pd.api.types.is_numeric_dtype(crudo[c])
            and crudo[c].nunique() < len(crudo) / 5
        ]
        if len(nombres) == 1 and len(fechas) == 1 and nombres[0] != fechas[0]:
            valor = [c for c in crudo.columns if c not in (nombres[0], fechas[0])][0]
            ancho = (
                crudo.assign(**{fechas[0]: _a_fecha(crudo[fechas[0]])})
                .pivot_table(index=fechas[0], columns=nombres[0], values=valor, aggfunc="last")
                .reset_index()
            )
            avisos.append(
                f"Formato largo detectado: {ancho.shape[1] - 1} instrumentos en la columna '{nombres[0]}'."
            )
            return ancho
    return crudo


def leer_archivo(
    nombre_archivo: str, contenido: bytes, hoja: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Devuelve (precios, pagos, avisos). precios: índice fecha, una columna por instrumento."""
    avisos: list[str] = []
    ext = nombre_archivo.lower().rsplit(".", 1)[-1]
    pagos = pd.DataFrame(columns=["fecha", "instrumento", "monto"])
    if ext in {"xlsx", "xlsm", "xls"}:
        hojas = pd.read_excel(BytesIO(contenido), sheet_name=None)
        por_nombre = {k.strip().lower(): v for k, v in hojas.items()}
        elegida = hoja or hoja_sugerida(list(hojas))
        crudo = hojas[elegida]
        if "pagos" in por_nombre and "ajust" not in elegida.lower():
            pagos = _leer_pagos(por_nombre["pagos"], avisos)
    elif ext in {"csv", "txt"}:
        texto = contenido.decode("utf-8-sig", errors="replace")
        sep = ";" if texto.splitlines()[0].count(";") > texto.splitlines()[0].count(",") else ","
        crudo = pd.read_csv(BytesIO(contenido), sep=sep, encoding="utf-8-sig")
    else:
        raise ValueError("Formato no soportado: subí un .xlsx, .xls o .csv")

    crudo = crudo.dropna(how="all").dropna(axis=1, how="all")
    crudo = _formatear(crudo, nombre_archivo, avisos)
    if crudo.shape[1] < 2:
        raise ValueError("La hoja de precios necesita una columna de fecha y al menos una de precios.")
    fechas = _a_fecha(crudo.iloc[:, 0])
    precios = crudo.iloc[:, 1:].apply(_a_numero)
    precios.index = fechas
    precios = precios[precios.index.notna()]
    sin_fecha = len(crudo) - len(precios)
    if sin_fecha:
        avisos.append(f"Se descartaron {sin_fecha} fila(s) sin fecha válida.")
    precios = precios[~precios.index.duplicated(keep="last")].sort_index()
    precios.index.name = "fecha"
    precios.columns = [str(c).strip() for c in precios.columns]
    precios = precios.loc[:, [c for c in precios.columns if not c.lower().startswith("unnamed")]]
    precios = precios.dropna(axis=1, how="all")
    for c in precios.columns:
        n = int(precios[c].notna().sum())
        if n < 20:
            avisos.append(f"{c}: solo {n} precios; con tan pocos datos las métricas no sirven.")
        if (precios[c] <= 0).any():
            avisos.append(f"{c}: tiene precios en cero o negativos; se ignoran esas filas.")
    precios = precios.where(precios > 0)
    return precios, pagos, avisos


def leer_varios(archivos: list[tuple[str, bytes]], hoja: str | None = None):
    """Lee varios archivos y junta todas las columnas (útil para subir un CSV por acción)."""
    todos, pagos, avisos = [], [], []
    for nombre, contenido in archivos:
        p, pg, av = leer_archivo(nombre, contenido, hoja)
        todos.append(p)
        pagos.append(pg)
        avisos += [f"{ticker_de_archivo(nombre)}: {a}" if len(archivos) > 1 else a for a in av]
    precios = pd.concat(todos, axis=1).sort_index()
    precios = precios.loc[:, ~precios.columns.duplicated(keep="last")]
    pagos_df = pd.concat([p for p in pagos if not p.empty]) if any(not p.empty for p in pagos) else pagos[0]
    return precios, pagos_df, avisos


def _leer_pagos(df: pd.DataFrame, avisos: list[str]) -> pd.DataFrame:
    df = df.dropna(how="all")
    if df.empty:
        return pd.DataFrame(columns=["fecha", "instrumento", "monto"])
    cols = {str(c).strip().lower(): c for c in df.columns}
    try:
        out = pd.DataFrame(
            {
                "fecha": _a_fecha(df[cols["fecha"]]),
                "instrumento": df[cols["instrumento"]].astype(str).str.strip(),
                "monto": _a_numero(df[cols["monto"]]),
            }
        )
    except KeyError:
        avisos.append("La hoja Pagos debe tener las columnas Fecha, Instrumento y Monto; se ignoró.")
        return pd.DataFrame(columns=["fecha", "instrumento", "monto"])
    return out.dropna()


def combinar(viejo: pd.Series, nuevo: pd.Series) -> pd.Series:
    """Une dos series de precios; donde se superponen, manda la nueva."""
    if viejo.empty:
        return nuevo.dropna().sort_index()
    return nuevo.dropna().combine_first(viejo).sort_index()
