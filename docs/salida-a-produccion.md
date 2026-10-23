# Salida a producción con la DIAN (prefijo EDSC)

> Guía del día de salida y de la operación posterior: pasar de habilitación (`DIAN_AMBIENTE=2`) a producción
> (`DIAN_AMBIENTE=1`), anular facturas con nota crédito y renovar la resolución. Los comandos son los reales del
> código (`backend/src/estacion/habilitacion.py` y `worker.py`). Ningún valor secreto va en este documento.

## 1. Estado del trámite DIAN (a 2026-10-23)

Ya hecho:

- 2026-10-13: el set de pruebas de habilitación pasó. El software "EDS System Facturation Concorde" está en producción en el catálogo.
- 2026-10-21: formulario 1302 firmado. Resolución 1876 n.º 18764117468740: prefijo **EDSC**, del 1 al 1.000.000,
  factura electrónica de venta, vigencia de 24 meses (hasta 2028-10-21 aprox.).
- 2026-10-23: EDSC asociado a nuestro software en catalogo-vpfe (producción). La asociación expira el 2028-10-21.

Falta (lo que cubre esta guía): configurar el sistema, verificarlo y emitir la primera factura real.

## 2. Antes del día de salida

### 2.1 Prueba previa de una nota crédito en habilitación

Se hace con `DIAN_AMBIENTE=2` (el `.env` actual). Sirve para despejar tres dudas que el código no puede resolver solo:

1. Si la DIAN acepta la nota. Si exige habilitar las notas aparte, se sabrá aquí y no en producción.
2. El prefijo en `CorporateRegistrationScheme/ID` de la nota (hoy NCP en habilitación, NCE en producción): confirmar que la DIAN lo acepta.
3. El `ProfileID` / `DocumentType` del `AttachedDocument` de la nota: confirmar que la DIAN y el correo del cliente lo aceptan.

Pasos:

1. `make habilitacion-probar` (certificado, firma y red).
2. Crear una factura de habilitación: `python -m estacion.habilitacion enviar --usuario <admin>` (desde `backend/`, con el `.env` cargado) y arrancar `make worker-dian`.
3. Esperar a que quede FACTURADO. Ver con `python -m estacion.habilitacion estado`.
4. En la web, en Facturación, pulsar **Anular** en esa factura, escribir el motivo y confirmar. La nota sale con prefijo NCP.
5. Confirmar que la nota queda aceptada por la DIAN. Si la rechaza, el error se ve en el detalle; anotarlo y corregir antes del día de salida.

Esta prueba usa números NCP: no gasta consecutivos NCE.

### 2.2 Dejar la cola de habilitación vacía

Con `DIAN_AMBIENTE=1` ningún documento de habilitación puede quedar pendiente en la cola.

- `configurar-produccion` y `verificar-produccion` lo bloquean y lo dicen (`documento(s) de habilitación (ambiente 2) pendientes`).
- Si un worker en producción encuentra uno, lo deja FALLIDO con `AMBIENTE_NO_COINCIDE` (queda en `auditoria`) y no habla con la DIAN.
  La factura de ambiente 2 queda EN_PROCESO. **No existe ninguna herramienta para descartarla.**

Cómo detectarlo antes (consulta de solo lectura, desde la raíz del repo):

```bash
docker compose --project-directory . -f infra/docker-compose.yml exec -T db psql -U estacion estacion -c \
  "SELECT f.numero, f.estado, o.tipo, o.estado FROM outbox o JOIN factura f ON f.id = o.factura_id
   WHERE o.estado = 'PENDIENTE' AND o.tipo IN ('ENVIAR_DIAN','CONSULTAR_DIAN') AND f.ambiente = '2'"
```

Qué hacer:

1. Dejar que se resuelvan: con `DIAN_AMBIENTE=2` y `make worker-dian` el worker las envía o las consulta hasta que dejen de estar pendientes.
2. Repetir la consulta hasta que no devuelva filas.
3. Solo entonces cambiar a `DIAN_AMBIENTE=1`.
4. Si una no se resuelve, no inventar SQL a mano: dejarla anotada y consultar antes de tocar la base. Las facturas de habilitación son de prueba y no afectan los reportes de producción (cada documento guarda su ambiente).

## 3. Procedimiento del día de salida

1. Resolución 1876 y asociación de EDSC en catalogo-vpfe: ya hechas (sección 1). Comprobar en el catálogo que EDSC sigue asociado a nuestro software.
2. Cola de habilitación vacía (sección 2.2) y nota de prueba aceptada (sección 2.1).
3. Editar `.env` (no se versiona):
   - `DIAN_AMBIENTE=1`
   - `DIAN_TEST_SET_ID=` vacío (en producción el worker no arranca con valor).
   - `DIAN_SOFTWARE_ID`, `DIAN_SOFTWARE_PIN`: los del software en producción.
   - `DIAN_CERTIFICADO_RUTA` y `DIAN_CERTIFICADO_CLAVE`: el certificado real de la empresa y vigente. El de `make certificado` (prueba) es rechazado.
   - `DIAN_CLAVE_TECNICA`: ver la sección 3.1.
   - `ESTACION_SMTP_*`: la cuenta real (sin `ESTACION_CORREO_SOLO_A`, que limita los destinatarios).
4. Reiniciar la API para que lea el `.env`. Con `DIAN_AMBIENTE=1` la API no emite si la configuración es incoherente.
5. Activar la numeración (desde la raíz del repo, lee el `.env`):
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
   certificado vigente y no de prueba, numeración EDSC activa y vigente, `GetNumberingRange` y prefijo, SMTP y cola sin documentos de habilitación.
   Si algo sale ✘, corregirlo y repetir. No seguir hasta que todo esté en ✔.
7. Arrancar el worker contra producción: `make worker-dian` (es `python -m estacion.worker --proveedor dian`). En el registro debe aparecer
   "proveedor DIAN, ambiente de PRODUCCIÓN". El proveedor simulado no arranca con `DIAN_AMBIENTE=1`.
8. Emitir **una factura real de verificación** (venta real, cliente real). Confirmar:
   - que la DIAN la acepta (estado FACTURADO en Facturación);
   - que el QR abre en el catálogo de producción (catalogo-vpfe);
   - que llega el correo con el ZIP y que el PDF no dice "SIN VALIDEZ FISCAL / AMBIENTE DE PRUEBAS".
9. Si algo sale mal en esa factura, anularla (sección 4). No reutilizar el número anulado.

### 3.1 La clave técnica

No se copia del portal a ciegas: `configurar-produccion` consulta el rango EDSC con `GetNumberingRange` y lo compara con `DIAN_CLAVE_TECNICA`.
Si no coincide, no configura nada y lo dice (sin imprimir ninguna de las dos claves).

Lo que se sabe del código:

- Ningún comando muestra la clave técnica que devuelve la DIAN; solo la comparan. No hay forma de "verla" sin imprimirla.
- `DIAN_CLAVE_TECNICA` debe tener un valor antes de correr `configurar-produccion` y `verificar-produccion`.

Qué hacer: **se confirma el día de salida.**

1. Tomar la clave técnica del rango EDSC en el catálogo de producción (donde se asoció el prefijo) y ponerla en `DIAN_CLAVE_TECNICA`. No es la de SETP.
2. Correr `make configurar-produccion PREFIJO=EDSC`.
3. Si responde que no coincide: la clave del `.env` está mal copiada (espacios, caracteres) o es la de otro rango. Corregir y repetir. No se activa nada mientras no coincida.

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
cd backend && set -a && . ../.env && set +a && .venv/bin/python -m estacion.habilitacion correo-emisor [--nuevo correo@dominio.com]
```

## 9. Pruebas en Linux

- La venv debe ser **Python 3.12** (`make install` la crea así). Con 3.14 `test_openapi` falla por textos HTTP renombrados.
- Las pruebas con base de datos necesitan `ESTACION_TEST_DSN` (PostgreSQL arriba); sin él se saltan y eso no cuenta como verde.
- Las pruebas nunca llaman a la DIAN real.
