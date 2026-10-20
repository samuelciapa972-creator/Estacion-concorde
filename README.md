# Facturación electrónica · EDS Concorde

Sistema de la estación: lectura de despachos de los surtidores, facturación electrónica con motor propio
(XML UBL 2.1, firma XAdES, CUFE) y entrega al cliente por correo, reportes de ventas y pantallas de pista y
administración. La cola de envíos usa por defecto el proveedor **simulado** (nada sale a la DIAN); con
`--proveedor dian` envía al ambiente de **habilitación** de la DIAN (producción está bloqueada en el código).

## Uso

Estructura: `backend/` (Python 3.12, FastAPI, Alembic), `Frontend/` (React + Vite, PWA) e `infra/`
(docker-compose y Caddy). Todo se maneja desde la raíz con `make` (la lista completa está al inicio del `Makefile`).
Requisitos: [uv](https://docs.astral.sh/uv/) (instala Python 3.12), Node 22 y Docker.

```bash
make install        # backend/.venv y Frontend/node_modules
make up             # PostgreSQL 16 del compose + migraciones de Alembic
make test           # backend (incluidas las de integración contra ese PostgreSQL) y Frontend (Vitest + compilado)
make lint           # ruff + mypy (backend) y eslint (Frontend)
make test-rapido    # backend sin PostgreSQL: las pruebas de integración se saltan

# Desarrollo (cada uno en su terminal). La API necesita JWT_SECRETO (ver .env.example).
make api            # http://127.0.0.1:8000/docs
make web            # http://localhost:5173 (usa la API de `make api` bajo /api)
make simulador      # surtidor SIMULADO: un despacho cada 20 s a la base de desarrollo
make worker         # cola de envíos con el proveedor SIMULADO (nada sale a la DIAN)
make mail           # buzón de PRUEBA (Mailpit) en http://localhost:8025
make certificado    # certificado de PRUEBA para firmar (datos/certificado-prueba.pfx, fuera de git)
make tipos          # tras cambiar la API: regenera Frontend/openapi.json (lo verifica test_openapi)

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

## Entrega de facturas por correo

Cada factura FACTURADA se entrega en un correo con un único ZIP (AttachedDocument firmado + PDF).
En desarrollo llega a Mailpit; nada sale a internet.

1. `make mail` y `make certificado` (una vez). En el `.env`: `DIAN_CERTIFICADO_RUTA` y `DIAN_CERTIFICADO_CLAVE`.
2. `make worker`. Sin certificado no se envía nada: las facturas esperan en la cola y el log dice qué falta.
3. Emitir una factura y abrir http://localhost:8025.

API: `GET /facturas/{id}` (campo `entrega`), `GET /facturas/{id}/zip`, `POST /facturas/{id}/reenviar` (administrador).

## Habilitación ante la DIAN (set de pruebas)

Requisitos en el `.env`: `DIAN_AMBIENTE=2`, `DIAN_SOFTWARE_ID`, `DIAN_SOFTWARE_PIN`, `DIAN_CLAVE_TECNICA` (la del
rango de pruebas), `DIAN_TEST_SET_ID` (portal de habilitación) y el certificado REAL de la empresa en
`DIAN_CERTIFICADO_RUTA` / `DIAN_CERTIFICADO_CLAVE`. Con las variables cargadas, desde `backend/`:

```bash
.venv/bin/python -m estacion.habilitacion probar-conexion          # 1. certificado, firma y red (no envía nada)
.venv/bin/python -m estacion.habilitacion configurar-numeracion    # 2. SETP 990000000-995000000 activa
.venv/bin/python -m estacion.habilitacion enviar --usuario admin1  # 3. una factura de prueba en cola
.venv/bin/python -m estacion.worker --proveedor dian               # 4. la envía y consulta el resultado
.venv/bin/python -m estacion.habilitacion estado                   # 5. qué respondió la DIAN
```

Después, revisar el portal: el set se aprueba con 1 factura aceptada. Si sigue "En proceso", enviar más con
`enviar --cantidad N`. Si la DIAN rechaza una factura, el error dice la regla: se corrige y se vuelve a facturar
**la misma venta** (queda pendiente) para reutilizar su número; `enviar` crearía otra venta y otro número.

## Windows (sin `make`)

Desde `backend/` con el entorno activo (`.\.venv\Scripts\Activate.ps1`), cargar el `.env` y arrancar la API:

```powershell
Get-Content ..\.env | Where-Object { $_ -match '^\s*[^#\s][^=]*=' } | ForEach-Object {
  $k,$v = $_ -split '=',2; Set-Item -Path "env:$($k.Trim())" -Value $v.Trim().Trim('"') }
$env:PYTHONPATH = "src"
python -m estacion.api --recargar        # en otras terminales: python -m estacion.worker / python -m estacion.simulador
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
| `worker.py` | Procesa la cola de envíos: `--proveedor simulado` (por defecto) o `dian` (habilitación) |
| `api/` | FastAPI: ingreso por PIN (JWT corto, bloqueo por intentos), pista, clientes, facturas, reportes, administración y registro público por QR (límite de tasa y captcha opcional) |
| `usuarios.py` | Crear usuarios, cambiar PIN, desbloquear y desactivar (`python -m estacion.usuarios`) |
| `reportes.py` | Excel de ventas (Diario, Mensual, Turnos, Datos, Alertas) |
| `facturacion/` | Interfaz `ProveedorFacturacion`, proveedor simulado, validación de clientes, XML UBL 2.1, CUFE, firma XAdES (`firma.py`), AttachedDocument, PDF y ZIP de entrega |
| `facturacion/dian/` | Web service de la DIAN: sobre SOAP firmado con WS-Security (`sobre.py`), cliente (`cliente.py`) y `ProveedorDian` (set de pruebas y envío síncrono). WSDL oficial fijado en `tests/fixtures/dian/` |
| `habilitacion.py` | `python -m estacion.habilitacion`: probar conexión, numeración del set, facturas de prueba y estado |
| `entrega/` | Envío del ZIP por SMTP (Mailpit en desarrollo) |
| `Frontend/src/pages/` | Ingreso, pista (`PosVentas`), facturación, ventas, clientes, reportes, usuarios y autorregistro por QR (`Registro`) |
| `Frontend/src/components/`, `lib/`, `services/` | Componentes compartidos, utilidades con pruebas (Vitest) y cliente de la API |
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
