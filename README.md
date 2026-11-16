# Comparador de riesgo y rendimiento

Dashboard para comparar **acciones, fondos (FCI), obligaciones negociables y bonos del Tesoro**
por riesgo y rendimiento. Los cálculos los hace [OpenFactsheet](https://github.com/bagnascotrader/openfactsheet)
(Prof. Andrés A. Bagnasco, licencia Apache-2.0); acá elegimos 14 métricas y las mostramos
en castellano, con gráficos.

## Qué hace

- **Una pestaña por categoría** (acciones, FCI, ON, bonos), cada una con su selector de instrumentos (hasta 8 a la vez).
- **Período:** 1 año, 3 años, 5 años o todo · **Frecuencia:** diaria o mensual.
- **Moneda:** original, todo en USD o todo en ARS (convierte con el tipo de cambio que cargues, ej. MEP).
- **Benchmark:** el de cada instrumento, uno común para todos, o ninguno. Cualquier instrumento cargado puede ser benchmark (ej. el GD30D para las ON).
- **Tasa libre de riesgo** configurable para Sharpe y Sortino.
- Tabla ordenable y descargable, rendimiento acumulado, riesgo vs. rendimiento, caídas desde el máximo y matriz de correlación.
- Avisos cuando los datos no alcanzan: historia corta, precios "suavizados" (cotiza poco), días sin operar.
- **➕ Agregar:** subís un Excel o CSV y queda guardado en este repo (`data/`), como un commit.
- **⚙️ Administrar:** cambiar nombre, categoría, moneda o benchmark de lo cargado, o borrarlo; crear benchmarks de rendimiento objetivo; actualizar el calendario de pagos de ON.

### Las métricas

| Grupo | Métricas |
|---|---|
| ¿Cuánto rindió? | Rendimiento anual (CAGR), rendimiento acumulado, peor año calendario, % de períodos positivos |
| ¿Cuánto riesgo tomó? | Volatilidad anual, caída máxima, duración de la peor caída, CVaR 95% |
| ¿Le pagaron bien por el riesgo? | Sharpe, Sortino, Calmar |
| ¿Cómo se mueve frente al mercado? | Beta, correlación y diferencia anual contra su benchmark |

Para cambiar la lista, editá `METRICAS` en `motor/calculo.py` (OpenFactsheet calcula 102; los IDs están en su manual).

## Datos que ya trae

- **Acciones** (Yahoo, ajustadas por dividendos): AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, AMD, MU, ASML, NU y GLD. Benchmarks: SPY, QQQ y SMH (los semiconductores AMD, MU y ASML se comparan contra SMH; el resto contra SPY).
- **ON** (53, índice ajustado por pagos) contra el GD30D, y **bonos** AL30D y GD30D.
- **FCI** (16 clases) con la referencia de su factsheet. Fima Premium, Gainvest FF y Galileo Ahorro Plus se comparan contra la BADLAR, y Gainvest Balanceado contra el CER. Galileo Income, Event Driven y Multi Strategy tienen benchmark de rendimiento objetivo (4,5%, 8% y 9% anual en USD, el punto medio de su meta).
- **Calendario de pagos de ON** (`data/on_calendario.json`): próximo pago, frecuencia y vencimiento en la pestaña de ON.

## Actualización automática

Cada instrumento puede tener una **fuente** de la que se actualiza solo:

| Fuente | Qué trae | De dónde |
|---|---|---|
| `yahoo` | Acciones, ETFs, CEDEARs (`AAPL.BA`), índices (`^MERV`) | Yahoo Finance (precio ajustado) |
| `bcra` | BADLAR (como índice que capitaliza la tasa día a día) y CER | API del BCRA, Estadísticas Monetarias v4.0 |
| `mep` | Dólar MEP histórico | ArgentinaDatos |
| `ppi` | ON y bonos (índice ajustado por pagos) | API de PPI, `MarketData/Search` (precios históricos) |
| `fci` | Cuotapartes de fondos (VCP) | Planilla diaria pública de CAFCI |

Hay tres formas de actualizar:

1. **Sola, todos los días hábiles a las 20:17 y a las 8:00** (la de la mañana es para los fondos, porque CAFCI publica tarde) con GitHub Actions (`.github/workflows/actualizar.yml`). No necesita configuración: usa el token propio de GitHub. La primera vez andá a la pestaña **Actions** del repo → *Actualizar precios* → **Run workflow**, así se bajan por primera vez el MEP, la BADLAR y el CER (hasta entonces no aparecen en el dashboard).
2. **Cuando quieras desde el dashboard**, con el botón **🔄 Actualizar precios ahora** de la barra lateral.
3. **A mano en tu PC:** `python scripts/actualizar.py` (o `python scripts/actualizar.py AAPL MEP` para algunos).

**ON y bonos (PPI):** PPI da el precio sin ajustar. Para seguir la serie igual que el Excel de
series de ON, el día en que el precio cae por un pago (entre los 3 días hábiles previos a la
fecha del calendario y el siguiente) su variación se toma como 0. En cada corrida se recalculan
los últimos 15 días, así un pago que todavía no había ocurrido se ajusta cuando pasa. Las ON
vencidas quedan con su historia pero ya no se actualizan.

**Claves de PPI** (nunca en el código):
- En GitHub: *Settings → Secrets and variables → Actions → New repository secret*: `PPI_PUBLIC_KEY` y `PPI_PRIVATE_KEY`.
- En Render: las mismas dos variables de entorno (para el botón del dashboard).
- Para probar en tu PC: `PPI_PUBLIC_KEY=... PPI_PRIVATE_KEY=... python scripts/probar_ppi.py RCCRD` (en Windows: `set PPI_PUBLIC_KEY=...` antes). Muestra si el login anda y los últimos precios.

**Fondos (CAFCI):** se baja la planilla diaria una vez por corrida. Se guarda el VCP del último
día y se vuelve a guardar el del día anterior, porque a veces la administradora lo corrige. Si un
fondo cambia de nombre en CAFCI (como Gainvest → Stonex), se agrega el nombre nuevo como `alias`
en su fuente. Si un valor nuevo difiere más de 25% del último guardado, no se guarda y se avisa
(protege contra cambios de escala en la planilla).

Si una fuente falla, las demás se actualizan igual y el informe dice cuál falló. Si la API del BCRA da error de certificado SSL en Render, agregá la variable `BCRA_VERIFY_SSL=0`.

Desde ⚙️ Administrar → **Agregar desde Yahoo Finance** escribís tickers (ej. `KO, PEP, AAPL.BA`), se bajan con toda su historia y quedan marcados para actualizarse solos.

## Cómo cargar datos

Desde ➕ Agregar se pueden subir directamente:

- **CSV de Yahoo Finance** (uno o varios a la vez, ej. `AAPL_1d.csv`): toma el *Adj Close* y el ticker del nombre del archivo.
- **Histórico en formato largo** (columnas `fondo, fecha, vcp` o similar): arma una columna por fondo.
- **Excel con varias hojas:** elegís la hoja; si hay una de precios ajustados, se sugiere esa.
- **Plantilla propia:**

Descargá la plantilla desde la pestaña ➕ Agregar (o `plantilla/plantilla_carga.xlsx`):

- **Hoja Precios:** columna A = fecha (dd/mm/aaaa), una columna por instrumento con su precio o cuotaparte. Acepta números con coma o punto decimal.
- **Hoja Pagos** (opcional, ON y bonos con precio *limpio*): Fecha, Instrumento, Monto (cupón + amortización, en la misma unidad que el precio). Sin esto, el cobro de un cupón se ve como una caída.
- **Benchmarks** (SPY, Merval, etc.) y **tipo de cambio** (MEP en pesos por dólar) se suben igual, con categoría `benchmark` o `tipo_cambio`.
- Si volvés a subir un instrumento que ya existe, se suman los precios nuevos y se pisan las fechas repetidas. Así lo podés ir actualizando.

Para acciones, `scripts/bajar_acciones.py` baja de Yahoo Finance las 7 magníficas + SPY + QQQ (o los tickers que le pases) y arma el Excel listo para subir:

```bash
pip install yfinance openpyxl
python scripts/bajar_acciones.py
python scripts/bajar_acciones.py GGAL.BA YPFD.BA ^MERV --salida argentinas.xlsx
```

## Publicarlo en Render

1. **Subí esta carpeta a un repo nuevo de GitHub** (conviene privado). Desde la web: *New repository* → *uploading an existing file* → arrastrás todo el contenido de la carpeta.
2. **Creá un token para que el dashboard pueda guardar en el repo:** GitHub → Settings → Developer settings → Personal access tokens → *Fine-grained tokens* → *Generate new token*. En *Repository access* elegí **solo este repo**, y en *Permissions → Repository permissions → Contents* poné **Read and write**. Copiá el token (se ve una sola vez).
3. **En Render:** *New* → *Blueprint* → conectá el repo. Render lee `render.yaml` y te pide completar:
   - `GITHUB_REPO`: `tu-usuario/nombre-del-repo`
   - `GITHUB_TOKEN`: el token del paso 2
   - `APP_PASSWORD`: una contraseña para entrar al dashboard (si lo dejás vacío, cualquiera con el link puede ver y cargar datos)
4. Esperá a que termine el build (unos minutos) y abrí la URL que te da Render.

Notas:
- En el plan gratis, Render "duerme" la app después de 15 minutos sin uso; la primera carga después tarda alrededor de un minuto.
- Lo que cargás desde el dashboard se guarda como commit en `data/` y **no** dispara un nuevo deploy (`buildFilter` en `render.yaml`).
- Si Render no te ofrece Blueprint, creá un *Web Service* a mano: runtime Python, build `pip install -r requirements.txt`, start `streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`, y las mismas variables de entorno.

## Correrlo en tu PC

```bash
pip install -r requirements.txt
streamlit run app.py
```

Sin `GITHUB_TOKEN` guarda en la carpeta local `data/` (modo prueba).

## Estructura

```
app.py                 dashboard (Streamlit)
motor/calculo.py       rendimientos, monedas, períodos, métricas (OpenFactsheet)
motor/datos.py         lectura de Excel/CSV subidos
motor/catalogo.py      alta, edición y baja de instrumentos
motor/almacen.py       guardado en GitHub (o en disco)
data/instrumentos.json catálogo de instrumentos; precios en data/precios/, pagos en data/pagos/
plantilla/             plantilla de carga
motor/fuentes.py       conectores (Yahoo, BCRA, MEP, PPI, CAFCI)
motor/actualizador.py  baja lo nuevo de cada fuente y lo guarda en un solo commit
scripts/actualizar.py  actualización completa (la usa GitHub Actions)
scripts/probar_ppi.py  prueba de la conexión con PPI
scripts/bajar_acciones.py  bajar precios de Yahoo a un Excel
.github/workflows/     actualización diaria
```

Información educativa: no es recomendación de inversión. Rendimientos pasados no garantizan rendimientos futuros.
