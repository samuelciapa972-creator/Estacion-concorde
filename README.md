# Facturación estación de servicio · banco de pruebas

Importador y normalizador de los despachos del surtidor **Speed Solutions**
(el formato **Wayne** se agrega como un parser más, sin tocar el resto).

## Uso

Estructura: `backend/` (Python 3.12, FastAPI, Alembic), `web/` (React + Vite + TypeScript, PWA) e `infra/`
(docker-compose y Caddy). Todo se maneja desde la raíz con `make` (la lista completa está al inicio del `Makefile`).
Requisitos: [uv](https://docs.astral.sh/uv/) (instala Python 3.12), Node 22 y Docker.

```bash
make install        # backend/.venv y web/node_modules
make up             # PostgreSQL 16 del compose + migraciones de Alembic
make test           # backend (incluidas las de integración contra ese PostgreSQL) y web (Vitest)
make lint           # ruff + mypy (backend), eslint + tsc + tipos del OpenAPI al día (web)
make test-rapido    # backend sin PostgreSQL: las pruebas de integración se saltan

# Desarrollo (cada uno en su terminal). La API necesita JWT_SECRETO (ver .env.example).
make api            # http://127.0.0.1:8000/docs
make web            # http://localhost:5173: /pista (bombero), /registro (QR público), /admin/... (administrador)
make simulador      # surtidor SIMULADO: un despacho cada 20 s a la base de desarrollo
make worker         # cola de envíos con el proveedor SIMULADO (nada sale a la DIAN)
make tipos          # tras cambiar la API: regenera web/src/api/esquema.ts desde el OpenAPI

# Usuarios (PIN con hash; se pide por consola)
cd backend && .venv/bin/python -m estacion.usuarios crear --usuario admin1 --nombre "Admin Uno" --rol ADMIN

# Importador del surtidor Speed Solutions (desde backend/)
.venv/bin/python -m estacion.cli speed_solutions D260928_084701.xls              # informe en seco
.venv/bin/python -m estacion.cli speed_solutions D260928_084701.xls \
    --dsn postgresql://estacion:estacion@localhost/estacion                     # carga idempotente
ARCHIVO_REAL=D260928_084701.xls .venv/bin/python -m pytest -k real              # el archivo NUNCA va al repo

# Todo en contenedores, como quedaría en el servidor de la estación (HTTPS local con Caddy)
make stack          # o make stack-simulador; ver docs/despliegue.md
```

## Estructura

| Ruta | Qué hace |
|---|---|
| `backend/migrations/` | Esquema PostgreSQL (Alembic): 0001 despachos y catálogos, 0002 facturación, 0003 simulador y "una factura por venta" con venta manual enlazada |
| `backend/src/estacion/parsers/speed_solutions.py` | XML → `Despacho` normalizado (streaming; fila dañada se rechaza, archivo truncado aborta todo) |
| `normalizacion.py` | Placa (válida / sin dato / nombre de equipo), kilometraje, forma de pago |
| `calidad.py` | Duplicados, fechas malas del reloj, valor vs volumen, continuidad de contadores, crédito sin placa, turnos |
| `carga.py` | Carga transaccional e idempotente a PostgreSQL; continuidad de contadores también contra lo ya guardado |
| `surtidores/` | `FuenteDespachos` (leer/confirmar), `ArchivoSpeedSolutions`, `SimuladorSurtidor` e `ingesta` (calidad + carga) |
| `simulador.py` | Comando del simulador; retoma ids, turno y contadores de la corrida anterior |
| `servicios/` | Numeración atómica, emisión, cola de envíos (`outbox`), ventas manuales y pendientes, clientes, vehículos, consulta de ventas, reportes desde la base e importación CSV |
| `worker.py` | Procesa la cola de envíos (hoy solo con el proveedor simulado) |
| `api/` | FastAPI: ingreso por PIN (JWT corto, bloqueo por intentos), pista, clientes, facturas, reportes, administración y registro público por QR (límite de tasa y captcha opcional) |
| `usuarios.py` | Crear usuarios, cambiar PIN, desbloquear y desactivar (`python -m estacion.usuarios`) |
| `reportes.py` | Excel de ventas (Diario, Mensual, Turnos, Datos, Alertas) |
| `facturacion/` | Interfaz `ProveedorFacturacion`, proveedor simulado, validación de clientes y generador del XML (UBL 2.1) **sin firma**, caso acotado. CUFE sin verificar. **Contexto, plan y reglas: ver `CLAUDE.md`** |
| `web/src/paginas/bombero/` | Pendientes, venta manual, cliente (con registro rápido y autorización), pago, resumen, emitir y estado |
| `web/src/paginas/publico/` | Autorregistro por QR |
| `web/src/paginas/admin/` | Búsqueda de ventas (filtros de Nexus, totales por manguera), reportes con Excel, clientes y vehículos (baja lógica, importación CSV) y anomalías |
| `infra/` | `docker-compose.yml` (db, migraciones, api, worker, Caddy, simulador) y Caddy (HTTPS local + PWA + `/api`) |

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

- `carga.py` y las migraciones ya corren contra PostgreSQL 16 (`backend/tests/test_carga_pg.py`: archivo repetido,
  ventanas solapadas, conflicto de contenido, atomicidad, turnos). El export real cargó 4.095 despachos.
- La corrección de fechas elige el bloque "bueno" por tamaño. Con ventanas muy pequeñas puede elegir mal;
  en ese caso no inventa nada y deja la anomalía en severidad ALTA. Mejora prevista: anclar a los últimos
  despachos ya guardados en la base y a la hora de exportación que trae el nombre del archivo.
- Una placa pertenece a un solo cliente a la vez (sin histórico de dueños).
- Corrige el reloj del surtidor: es la causa raíz de las fechas malas.
