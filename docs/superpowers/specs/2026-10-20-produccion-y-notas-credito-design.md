# Salida a producción DIAN (prefijo EDSC) y notas crédito de anulación

Fecha: 2026-10-20 · Estado: diseño, pendiente de aprobación del usuario

## 1. Intención

La estación (ALISTACONCORDE S.A.S., NIT 901335327-1, EDS Concorde en Sogamoso) va a emitir **facturas
electrónicas reales** con nuestro software. La habilitación ya pasó (set SETP aceptado el 2026-10-13) y la
numeración de producción se pidió en MUISCA: prefijo **EDSC**, rango 1–1.000.000, establecimiento "EDS CONCORDE
CL 16 27 05 BRR SIETE DE AGOSTO" (formulario 1302 en borrador 13028147777141, falta la firma del representante
legal para obtener el formato 1876).

El objetivo de este trabajo es que, cuando lleguen el 1876 y la clave técnica, pasar a producción sea **solo
configuración y una factura de verificación**, sin escribir código ese día. Y que, en producción, una factura que
salió mal se pueda **anular con una nota crédito** (en producción una factura aceptada no se borra).

Contexto de operación (decidido por el usuario):

- Al principio solo se facturan **ventas manuales**: el bombero registra la venta y emite la factura **desde la
  tablet**. No hay conexión con surtidores todavía.
- El software actual de la estación sigue facturando con su prefijo **CEMC** por su lado. Regla de operación (no
  la garantiza el código): **una venta se factura en un solo sistema**; si se factura con EDSC, no se factura en el
  otro software.
- Los correos con la factura salen desde **alistaconcordefactutacion@gmail.com** (Gmail gratuito, contraseña de
  aplicación en `.env`; ya probado con `probar_correo` el 2026-10-20). Más adelante la empresa puede pasar a un
  dominio propio (Google Workspace).
- El sistema quedará desplegado en un servidor en internet; ese despliegue es **otro proyecto** (ver §8).

## 2. Objetivos

1. **Producción por configuración.** Con `DIAN_AMBIENTE=1` y los datos de EDSC, el sistema emite en producción:
   web service de producción, `SendBillSync`, QR del catálogo de producción, PDF y correo sin marcas de prueba.
2. **Arranque seguro.** Un comando de verificación revisa todo lo necesario antes de emitir en producción, y el
   worker y la API se niegan a operar en producción si la configuración no es coherente (§4.3).
3. **Numeración EDSC.** Un comando carga la numeración EDSC consultándola a la DIAN (`GetNumberingRange` en
   producción), en lugar de digitarla a mano.
4. **Notas crédito de anulación total** (§5), emitidas por un administrador desde la web.
5. **Correo del emisor editable** sin entrar a la base de datos (§6).

## 3. No objetivos y anti-objetivos

No objetivos (fuera de este trabajo):

- Notas crédito parciales (devolución parcial, rebaja, descuento) y notas débito.
- Integración o control cruzado con el software de CEMC.
- Conexión con surtidores (Wayne / Speed Solutions).
- Contingencia (facturar sin internet o con la DIAN caída).
- Despliegue en la nube, dominio, HTTPS público y copias de seguridad (proyecto 2).
- Líneas con IVA (productos que no son combustible): siguen rechazadas a propósito.

Anti-objetivos (aunque "funcione", sería un fracaso):

- **Emitir facturas reales por accidente**: desde las pruebas automáticas, desde el banco de pruebas, con el
  proveedor simulado, con la numeración SETP o con un certificado autofirmado.
- **Facturar dos veces la misma venta**: una venta anulada no se vuelve a facturar nunca (queda NO_FACTURABLE);
  la corrección se hace con una venta nueva, y solo después de que la DIAN **aceptó** la nota crédito.
- **Contar dos veces una venta en los reportes** o en la exportación al contador (factura anulada + factura nueva).
- Mezclar documentos de habilitación y de producción sin poder distinguirlos.
- Que un bombero pueda anular facturas.
- Romper los 521 tests actuales o el flujo de habilitación (`DIAN_AMBIENTE=2` debe seguir funcionando igual).

## 4. Salida a producción

### 4.1 Lo que ya existe (no rehacer)

- `facturacion/xml_factura.py`: `_URL_QR["1"]` apunta a `catalogo-vpfe.dian.gov.co`.
- `facturacion/dian/cliente.py`: `enviar_sincrono` (`SendBillSync`), `estado` (`GetStatus`) y
  `rangos_numeracion` (`GetNumberingRange`).
- `facturacion/dian/proveedor.py`: modo normal con `SendBillSync` cuando no hay `test_set_id`.
- `facturacion/representacion.py` y `entrega/correo.py`: la marca "SIN VALIDEZ FISCAL / AMBIENTE DE PRUEBAS" del
  PDF y el aviso del correo dependen del ambiente.

### 4.2 Lo que bloquea hoy

- `servicios/configuracion.py`: `AMBIENTES_PERMITIDOS = {"2"}`.
- `worker.py`: rechaza ambientes distintos de "2" y usa siempre `URL_HABILITACION`.
- `habilitacion.py`: exige `DIAN_AMBIENTE=2`; `configurar-numeracion` solo activa SETP.

### 4.3 Cambios

- Permitir el ambiente "1" y elegir la URL del web service según el ambiente (producción:
  `https://vpfe.dian.gov.co/WcfDianCustomerServices.svc`; **verificar** contra el anexo técnico vigente).
- Con `DIAN_AMBIENTE=1`, fallar cerrado (worker no arranca; la API no emite) si:
  - el worker se inicia con `--proveedor simulado`;
  - hay `DIAN_TEST_SET_ID` definido;
  - la numeración activa no es una numeración de producción (p. ej. la SETP de habilitación);
  - el certificado es el autofirmado de pruebas (`certificado_prueba.py` / `estacion.certificado`) o está vencido;
  - falta `DIAN_CLAVE_TECNICA` o no coincide con la que la DIAN devuelve para EDSC (si se puede consultar).
  Y simétricamente, con `DIAN_AMBIENTE=2` no se emite con una numeración de producción.
- Cada factura y nota guarda el ambiente con el que se emitió, para poder distinguir siempre habilitación de
  producción (columna nueva; la migración marca las existentes como "2").
- Comando `verificar-produccion`: corre todas las comprobaciones anteriores más `GetNumberingRange` contra
  producción, e imprime un informe legible (qué está bien, qué falta), **sin enviar documentos**. Termina con
  código distinto de 0 si algo falta.
- Comando para configurar la numeración de producción: consulta `GetNumberingRange` en producción, muestra el
  rango EDSC (resolución, rango, vigencia), pide confirmación escrita y lo deja como numeración activa. No borra
  la numeración SETP ni las facturas de habilitación.
- Nombres de los comandos: decisión del constructor (pueden vivir en `habilitacion.py` o en un módulo nuevo).

### 4.4 Procedimiento del día de salida (va en la documentación, no es código)

1. Representante legal firma el 1302 → la DIAN genera el **1876**.
2. Asociar el prefijo EDSC a nuestro software en el catálogo de producción (catalogo-vpfe) y anotar la clave técnica.
3. Poner en `.env`: `DIAN_AMBIENTE=1`, `DIAN_CLAVE_TECNICA`, quitar `DIAN_TEST_SET_ID`.
4. Configurar la numeración EDSC con el comando de §4.3.
5. `verificar-produccion` en verde.
6. Arrancar el worker con `--proveedor dian` y emitir **una factura real de verificación** (venta real, cliente
   real). Confirmar que la DIAN la acepta, que el QR abre en el catálogo y que llega el correo.
7. Si algo sale mal en esa factura: anularla con nota crédito (§5).

Esta guía se agrega a `docs/` (archivo nuevo o sección en `docs/despliegue.md`, a criterio del constructor).

## 5. Notas crédito de anulación total

### 5.1 Comportamiento

- Solo un usuario **ADMIN** puede anular. En la web, en el detalle de una factura **aceptada por la DIAN**
  (estado FACTURADO), un botón "Anular con nota crédito" pide un **motivo obligatorio** (texto) y una confirmación
  explícita. El bombero no ve el botón y la API se lo niega.
- Solo se anulan facturas FACTURADO. Una factura RECHAZADA ya se corrige con el reenvío actual; EN_PROCESO e
  INCIERTO esperan a resolverse.
- Una factura se anula **una sola vez** (una nota de anulación viva por factura, garantizado en la base de datos).
- La nota crédito anula el **valor total** de la factura: mismas líneas, mismos valores, mismo cliente.
- La nota se numera, se firma, se envía a la DIAN y se entrega por correo al cliente con el mismo mecanismo que
  la factura (outbox, worker, AttachedDocument, PDF, ZIP).
- Cuando la DIAN **acepta** la nota: la factura queda marcada como anulada (con referencia a su nota) y la venta
  de origen (despacho o venta manual) pasa a **NO_FACTURABLE** (estado que ya existe). **Decisión del usuario:** la
  corrección se hace registrando una **venta manual nueva** con los datos correctos y facturándola normalmente
  (nuevo consecutivo EDSC; el número anulado no se reutiliza). No se editan ventas ya registradas. La venta nueva
  no queda enlazada a la anulada; el motivo de la anulación en la auditoría es el rastro. Mientras la nota no haya
  sido aceptada, la venta sigue FACTURADO.
- Solo se anulan facturas emitidas en el **mismo ambiente** en que corre el sistema: en producción, las facturas
  SETP de habilitación no muestran el botón ni la API lo permite (y al revés).
- Si la DIAN **rechaza** la nota, la factura sigue vigente y la venta sigue facturada; el admin ve el error y
  puede reintentar (mismo número de nota, como las facturas rechazadas).
- Todo queda en `auditoria`: quién pidió la anulación, cuándo, el motivo, y el resultado.
- La lista de facturas y el detalle muestran el estado de anulación y el número de la nota; en el detalle se
  pueden descargar el PDF y el ZIP de la nota.

### 5.2 Requisitos DIAN (verificar cada uno contra el anexo técnico 1.9 al construir)

- `CreditNote` UBL 2.1, `CustomizationID` 20 (nota crédito que referencia una factura electrónica),
  `ProfileID` "DIAN 2.1: Nota Crédito de Factura Electrónica de Venta", `CreditNoteTypeCode` 91.
- `DiscrepancyResponse/ResponseCode` = **2 "Anulación de factura electrónica"**, con el motivo como descripción.
- `BillingReference/InvoiceDocumentReference` con el número, el CUFE y la fecha de la factura anulada.
- **CUDE** (no CUFE): SHA-384 de la concatenación definida en el anexo; usa el **PIN del software**, no la clave
  técnica.
- Las notas crédito no requieren resolución de numeración de la DIAN, pero sí numeración propia del software,
  **una serie por ambiente**: **NCE** en producción y **NCP** en habilitación, cada una desde 1, sin huecos,
  asignada en la misma transacción que guarda la nota (mismo patrón que `servicios/numeracion.py`). Así las notas
  de prueba nunca ocupan números de la serie real.
- QR de la nota: catálogo del ambiente correspondiente con el CUDE.
- Referencia del borrador anterior: `docs/referencia/borrador-dian/nota_credito.xml` (solo estructura; su firma y
  sus `float` están descartados).
- **SIN VERIFICAR:** si la DIAN exige que el software haya enviado notas crédito en habilitación antes de
  aceptarlas en producción. Se comprueba enviando una nota en habilitación (`DIAN_AMBIENTE=2`) antes del día de
  salida; si la DIAN la acepta, se asume habilitado.

### 5.3 Base de datos

La base ya garantiza **una factura por venta** (`UNIQUE` en `factura.despacho_id` / `venta_manual_id` y el
trigger `factura_una_por_venta` de la 0003). Como la venta anulada pasa a NO_FACTURABLE y la corrección es una
venta nueva, **esa regla no cambia**. Lo que sí debe garantizar la base: una sola nota de anulación por factura, y
que una venta NO_FACTURABLE con factura anulada no vuelva a PENDIENTE. Migración nueva (0007); no se edita ninguna
migración existente.

### 5.4 Reportes y exportación al contador

**Decisión del usuario** (forma contable usual; conviene confirmarla con el contador):

- La factura anulada **sigue apareciendo** en reportes y exportación, marcada como anulada, con su valor en la
  fecha de la factura.
- La nota crédito aparece como **valor negativo en la fecha de la nota**. Si la factura es de octubre y la nota de
  noviembre, octubre no cambia y noviembre se reduce.
- Los totales del período = facturas del período − notas del período. Así una venta anulada y vuelta a facturar
  cuenta una sola vez en el neto.
- Afecta `servicios/reportes_bd.py`, `servicios/reportes_pdf.py`, `servicios/exportacion_contador.py` y la pantalla
  de reportes. Solo cuentan los documentos del ambiente en que corre el sistema (la habilitación no se mezcla con
  producción).

Dónde guardar las notas (tabla propia o generalizar `factura`/`outbox`/`entrega` a "documento") es decisión del
constructor; lo que no puede pasar es duplicar la lógica de firma, envío y entrega.

## 6. Correo del emisor

- El remitente de los correos ya se configura por `.env` (`ESTACION_SMTP_USUARIO`, `ESTACION_SMTP_CLAVE`,
  `ESTACION_SMTP_REMITENTE`) y no cambia.
- El correo del emisor que aparece en la factura vive en `estacion.email`. Se agrega una forma de cambiarlo sin
  SQL: un comando de consola que muestra el valor actual, pide el nuevo y confirma (decisión mía: comando y no
  pantalla, porque se usará muy pocas veces). El cambio queda en `auditoria`. Solo afecta documentos nuevos.

## 7. Restricciones

- Python 3 / FastAPI / PostgreSQL / psycopg / lxml como el resto del backend; React (Vite, JS) en `Frontend/`,
  siguiendo el estilo "papel y tinta" actual (sin color de acento, tipografía sin cambios).
- Dinero en `Decimal`. Mensajes y logs sin secretos ni datos personales.
- Fallar cerrado ante cualquier dato fiscal faltante.
- Tests para todo lo nuevo; las pruebas automáticas **nunca** llaman a la DIAN real (transporte falso, como hoy).
- Al terminar: tests del backend con PostgreSQL, ruff, y lint/tests/build del frontend en verde.
- Commits sin coautoría de Claude.

## 8. Fuera de este documento

**Proyecto 2: despliegue en la nube.** Servidor (VPS), dominio con HTTPS público, copias de seguridad de
PostgreSQL con restauración probada, manejo del `.pfx` en el servidor, acceso desde la tablet. Se diseña después
de este.

## 9. Decisiones que tomé yo (revisar)

- Comando de consola para cambiar el correo del emisor, en vez de pantalla.
- Prefijos **NCE** (producción) y **NCP** (habilitación) para las notas crédito, una serie por ambiente.
- Solo se anulan documentos del ambiente en que corre el sistema.
- La venta pasa a NO_FACTURABLE solo cuando la DIAN **acepta** la nota (no al pedirla).
- El reintento de una nota rechazada conserva su número (igual que las facturas).
- Guardar el ambiente en cada documento.
- Una nota de habilitación de prueba antes del día de salida, para despejar la duda de §5.2.
