"""Comparador de riesgo y rendimiento — acciones, FCI, ON y bonos del Tesoro.

Correr local:  streamlit run app.py
"""

from __future__ import annotations

import hmac
import json
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from motor import calculo as calc
from motor import catalogo as cat
from motor import tecnico as tec
from motor import valuacion as val
from plotly.subplots import make_subplots
from motor.actualizador import actualizar, alta_desde_fuente
from motor.almacen import crear_almacen
from motor.datos import hoja_sugerida, hojas_excel, leer_varios, limpiar_id

st.set_page_config(page_title="Comparador de riesgo y rendimiento", page_icon="📈", layout="wide")

# Paleta categórica en orden fijo (azul, naranja, aqua, amarillo, magenta, verde, violeta, rojo)
COLORES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GRIS_BENCH = "#8a8f99"
TINTA = "#1f2a30"
TINTA_2 = "#5b6770"
GRILLA = "#e6e8eb"
TEAL = "#007974"
PETROLEO = "#27515e"
MAX_SELECCION = 8

# Atajos de selección por categoría (se usan solo los que estén cargados)
GRUPOS = {
    "acciones": {
        "7 magníficas": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"],
        "Semiconductores": ["NVDA", "AMD", "MU", "ASML"],
    },
}

PRECIOS = {
    "cierre": "Precio de cierre",
    "ajustado": "Ajustado (incluye dividendos/cupones)",
    "limpio": "Limpio (sin cupones: cargar hoja Pagos)",
    "cuotaparte": "Cuotaparte (FCI)",
    "indice": "Valor de índice",
    "objetivo": "Rendimiento objetivo (tasa fija)",
}

CSS = f"""
<style>
.block-container {{padding-top: 3.2rem;}}
.cab {{display:flex; align-items:center; gap:14px; margin-bottom:.4rem;}}
.cab .logo {{width:46px; height:46px; border-radius:10px; background:{TEAL}; color:white;
  display:flex; align-items:center; justify-content:center; font-size:24px; font-weight:700;}}
.cab h1 {{font-size:1.6rem; color:{PETROLEO}; margin:0; padding:0; font-weight:700;}}
.cab .sub {{color:#8a8f99; font-size:.9rem;}}
.chip {{display:inline-block; border:1px solid #dfe1e4; border-radius:8px; padding:2px 10px;
  margin-right:6px; font-size:.85rem; color:{PETROLEO}; background:white;}}
.chip b {{color:{TINTA};}}
h2, h3 {{color:{PETROLEO} !important;}}
.stTabs [data-baseweb="tab"] p {{font-weight:600;}}
[data-testid="stSidebar"] h1 {{color:{PETROLEO};}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# --- acceso -------------------------------------------------------------------------------


def control_acceso() -> None:
    clave = os.environ.get("APP_PASSWORD", "")
    if not clave or st.session_state.get("ok_acceso"):
        return
    st.title("📊 Comparador de riesgo y rendimiento")
    intento = st.text_input("Contraseña", type="password")
    if intento:
        if hmac.compare_digest(intento, clave):
            st.session_state["ok_acceso"] = True
            st.rerun()
        st.error("Contraseña incorrecta.")
    st.stop()


control_acceso()


# --- datos (con caché) --------------------------------------------------------------------


@st.cache_resource
def almacen():
    return crear_almacen()


ALM = almacen()


@st.cache_data(ttl=30, show_spinner=False)
def version_actual() -> str:
    return ALM.version()


@st.cache_data(show_spinner=False)
def indice_instrumentos(version: str) -> list[dict]:
    return cat.cargar_indice(ALM, version)


@st.cache_data(show_spinner=False)
def serie_total(iid: str, version: str, conversion: str, fx_id: str | None, frecuencia: str) -> pd.Series:
    por_id = {i["id"]: i for i in indice_instrumentos(version)}
    inst = por_id[iid]
    precios = cat.cargar_precios(ALM, inst, version)
    pagos = cat.cargar_pagos(ALM, inst, version)
    fx = cat.cargar_precios(ALM, por_id[fx_id], version) if (fx_id and conversion != "ninguna") else None
    idx = calc.indice_total(precios, pagos, fx, conversion)
    return calc.a_frecuencia(idx, frecuencia)


@st.cache_data(show_spinner=False)
def metricas(nombre: str, indice: pd.Series, bench: pd.Series | None, rf: float) -> calc.Resultado:
    return calc.calcular(nombre, indice, bench, rf)


def refrescar() -> None:
    version_actual.clear()
    datos_tecnicos.clear()
    meta_valuacion.clear()
    calendario_on.clear()
    indice_instrumentos.clear()
    serie_total.clear()


VERSION = version_actual()
INDICE = indice_instrumentos(VERSION)
POR_ID = {i["id"]: i for i in INDICE}


def de_categoria(c: str) -> list[dict]:
    return [i for i in INDICE if i["categoria"] == c and cat.disponible(i)]


def candidatos_benchmark() -> list[str]:
    """Cualquier instrumento puede ser benchmark (ej. GD30D para las ON); primero los 'benchmark'."""
    orden = {"benchmark": 0, "bonos": 1, "acciones": 2, "fci": 3, "on": 4}
    return [
        i["id"] for i in sorted(INDICE, key=lambda i: (orden.get(i["categoria"], 9), i["id"]))
        if i["categoria"] != "tipo_cambio" and cat.disponible(i)
    ]


@st.cache_data(show_spinner=False)
def calendario_on(version: str) -> dict:
    return cat.cargar_calendario(ALM, version)


def etiqueta(iid: str) -> str:
    i = POR_ID.get(iid)
    if not i or i["nombre"] == iid or iid in i["nombre"]:
        return i["nombre"] if i else iid
    return f"{i['nombre']} ({iid})"


# --- barra lateral --------------------------------------------------------------------------

st.sidebar.title("Opciones")
periodo = st.sidebar.radio("Período", list(calc.PERIODOS), index=1, horizontal=True)
frecuencia = st.sidebar.radio(
    "Frecuencia",
    ["Diaria", "Mensual"],
    horizontal=True,
    help="Diaria da más datos. Mensual es mejor para ON y bonos que cotizan poco.",
)
vista_moneda = st.sidebar.radio(
    "Moneda",
    ["Moneda original", "Todo en USD", "Todo en ARS"],
    help="En 'Todo en USD' los instrumentos en pesos se dividen por el tipo de cambio de cada día.",
)
tipos_cambio = de_categoria("tipo_cambio")
fx_id = None
if tipos_cambio:
    fx_id = st.sidebar.selectbox(
        "Tipo de cambio para convertir", [t["id"] for t in tipos_cambio], format_func=etiqueta
    )
elif vista_moneda != "Moneda original":
    st.sidebar.warning("Para convertir monedas cargá un tipo de cambio (MEP o CCL) en ➕ Agregar.")
solo_comun = st.sidebar.checkbox(
    "Solo período común", help="Corta todo a las fechas en que existen todos los instrumentos elegidos."
)
rf_pct = st.sidebar.number_input(
    "Tasa libre de riesgo anual (%)",
    min_value=0.0,
    max_value=500.0,
    value=0.0,
    step=0.5,
    help="Se usa solo en Sharpe y Sortino. Con 0% los fondos en pesos parecen mejores de lo que son.",
)
st.sidebar.caption(
    f"Guardado: {'GitHub · ' + ALM.repo if ALM.modo == 'github' else 'carpeta local (modo prueba)'}"
)
st.sidebar.divider()
con_fuente = [i for i in INDICE if i.get("fuente")]
if st.sidebar.button("🔄 Actualizar precios ahora", type="primary", help="Baja lo último de Yahoo, BCRA y MEP (y PPI/fondos cuando estén conectados)."):
    barra = st.sidebar.progress(0.0, text="Actualizando…")
    informe, cambios = actualizar(ALM, progreso=lambda f, iid: barra.progress(f, text=f"Actualizando {iid}…"))
    barra.empty()
    st.session_state["informe"] = informe
    refrescar()
    st.rerun()
if "informe" in st.session_state:
    inf = st.session_state["informe"]
    errores = [x for x in inf if x["estado"] != "ok"]
    if errores:
        st.sidebar.warning(f"{len(errores)} con error de {len(inf)}.")
    else:
        st.sidebar.success(f"Actualizados {len(inf)} de {len(inf)}.")
    with st.sidebar.expander("Detalle de la actualización"):
        for x in inf:
            st.markdown(f"{'✅' if x['estado'] == 'ok' else '⚠️'} **{x['id']}**: {x['detalle']}")
ultima = max((i.get("actualizado") or "" for i in con_fuente), default="")
st.sidebar.caption(f"{len(con_fuente)} series se actualizan solas" + (f" · última: {ultima}" if ultima else ""))
if st.sidebar.button("↻ Recargar datos"):
    refrescar()
    st.rerun()


def conversion(moneda: str, destino: str) -> str:
    if moneda == destino:
        return "ninguna"
    return "a_usd" if destino == "USD" else "a_ars"


def destino_de(moneda_inst: str) -> str:
    return {"Todo en USD": "USD", "Todo en ARS": "ARS"}.get(vista_moneda, moneda_inst)


# --- formato y gráficos -------------------------------------------------------------------


def estilo(fig: go.Figure, titulo: str, alto: int = 420) -> go.Figure:
    fig.update_layout(
        title=dict(text=titulo, font=dict(size=16, color=TINTA)),
        height=alto,
        margin=dict(l=10, r=10, t=50, b=10),
        title_x=0, title_y=0.98, title_yanchor="top",
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(color=TINTA_2),
        legend=dict(orientation="h", yanchor="top", y=-0.12, x=0, title=None),
        hoverlabel=dict(bgcolor="white", font_color=TINTA),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRILLA, ticks="outside", tickcolor=GRILLA)
    fig.update_yaxes(gridcolor=GRILLA, zerolinecolor="#c3c2b7", linecolor=GRILLA)
    return fig


def colores_para(ids: list[str], categoria: str) -> dict[str, str]:
    """El color sigue al instrumento: misma posición en el catálogo = mismo color siempre."""
    todos = [i["id"] for i in de_categoria(categoria)]
    if len(todos) <= len(COLORES):
        return {i: COLORES[todos.index(i)] for i in ids}
    return {i: COLORES[k % len(COLORES)] for k, i in enumerate(ids)}


def fmt_valor(v, tipo: str, ppy: float) -> float | None:
    if v is None or not np.isfinite(v):
        return None
    if tipo == "dur":
        return v / ppy if np.isfinite(ppy) and ppy else None
    return v


def tabla_resultados(resultados: list[calc.Resultado], ids: list[str]) -> pd.DataFrame:
    filas = []
    for iid, r in zip(ids, resultados):
        fila = {"Instrumento": etiqueta(iid)}
        for mid, nombre, tipo, _ in calc.METRICAS:
            v = fmt_valor(r.valores.get(mid), tipo, r.periodos_por_anio)
            fila[nombre] = v * 100 if (v is not None and tipo == "pct") else v
        fila["Años de datos"] = r.anios
        fila["Alertas"] = len(r.avisos) + (1 if r.baja_confianza else 0)
        filas.append(fila)
    df = pd.DataFrame(filas)
    numericas = [c for c in df.columns if c != "Instrumento"]
    df[numericas] = df[numericas].apply(pd.to_numeric, errors="coerce")
    return df


def config_columnas() -> dict:
    cfg = {"Instrumento": st.column_config.TextColumn(pinned=True)}
    for _, nombre, tipo, ayuda in calc.METRICAS:
        fmt = {"pct": "%.2f%%", "num": "%.2f", "dur": "%.1f años"}[tipo]
        cfg[nombre] = st.column_config.NumberColumn(nombre, help=ayuda, format=fmt)
    cfg["Años de datos"] = st.column_config.NumberColumn(format="%.1f")
    cfg["Alertas"] = st.column_config.NumberColumn(help="Cantidad de avisos (ver abajo).", format="%d ⚠️")
    return cfg


def grafico_acumulado(indices, colores, benches) -> go.Figure:
    fig = go.Figure()
    for iid, s in indices.items():
        fig.add_trace(
            go.Scatter(
                x=s.index, y=(s - 1) * 100, name=etiqueta(iid), mode="lines",
                line=dict(width=2, color=colores[iid]),
                hovertemplate="%{y:.1f}%<extra>" + etiqueta(iid) + "</extra>",
            )
        )
    for bid, s in benches.items():
        fig.add_trace(
            go.Scatter(
                x=s.index, y=(s - 1) * 100, name=f"{etiqueta(bid)} (benchmark)", mode="lines",
                line=dict(width=2, color=GRIS_BENCH, dash="dash"),
                hovertemplate="%{y:.1f}%<extra>" + etiqueta(bid) + "</extra>",
            )
        )
    fig.update_layout(hovermode="x unified")
    fig.update_yaxes(ticksuffix="%")
    return estilo(fig, "Rendimiento acumulado", 460 + 18 * ((len(indices) + len(benches)) // 3))


def grafico_riesgo_rendimiento(resultados, ids, colores) -> go.Figure:
    fig = go.Figure()
    for iid, r in zip(ids, resultados):
        vol, cagr = r.valores.get("annualized_volatility"), r.valores.get("cagr")
        if vol is None or cagr is None:
            continue
        fig.add_trace(
            go.Scatter(
                x=[vol * 100], y=[cagr * 100], name=etiqueta(iid), mode="markers+text",
                text=[iid], textposition="top center", textfont=dict(color=TINTA, size=12),
                marker=dict(size=14, color=colores[iid], line=dict(width=2, color="white")),
                hovertemplate=(
                    f"<b>{etiqueta(iid)}</b><br>Volatilidad: %{{x:.1f}}%<br>Rend. anual: %{{y:.1f}}%"
                    f"<br>Caída máx.: {(r.valores.get('max_drawdown') or 0) * 100:.1f}%<extra></extra>"
                ),
            )
        )
    xs = [t.x[0] for t in fig.data] or [0]
    ys = [t.y[0] for t in fig.data] or [0]
    pad_y = max((max(ys) - min(ys)) * 0.15, 1)
    fig.update_xaxes(title="Volatilidad anual (riesgo) →", ticksuffix="%", range=[0, max(xs) * 1.2 + 1])
    fig.update_yaxes(title="Rendimiento anual →", ticksuffix="%", range=[min(min(ys), 0) - pad_y, max(ys) + pad_y])
    fig.update_layout(showlegend=False)
    return estilo(fig, "Riesgo vs. rendimiento (arriba a la izquierda = mejor)")


def grafico_caidas(indices, colores) -> go.Figure:
    fig = go.Figure()
    for iid, s in indices.items():
        dd = calc.drawdowns(s) * 100
        fig.add_trace(
            go.Scatter(
                x=dd.index, y=dd, name=etiqueta(iid), mode="lines",
                line=dict(width=2, color=colores[iid]),
                hovertemplate="%{y:.1f}%<extra>" + etiqueta(iid) + "</extra>",
            )
        )
    fig.update_layout(hovermode="x unified")
    fig.update_yaxes(ticksuffix="%")
    return estilo(fig, "Caídas desde el máximo (cuánto se sufrió en el camino)", 400)


def grafico_correlacion(corr: pd.DataFrame) -> go.Figure:
    labels = list(corr.columns)
    z = corr.to_numpy(dtype=float, copy=True)
    np.fill_diagonal(z, np.nan)  # la diagonal (1) no aporta y tapa el resto
    texto = np.where(np.isnan(z), "", np.round(z, 2).astype(str))
    fig = go.Figure(
        go.Heatmap(
            z=z, x=labels, y=labels, zmin=-1, zmax=1,
            colorscale=[[0, "#eb6834"], [0.5, "#f0efec"], [1, "#2a78d6"]],
            text=texto, texttemplate="%{text}", textfont=dict(color=TINTA),
            hovertemplate="%{y} vs %{x}: %{z:.2f}<extra></extra>", xgap=2, ygap=2,
            colorbar=dict(title="Correlación", thickness=12),
        )
    )
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return estilo(fig, "¿Se mueven juntos? (1 = igual, 0 = independientes)", 120 + 45 * len(labels))


# --- pestaña de comparación ---------------------------------------------------------------


def pestania_categoria(categoria: str) -> None:
    disponibles = de_categoria(categoria)
    if not disponibles:
        st.info(
            f"Todavía no cargaste {calc.CATEGORIAS[categoria].lower()}. "
            "Andá a la pestaña ➕ Agregar para subir un Excel con precios."
        )
        return
    c1, c2, c3 = st.columns([1, 3, 2])
    monedas = sorted({i["moneda"] for i in disponibles})
    opciones_mon = ["Todas", *monedas]
    filtro = c1.radio(
        "Moneda del instrumento", opciones_mon, key=f"mon_{categoria}", horizontal=True,
        index=1 if (categoria == "fci" and len(monedas) > 1) else 0,
    )
    lista = [i["id"] for i in disponibles if filtro == "Todas" or i["moneda"] == filtro]
    clave_sel = f"sel_{categoria}_{filtro}"
    grupos = {k: [t for t in v if t in lista] for k, v in GRUPOS.get(categoria, {}).items()}
    grupos = {k: v for k, v in grupos.items() if len(v) >= 2}
    if grupos:
        def aplicar_grupo():
            g = st.session_state[f"grupo_{categoria}"]
            if g in grupos:
                st.session_state[clave_sel] = grupos[g][:MAX_SELECCION]

        c1.selectbox("Atajo", ["—", *grupos], key=f"grupo_{categoria}", on_change=aplicar_grupo)
    if clave_sel not in st.session_state:
        por_defecto = next(iter(grupos.values()), None) or lista[:MAX_SELECCION]
        st.session_state[clave_sel] = por_defecto[:MAX_SELECCION]
    st.session_state[clave_sel] = [x for x in st.session_state[clave_sel] if x in lista]
    elegidos = c2.multiselect(
        "Instrumentos a comparar", lista, format_func=etiqueta, max_selections=MAX_SELECCION, key=clave_sel,
    )
    benches = candidatos_benchmark()
    opciones_bench = ["(el de cada instrumento)", "(ninguno)", *benches]
    bench_elegido = c3.selectbox(
        "Benchmark", opciones_bench, key=f"bench_{categoria}",
        format_func=lambda b: b if b.startswith("(") else etiqueta(b),
        help="Por defecto usa el benchmark asignado a cada instrumento.",
    )
    if not elegidos:
        st.caption("Elegí al menos un instrumento.")
        return

    if vista_moneda == "Moneda original" and len({POR_ID[i]["moneda"] for i in elegidos}) > 1:
        st.warning(
            "Estás comparando instrumentos en pesos y en dólares en su moneda original: los "
            "rendimientos no son comparables. Elegí 'Todo en USD' o 'Todo en ARS' en la barra lateral."
        )

    pesos = any(POR_ID[i]["moneda"] == "ARS" for i in elegidos) and vista_moneda != "Todo en USD"
    if pesos and not rf_pct:
        st.info(
            "Fondos en pesos con tasa libre de riesgo en 0%: el Sharpe y el Sortino salen inflados. "
            "Poné una tasa de referencia (BADLAR, caución) en la barra lateral para que sean comparables."
        )
    if categoria == "fci" and not tipos_cambio:
        st.caption("Para comparar fondos en pesos contra fondos en dólares, cargá el histórico del dólar MEP (categoría tipo_cambio).")

    avisos_generales: list[str] = []
    indices: dict[str, pd.Series] = {}
    bench_de: dict[str, str | None] = {}
    with st.spinner("Calculando…"):
        for iid in elegidos:
            inst = POR_ID[iid]
            conv = conversion(inst["moneda"], destino_de(inst["moneda"]))
            try:
                indices[iid] = serie_total(iid, VERSION, conv, fx_id, frecuencia)
            except ValueError as e:
                avisos_generales.append(f"{iid}: {e}")
                continue
            b = inst.get("benchmark") if bench_elegido == "(el de cada instrumento)" else (
                None if bench_elegido == "(ninguno)" else bench_elegido
            )
            bench_de[iid] = b if (b in POR_ID and cat.disponible(POR_ID[b])) else None

        recortados, desde, hasta, avisos_rec = calc.recortar(indices, calc.PERIODOS[periodo], solo_comun)
        avisos_generales += avisos_rec
        if not recortados:
            for a in avisos_generales:
                st.warning(a)
            st.error("No hay datos suficientes para el período elegido.")
            return

        ids = list(recortados)
        resultados: list[calc.Resultado] = []
        benches_graf: dict[str, pd.Series] = {}
        for iid in ids:
            b = bench_de.get(iid)
            serie_b = None
            if b:
                moneda_obj = destino_de(POR_ID[iid]["moneda"])
                try:
                    sb = serie_total(b, VERSION, conversion(POR_ID[b]["moneda"], moneda_obj), fx_id, frecuencia)
                    sb = sb[(sb.index >= desde) & (sb.index <= hasta)]
                    if len(sb) > 2:
                        serie_b = sb / sb.iloc[0]
                        benches_graf.setdefault(b, serie_b)
                except ValueError as e:
                    avisos_generales.append(f"Benchmark {b}: {e}")
            resultados.append(metricas(iid, recortados[iid], serie_b, rf_pct / 100))

    st.caption(
        f"Del **{desde:%d/%m/%Y}** al **{hasta:%d/%m/%Y}** · frecuencia {frecuencia.lower()} · "
        f"{vista_moneda.lower()}" + (f" · tasa libre {rf_pct:.1f}%" if rf_pct else " · tasa libre 0%")
    )
    for a in avisos_generales:
        st.warning(a)

    colores = colores_para(ids, categoria)
    df = tabla_resultados(resultados, ids)
    st.dataframe(df, hide_index=True, width="stretch", column_config=config_columnas())
    st.download_button(
        "⬇️ Descargar tabla (CSV)", df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
        file_name=f"comparacion_{categoria}_{hasta:%Y%m%d}.csv", mime="text/csv", key=f"dl_{categoria}",
    )

    if categoria in {"on", "bonos"}:
        tabla_pagos(ids)

    g1, g2 = st.columns(2)
    g1.plotly_chart(grafico_acumulado(recortados, colores, benches_graf), width="stretch")
    g2.plotly_chart(grafico_riesgo_rendimiento(resultados, ids, colores), width="stretch")
    st.plotly_chart(grafico_caidas(recortados, colores), width="stretch")
    if len(ids) >= 2:
        corr = calc.correlaciones(recortados)
        if corr.notna().values.sum() > len(ids):
            st.plotly_chart(grafico_correlacion(corr), width="stretch")

    with st.expander("⚠️ Avisos y confianza de cada instrumento"):
        nombres = {mid: n for mid, n, *_ in calc.METRICAS}
        for iid, r in zip(ids, resultados):
            st.markdown(f"**{etiqueta(iid)}** — {r.observaciones} períodos, {r.anios:.1f} años")
            if r.error:
                st.error(r.error)
            for a in r.avisos:
                st.markdown(f"- {a}")
            if r.baja_confianza:
                st.markdown("- Baja confianza en: " + ", ".join(nombres[m] for m in sorted(r.baja_confianza)))
            if POR_ID[iid].get("referencia"):
                st.markdown(f"- Benchmark según factsheet: {POR_ID[iid]['referencia']}")
            if not bench_de.get(iid):
                st.markdown("- Sin benchmark asignado: Beta, Correlación y Diferencia vs. benchmark quedan vacías.")
            elif POR_ID.get(bench_de[iid], {}).get("tasa_anual") is not None:
                st.markdown("- Benchmark de rendimiento objetivo (tasa fija): Beta y Correlación no aplican; mirá la Diferencia vs. benchmark.")
            if not (r.avisos or r.baja_confianza or r.error):
                st.markdown("- Sin alertas.")

    with st.expander("¿Qué significa cada métrica?"):
        for _, nombre, _, ayuda in calc.METRICAS:
            st.markdown(f"- **{nombre}:** {ayuda}")
        st.caption("Cálculos con OpenFactsheet (metodología 0.1.0). Información educativa, no es recomendación de inversión.")


def tabla_pagos(ids: list[str]) -> None:
    cal = calendario_on(VERSION)
    if not cal:
        return
    hoy = pd.Timestamp.today().normalize()
    filas = []
    for iid in ids:
        clave = iid[:4]
        info = cal.get(clave)
        if not info:
            continue
        cupones = sorted(pd.to_datetime(info.get("cupones") or []))
        venc = pd.to_datetime(info.get("vencimiento")) if info.get("vencimiento") else pd.NaT
        futuros = [c for c in cupones if c >= hoy]
        if len(cupones) >= 2:
            dias = float(np.median(np.diff(cupones).astype("timedelta64[D]").astype(int)))
            frec = "Mensual" if dias < 45 else "Trimestral" if dias < 120 else "Semestral" if dias < 250 else "Anual"
        else:
            frec = "Al vencimiento" if cupones else "—"
        filas.append(
            {
                "Instrumento": etiqueta(iid),
                "Estado": "Vencida" if pd.notna(venc) and venc < hoy else "Vigente",
                "Próximo pago": futuros[0].date() if futuros else None,
                "Pagos restantes": len(futuros),
                "Frecuencia": frec,
                "Vencimiento": venc.date() if pd.notna(venc) else None,
                "Años al vencimiento": round((venc - hoy).days / 365.25, 1) if pd.notna(venc) and venc >= hoy else None,
            }
        )
    if filas:
        with st.expander("📅 Pagos y vencimientos", expanded=False):
            st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch")
            st.caption("Del calendario de pagos cargado en ⚙️ Administrar (on_data.json). Los precios ya están ajustados por estos pagos.")



# --- pestaña Valuación y tendencia -------------------------------------------------------------

VERDE, ROJO, AMBAR = "#1a8f4d", "#c93a3a", "#c98a1a"


@st.cache_data(show_spinner=False)
def datos_tecnicos(iid: str, version: str) -> pd.DataFrame | None:
    t = val.leer_tecnico(ALM, iid, version)
    if t is not None and not t.empty:
        precio, volumen = t["cierre"], t["volumen"]
    else:  # sin archivo de volumen: se usa el precio ajustado y no hay volumen
        precio, volumen = cat.cargar_precios(ALM, POR_ID[iid], version), None
    if precio.dropna().size < 30:
        return None
    eps = val.leer_eps(ALM, iid, version) if POR_ID[iid]["categoria"] == "acciones" else None
    return tec.preparar(precio, volumen, eps)


@st.cache_data(show_spinner=False)
def meta_valuacion(version: str) -> dict:
    return val.leer_meta(ALM, version)


def _flecha(estado: str | None, desde, alcista: str, bajista: str) -> str:
    if estado is None:
        return "—"
    txt = f"▲ {alcista}" if estado == "arriba" else f"▼ {bajista}"
    return txt + (f" desde {desde:%d/%m/%y}" if desde is not None else "")


def _rsi_txt(v) -> str:
    if v is None:
        return "—"
    zona = " · sobrecompra" if v >= 70 else " · sobreventa" if v <= 30 else ""
    return f"{v:.0f}{zona}"


def pestania_tecnica() -> None:
    disponibles = [i["id"] for i in INDICE if val.aplica(i) and cat.disponible(i)]
    if not disponibles:
        st.info("Cargá acciones desde Yahoo para ver esta pestaña.")
        return
    acciones = [i for i in disponibles if POR_ID[i]["categoria"] == "acciones"]
    c1, c2 = st.columns([1, 4])
    grupos = {k: [t for t in v if t in disponibles] for k, v in GRUPOS["acciones"].items()}
    grupos = {k: v for k, v in grupos.items() if len(v) >= 2}
    if "sel_tec" not in st.session_state:
        st.session_state["sel_tec"] = (next(iter(grupos.values()), None) or acciones)[:12]

    def aplicar():
        g = st.session_state["grupo_tec"]
        if g in grupos:
            st.session_state["sel_tec"] = grupos[g]
        elif g == "Todas las acciones":
            st.session_state["sel_tec"] = acciones

    c1.selectbox("Atajo", ["—", *grupos, "Todas las acciones"], key="grupo_tec", on_change=aplicar)
    elegidos = c2.multiselect("Acciones y ETFs", disponibles, format_func=etiqueta, key="sel_tec")
    if not elegidos:
        st.caption("Elegí al menos una.")
        return

    meta = meta_valuacion(VERSION)
    filas, datos = [], {}
    for iid in elegidos:
        df = datos_tecnicos(iid, VERSION)
        if df is None:
            continue
        datos[iid] = df
        r = tec.resumen(df)
        filas.append(
            {
                "Instrumento": etiqueta(iid),
                "Fecha": r["fecha"].date(),
                "Precio": r["precio"],
                "vs. media 50": None if r["dist_media_corta"] is None else r["dist_media_corta"] * 100,
                "vs. media 200": None if r["dist_media_larga"] is None else r["dist_media_larga"] * 100,
                "Tendencia (50 vs 200)": _flecha(r["cruce"], r["cruce_desde"], "alcista", "bajista"),
                "RSI 14": _rsi_txt(r["rsi"]),
                "MACD": _flecha(r["macd_estado"], r["macd_desde"], "alcista", "bajista"),
                "Volumen vs. prom. 20": r["vol_relativo"],
                "P/E": r["pe"],
                "P/E mediana 5 años": r["pe_mediana"],
                "P/E: más caro que el": None if r["pe_percentil"] is None else r["pe_percentil"] * 100,
                "P/E forward": (meta.get(iid) or {}).get("forwardPE"),
            }
        )
    if not filas:
        st.warning("No hay datos suficientes para las elegidas.")
        return
    tabla = pd.DataFrame(filas)
    texto = {"Instrumento", "Fecha", "Tendencia (50 vs 200)", "RSI 14", "MACD"}
    for c in tabla.columns.difference(list(texto)):
        tabla[c] = pd.to_numeric(tabla[c], errors="coerce")
    st.dataframe(
        tabla, hide_index=True, width="stretch",
        column_config={
            "Instrumento": st.column_config.TextColumn(pinned=True),
            "Precio": st.column_config.NumberColumn(format="%.2f"),
            "vs. media 50": st.column_config.NumberColumn(format="%+.1f%%", help="Cuánto está el precio arriba (+) o abajo (−) de su media de 50 ruedas."),
            "vs. media 200": st.column_config.NumberColumn(format="%+.1f%%", help="Ídem contra la media de 200 ruedas (tendencia de largo plazo)."),
            "Tendencia (50 vs 200)": st.column_config.TextColumn(help="▲ media 50 arriba de la 200 (cruce dorado); ▼ abajo (cruce de la muerte)."),
            "RSI 14": st.column_config.TextColumn(width="medium", help="0 a 100. Arriba de 70 suele leerse como sobrecompra; abajo de 30, sobreventa."),
            "MACD": st.column_config.TextColumn(help="▲ la línea MACD está arriba de su señal (impulso alcista); ▼ abajo."),
            "Volumen vs. prom. 20": st.column_config.NumberColumn(format="%.1fx", help="Volumen del último día dividido por su promedio de 20 ruedas."),
            "P/E": st.column_config.NumberColumn(format="%.1f", help="Precio / ganancia por acción de los últimos 4 trimestres."),
            "P/E mediana 5 años": st.column_config.NumberColumn(format="%.1f"),
            "P/E: más caro que el": st.column_config.ProgressColumn(
                format="%.0f%% de los días", min_value=0, max_value=100,
                help="En qué parte de su rango de 5 años está el P/E de hoy. 90% = más caro que el 90% de los días.",
            ),
            "P/E forward": st.column_config.NumberColumn(format="%.1f", help="P/E sobre la ganancia esperada (Yahoo)."),
        },
    )
    st.download_button(
        "⬇️ Descargar tabla (CSV)", tabla.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
        file_name="valuacion_tendencia.csv", mime="text/csv", key="dl_tec",
    )
    if tabla["P/E"].isna().all():
        st.caption("El P/E aparece después de la primera actualización (botón 🔄 o la corrida diaria), que baja las ganancias trimestrales de Yahoo.")

    st.divider()
    g1, g2 = st.columns([3, 2])
    ver = g1.radio("Gráfico de", list(datos), format_func=etiqueta, horizontal=True, key="ver_tec")
    rango = g2.radio("Período", ["6 meses", "1 año", "3 años", "5 años"], index=1, horizontal=True, key="rango_tec")
    df = datos[ver]
    meses = {"6 meses": 6, "1 año": 12, "3 años": 36, "5 años": 60}[rango]
    d = df[df.index >= df.index[-1] - pd.DateOffset(months=meses)]
    st.plotly_chart(grafico_tecnico(d, etiqueta(ver)), width="stretch")
    pe = df["pe"].dropna()
    if not pe.empty:
        st.plotly_chart(grafico_pe(pe, etiqueta(ver)), width="stretch")
    elif POR_ID[ver]["categoria"] == "acciones":
        st.caption("Sin P/E: todavía no se bajaron sus ganancias, o tuvo ganancias negativas.")

    st.divider()
    l1, l2 = st.columns([3, 2], gap="large")
    with l1:
        st.subheader(f"🧭 Qué muestra hoy {etiqueta(ver)}")
        st.caption(f"Con los datos al {df.index[-1]:%d/%m/%Y}.")
        for frase in lectura_de_hoy(df, tec.resumen(df), POR_ID[ver]["categoria"] == "acciones"):
            st.markdown(f"- {frase}")
        st.caption("Lectura automática de los indicadores, no es una recomendación de compra o venta.")
    with l2:
        st.subheader("📖 Cómo leer el gráfico")
        st.markdown(GUIA_GRAFICO)

    with st.expander("¿Cómo se lee cada indicador?"):
        st.markdown(
            "- **Medias de 50 y 200 ruedas:** el promedio del precio de los últimos 50 y 200 días hábiles. "
            "Precio arriba de la 200 = tendencia de largo plazo alcista.\n"
            "- **Cruce dorado / de la muerte:** la media de 50 cruza hacia arriba / hacia abajo a la de 200.\n"
            "- **RSI (14):** mide la fuerza de las subas contra las bajas de las últimas 14 ruedas. "
            "Más de 70 = sobrecompra, menos de 30 = sobreventa.\n"
            "- **MACD (12, 26, 9):** diferencia entre dos medias exponenciales; cuando cruza su línea de señal "
            "hacia arriba indica impulso alcista. El histograma es la distancia entre las dos.\n"
            "- **Volumen relativo:** más de 1,5x el promedio suele acompañar movimientos fuertes.\n"
            "- **P/E histórico:** precio / ganancia por acción de los últimos 12 meses (ganancia *ajustada* "
            "que informa Yahoo). Sirve para ver si la acción está cara o barata **contra sí misma**."
        )
        st.caption("Indicadores calculados sobre el precio de cierre. Información educativa, no es recomendación de inversión.")



def lectura_de_hoy(df: pd.DataFrame, r: dict, es_accion: bool) -> list[str]:
    """Traduce los indicadores de hoy a frases simples. Cambia según la acción elegida."""
    hoy = df.index[-1]
    reciente = lambda f: f is not None and (hoy - f).days <= 30  # noqa: E731
    out: list[str] = []
    alcistas = bajistas = 0

    d200, d50 = r["dist_media_larga"], r["dist_media_corta"]
    if d200 is not None:
        if d200 >= 0:
            alcistas += 1
            txt = f"**Largo plazo:** el precio está **{d200:+.1%} arriba de su media de 200** → la tendencia de largo plazo es **alcista**."
        else:
            bajistas += 1
            txt = f"**Largo plazo:** el precio está **{d200:+.1%} debajo de su media de 200** → la tendencia de largo plazo es **bajista**."
        if abs(d200) >= 0.2:
            txt += " Está muy alejado de ese promedio: cuando pasa esto suelen darse correcciones o rebotes hacia la media."
        out.append(txt)
    if d50 is not None:
        out.append(
            f"**Corto plazo:** está **{d50:+.1%} {'arriba' if d50 >= 0 else 'debajo'} de su media de 50** → "
            + ("viene subiendo en las últimas semanas." if d50 >= 0 else "viene flojo en las últimas semanas.")
        )

    if r["cruce"]:
        f = r["cruce_desde"]
        cuando = f" el {f:%d/%m/%y}" if f is not None else ""
        if r["cruce"] == "arriba":
            alcistas += 1
            out.append(
                f"**Cruce de medias:** la media de 50 (naranja) está **arriba** de la de 200 (azul) desde{cuando or ' hace mucho'}"
                + (" — **cruce dorado reciente**: señal de que la tendencia de mediano plazo se dio vuelta a alcista." if reciente(f) else ": tendencia de mediano plazo alcista.")
            )
        else:
            bajistas += 1
            out.append(
                f"**Cruce de medias:** la media de 50 (naranja) está **debajo** de la de 200 (azul) desde{cuando or ' hace mucho'}"
                + (" — **cruce de la muerte reciente**: la tendencia de mediano plazo se dio vuelta a bajista." if reciente(f) else ": tendencia de mediano plazo bajista.")
            )

    v = r["rsi"]
    if v is not None:
        if v >= 70:
            t = "**sobrecompra**: subió mucho en poco tiempo. Puede frenar o corregir, aunque en tendencias fuertes puede quedarse arriba de 70 un buen rato."
        elif v <= 30:
            t = "**sobreventa**: bajó mucho en poco tiempo. Suelen aparecer rebotes, pero en caídas fuertes puede seguir abajo de 30."
        elif v >= 50:
            t = "zona neutral con más fuerza compradora que vendedora."
        else:
            t = "zona neutral con más fuerza vendedora que compradora."
        out.append(f"**RSI {v:.0f}:** {t}")

    if r["macd_estado"]:
        hist = df["histograma"].dropna()
        crece = len(hist) > 5 and abs(hist.iloc[-1]) > abs(hist.iloc[-6])
        f = r["macd_desde"]
        cuando = f" desde el {f:%d/%m/%y}" if f is not None else ""
        if r["macd_estado"] == "arriba":
            alcistas += 1
            t = f"la línea MACD (oscura) está **arriba** de su señal (naranja){cuando} → **impulso alcista**"
        else:
            bajistas += 1
            t = f"la línea MACD (oscura) está **debajo** de su señal (naranja){cuando} → **impulso bajista**"
        t += ", y las barras se están " + ("**agrandando** (el impulso gana fuerza)." if crece else "**achicando** (el impulso pierde fuerza; puede venir un cruce).")
        out.append(f"**MACD:** {t}")

    vr = r["vol_relativo"]
    if vr is not None:
        if vr >= 1.5:
            out.append(f"**Volumen:** {vr:.1f} veces su promedio → mucho movimiento; el cambio de precio del día tiene más convicción.")
        elif vr <= 0.7:
            out.append(f"**Volumen:** {vr:.1f} veces su promedio → poco movimiento; el cambio de precio del día pesa menos.")
        else:
            out.append(f"**Volumen:** {vr:.1f} veces su promedio → normal.")

    if es_accion and r["pe"] is not None:
        pct, med = r["pe_percentil"], r["pe_mediana"]
        nivel = "**cara** contra su propia historia" if pct >= 0.8 else "**barata** contra su propia historia" if pct <= 0.2 else "en un nivel **intermedio** de su historia"
        out.append(
            f"**P/E {r['pe']:.1f}** vs. mediana de 5 años {med:.1f} ({r['pe'] / med - 1:+.0%}): más caro que el {pct:.0%} de los días → {nivel}."
        )

    total = alcistas + bajistas
    if total:
        out.append(
            f"**En resumen:** {alcistas} de {total} señales de tendencia (media 200, cruce de medias y MACD) son alcistas"
            + (" — todas apuntan para arriba." if bajistas == 0 else " — todas apuntan para abajo." if alcistas == 0 else " — señales mixtas.")
        )
    return out


GUIA_GRAFICO = """
**Panel de precio**
- **Línea oscura:** el precio.
- **Naranja:** media de 50 ruedas (tendencia de corto/mediano plazo).
- **Azul:** media de 200 ruedas (tendencia de largo plazo).
- Precio **arriba** de la azul = tendencia alcista; **debajo** = bajista.
- Naranja cruza la azul **hacia arriba** = *cruce dorado* (alcista). **Hacia abajo** = *cruce de la muerte* (bajista).

**Volumen**
- Barra **verde** = ese día subió; **roja** = bajó. La línea gris es el promedio de 20 ruedas.
- Barras muy altas = días con mucho movimiento: confirman la suba o la baja.

**RSI (violeta)**
- Va de 0 a 100. Las líneas punteadas marcan 30 y 70.
- **Arriba de 70:** sobrecompra (subió mucho, rápido). **Abajo de 30:** sobreventa (bajó mucho, rápido).

**MACD**
- **Línea oscura** = MACD; **naranja** = su señal.
- Oscura cruza la naranja **hacia arriba** = impulso alcista; **hacia abajo** = impulso bajista.
- Barras **verdes** = impulso alcista; **rojas** = bajista. Si se achican, el impulso se está agotando.

**P/E histórico**
- La línea punteada es la mediana de 5 años; la banda gris, los valores habituales.
- **Arriba de la banda:** cara contra su historia. **Debajo:** barata.
"""

def grafico_tecnico(d: pd.DataFrame, nombre: str) -> go.Figure:
    hay_vol = "volumen" in d and d["volumen"].notna().any()
    filas = 4 if hay_vol else 3
    alturas = [0.46, 0.14, 0.2, 0.2] if hay_vol else [0.55, 0.22, 0.23]
    titulos = ["Precio y medias móviles", *(["Volumen"] if hay_vol else []), "RSI (14)", "MACD (12, 26, 9)"]
    fig = make_subplots(rows=filas, cols=1, shared_xaxes=True, vertical_spacing=0.045, row_heights=alturas, subplot_titles=titulos)
    fig.add_trace(go.Scatter(x=d.index, y=d["precio"], name="Precio", line=dict(color=PETROLEO, width=2)), 1, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d["media_corta"], name="Media 50", line=dict(color=COLORES[1], width=2)), 1, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d["media_larga"], name="Media 200", line=dict(color=COLORES[0], width=2)), 1, 1)
    f = 2
    if hay_vol:
        sube = d["precio"].diff().fillna(0) >= 0
        fig.add_trace(
            go.Bar(x=d.index, y=d["volumen"], name="Volumen", marker_color=np.where(sube, "#8fc9a8", "#e6a3a3"),
                   showlegend=False, hovertemplate="%{y:,.0f}<extra>Volumen</extra>"), f, 1,
        )
        fig.add_trace(go.Scatter(x=d.index, y=d["volumen_prom"], name="Prom. 20", line=dict(color=GRIS_BENCH, width=1.5), showlegend=False), f, 1)
        f += 1
    fig.add_trace(go.Scatter(x=d.index, y=d["rsi"], name="RSI", line=dict(color=COLORES[6], width=2), showlegend=False), f, 1)
    for nivel in (30, 70):
        fig.add_hline(y=nivel, line=dict(color=GRIS_BENCH, width=1, dash="dot"), row=f, col=1)
    fig.update_yaxes(range=[0, 100], row=f, col=1)
    f += 1
    fig.add_trace(
        go.Bar(x=d.index, y=d["histograma"], name="Histograma", showlegend=False,
               marker_color=np.where(d["histograma"] >= 0, "#8fc9a8", "#e6a3a3")), f, 1,
    )
    fig.add_trace(go.Scatter(x=d.index, y=d["macd"], name="MACD", line=dict(color=PETROLEO, width=2), showlegend=False), f, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d["senal"], name="Señal", line=dict(color=COLORES[1], width=1.5), showlegend=False), f, 1)
    fig = estilo(fig, nombre, 820 if hay_vol else 700)
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0.35), bargap=0)
    fig.update_annotations(font=dict(size=13, color=TINTA_2), x=0, xanchor="left")
    return fig


def grafico_pe(pe: pd.Series, nombre: str) -> go.Figure:
    ventana = pe[pe.index >= pe.index[-1] - pd.DateOffset(years=5)]
    med = ventana.median()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pe.index, y=pe, name="P/E", line=dict(color=PETROLEO, width=2),
                             hovertemplate="P/E %{y:.1f}<extra></extra>"))
    fig.add_hline(y=med, line=dict(color=COLORES[1], width=2, dash="dash"),
                  annotation_text=f"Mediana 5 años: {med:.1f}", annotation_position="top left")
    fig.add_hrect(y0=ventana.quantile(0.25), y1=ventana.quantile(0.75), fillcolor="#27515e", opacity=0.07, line_width=0)
    fig = estilo(fig, f"P/E histórico — {nombre} (banda: rango del 50% central de 5 años)", 380)
    fig.update_layout(showlegend=False)
    return fig

# --- pestaña Agregar ------------------------------------------------------------------------


def pestania_agregar() -> None:
    st.subheader("Agregar o actualizar instrumentos")
    st.markdown(
        "Subí un Excel o CSV: **primera columna la fecha y una columna por instrumento** con su "
        "precio o cuotaparte. Para ON y bonos con precio *limpio*, agregá una hoja **Pagos** "
        "(Fecha, Instrumento, Monto) con los cupones y amortizaciones. Si el instrumento ya "
        "existe, se suman los precios nuevos y se pisan las fechas repetidas."
    )
    try:
        with open("plantilla/plantilla_carga.xlsx", "rb") as f:
            st.download_button("⬇️ Descargar plantilla de carga", f.read(), "plantilla_carga.xlsx")
    except FileNotFoundError:
        pass

    archivos = st.file_uploader(
        "Archivos de precios (podés subir varios a la vez, ej. un CSV por acción)",
        type=["xlsx", "xls", "csv"], accept_multiple_files=True,
    )
    if not archivos:
        return
    datos = [(a.name, a.getvalue()) for a in archivos]
    hoja = None
    hojas = hojas_excel(*datos[0]) if len(datos) == 1 else []
    if len(hojas) > 1:
        hoja = st.selectbox("Hoja a usar", hojas, index=hojas.index(hoja_sugerida(hojas)),
                            help="Si hay una hoja de precios ajustados, conviene esa.")
    try:
        precios, pagos, avisos = leer_varios(datos, hoja)
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude leer el archivo: {e}")
        return
    clave_editor = "_".join(sorted(a.name for a in archivos))[:80] + str(hoja)
    for a in avisos:
        st.warning(a)
    st.success(
        f"Leí {precios.shape[1]} columna(s) de precios, del {precios.index.min():%d/%m/%Y} "
        f"al {precios.index.max():%d/%m/%Y}" + (f", y {len(pagos)} pagos." if len(pagos) else ".")
    )

    c1, c2, c3 = st.columns(3)
    cat_def = c1.selectbox("Categoría (para los nuevos)", list(calc.TODAS_CATEGORIAS), format_func=calc.TODAS_CATEGORIAS.get)
    mon_def = c2.selectbox("Moneda (para los nuevos)", ["USD", "ARS"])
    precio_def = c3.selectbox("Tipo de precio (para los nuevos)", list(PRECIOS), format_func=PRECIOS.get)

    benches_existentes = candidatos_benchmark()
    filas = pd.DataFrame(
        {
            "columna": list(precios.columns),
            "id": [limpiar_id(c) for c in precios.columns],
            "nombre": [POR_ID.get(limpiar_id(c), {}).get("nombre", str(c)) for c in precios.columns],
            "categoria": [POR_ID.get(limpiar_id(c), {}).get("categoria", cat_def) for c in precios.columns],
            "moneda": [POR_ID.get(limpiar_id(c), {}).get("moneda", mon_def) for c in precios.columns],
            "benchmark": [POR_ID.get(limpiar_id(c), {}).get("benchmark") or "" for c in precios.columns],
            "precio": [POR_ID.get(limpiar_id(c), {}).get("precio", precio_def) for c in precios.columns],
            "estado": ["actualiza" if limpiar_id(c) in POR_ID else "nuevo" for c in precios.columns],
            "precios": [int(precios[c].notna().sum()) for c in precios.columns],
        }
    )
    ids_subidos = list(filas["id"])
    editado = st.data_editor(
        filas,
        hide_index=True,
        width="stretch",
        disabled=["columna", "estado", "precios"],
        column_config={
            "id": st.column_config.TextColumn("ID / ticker", help="Identificador único (ej. AAPL, GD30, MEP)."),
            "categoria": st.column_config.SelectboxColumn(
                options=list(calc.TODAS_CATEGORIAS), required=True,
                help="acciones · fci · on · bonos · benchmark · tipo_cambio",
            ),
            "moneda": st.column_config.SelectboxColumn(options=["USD", "ARS"], required=True),
            "benchmark": st.column_config.SelectboxColumn(
                options=["", *sorted(set(benches_existentes) | set(ids_subidos))],
                help="Contra qué se compara. Si lo cargás en este mismo archivo, ponele categoría 'benchmark'.",
            ),
            "precio": st.column_config.SelectboxColumn(options=list(PRECIOS)),
        },
        key=f"editor_{clave_editor}_{cat_def}_{mon_def}_{precio_def}",
    )
    limpios = [r for r in editado.to_dict("records") if r["categoria"] not in {"benchmark", "tipo_cambio"}]
    if any(r["categoria"] in {"on", "bonos"} and r["precio"] == "limpio" for r in limpios) and pagos.empty:
        st.warning(
            "Marcaste ON/bonos con precio limpio pero el archivo no tiene hoja Pagos: los cobros de "
            "cupón se van a ver como caídas de precio."
        )
    if any(r["moneda"] == "ARS" for r in limpios) and not tipos_cambio and "tipo_cambio" not in set(editado["categoria"]):
        st.info("Tip: cargá también el dólar MEP (categoría tipo_cambio) para poder comparar pesos contra dólares.")

    if st.button("💾 Guardar", type="primary"):
        ids = [limpiar_id(i) for i in editado["id"]]
        if len(set(ids)) != len(ids):
            st.error("Hay IDs repetidos. Cada instrumento necesita un ID distinto.")
            return
        with st.spinner("Guardando…"):
            try:
                archivos, _, resumen = cat.preparar_alta(
                    ALM, VERSION, INDICE, editado.to_dict("records"), precios, pagos
                )
                ALM.guardar(archivos, [], "Datos: alta/actualización de " + ", ".join(ids))
            except Exception as e:  # noqa: BLE001
                st.error(f"No se pudo guardar: {e}")
                return
        refrescar()
        st.success("Guardado. " + " ".join(resumen))
        st.balloons()


# --- pestaña Administrar ------------------------------------------------------------------


def pestania_administrar() -> None:
    st.subheader("Instrumentos cargados")
    if not INDICE:
        st.info("Todavía no hay instrumentos.")
        return
    df = pd.DataFrame(INDICE)
    df["benchmark"] = df["benchmark"].fillna("")
    df["borrar"] = False
    df["fuente"] = df.get("fuente", pd.Series([None] * len(df))).map(
        lambda f: f"{f.get('tipo')}{': ' + f['ticker'] if f.get('ticker') else ''}" if isinstance(f, dict) else "manual"
    )
    cols = ["id", "nombre", "categoria", "moneda", "benchmark", "precio", "fuente", "desde", "hasta", "borrar"]
    editado = st.data_editor(
        df[cols],
        hide_index=True,
        width="stretch",
        disabled=["id", "fuente", "desde", "hasta"],
        column_config={
            "categoria": st.column_config.SelectboxColumn(options=list(calc.TODAS_CATEGORIAS), required=True),
            "moneda": st.column_config.SelectboxColumn(options=["USD", "ARS"], required=True),
            "benchmark": st.column_config.SelectboxColumn(options=["", *candidatos_benchmark()]),
            "precio": st.column_config.SelectboxColumn(options=list(PRECIOS)),
            "borrar": st.column_config.CheckboxColumn("🗑️ borrar"),
        },
        key="admin",
    )
    a_borrar = set(editado.loc[editado["borrar"], "id"])
    if a_borrar:
        st.warning("Se van a borrar (con sus precios): " + ", ".join(sorted(a_borrar)))
    if st.button("💾 Guardar cambios", type="primary"):
        archivos, borrar, _ = cat.preparar_edicion(INDICE, editado.to_dict("records"), a_borrar)
        with st.spinner("Guardando…"):
            try:
                ALM.guardar(archivos, borrar, "Datos: edición del catálogo")
            except Exception as e:  # noqa: BLE001
                st.error(f"No se pudo guardar: {e}")
                return
        refrescar()
        st.success("Cambios guardados.")
        st.rerun()

    st.divider()
    st.subheader("Agregar desde Yahoo Finance")
    st.caption(
        "Escribí uno o varios tickers separados por coma. Acciones de EE.UU. tal cual (KO, JPM), "
        "CEDEARs y acciones argentinas con .BA (AAPL.BA, GGAL.BA, en pesos), índices con ^ (^MERV). "
        "Quedan marcados para actualizarse solos."
    )
    with st.form("yahoo"):
        y1, y2, y3, y4 = st.columns([3, 2, 1, 2])
        tickers = y1.text_input("Tickers", placeholder="KO, PEP, AAPL.BA")
        categoria = y2.selectbox("Categoría", list(calc.TODAS_CATEGORIAS), format_func=calc.TODAS_CATEGORIAS.get)
        moneda = y3.selectbox("Moneda", ["Auto", "USD", "ARS"], help="Auto: .BA en pesos, el resto en dólares.")
        bench = y4.selectbox("Benchmark", ["", *candidatos_benchmark()])
        if st.form_submit_button("⬇️ Bajar y guardar") and tickers.strip():
            for t in [x.strip().upper() for x in tickers.split(",") if x.strip()]:
                mon = moneda if moneda != "Auto" else ("ARS" if t.endswith(".BA") else "USD")
                try:
                    with st.spinner(f"Bajando {t}…"):
                        msg = alta_desde_fuente(
                            ALM, t.replace("^", "IDX_"), t, categoria, mon, bench or None, "ajustado",
                            {"tipo": "yahoo", "ticker": t},
                        )
                    st.success(msg)
                except Exception as e:  # noqa: BLE001
                    st.error(f"{t}: {e}")
            refrescar()

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Benchmark de rendimiento objetivo")
        st.caption("Para fondos con meta de retorno (ej. 'retorno absoluto 4%–5% anual en USD').")
        with st.form("objetivo"):
            nombre = st.text_input("Nombre", "Meta 5% anual USD")
            iid = st.text_input("ID", "META_USD_5")
            tasa = st.number_input("Tasa anual (%)", 0.0, 1000.0, 5.0, 0.5)
            moneda = st.selectbox("Moneda", ["USD", "ARS"])
            if st.form_submit_button("➕ Crear benchmark"):
                archivos, _ = cat.preparar_objetivo(INDICE, iid, nombre, tasa / 100, moneda)
                ALM.guardar(archivos, [], f"Datos: benchmark objetivo {iid}")
                refrescar()
                st.success("Creado.")
                st.rerun()
    with c2:
        st.subheader("Calendario de pagos de ON")
        cal = calendario_on(VERSION)
        st.caption(f"Cargado: {len(cal)} ON." if cal else "Todavía no hay calendario cargado.")
        archivo_cal = st.file_uploader("Subir on_data.json (reemplaza el actual)", type=["json"])
        if archivo_cal and st.button("💾 Guardar calendario"):
            try:
                nuevo = json.loads(archivo_cal.getvalue().decode("utf-8"))
                assert isinstance(nuevo, dict)
            except Exception:  # noqa: BLE001
                st.error("El archivo no tiene el formato esperado ({ TICKER: {vencimiento, cupones} }).")
            else:
                ALM.guardar({cat.CALENDARIO_ON: cat.calendario_a_texto(nuevo)}, [], "Datos: calendario de pagos de ON")
                refrescar()
                st.success(f"Guardado ({len(nuevo)} ON).")
                st.rerun()


# --- página ---------------------------------------------------------------------------------

_hasta = max((i.get("hasta") or "") for i in INDICE) if INDICE else ""
st.markdown(
    f'''<div class="cab"><div class="logo">↗</div><div><h1>Comparador · Riesgo y rendimiento</h1>
    <div class="sub">{len([i for i in INDICE if i["categoria"] in calc.CATEGORIAS])} instrumentos
    {"· datos al " + pd.Timestamp(_hasta).strftime("%d/%m/%Y") if _hasta else ""}</div></div></div>''',
    unsafe_allow_html=True,
)
pestanias = st.tabs([*calc.CATEGORIAS.values(), "📈 Valuación y tendencia", "➕ Agregar", "⚙️ Administrar"])
for tab, categoria in zip(pestanias, calc.CATEGORIAS):
    with tab:
        pestania_categoria(categoria)
with pestanias[len(calc.CATEGORIAS)]:
    pestania_tecnica()
with pestanias[-2]:
    pestania_agregar()
with pestanias[-1]:
    pestania_administrar()
