# Facturación estación de servicio · banco de pruebas

Importador y normalizador de los despachos del surtidor **Speed Solutions**
(el formato **Wayne** se agrega como un parser más, sin tocar el resto).

## Uso

Requisitos: [uv](https://docs.astral.sh/uv/) (instala Python 3.12) y Docker.

```bash
make install        # .venv con Python 3.12 y dependencias de desarrollo
make up             # PostgreSQL 16 del docker-compose + migraciones de Alembic
make test           # pruebas, incluidas las de integración contra ese PostgreSQL
make lint           # ruff (lint + formato) y mypy
make test-rapido    # sin PostgreSQL: las pruebas de integración se saltan

# Informe en seco (no necesita base de datos)
.venv/bin/python -m estacion.cli speed_solutions D260928_084701.xls

# Carga a la base de desarrollo (idempotente: repetir el archivo no hace nada)
.venv/bin/python -m estacion.cli speed_solutions D260928_084701.xls \
    --dsn postgresql://estacion:estacion@localhost/estacion

# Surtidor SIMULADO (sin ningún equipo real): un despacho cada 20 s a la base de desarrollo
make simulador                     # o: .venv/bin/python -m estacion.simulador --lado A --cada 20s
.venv/bin/python -m estacion.simulador --cantidad 10 --cada 0 --en-seco   # solo mostrar
make worker                        # cola de envíos con el proveedor SIMULADO (nada sale a la DIAN)

# Prueba contra el export real (el archivo NUNCA va al repositorio)
ARCHIVO_REAL=D260928_084701.xls .venv/bin/python -m pytest -k real
```

## Estructura

| Archivo | Qué hace |
|---|---|
| `migrations/` | Esquema PostgreSQL (Alembic): 0001 despachos y catálogos, 0002 facturación, 0003 simulador y "una factura por venta" con venta manual enlazada |
| `parsers/speed_solutions.py` | XML → `Despacho` normalizado (streaming; fila dañada se rechaza, archivo truncado aborta todo) |
| `normalizacion.py` | Placa (válida / sin dato / nombre de equipo), kilometraje, forma de pago |
| `calidad.py` | Duplicados, fechas malas del reloj, valor vs volumen, continuidad de contadores, crédito sin placa, turnos |
| `carga.py` | Carga transaccional e idempotente a PostgreSQL; continuidad de contadores también contra lo ya guardado |
| `surtidores/` | `FuenteDespachos` (leer/confirmar), `ArchivoSpeedSolutions`, `SimuladorSurtidor` e `ingesta` (calidad + carga) |
| `simulador.py` | Comando del simulador; retoma ids, turno y contadores de la corrida anterior |
| `servicios/` | Numeración atómica, emisión (`solicitar`), cola de envíos (`outbox`), ventas manuales, pendientes y enlace venta manual ↔ despacho |
| `worker.py` | Procesa la cola de envíos (hoy solo con el proveedor simulado) |
| `reportes.py` | Excel de ventas (Diario, Mensual, Turnos, Datos, Alertas): `python -m estacion.reportes speed_solutions archivo.xls -o reporte.xlsx` |
| `facturacion/` | Interfaz `ProveedorFacturacion`, proveedor simulado y validación de clientes. **Contexto, plan y reglas: ver `CLAUDE.md`** |
| `facturacion/xml_factura.py` | Generador del XML de factura electrónica (UBL 2.1 DIAN) **sin firma**, caso acotado; su estructura coincide con una factura real validada. CUFE sin verificar. |

## Hallazgos en el export real (4.095 despachos, 4-jul a 28-sep-2026)

- **El reloj del surtidor grabó el 31-ago como 1-ago** (47 despachos, ids 34631-34677). Sin corregir, el 1-ago
  muestra 95 despachos y el 31-ago cero. Se corrige desplazando 30 días, únicamente porque ese es el único
  número entero de días que encaja entre el bloque anterior y el siguiente.
- 1 despacho con fecha 2005: se interpola entre sus vecinos y queda marcado como **estimación**.
- El valor (`MONEY`) coincide con **volumen bruto × precio** (error máx. 5,3 pesos), no con el neto: se factura el bruto.
- Los `TOT-*` son contadores acumulados por lado; suben exactamente lo despachado en el 100 % de los casos,
  así que cualquier salto delata despachos faltantes (`GAP_TOTALIZADOR`).
- Placa: 3.623 válidas, 357 sin dato (`0`, `1`, `5`...), 115 nombres de maquinaria (`BOBCAT`, `MOTONIVELADORA`...).
- Kilometraje: casi todo es relleno (`0`/`1`), hay negativos y valores absurdos. Solo se conserva si es plausible.
- 18 despachos a crédito sin placa: no se pueden facturar a un cliente.

## Supuestos por confirmar

1. `TOT-*-N` corresponde a la pistola N (en el archivo solo se usa la ranura 1).
2. El turno abierto es el de mayor `ID-CIERRE`; el archivo no trae hora de cierre.
3. Las exportaciones se solapan (ventana móvil): por eso la carga es idempotente por id de despacho.

## Límites conocidos

- `carga.py` y `schema.sql` ya corren contra PostgreSQL 16 (`tests/test_carga_pg.py`: archivo repetido,
  ventanas solapadas, conflicto de contenido, atomicidad, turnos). El export real cargó 4.095 despachos.
- La corrección de fechas elige el bloque "bueno" por tamaño. Con ventanas muy pequeñas puede elegir mal;
  en ese caso no inventa nada y deja la anomalía en severidad ALTA. Mejora prevista: anclar a los últimos
  despachos ya guardados en la base y a la hora de exportación que trae el nombre del archivo.
- Una placa pertenece a un solo cliente a la vez (sin histórico de dueños).
- Corrige el reloj del surtidor: es la causa raíz de las fechas malas.
