# Salida a producción (EDSC) y notas crédito de anulación — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dejar el sistema listo para emitir en producción con el prefijo EDSC solo cambiando configuración, y permitir que un administrador anule una factura aceptada con una nota crédito.

**Architecture:** Las notas crédito se guardan en la misma tabla `factura` con `tipo = 'NOTA_CREDITO'` y `referencia_id` hacia la factura que anulan, para reutilizar sin duplicar la cola (`outbox`), la firma, el envío a la DIAN y la entrega por correo (`entrega`). Cada documento y cada numeración guardan su `ambiente` ("1" producción, "2" habilitación), y la configuración falla cerrado si no coinciden con `DIAN_AMBIENTE`. La venta de una factura anulada pasa a `NO_FACTURABLE` cuando la DIAN acepta la nota; la corrección es una venta manual nueva.

**Tech Stack:** Python 3 · FastAPI · PostgreSQL 16 · psycopg 3 · Alembic (SQL crudo) · lxml · reportlab · pytest · React (Vite, JS) · vitest.

**Spec:** `docs/superpowers/specs/2026-10-20-produccion-y-notas-credito-design.md`

## Global Constraints

- Dinero en `Decimal`; nunca `float`.
- Mensajes de error y logs sin secretos (PIN, clave técnica, clave del certificado, contraseña SMTP) ni datos personales (cédula, nombre, correo).
- Fallar cerrado: si falta un dato fiscal o la configuración es incoherente, no se emite (`ConfiguracionIncompleta`).
- Las pruebas automáticas **nunca** llaman a la DIAN real: transporte falso como en `tests/test_cliente_dian.py`.
- `DIAN_AMBIENTE=2` (habilitación) debe seguir funcionando igual: los 521 tests actuales siguen en verde.
- Migración nueva `0007`; no se edita ninguna migración existente.
- Prefijos de notas crédito: `NCE` en ambiente "1", `NCP` en ambiente "2"; una serie por ambiente, desde 1, sin huecos.
- `DiscrepancyResponse/ResponseCode` = `2` (Anulación de factura electrónica); `CustomizationID` = `20`; `CreditNoteTypeCode` = `91`; `ProfileID` = `DIAN 2.1: Nota Crédito de Factura Electrónica de Venta`.
- URL del web service: habilitación `https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc`, producción `https://vpfe.dian.gov.co/WcfDianCustomerServices.svc` (verificar contra el anexo técnico al construir).
- Frontend: estilo "papel y tinta" actual, sin color de acento, tipografía sin cambios.
- Commits en Conventional Commits, en español, **sin** líneas `Co-Authored-By` de Claude.
- Entorno de pruebas Linux: venv propio (no usar `backend/.venv` ni `Frontend/node_modules`, son copias de Windows). Backend: `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion <venv>/bin/python -m pytest -q` con `docker compose --project-directory . -f infra/docker-compose.yml up -d --wait db`.

## Decisiones del plan (tomadas al escribirlo; están en "Decisiones que tomé yo")

- **Solo se anulan facturas cuyo origen es una venta manual.** En producción solo hay ventas manuales; anular facturas de despachos del surtidor (con su enlace a ventas manuales) queda para cuando se conecten los surtidores. La API responde 409 con un mensaje claro.
- **Una venta manual anulada deja de contar en los reportes de ventas** (galones y valor): la reemplaza la venta nueva. Sin esto, la venta se contaría dos veces en volumen.
- Una nota rechazada se reintenta pidiendo otra vez la anulación de la misma factura: se reutiliza la fila de la nota y su número (igual que las facturas).

## Review Focus

1. **Doble clic en "Anular"** o dos administradores a la vez: una sola nota por factura (bloqueo de la factura + `UNIQUE (referencia_id)`); la segunda petición con la misma clave devuelve la misma nota. Test en Task 5.
2. **Anular una factura que no está FACTURADO** (EN_PROCESO, INCIERTO, RECHAZADO) o que ya tiene nota aceptada: 409 sin gastar consecutivo NCE. Test en Task 5.
3. **Nota incierta o rechazada**: la venta sigue FACTURADO y la factura sigue vigente; nunca queda la venta liberada antes de la aceptación. Test en Task 6.
4. **Cambiar `DIAN_AMBIENTE` con la numeración del otro ambiente activa** (p. ej. `1` con SETP): no se emite nada, mensaje nombra el problema. Test en Task 2.
5. **Facturas SETP visibles en producción**: no se pueden anular desde producción (ambiente distinto). Test en Task 5.

---

### Task 1: Migración 0007 (ambiente, notas crédito en `factura`, numeración de notas, vistas)

**Files:**
- Create: `backend/migrations/versions/0007_produccion_notas.py`
- Modify: `backend/tests/test_migraciones.py`
- Test: `backend/tests/test_notas_bd.py` (nuevo)

**Interfaces:**
- Produces (esquema):
  - `factura.tipo TEXT NOT NULL DEFAULT 'FACTURA' CHECK (tipo IN ('FACTURA','NOTA_CREDITO'))`
  - `factura.referencia_id BIGINT UNIQUE REFERENCES factura(id)` (la factura que anula la nota)
  - `factura.motivo TEXT` (motivo de la anulación; obligatorio en notas)
  - `factura.ambiente CHAR(1) NOT NULL DEFAULT '2' CHECK (ambiente IN ('1','2'))`; quitar el DEFAULT después de rellenar las filas existentes con `'2'`.
  - `factura.numeracion_id` pasa a admitir NULL.
  - Reemplazar `factura_origen_check` por: FACTURA ⇒ exactamente un origen, `referencia_id IS NULL`, `numeracion_id IS NOT NULL`; NOTA_CREDITO ⇒ sin origen, `referencia_id IS NOT NULL`, `numeracion_id IS NULL`, `btrim(motivo) <> ''`.
  - `numeracion.ambiente CHAR(1) NOT NULL DEFAULT '2' CHECK (ambiente IN ('1','2'))` (mismo tratamiento del DEFAULT).
  - Tabla `numeracion_nota (id SMALLINT identity PK, estacion_id SMALLINT NOT NULL REFERENCES estacion, ambiente CHAR(1) NOT NULL CHECK IN ('1','2'), prefijo TEXT NOT NULL CHECK (prefijo ~ '^[A-Z0-9]{1,4}$'), siguiente BIGINT NOT NULL DEFAULT 1 CHECK (siguiente >= 1), UNIQUE (estacion_id, ambiente), UNIQUE (prefijo))`.
  - `v_ventas_origen` recreada excluyendo ventas manuales cuya factura tiene una nota crédito FACTURADO (`v_ventas_diarias` y `v_ventas_mensuales` se recrean igual que en la 0004 porque dependen de ella). El DOWNGRADE deja las vistas de la 0004 tal cual.
  - Función SQL `factura_anulada(f BIGINT) RETURNS BOOLEAN` = existe nota con `referencia_id = f` y `estado = 'FACTURADO'` (la usan vistas y consultas).

- [ ] **Step 1: Write the failing tests** en `backend/tests/test_notas_bd.py` (marcados `pytest.mark.bd`, usando `escenario`/`datos`):
  - `test_nota_sin_referencia_o_sin_motivo_se_rechaza`: insertar `tipo='NOTA_CREDITO'` sin `referencia_id`, y otra con motivo `'  '` ⇒ `psycopg.errors.CheckViolation`.
  - `test_nota_con_origen_se_rechaza`: nota con `venta_manual_id` ⇒ `CheckViolation`.
  - `test_una_sola_nota_por_factura`: dos notas con el mismo `referencia_id` ⇒ `UniqueViolation`.
  - `test_filas_existentes_quedan_en_habilitacion`: migrar a `0006`, insertar factura y numeración, migrar a `head` ⇒ `ambiente == '2'` en ambas.
  - `test_venta_anulada_no_cuenta_en_ventas`: venta manual con factura FACTURADO + nota FACTURADO ⇒ no aparece en `v_ventas_origen`; con la nota `EN_PROCESO` sí aparece.
  - En `test_migraciones.py`, el test existente de subir/bajar todas las migraciones debe incluir la 0007 (bajar a `0006` y volver a `head` sin error).
- [ ] **Step 2: Run** `pytest tests/test_notas_bd.py tests/test_migraciones.py -q` → FAIL (columna `tipo` no existe).
- [ ] **Step 3: Write `0007_produccion_notas.py`** con `UPGRADE`/`DOWNGRADE` en SQL crudo como la 0005/0006 (`revision = "0007"`, `down_revision = "0006"`). Actualizar `Datos.numeracion` en `conftest.py` solo si hace falta pasar `ambiente`.
- [ ] **Step 4: Run** `pytest -q` completo → PASS (521 + nuevos).
- [ ] **Step 5: Commit** `feat(bd): migración 0007 con ambiente, notas crédito y numeración de notas`

---

### Task 2: Ambiente de producción en configuración, emisión y worker

**Files:**
- Modify: `backend/src/estacion/servicios/configuracion.py`, `backend/src/estacion/servicios/emision.py`, `backend/src/estacion/facturacion/dian/sobre.py`, `backend/src/estacion/worker.py`, `backend/src/estacion/facturacion/firma.py`
- Test: `backend/tests/test_emision.py`, `backend/tests/test_outbox.py`, `backend/tests/test_sobre_dian.py`

**Interfaces:**
- Produces:
  - `servicios/configuracion.py`: `AMBIENTES_PERMITIDOS = {"1", "2"}`; `cargar_configuracion` lee `n.ambiente` y lanza `ConfiguracionIncompleta("la numeración activa es del ambiente X y DIAN_AMBIENTE es Y")` si difieren.
  - `facturacion/dian/sobre.py`: `URL_PRODUCCION = "https://vpfe.dian.gov.co/WcfDianCustomerServices.svc"` y `url_servicio(ambiente: str) -> str` (`"1"`→producción, `"2"`→habilitación, otro → `ValueError`).
  - `facturacion/firma.py`: `es_certificado_de_prueba(cert: Certificado) -> bool` (True si el CN contiene `certificado_prueba.MARCA`).
  - `worker.construir_proveedor(nombre, conn, entorno, certificado)` con ambiente "1" lanza `ConfiguracionIncompleta` si `nombre == "simulado"`, si hay `DIAN_TEST_SET_ID`, o si `es_certificado_de_prueba(certificado)`; usa `ClienteDian(url_servicio(ambiente), certificado)`. El log de arranque dice "PRODUCCIÓN" o "HABILITACIÓN".
  - `emision.ServicioEmision.solicitar` guarda `factura.ambiente = config.software.ambiente`.

- [ ] **Step 1: Write the failing tests**
  - `test_emision.py::test_produccion_con_numeracion_de_habilitacion_no_emite`: entorno con `DIAN_AMBIENTE="1"` y la numeración SETP del escenario (ambiente '2') ⇒ `ConfiguracionIncompleta` y `escenario.siguiente() == 1` (no gasta consecutivo).
  - `test_emision.py::test_produccion_con_numeracion_de_produccion_emite`: numeración con `ambiente='1'`, prefijo `EDSC` ⇒ factura con `ambiente == '1'` y su XML con `<cbc:ProfileExecutionID>1</cbc:ProfileExecutionID>` y QR que empieza por `https://catalogo-vpfe.dian.gov.co/`.
  - Reemplazar en `test_emision.py` el caso parametrizado que hoy espera error con `DIAN_AMBIENTE="1"` (línea ~215) por el caso `"3"` ⇒ `ConfiguracionIncompleta`.
  - `test_outbox.py`: reemplazar `worker.construir_proveedor(... "DIAN_AMBIENTE": "1" ...)` que esperaba error por: `test_produccion_rechaza_simulado`, `test_produccion_rechaza_test_set_id`, `test_produccion_rechaza_certificado_de_prueba` (el fixture `certificado` es de prueba ⇒ error), y `test_produccion_usa_url_de_produccion` (certificado cuyo CN no lleva la marca, generado en el test con `cryptography`; `proveedor._cliente` apunta a `URL_PRODUCCION`).
  - `test_sobre_dian.py::test_url_servicio`: `"1"`→`URL_PRODUCCION`, `"2"`→`URL_HABILITACION`, `"3"`→`ValueError`.
- [ ] **Step 2: Run** esos tests → FAIL.
- [ ] **Step 3: Implement** los cambios de Interfaces. Actualizar docstrings de `worker.py` y `configuracion.py` (ya no dicen "producción bloqueada").
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(dian): permite producción con verificación de ambiente, URL y certificado`

---

### Task 3: Comandos de salida a producción y correo del emisor

**Files:**
- Modify: `backend/src/estacion/habilitacion.py`, `Makefile`
- Test: `backend/tests/test_habilitacion.py`

**Interfaces:**
- Consumes: `url_servicio`, `es_certificado_de_prueba` (Task 2); `ClienteDian.rangos_numeracion(nit, software_id) -> ConsultaRangos` (existe).
- Produces (subcomandos de `python -m estacion.habilitacion`):
  - `configurar-produccion --prefijo EDSC`: exige `DIAN_AMBIENTE=1`; consulta `GetNumberingRange` en producción; busca el rango con ese prefijo; imprime resolución, rango y vigencia; pide escribir el prefijo para confirmar; inserta `numeracion` con `ambiente='1'`, `siguiente=desde`, la marca activa y desactiva la anterior (en una transacción). Si la DIAN devuelve clave técnica distinta de `DIAN_CLAVE_TECNICA`, avisa y no configura. Nunca imprime la clave técnica.
  - `verificar-produccion`: exige `DIAN_AMBIENTE=1`; revisa y lista con ✔/✘: variables DIAN presentes, `DIAN_TEST_SET_ID` vacío, certificado cargable, vigente y no de prueba (días que le quedan), numeración activa de ambiente '1' vigente y con rango disponible, `GetNumberingRange` responde y contiene el prefijo activo, SMTP configurado (`ESTACION_SMTP_USUARIO` no vacío). No envía documentos. Código de salida 0 solo si todo está ✔.
  - `correo-emisor [--nuevo CORREO]`: muestra `estacion.email` actual; con `--nuevo`, valida con el mismo patrón del CHECK de la tabla, pide confirmación, actualiza y audita `EMISOR_CORREO_CAMBIADO` (detalle sin el correo).
  - Makefile: `verificar-produccion` y `configurar-produccion` como atajos (mismo estilo que `habilitacion-probar`).

- [ ] **Step 1: Write the failing tests** (con el `ClienteDian` falso que ya usa `test_habilitacion.py`):
  - `test_configurar_produccion_activa_edsc`: respuesta falsa con rango `EDSC 1-1000000`, confirmación `"EDSC"` ⇒ numeración activa `prefijo='EDSC'`, `ambiente='1'`, la SETP queda `activa=false`, retorno 0.
  - `test_configurar_produccion_sin_confirmar_no_cambia_nada`: confirmación `"no"` ⇒ retorno ≠0 y SETP sigue activa.
  - `test_configurar_produccion_clave_tecnica_distinta`: ⇒ retorno ≠0, salida sin la clave.
  - `test_configurar_produccion_exige_ambiente_1`: con `DIAN_AMBIENTE=2` ⇒ retorno 2.
  - `test_verificar_produccion_todo_bien` ⇒ 0; `test_verificar_produccion_con_test_set_id` ⇒ ≠0 y la salida nombra `DIAN_TEST_SET_ID`; `test_verificar_produccion_certificado_de_prueba` ⇒ ≠0.
  - `test_correo_emisor_cambia_y_audita`: `--nuevo alistaconcordefactutacion@gmail.com` con confirmación ⇒ `estacion.email` cambiado, una fila `EMISOR_CORREO_CAMBIADO`; `--nuevo no-es-correo` ⇒ ≠0 sin cambios.
- [ ] **Step 2: Run** `pytest tests/test_habilitacion.py -q` → FAIL.
- [ ] **Step 3: Implement** los subcomandos en `habilitacion.py` (mismo estilo de `_probar`/`_configurar`, confirmación por la función `confirmar` inyectable que ya existe).
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(dian): comandos configurar-produccion, verificar-produccion y correo-emisor`

---

### Task 4: XML de la nota crédito y CUDE

**Files:**
- Create: `backend/src/estacion/facturacion/xml_nota.py`
- Create: `backend/tests/fixtures/ubl21/maindoc/UBL-CreditNote-2.1.xsd` (del paquete oficial OASIS UBL 2.1 `os-UBL-2.1/xsd/maindoc/`; anotar el origen en `fixtures/ubl21/LEEME.txt`)
- Modify: `backend/src/estacion/facturacion/xml_factura.py` (solo exponer lo que se reutiliza)
- Test: `backend/tests/test_xml_nota.py` (nuevo)

**Interfaces:**
- Produces:
  - `calcular_cude(*, numero, fecha, hora, valor, valor_total, nit_emisor, num_doc_adquirente, pin_software, ambiente, iva=0, inc=0, ica=0) -> str` = SHA-384 de `NumNC + FecNC + HorNC + ValNC + "01" + ValIVA + "04" + ValINC + "03" + ValICA + ValTot + NitOFE + NumAdq + PIN + TipoAmbiente` (valores a 2 decimales; verificar la fórmula contra el anexo 1.9).
  - `@dataclass(frozen=True) ReferenciaFactura(numero: str, cufe: str, fecha_emision: datetime)`.
  - `construir_nota_credito(sol: SolicitudFactura, *, prefijo: str, consecutivo: int, referencia: ReferenciaFactura, motivo: str, emisor: Emisor, software: SoftwareDian) -> FacturaXml` (reutiliza `FacturaXml`; en `cufe` va el CUDE). Raíz `CreditNote` (namespace `urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2`), mismas partes de vendedor/comprador/pago/totales que la factura (extraer helpers comunes de `xml_factura.py` en lugar de copiarlos), `cac:DiscrepancyResponse` (ReferenceID = número de la factura, ResponseCode `2`, Description = motivo), `cac:BillingReference/cac:InvoiceDocumentReference` (ID, UUID `schemeName="CUFE-SHA384"`, IssueDate), líneas `cac:CreditNoteLine` con `cbc:CreditedQuantity`. Sin `sts:InvoiceControl` (las notas no llevan resolución). `UUID schemeName="CUDE-SHA384"`. QR = catálogo del ambiente + CUDE.

- [ ] **Step 1: Write the failing tests**
  - `test_cude_cambia_con_el_pin_y_no_con_la_clave_tecnica`: mismo input con PIN distinto ⇒ CUDE distinto; la función no recibe clave técnica.
  - `test_nota_valida_contra_xsd_creditnote` (fixture `esquema_ubl("CreditNote")`).
  - `test_nota_referencia_la_factura`: `BillingReference/InvoiceDocumentReference/cbc:ID == "EDSC1"`, `cbc:UUID == cufe`, `DiscrepancyResponse/cbc:ResponseCode == "2"`, `cbc:Description == motivo`, `cbc:CustomizationID == "20"`, `cbc:CreditNoteTypeCode == "91"`, `cbc:ID == "NCE1"`.
  - `test_nota_totales_iguales_a_la_factura`: `LegalMonetaryTotal/PayableAmount` = total de la factura.
  - `test_nota_firmada_valida`: `firmar_xml(nota.xml, certificado)` funciona (la firma no depende de la raíz `Invoice`).
- [ ] **Step 2: Run** `pytest tests/test_xml_nota.py -q` → FAIL.
- [ ] **Step 3: Implement** `xml_nota.py` y el refactor mínimo de helpers en `xml_factura.py`.
- [ ] **Step 4: Run** `pytest -q` → PASS (incluye `test_xml_factura.py` intacto).
- [ ] **Step 5: Commit** `feat(dian): XML de nota crédito de anulación con CUDE`

---

### Task 5: Servicio de anulación (numerar, guardar y encolar la nota)

**Files:**
- Create: `backend/src/estacion/servicios/anulacion.py`
- Test: `backend/tests/test_anulacion.py` (nuevo)

**Interfaces:**
- Consumes: `construir_nota_credito`, `ReferenciaFactura` (Task 4); `cargar_configuracion` (Task 2); `leer_origen`, `leer_cliente` de `emision.py`; `encolar`, `auditar`.
- Produces:
  - `PREFIJO_NOTA = {"1": "NCE", "2": "NCP"}`.
  - `asignar_consecutivo_nota(conn, estacion_id: int, ambiente: str) -> tuple[str, int]`: crea la fila de `numeracion_nota` si falta (`ON CONFLICT DO NOTHING`), la bloquea `FOR UPDATE`, devuelve `(prefijo, siguiente)` e incrementa. Debe llamarse dentro de la transacción (mismo control que `asignar_consecutivo`).
  - `class FacturaNoAnulable(Exception)` (mensaje sin datos personales).
  - `@dataclass(frozen=True) NotaRegistrada(id: int, numero: str, estado: EstadoFactura, nueva: bool)`.
  - `ServicioAnulacion(entorno=None, reloj=ahora_local).solicitar(conn, *, clave: str, factura_id: int, usuario_id: int, motivo: str) -> NotaRegistrada`, en UNA transacción: bloquear la factura; idempotente por `clave` (misma factura ⇒ devuelve la nota, `nueva=False`; otra factura ⇒ `ClaveReutilizada`); exigir `tipo='FACTURA'`, `estado='FACTURADO'`, origen `venta_manual`, `ambiente == DIAN_AMBIENTE`, sin nota viva (EN_PROCESO/INCIERTO/FACTURADO); motivo con texto (recortado a 500); si existe nota RECHAZADA de esa factura, reutilizar fila y número (`FACTURA_REENVIADA`-equivalente: auditar `NOTA_REENVIADA`); si no, numerar e insertar `tipo='NOTA_CREDITO'`, `referencia_id`, `motivo`, `cliente_id`/`forma_pago`/`medio_pago`/`total` de la factura, `ambiente`; generar XML; encolar `ENVIAR_DIAN`; auditar `ANULACION_SOLICITADA` con `{"factura": numero, "nota": numero_nota}` (el motivo NO va en la auditoría si puede llevar datos personales: guardar solo su largo). Cualquier error antes del commit no gasta consecutivo.

- [ ] **Step 1: Write the failing tests** (con una factura llevada a FACTURADO por `procesar_pendientes` + `ProveedorSimulado`, como en `test_outbox.py`):
  - `test_anular_crea_nota_en_proceso_y_encola`: nota `NCP1`, `estado='EN_PROCESO'`, `referencia_id` correcto, un trabajo `ENVIAR_DIAN`, la venta sigue `FACTURADO`.
  - `test_misma_clave_devuelve_la_misma_nota` (`nueva is False`, sigue habiendo una sola nota y `siguiente` de `numeracion_nota` no avanzó).
  - `test_otra_clave_con_nota_viva_es_rechazada` ⇒ `FacturaNoAnulable`.
  - `test_no_se_anula_si_no_esta_facturada` (parametrizado EN_PROCESO, INCIERTO, RECHAZADO) ⇒ `FacturaNoAnulable`, sin consumir número.
  - `test_no_se_anula_factura_de_despacho` ⇒ `FacturaNoAnulable`.
  - `test_no_se_anula_factura_de_otro_ambiente`: factura `ambiente='2'` con `DIAN_AMBIENTE="1"` ⇒ `FacturaNoAnulable`.
  - `test_motivo_vacio` ⇒ `ValueError`.
  - `test_series_por_ambiente`: la primera nota en ambiente "1" es `NCE1` aunque ya exista `NCP1`.
- [ ] **Step 2: Run** `pytest tests/test_anulacion.py -q` → FAIL.
- [ ] **Step 3: Implement** `servicios/anulacion.py`.
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(notas): servicio de anulación con numeración NCE/NCP`

---

### Task 6: La cola procesa notas crédito

**Files:**
- Modify: `backend/src/estacion/servicios/outbox.py`, `backend/src/estacion/facturacion/modelos.py`, `backend/src/estacion/facturacion/paquete.py`, `backend/src/estacion/facturacion/dian/proveedor.py`
- Test: `backend/tests/test_outbox_notas.py` (nuevo), `backend/tests/test_proveedor_dian.py`

**Interfaces:**
- Consumes: filas creadas por `ServicioAnulacion` (Task 5).
- Produces:
  - `SolicitudFactura.tipo_documento: Literal["fv", "nc"] = "fv"` (último campo, con default).
  - `paquete.nombre_archivo` acepta prefijo `"nc"`.
  - `ProveedorDian._zip` nombra el XML interno con `solicitud.tipo_documento` (`nc…xml` para notas; el ZIP sigue con `z`).
  - En `outbox.py`: `_Factura` gana `tipo: str` y `referencia_id: int | None`, y `origen: Origen | None` (None en notas). `_solicitud` de una nota toma líneas, cliente y forma de pago de la factura referenciada. `_marcar` no toca ningún origen cuando `f.origen is None`. `_aceptada` de una nota: además pone `NO_FACTURABLE` el origen de la factura referenciada y audita `FACTURA_ANULADA` en la factura (`{"nota": numero}`). Rechazo, incierto y consulta de una nota solo cambian la fila de la nota. La entrega (ENVIAR_CORREO) funciona igual.

- [ ] **Step 1: Write the failing tests**
  - `test_nota_aceptada_anula_y_cierra_la_venta`: tras procesar, nota `FACTURADO`, venta `NO_FACTURABLE`, auditoría `FACTURA_ANULADA`, se encoló `ENVIAR_CORREO` para la nota.
  - `test_nota_rechazada_no_libera_la_venta`: `falla_siguiente="rechazo"` ⇒ nota `RECHAZADO`, venta sigue `FACTURADO`, y `ServicioAnulacion.solicitar` con otra clave reutiliza la fila y el número `NCP1`.
  - `test_nota_incierta_no_toca_la_venta`: `timeout_antes` ⇒ nota `INCIERTO`, venta `FACTURADO`.
  - `test_proveedor_recibe_nc_en_el_nombre`: en `test_proveedor_dian.py`, el ZIP enviado contiene un archivo que empieza por `nc`.
  - `test_correo_de_la_nota_se_entrega`: con `EnviadorEnMemoria`, un mensaje cuyo adjunto contiene el AttachedDocument de la nota.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** los cambios de Interfaces.
- [ ] **Step 4: Run** `pytest -q` → PASS (todos los de `test_outbox.py` intactos).
- [ ] **Step 5: Commit** `feat(notas): la cola firma, envía, concilia y entrega notas crédito`

---

### Task 7: PDF, asunto, AttachedDocument y respuesta simulada para notas

**Files:**
- Modify: `backend/src/estacion/facturacion/representacion.py`, `backend/src/estacion/facturacion/paquete.py`, `backend/src/estacion/facturacion/attached.py`, `backend/src/estacion/facturacion/respuesta_simulada.py`, `backend/src/estacion/entrega/correo.py`
- Test: `backend/tests/test_representacion.py`, `backend/tests/test_paquete.py`, `backend/tests/test_attached.py`, `backend/tests/test_respuesta_simulada.py`

**Interfaces:**
- Produces:
  - `representacion.leer_xml(xml)` acepta raíz `CreditNote`: `DatosRepresentacion` gana `tipo: Literal["FACTURA","NOTA_CREDITO"]`, `factura_referencia: str | None`, `motivo: str | None`; en notas los campos de resolución quedan vacíos.
  - `generar_pdf` de una nota: título "NOTA CRÉDITO ELECTRÓNICA", línea "Anula la factura EDSC… — Motivo: …", etiqueta "CUDE" en lugar de "CUFE". La marca "SIN VALIDEZ FISCAL" sigue dependiendo del ambiente.
  - `paquete.asunto` usa `CreditNoteTypeCode` (`91`) cuando la raíz es `CreditNote`; `paquete.armar` nombra el PDF con `nc` para notas.
  - `attached.construir_attached` y `respuesta_simulada.generar_respuesta` aceptan una nota (si ya lo hacen, solo se agrega el test).
  - `correo.mensaje_factura`: el texto dice "nota crédito" cuando el asunto es de una nota (parámetro `tipo_documento: Literal["fv","nc"] = "fv"`).

- [ ] **Step 1: Write the failing tests**: `test_pdf_de_nota_dice_nota_credito_y_la_factura_anulada` (texto extraído del PDF contiene `NOTA CRÉDITO`, `EDSC1`, `CUDE`); `test_asunto_de_nota_lleva_91`; `test_attached_de_una_nota_valida_contra_xsd`; `test_respuesta_simulada_de_una_nota`; `test_correo_de_nota_dice_nota_credito`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(notas): PDF, asunto y contenedor de la nota crédito`

---

### Task 8: API de anulación y salida con tipo y estado de anulación

**Files:**
- Modify: `backend/src/estacion/api/rutas/facturas.py`, `backend/src/estacion/api/esquemas.py`, `backend/src/estacion/servicios/consulta_facturas.py`, `backend/src/estacion/api/app.py` (inyección del servicio, igual que `ServicioEmision`), `Frontend/openapi.json` (regenerado como hoy)
- Test: `backend/tests/test_api_anulacion.py` (nuevo), `backend/tests/test_openapi.py`

**Interfaces:**
- Consumes: `ServicioAnulacion`, `FacturaNoAnulable`, `ClaveReutilizada` (Task 5).
- Produces:
  - `POST /facturas/{factura_id}/anular` (solo `SesionAdmin`), cuerpo `AnulacionEntrada {clave: str (mismo patrón que FacturaEntrada.clave), motivo: str (1–500, recortado)}`; 202 con `FacturaSalida` de la nota; 404 si no existe; 409 `FacturaNoAnulable`/`ClaveReutilizada` con mensaje; 422 validación; 503 `ConfiguracionIncompleta` (mismo manejo que `emitir`).
  - `FacturaSalida` gana `tipo: Literal["FACTURA","NOTA_CREDITO"]`, `ambiente: Literal["1","2"]`, `referencia: str | None` (número de la factura que anula), `anulada_por: str | None` (número de la nota FACTURADO que la anula), `anulable: bool` (FACTURADO, tipo FACTURA, origen venta manual, ambiente actual, sin nota viva).
  - `FacturaListada` y `GET /facturas` incluyen notas y los mismos cuatro campos; filtro opcional `tipo`.

- [ ] **Step 1: Write the failing tests**: `test_admin_anula_y_el_worker_completa` (POST ⇒ 202 `tipo == "NOTA_CREDITO"`, `referencia == numero`; worker; GET factura ⇒ `anulada_por == "NCP1"`, `anulable is False`); `test_bombero_no_puede_anular` ⇒ 403; `test_doble_clic_misma_clave` ⇒ misma nota; `test_motivo_vacio_422`; `test_factura_de_despacho_409`; `test_listado_incluye_la_nota`; `test_pdf_y_zip_de_la_nota` (las rutas existentes `/facturas/{id}/pdf` y `/zip` sirven para la nota); openapi actualizado.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(api): anulación de facturas con nota crédito (solo administrador)`

---

### Task 9: Reportes y exportación al contador

**Files:**
- Modify: `backend/src/estacion/servicios/exportacion_contador.py`, `backend/src/estacion/servicios/reportes_bd.py` (solo si alguna consulta lee `factura` directamente), `backend/src/estacion/servicios/reportes_pdf.py` (ídem)
- Test: `backend/tests/test_reportes_pdf.py`, test de exportación existente (`grep -l exportacion_contador tests/`)

**Interfaces:**
- Produces:
  - `exportacion_contador.facturas(conn, desde, hasta)` devuelve facturas y notas del **ambiente configurado** por fecha de emisión; `FacturaContable` gana `tipo` ("Factura"/"Nota crédito"), `referencia` y `anulada` (bool); el valor de la nota sale **negativo**. Columnas nuevas en Excel y CSV: "Tipo", "Factura referenciada", "Anulada".
  - `resumen_diario`: una venta manual anulada (nota FACTURADO) no cuenta en ninguna columna (usa `factura_anulada()` de la 0007); "Valor facturado" excluye facturas anuladas.
  - Hoja "Notas": una línea que explica que la nota resta en la fecha de la nota y que hay que confirmarlo con el contador.

- [ ] **Step 1: Write the failing tests**: `test_exportacion_nota_negativa_en_su_fecha` (factura del 2026-10-31, nota del 2026-11-02: exportación de octubre tiene la factura marcada `Anulada` y no la nota; noviembre tiene la nota con valor `-total`); `test_resumen_no_cuenta_la_venta_anulada`; `test_exportacion_excluye_otro_ambiente`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** (el ambiente configurado sale de `DIAN_AMBIENTE` del entorno, pasado por la ruta como hoy se pasa `entorno_dian`).
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(reportes): notas crédito restan en su fecha y ventas anuladas no se cuentan`

---

### Task 10: Frontend — anular desde Facturación

**Files:**
- Modify: `Frontend/src/services/api.js`, `Frontend/src/pages/Facturacion.jsx`, `Frontend/src/lib/formato.js` (si hace falta una etiqueta)
- Create: `Frontend/src/components/DialogoAnular.jsx`
- Test: `Frontend/src/lib/anulacion.test.js` (nuevo) con la lógica pura que se extraiga a `Frontend/src/lib/anulacion.js`

**Interfaces:**
- Consumes: `POST /facturas/{id}/anular`, campos `tipo`, `referencia`, `anulada_por`, `anulable` (Task 8).
- Produces:
  - `api.anular(id, { clave, motivo })`.
  - `lib/anulacion.js`: `etiquetaDocumento(f) -> string` ("Nota crédito NCE1 · anula EDSC5" / "Anulada por NCE1" / ""), `puedeAnular(f, usuario) -> boolean` (`usuario.rol === 'ADMIN' && f.anulable`).
  - `DialogoAnular`: campo de motivo obligatorio con `<label>`, texto de advertencia ("La factura quedará anulada ante la DIAN. La venta no se podrá volver a facturar: registre una venta nueva con los datos correctos."), botones Cancelar / "Anular factura" (deshabilitado sin motivo o mientras envía), genera la `clave` una sola vez al abrir (doble clic seguro), muestra el error de la API.
  - En la lista: columna "Estado" muestra `etiquetaDocumento`; botón "Anular" en la fila si `puedeAnular`; tras anular, recarga la lista. Filas de nota con el total precedido de "−".

- [ ] **Step 1: Write the failing tests** en `lib/anulacion.test.js`: las tres variantes de `etiquetaDocumento` y `puedeAnular` (bombero ⇒ false, admin con `anulable:false` ⇒ false, admin con `anulable:true` ⇒ true).
- [ ] **Step 2: Run** `npx vitest run src/lib/anulacion.test.js` → FAIL.
- [ ] **Step 3: Implement** `anulacion.js`, `DialogoAnular.jsx`, cambios de `Facturacion.jsx` y `api.js`.
- [ ] **Step 4: Run** `npm run lint && npx vitest run && npm run build` → todo verde. Verificar en el navegador (Chromium headless contra una base desechable, como el 2026-10-14) a 390 px y 1366 px: el botón aparece solo para admin, el diálogo se puede usar con teclado, sin desbordes.
- [ ] **Step 5: Commit** `feat(frontend): anular facturas con nota crédito desde Facturación`

---

### Task 11: Documentación del día de salida y verificación final

**Files:**
- Create: `docs/salida-a-produccion.md`
- Modify: `README.md` (enlace y quitar "producción bloqueada"), `.env.example` (comentario de `DIAN_AMBIENTE=1` y que `DIAN_TEST_SET_ID` debe quedar vacío en producción)

- [ ] **Step 1: Write `docs/salida-a-produccion.md`** con el procedimiento de la §4.4 del spec, usando los nombres reales de los comandos de la Task 3, más: cómo anular una factura (Task 10), la regla operativa "una venta se factura en un solo sistema (EDSC o CEMC)", y la prueba previa de una nota en habilitación (`DIAN_AMBIENTE=2`) para despejar si la DIAN exige habilitar notas.
- [ ] **Step 2: Verificación completa**: `pytest -q` con PostgreSQL, `ruff check .`, y en `Frontend/` `npm run lint && npx vitest run && npm run build`. Esperado: todo en verde; anotar el número de tests.
- [ ] **Step 3: Commit** `docs: procedimiento de salida a producción y anulación de facturas`
