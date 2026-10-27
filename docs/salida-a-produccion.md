# Salida a producción con la DIAN (prefijo EDSC)

> Guía del día de salida y de la operación posterior: pasar de habilitación (`DIAN_AMBIENTE=2`) a producción
> (`DIAN_AMBIENTE=1`), anular facturas con nota crédito y renovar la resolución. Los comandos son los reales del
> código (`backend/src/estacion/habilitacion.py` y `worker.py`). Ningún valor secreto va en este documento.

## 1. Estado del trámite DIAN (a 2026-10-23)

Ya hecho:

- 2026-10-13: el set de pruebas de habilitación pasó.
- 2026-10-14: solicitud de numeración EDSC en MUISCA (borrador).
- 2026-10-21: formulario 1302 firmado y resolución 1876 n.º 18764117468740: prefijo **EDSC**, del 1 al 1.000.000,
  factura electrónica de venta, vigencia de 24 meses (hasta 2028-10-21 aprox.).
- 2026-10-21: el software "EDS System Facturation Concorde" figura en producción en catalogo-vpfe (lo vio el usuario).
- 2026-10-23: EDSC asociado a nuestro software en catalogo-vpfe. La asociación expira el 2028-10-21.

Falta (lo que cubre esta guía): configurar el sistema, verificarlo y emitir la primera factura real.

## 2. Antes del día de salida

> **Resultado de las pruebas del 2026-10-27 (habilitación, modo normal):** la DIAN aceptó ("Procesado Correctamente",
> código 00) facturas a un NIT de contado con tarjeta (SETP990000001, SETP990000003), a crédito (SETP990000002) y las
> notas crédito NCP1 y NCP2; los correos con el ZIP salieron. Con `DIAN_TEST_SET_ID` **con valor** la DIAN rechaza todo
> con "Set de prueba ... se encuentra Aceptado" (el set ya pasó): para seguir probando en habilitación, dejarlo **vacío**
> en el `.env`. Un rechazo así no gasta el número: la venta vuelve a pendiente y se reemite con el mismo consecutivo.

### 2.1 Prueba previa de una nota crédito en habilitación

Se hace con `DIAN_AMBIENTE=2` (el `.env` actual). Sirve para despejar tres dudas que el código no puede resolver solo
(las dos primeras las responde la DIAN; la tercera, no):

1. Si la DIAN acepta la nota. Si exige habilitar las notas aparte, se sabrá aquí y no en producción.
2. El prefijo en `CorporateRegistrationScheme/ID` de la nota (hoy NCP en habilitación, NCE en producción): confirmar que la DIAN lo acepta.
3. El `ProfileID` / `DocumentType` del `AttachedDocument` de la nota. El `AttachedDocument` **no se envía a la DIAN**: va solo
   en el ZIP del correo, así que la habilitación no lo confirma. Se valida únicamente con lo que recibe el cliente: abrir el ZIP
   del correo de la nota (Mailpit o el buzón de prueba) y comprobar que el XML y el PDF se abren y corresponden a la nota.

Comandos (desde la raíz del repo; cargan el `.env`):

```bash
hab() { (cd backend && set -a && . ../.env && set +a && ~/venvs/estacion/bin/python -m estacion.habilitacion "$@"); }
```

Pasos:

1. `make habilitacion-probar` (certificado, firma y red).
2. `hab configurar-numeracion` (activa SETP; si ya está activa no cambia nada). Hace falta antes de `enviar`.
3. `hab enviar --usuario <admin>` crea una factura de habilitación en la cola. Arrancar `make worker-dian` (con `DIAN_AMBIENTE=2`).
4. Esperar a que quede FACTURADO. Ver con `hab estado`.
5. En la web, en Facturación, pulsar **Anular** en esa factura, escribir el motivo y confirmar. La nota sale con prefijo NCP.
6. Confirmar que la nota queda aceptada por la DIAN. Si la rechaza, el error se ve en el detalle; anotarlo y corregir antes del día de salida.

Esta prueba usa números NCP: no gasta consecutivos NCE. Al terminar, volver a la numeración de producción solo el día de salida (sección 3).

`DIAN_TEST_SET_ID` en esta prueba (según `facturacion/dian/proveedor.py`):

- Con valor: el worker envía por el set de pruebas (`SendTestSetAsync`, asíncrono; luego consulta por `ZipKey`). Es el camino del set ya pasado.
- Vacío: el worker envía con `SendBillSync` (modo normal), que es el mismo camino que usará en producción.
- Recomendado: dejarlo **vacío** para la nota, porque prueba el camino real de producción. Si la DIAN en habilitación rechazara el envío normal
  por pedir el set, el rechazo lo dirá; en ese caso volver a ponerlo. Qué acepta la DIAN aquí **se confirma al hacer la prueba**.
- En producción debe estar siempre vacío.

### 2.2 Dejar la cola de habilitación vacía

Con `DIAN_AMBIENTE=1` ningún documento de habilitación puede quedar pendiente en la cola.

- `configurar-produccion` y `verificar-produccion` lo bloquean y lo dicen (`documento(s) de habilitación (ambiente 2) pendientes`).
- Si un worker en producción encuentra uno, lo deja FALLIDO con `AMBIENTE_NO_COINCIDE` (queda en `auditoria`) y no habla con la DIAN.
  La factura de ambiente 2 queda EN_PROCESO. **No existe ninguna herramienta para descartarla.**

Cómo detectarlo antes (consultas de solo lectura, desde la raíz del repo):

```bash
psql_db() { docker compose --project-directory . -f infra/docker-compose.yml exec -T db psql -U estacion estacion -c "$1"; }
psql_db "SELECT f.numero, f.estado, o.tipo, o.estado FROM outbox o JOIN factura f ON f.id = o.factura_id
       WHERE o.estado = 'PENDIENTE' AND o.tipo IN ('ENVIAR_DIAN','CONSULTAR_DIAN') AND f.ambiente = '2'"
psql_db "SELECT f.numero, f.estado, o.estado, o.ultimo_error FROM factura f JOIN outbox o ON o.factura_id = f.id
       WHERE f.ambiente = '2' AND f.estado = 'EN_PROCESO' AND o.estado = 'FALLIDO'"
```

La primera lista lo que aún espera en la cola (debe quedar vacía). La segunda lista lo que un worker de producción ya dejó
FALLIDO con `AMBIENTE_NO_COINCIDE`. También queda un registro con esa acción en `auditoria`.

Qué hacer:

1. Dejar que se resuelvan: con `DIAN_AMBIENTE=2` y `make worker-dian` el worker las envía o las consulta hasta que dejen de estar pendientes.
2. Repetir la consulta hasta que no devuelva filas.
3. Solo entonces cambiar a `DIAN_AMBIENTE=1`.
4. Si una no se resuelve, no inventar SQL a mano: dejarla anotada y consultar antes de tocar la base. Las facturas de habilitación son de prueba y no afectan los reportes de producción: cada documento guarda su ambiente y tanto el libro de facturas como el resumen diario filtran por el ambiente configurado.

### 2.3 Prueba previa de una factura a empresa (NIT), a crédito y con tarjeta

El set de pruebas pasó con clientes de cédula, de contado y en efectivo. Facturar a un NIT, a crédito o con tarjeta
usa códigos del anexo 1.9 que la DIAN todavía no nos ha validado (`facturacion/xml_factura.py`): comprador persona
jurídica (`AdditionalAccountID` 1) con su DV en `schemeID` y `TaxLevelCode` R-99-PN, forma de pago 2 (crédito, vence a
30 días) y medio de pago 48 (tarjeta). Se confirman en habilitación, con `DIAN_AMBIENTE=2` y `make worker-dian`:

1. En Clientes (o al facturar), registrar una empresa real o de prueba con NIT y DV.
2. Facturar una venta a ese NIT con medio **Tarjeta**, y una venta que venga del surtidor como **CREDITO** (si no hay,
   una venta manual a crédito).
3. Esperar a FACTURADO. Si la DIAN rechaza, el motivo queda en el detalle de la factura: anotarlo y corregir antes de producción.
4. Abrir el PDF y el recibo: la razón social, el NIT con DV, la forma y el medio de pago y, a crédito, el vencimiento.

### 2.4 Impresora de recibos (térmica de 58 mm de la isla)

El botón **Imprimir recibo** aparece cuando la factura queda validada (y en Facturación, **Recibo**). La API arma el
recibo en ESC/POS desde el XML (`facturacion/recibo.py`), con la razón social y el NIT si el cliente es una empresa.
La tablet lo manda a la impresora por uno de dos caminos (`Frontend/src/lib/impresion.js`):

- **Por cable USB (WebUSB de Chrome):** la primera vez Chrome pregunta qué impresora usar; se elige la térmica y queda
  recordada. Exige abrir el sistema por HTTPS (`make ca`).
- **Por RawBT (USB o Bluetooth):** instalar "RawBT" desde Play Store en la tablet, abrirla, elegir la impresora
  (USB o Bluetooth) y pulsar su impresión de prueba. Si en la pregunta de Chrome no se elige ninguna impresora USB, el
  sistema usa RawBT desde entonces.

Antes de la primera factura del día de salida: imprimir el recibo de una factura de habilitación y comprobar que salen
el logo, las tildes y el QR (que se pueda escanear con un celular). Lo que no se ha probado todavía con esta impresora:
el modo de caracteres (PC437) y la imagen (`GS v 0`); si salen símbolos raros o la imagen no sale, anotarlo.

El logo es un texto "BIOMAX" (`backend/src/estacion/facturacion/recursos/logo_recibo.png`). Para usar el logo oficial:
reemplazar ese PNG (fondo blanco; se ajusta solo a 48 mm x 12 mm) y reconstruir la imagen (`make stack`). Fuera de Docker
también sirve poner la ruta del PNG en `RECIBO_LOGO` y reiniciar la API.

## 3. Procedimiento del día de salida

1. Resolución 1876 y asociación de EDSC en catalogo-vpfe: ya hechas (sección 1). Comprobar en el catálogo que EDSC sigue asociado a nuestro software.
2. Cola de habilitación vacía (sección 2.2), nota de prueba aceptada (sección 2.1), factura a empresa aceptada
   (sección 2.3) y recibo impreso en la isla (sección 2.4).
3. Editar `.env` (no se versiona):
   - `DIAN_AMBIENTE=1`
   - `DIAN_TEST_SET_ID=` vacío (en producción el worker no arranca con valor).
   - `DIAN_SOFTWARE_ID`, `DIAN_SOFTWARE_PIN`: los del software en producción.
   - `DIAN_CERTIFICADO_RUTA` y `DIAN_CERTIFICADO_CLAVE`: el certificado real de la empresa y vigente. El de `make certificado` (prueba) es rechazado.
   - `DIAN_CLAVE_TECNICA`: ver la sección 3.1 (se guarda antes de configurar la numeración).
   - `ESTACION_SMTP_*`: la cuenta real (sin `ESTACION_CORREO_SOLO_A`, que limita los destinatarios).
4. Reiniciar la API para que lea el `.env`. Con `DIAN_AMBIENTE=1` la API no emite si la configuración es incoherente.
5. Guardar la clave técnica (sección 3.1) y, con `DIAN_CLAVE_TECNICA` ya en el `.env`, activar la numeración (desde la raíz del repo):
   ```bash
   make configurar-produccion PREFIJO=EDSC
   ```
   Consulta `GetNumberingRange` en producción, muestra resolución, rango y vigencia, y pide escribir `EDSC` para confirmar.
   La deja como numeración activa. No borra SETP ni las facturas de habilitación.
6. Verificar:
   ```bash
   make verificar-produccion
   ```
   Imprime una lista con ✔/✘ y sale con 0 solo si todo está ✔. No envía documentos. Revisa: variables DIAN, `DIAN_TEST_SET_ID` vacío,
   certificado vigente y no de prueba, una numeración activa del ambiente 1 (EDSC) y vigente, `GetNumberingRange` y prefijo, SMTP y cola sin documentos de habilitación.
   Si algo sale ✘, corregirlo y repetir. No seguir hasta que todo esté en ✔.
7. Comprobar que **no corre ningún worker simulado**: `make stack` ya no lo arranca (va en el perfil `simulador`), pero si
   se usó `make stack-simulador` o quedó un `make worker` abierto, detenerlos
   (`docker compose --project-directory . -f infra/docker-compose.yml stop worker simulador`). Por qué: varios workers
   toman trabajos de la misma cola, y uno de habilitación tomaría trabajos de producción. Comprobar con
   `docker compose --project-directory . -f infra/docker-compose.yml ps` que `worker` no aparece.
8. Arrancar el worker contra producción: `make worker-dian` (es `python -m estacion.worker --proveedor dian`; si
   `backend/.venv` no ejecuta, el Makefile usa `~/venvs/estacion`). En el registro debe aparecer
   "proveedor DIAN, ambiente de PRODUCCIÓN". El proveedor simulado no arranca con `DIAN_AMBIENTE=1`, y ningún worker arranca sin
   `DIAN_AMBIENTE` válido.
9. Emitir **una factura real de verificación** (venta real, cliente real). Confirmar:
   - que la DIAN la acepta (estado FACTURADO en Facturación);
   - que el QR abre en el catálogo de producción (catalogo-vpfe);
   - que llega el correo con el ZIP y que el PDF no dice "SIN VALIDEZ FISCAL / AMBIENTE DE PRUEBAS";
   - que el recibo impreso tampoco lo dice y lleva el número EDSC.
10. Si algo sale mal en esa factura, anularla (sección 4). No reutilizar el número anulado.

### 3.1 La clave técnica

La clave técnica del rango EDSC la tiene la DIAN. No se copia del portal a mano: el comando `guardar-clave-tecnica` la consulta con
`GetNumberingRange` (producción) y la escribe en un archivo nuevo, sin mostrarla.

1. Elegir una ruta nueva fuera del repositorio, p. ej. `$HOME/clave-edsc.env` (si existe, el comando se niega).
   Escribir `$HOME`, no `~`: en `ARCHIVO=~/...` zsh no expande la tilde y el archivo acabaría en una carpeta llamada `~`.
2. `make guardar-clave-tecnica PREFIJO=EDSC ARCHIVO=$HOME/clave-edsc.env`
   Exige `DIAN_AMBIENTE=1`. Crea el archivo con permisos 0600 y una sola línea `DIAN_CLAVE_TECNICA=<valor>`. Solo dice dónde la escribió.
   Se niega si el archivo ya existe, si la ruta (resueltos los enlaces) queda dentro del repositorio, si la DIAN no tiene ese
   prefijo o si no devuelve clave. Si la escritura falla a medias, borra el archivo y lo dice.
3. Copiar esa línea al `.env` y **borrar el archivo** (`rm $HOME/clave-edsc.env`).
4. Correr `make configurar-produccion PREFIJO=EDSC`. Vuelve a consultar la DIAN y compara con `DIAN_CLAVE_TECNICA`; si no coincide, no activa nada
   y lo dice, sin imprimir ninguna de las dos claves.

Nunca pegar la clave en chats, correos ni commits. Es distinta de la de SETP.

## 4. Anular una factura con nota crédito

Solo lo hace un usuario ADMIN. El bombero no ve el botón y la API se lo niega.

1. En la web: Facturación, fila de la factura, botón **Anular**.
2. Escribir el motivo (obligatorio, hasta 500 caracteres) y pulsar **Anular factura**.
3. La nota (prefijo NCE en producción) se numera, se firma, se envía a la DIAN y se entrega por correo al cliente, igual que una factura.
4. Cuando la DIAN acepta la nota, la factura queda anulada y la venta pasa a NO_FACTURABLE. Hasta entonces sigue FACTURADO.
5. Registrar una **venta manual nueva** con los datos correctos y facturarla de forma normal (nuevo consecutivo EDSC).
   La venta anulada no se edita ni se vuelve a facturar.

Reglas:

- Solo se anula una factura FACTURADO, de una venta manual, del mismo ambiente en que corre el sistema (en producción no se anulan las SETP) y una sola vez.
  Las facturas de despachos del surtidor no muestran el botón.
- Una factura RECHAZADA, EN_PROCESO o INCIERTO no se anula: la rechazada se corrige con el reenvío; las demás esperan a resolverse.
- Si la DIAN rechaza la nota, la factura sigue vigente. El error se ve en el detalle y se reintenta con el mismo número de nota.
- Todo queda en `auditoria`: quién, cuándo, motivo y resultado.

## 5. Reglas operativas

- **Una venta se factura en un solo sistema (EDSC o CEMC).** Si ya se facturó en uno, no se factura en el otro. Si se factura por error en los dos, hay que anular en uno.
- Una venta, una factura. La corrección de un error es anular y registrar una venta nueva, no editar.
- No usar el ambiente de habilitación con ventas reales.

## 6. Reportes y contador

- La factura anulada sigue en reportes y exportación, marcada, con su valor en la fecha de la factura.
- La nota crédito resta en su propia fecha. Si la factura es de octubre y la nota de noviembre, octubre no cambia y noviembre baja.
- El resumen diario no cuenta las ventas manuales cuya factura fue anulada. Por eso los totales mensuales de las hojas de facturas y de resumen diario
  pueden no conciliar cuando hay anulaciones. La exportación lo avisa en la hoja "Notas" y en el PDF.
- **Pendiente de confirmar con el contador** que esta forma de contar es la que necesita.

## 7. Vencimientos

- La resolución EDSC y su asociación expiran el 2028-10-21. `verificar-produccion` avisa si la numeración está fuera de vigencia y cuántos consecutivos quedan.
- Antes de esa fecha (dejar meses de margen):
  1. Pedir una nueva resolución en MUISCA: formulario 1302, luego la resolución 1876.
  2. Asociar el nuevo rango a nuestro software en el catálogo (catalogo-vpfe).
  3. Poner `DIAN_CLAVE_TECNICA` del nuevo rango y correr `make configurar-produccion PREFIJO=<prefijo>`, luego `make verificar-produccion`.
- El certificado de firma también vence: `verificar-produccion` muestra los días que le quedan.

## 8. Correo del emisor

El correo que aparece en la factura (no el remitente SMTP) se muestra o se cambia con un comando; el cambio queda en `auditoria` y solo afecta documentos nuevos:

```bash
hab correo-emisor [--nuevo correo@dominio.com]   # hab: la función de la sección 2.1
```

## 9. Pruebas en Linux

- La venv debe ser **Python 3.12** (`make install` la crea así). Con 3.14 `test_openapi` falla por textos HTTP renombrados.
- Las pruebas con base de datos necesitan `ESTACION_TEST_DSN` (PostgreSQL arriba); sin él se saltan y eso no cuenta como verde.
- Las pruebas nunca llaman a la DIAN real.
