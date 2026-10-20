# Cliente del web service de la DIAN y envío del set de pruebas de habilitación

Fecha: 2026-10-13 · Estado: diseño aprobado en conversación, pendiente de revisión del spec escrito.

## 1. Objetivo

Que el sistema hable con la DIAN de verdad: firmar y enviar facturas al web service, recibir la validación
(`ApplicationResponse`) y conciliar los casos inciertos. Primer uso: **pasar el set de pruebas de habilitación**
del software propio "EDS System Facturation Concorde" con facturas que recorran el flujo real del sistema
(venta → `ServicioEmision` → cola → firma → DIAN), no un script aparte.

**Éxito:**
1. `python -m estacion.habilitacion probar-conexion` recibe una respuesta válida de `GetNumberingRange` en el
   ambiente de habilitación (certificado, WS-Security y red funcionan).
2. Una factura enviada con `habilitacion enviar --cantidad 1` queda **aceptada** por la DIAN (FACTURADO con el
   `ApplicationResponse` real guardado) y el correo con el ZIP sale como hoy.
3. El portal de habilitación muestra el set como aceptado (requiere 1 factura aceptada; ver §2).
4. La suite completa pasa sin red (447 pruebas actuales + las nuevas).

**Fuera de alcance:** notas crédito y débito (solo si el portal las exige; plantillas de referencia en
`docs/referencia/borrador-dian/`), desbloquear producción (`DIAN_AMBIENTE=1`), contingencia, eventos del
adquirente (acuse, aceptación), cambios en el frontend.

## 2. Datos de habilitación (portal DIAN, capturas del 6-oct-2026)

| Dato | Valor | Dónde vive |
|---|---|---|
| Estado del set | **En proceso** | portal |
| TestSetId | (en el portal) | `.env`: `DIAN_TEST_SET_ID` |
| Identificación del software | (en el portal) | `.env`: `DIAN_SOFTWARE_ID` |
| PIN | (en el portal) — **secreto** | `.env`: `DIAN_SOFTWARE_PIN` |
| Clave técnica del rango de pruebas | la genérica de habilitación | `.env`: `DIAN_CLAVE_TECNICA` |
| Numeración | prefijo `SETP`, 990000000 a 995000000, resolución 18760000001, vigente 2019-01-19 a 2030-01-19 | tabla `numeracion` |
| Documentos requeridos | 50 (30 facturas, 10 notas débito, 10 notas crédito) | portal |
| **Aceptados requeridos** | **1** (1 factura, 0 notas) | portal |
| Certificado de firma | real, de entidad certificadora (lo tramitó la empresa) | `.env`: `DIAN_CERTIFICADO_RUTA` / `_CLAVE` |

Lectura adoptada: el set se aprueba al tener **1 factura aceptada**. **Sin verificar** si la DIAN exige además
enviar los 50; se comprueba en la práctica: si tras 1 aceptada el portal sigue "En proceso", se envían más facturas
y solo si sigue faltando se construyen las notas (proyecto aparte).

Confirmado (2026-10-13): **no se ha enviado nada** a la DIAN con este set; la numeración arranca en 990000000.

## 3. Web service (lo que se sabe y lo que se verifica)

Endpoints: habilitación `https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc`; producción
`https://vpfe.dian.gov.co/WcfDianCustomerServices.svc` (no se usa en este trabajo).

SOAP 1.2, espacio de nombres de las operaciones `http://wcf.dian.colombia`, acción
`http://wcf.dian.colombia/IWcfDianCustomerServices/<Operación>` en el `Content-Type` y en `wsa:Action`.

| Operación | Entrada | Salida | Uso |
|---|---|---|---|
| `GetNumberingRange` | `accountCode` (NIT), `accountCodeT` (NIT), `softwareCode` | rangos de numeración | prueba de conexión (solo lectura) |
| `SendTestSetAsync` | `fileName`, `contentFile` (ZIP en base64), `testSetId` | `ZipKey` + `ErrorMessageList` | set de pruebas |
| `GetStatusZip` | `trackId` (= ZipKey) | lista de `DianResponse` | resultado del set |
| `GetStatus` | `trackId` (= CUFE) | `DianResponse` | conciliar inciertos |
| `SendBillSync` | `fileName`, `contentFile` | `DianResponse` | envío normal (tras habilitar) |

`DianResponse`: `IsValid`, `StatusCode`, `StatusDescription`, `StatusMessage`, `ErrorMessage` (lista de textos),
`XmlBase64Bytes` (el ApplicationResponse), `XmlDocumentKey` (CUFE), `XmlFileName`.

Encabezado de seguridad (WS-Security 1.0 + WS-Addressing):
- `wsa:Action` y `wsa:To` (este último con `wsu:Id`), dentro de `soap:Header`.
- `wsse:Security` con `wsu:Timestamp` (Created, Expires = Created + 60 s), `wsse:BinarySecurityToken`
  (certificado X.509 v3 en base64) y `ds:Signature`: `SignedInfo` con C14N exclusiva, RSA-SHA256, una referencia
  a `wsa:To` con transformación C14N exclusiva y digest SHA-256; `KeyInfo` → `SecurityTokenReference` → el token.

**SIN VERIFICAR** (de memoria y de implementaciones públicas; se confirma así): el primer paso del plan descarga el
WSDL (GET público, sin credenciales) y se guarda en `tests/fixtures/dian/` para fijar nombres de operaciones y
campos; luego `probar-conexion` confirma la seguridad. Códigos de estado conocidos de memoria (`00` procesado
correctamente, `99` errores de validación, "en proceso" mientras se valida el lote): el cliente NO depende de ellos
para decidir; decide por `IsValid` y por la presencia de la respuesta, y guarda el código tal cual.

## 4. Componentes

### 4.1 `facturacion/dian/sobre.py` — mensaje SOAP firmado (función pura)
`sobre(operacion, cuerpo, *, destino, certificado, ahora) -> bytes`. Arma el envelope SOAP 1.2 con
WS-Addressing y WS-Security según §3, firmando con el `Certificado` de `facturacion/firma.py` (reutiliza sus
utilidades de canonicalización, digest y firma RSA; se extraen a funciones compartidas si hace falta, sin
duplicarlas). Incluye `verificar_sobre(bytes) -> None` para las pruebas (valida digest y firma con la clave
pública del token).

### 4.2 `facturacion/dian/cliente.py` — operaciones
`ClienteDian(url, certificado, transporte=urllib)` con un método por operación de §3. `transporte` es una función
`(url, cabeceras, cuerpo, timeout) -> (estado_http, cuerpo)`: la real usa `urllib.request` (librería estándar, sin
dependencias nuevas, TLS verificado, timeout de 60 s); las pruebas inyectan una falsa. Devuelve dataclasses
(`RespuestaDian`, `RespuestaEnvioAsincrono`, `RangoNumeracion`), con el XML de la respuesta parseado con lxml sin
resolver entidades ni red. Errores:
- `ErrorSeguridadDian` — SOAP Fault de seguridad/autenticación (certificado no aceptado, firma inválida, reloj).
- `ErrorTransporteDian` — timeout, conexión, HTTP 5xx: no se sabe si llegó.
- Fault de otro tipo o HTTP 4xx no reconocido → `ErrorTransporteDian` con el detalle (se trata como incierto).
Nunca se registran en el log el PIN, la clave del certificado ni el contenido del documento.

### 4.3 `facturacion/dian/proveedor.py` — `ProveedorDian`
Implementa `ProveedorFacturacion` con dos modos:
- `set_pruebas` (requiere `DIAN_TEST_SET_ID`): `emitir` empaqueta el XML firmado en el ZIP (nombres de
  `paquete.nombre_archivo`: `fv…xml` dentro de `z…zip`, consecutivo = el de la factura), llama
  `SendTestSetAsync` y devuelve `EnvioPendiente(track_id=ZipKey)`. El resultado llega por `consultar`.
- `normal`: `SendBillSync`; devuelve el resultado directo.
`consultar(cufe, track_id)` usa `GetStatusZip(track_id)` si hay track id del set, si no `GetStatus(cufe)`.
Mapeo: `IsValid=true` → `ResultadoEmision` con `application_response` = `XmlBase64Bytes` decodificado;
`IsValid=false` con errores → `ProveedorRechazo` con los mensajes; "en proceso" → `EnvioPendiente`;
`ErrorTransporteDian` → `ProveedorIncierto`; `ErrorSeguridadDian` → `ProveedorNoConfigurado` (nuevo).

### 4.4 Cambios a la interfaz y a la cola (`facturacion/proveedor.py`, `servicios/outbox.py`)
- `buscar_por_clave(clave)` se reemplaza por `consultar(cufe, track_id) -> ResultadoEmision | EnvioPendiente | None`
  (la DIAN no conoce la clave interna). `emitir` puede devolver `EnvioPendiente`. `ProveedorSimulado` se adapta y
  gana un modo asíncrono para probar `EnvioPendiente`.
- `EnvioPendiente` en `ENVIAR_DIAN`: se guarda `factura.dian_track_id`, la factura sigue `EN_PROCESO` y se encola
  `CONSULTAR_DIAN`. En `CONSULTAR_DIAN`, `EnvioPendiente` reprograma con `_reprogramar` (espera creciente).
- `ProveedorNoConfigurado`: ni rechazo ni incierto; `_reprogramar` (es configuración corregible) y auditoría
  `DIAN_SEGURIDAD`. Si se agotan los intentos, FALLIDO para revisión humana.
- Rechazo: se guardan los mensajes de la DIAN en `factura.error` (recortados) y la respuesta completa en
  `respuesta_dian`.

### 4.5 Datos (migración `0006_cliente_dian`)
- `factura.dian_track_id text NULL`.
- Nada más: la numeración SETP se carga con el comando de §4.6, no con la migración (es dato de la empresa).

### 4.6 Comando `python -m estacion.habilitacion`
- `probar-conexion`: `GetNumberingRange` con el NIT de la estación activa y `DIAN_SOFTWARE_ID`; imprime si
  respondió y cuántos rangos trae. No escribe nada.
- `configurar-numeracion`: crea la numeración SETP de §2 para la estación activa y desactiva la anterior (en una
  transacción; pide confirmación; idempotente si ya existe).
- `enviar --cantidad N` (por defecto 1): por cada una, crea una venta manual de prueba (producto existente, valores
  sintéticos, marca de prueba) y un cliente sintético con autorización de datos, y la emite con
  `ServicioEmision.solicitar` a nombre de un usuario ADMIN existente (`--usuario <login>`, obligatorio; queda en
  la auditoría). El envío lo hace el worker con `--proveedor dian`; el comando
  puede procesar la cola él mismo con `--procesar` para no depender del worker. Pide confirmación escrita antes de
  enviar ("Se enviarán N documentos al ambiente de HABILITACIÓN de la DIAN").
- `estado`: lista las facturas SETP con su estado, track id, código y mensajes de la DIAN.
Todo exige `DIAN_AMBIENTE=2`; producción sigue bloqueada en `servicios/configuracion.py`.

### 4.7 Worker
`--proveedor {simulado,dian}`; con `dian`, el modo sale de `DIAN_TEST_SET_ID` (presente → `set_pruebas`). El log
dice qué proveedor, ambiente y modo usa, sin secretos.

## 5. Manejo de errores (resumen)

| Situación | Factura | Origen | Cola |
|---|---|---|---|
| DIAN acepta | FACTURADO, `application_response` real | FACTURADO | HECHO + `ENVIAR_CORREO` |
| DIAN rechaza (errores de validación) | RECHAZADO, mensajes en `error` | PENDIENTE | HECHO |
| Set: lote en proceso | EN_PROCESO, `dian_track_id` | EN_PROCESO | `CONSULTAR_DIAN` reprogramado |
| Timeout / red / 5xx | INCIERTO | INCIERTO | `CONSULTAR_DIAN` |
| Fault de seguridad / certificado | sin cambio | sin cambio | reprogramado; FALLIDO al agotar |
| Sin firma (como hoy) | sin cambio | sin cambio | reprogramado |

## 6. Pruebas (TDD, sin red)

- `sobre.py`: el envelope tiene Action/To/Timestamp/token/firma; `verificar_sobre` acepta el original y rechaza
  cualquier cambio en `wsa:To` o en la firma; expira en 60 s.
- `cliente.py` con transporte falso: respuestas grabadas (las del WSDL fijado y ejemplos sintéticos) para válida,
  rechazada con varios errores, Fault de seguridad, HTTP 500, timeout, `ZipKey`, `GetStatusZip` en proceso y final,
  XML malicioso (entidades) rechazado sin resolver.
- `ProveedorDian`: mapeo completo de §4.3, nombres de ZIP y XML, `XmlBase64Bytes` decodificado.
- Cola: los nuevos caminos de §5 con `ProveedorSimulado` asíncrono, incluido que un `EnvioPendiente` nunca gaste un
  número nuevo ni duplique el envío.
- Comando: `configurar-numeracion` idempotente; `enviar` crea ventas y facturas SETP consecutivas; se niega sin
  confirmación y con `DIAN_AMBIENTE` distinto de 2.
- Regresión: suite completa contra PostgreSQL, ruff y mypy.

Envío real: solo lo ejecuta el usuario, en este orden: `probar-conexion` → `configurar-numeracion` →
`enviar --cantidad 1` → revisar portal.

## 7. Riesgos abiertos

1. Detalles de WS-Security no confirmados (§3): los resuelve `probar-conexion`.
2. Firma XAdES (`firma.py`: política y su hash, formato del emisor del certificado, `SigningTime`) marcada como sin
   verificar: si la primera factura se rechaza por firma, empezar ahí.
3. Campos del XML que solo valida la DIAN (CUFE, responsabilidades `O-23` de la estación, sin impuestos): los
   mensajes de rechazo dirán qué corregir; corregir y reenviar con el mismo número.
4. Si el set exige más que 1 factura aceptada, enviar más; si exige notas, proyecto aparte.
5. Hora del servidor: el Timestamp de WS-Security falla si el reloj está desfasado; `probar-conexion` lo reporta.
