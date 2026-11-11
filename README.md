# Panel de licitaciones activas · Región de O'Higgins

Panel público de las licitaciones activas de Mercado Público en la Región de O'Higgins: mapa por comuna, distribución por montos, plazos de cierre, organismo comprador, sector, rubro y link directo a la ficha. Elaborado por la Cámara Chilena de la Construcción, Sede Región de O'Higgins (Estudios).

Se actualiza solo, dos veces al día, con GitHub Actions. No requiere descargar ni cargar planillas.

## Qué muestra

- Indicadores: licitaciones vigentes, cierres en 7 días o menos, nuevas de los últimos 2 días, rubros afines a construcción, monto informado (suma y mediana) y mayor monto.
- Mapa por comuna, con tamaño según número de licitaciones y anillo naranja cuando hay cierres próximos.
- Gráficos por plazo, tipo de licitación (tramo UTM), tramo de monto, sector, comuna, organismo y rubro. Al hacer clic en una barra o en una comuna del mapa se filtra todo el panel.
- Listado con ID, link directo a la ficha, días restantes, organismo, comuna, sector, rubro y monto. Se puede ordenar y descargar en CSV.

## Cómo funciona

1. `actualizar_licitaciones.py` consulta la API de Mercado Público, toma las licitaciones activas del país, consulta el detalle de las nuevas y se queda con las de la Región de O'Higgins.
2. Los resultados quedan en `docs/data.json` (lo que lee el panel) y `data/cache.json` (memoria para no repetir consultas, cuidar el cupo diario de la API y saber cuáles son nuevas).
3. `docs/index.html` es el panel. GitHub Pages lo publica. El mapa usa la biblioteca Leaflet, incluida en `docs/vendor/leaflet`, y mosaicos de OpenStreetMap.
4. `.github/workflows/actualizar.yml` ejecuta todo automáticamente.

## Configuración (una sola vez)

1. **Ticket de la API:** en el repositorio, `Settings > Secrets and variables > Actions > New repository secret`. Nombre: `MERCADO_PUBLICO_TICKET`. Valor: el ticket personal entregado por ChileCompra. Nunca se escribe en el código.
2. **Publicación:** `Settings > Pages > Source: GitHub Actions`.
3. **Primera ejecución:** pestaña `Actions > Actualizar panel de licitaciones > Run workflow`. La primera vez consulta todas las licitaciones activas del país y puede tardar cerca de una hora; las siguientes son mucho más rápidas.

## Actualizar el panel con una versión nueva

Se suben los archivos nuevos con `Add file > Upload files` (los que tienen el mismo nombre se reemplazan). **No subir `data/cache.json` ni `docs/data.json` si el panel ya se ejecutó alguna vez**, porque reiniciarían la memoria de licitaciones y las marcas de "nueva".

## Ajustes frecuentes

Se hacen en `actualizar_licitaciones.py` (o con variables de entorno en el workflow):

| Qué | Dónde |
|---|---|
| Formato del link a la ficha | `URL_FICHA` |
| Clasificación de sector y ministerio | `REGLAS_SECTOR` |
| Rubros afines a construcción | `AFIN_PREFIJOS` y `AFIN_TEXTO` |
| Horario de actualización | `cron` en `actualizar.yml` (hora UTC) |
| Ubicación de cada comuna en el mapa | lista `COMUNAS` en `docs/index.html` |

## Notas

- Región: se usa la región de la unidad compradora que informa Mercado Público. Una obra en O'Higgins licitada por una unidad de otra región no aparece.
- Montos: muchas licitaciones no publican monto estimado. Los tramos y totales en pesos consideran solo las que lo informan en CLP; las informadas en UF u otras monedas se muestran aparte. Para esas licitaciones, el tipo de licitación (L1, LE, LP...) entrega el tramo en UTM.
- «Sector», «Rubro afín a construcción», «Nueva» y la ubicación en el mapa (centro referencial de la comuna) son cálculos referenciales, no datos oficiales.
- Mapa: los mosaicos de OpenStreetMap son de uso libre con atribución y para un tráfico moderado. Si el panel llegara a tener mucho uso, conviene cambiar a un proveedor de mapas con clave propia.
- Fuente de los datos: Dirección ChileCompra, API de Mercado Público.
