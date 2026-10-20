# Entrega de la factura al cliente: ZIP con AttachedDocument + PDF, por correo

Fecha: 2026-10-08 · Estado: diseño aprobado por secciones, pendiente de revisión del spec escrito.

## 1. Objetivo

Que la entrega de una factura FACTURADA sea igual a la de un facturador electrónico real: un correo al cliente con
**un único ZIP** que contiene el `AttachedDocument` firmado (factura firmada + validación de la DIAN) y el PDF de la
representación gráfica. En el banco de pruebas el correo llega a un buzón local (Mailpit), nunca a un correo real.

**Éxito:** al emitir una factura en el flujo normal (API → worker), en `http://localhost:8025` aparece el correo con
el asunto reglamentado y el ZIP adjunto; el `AttachedDocument` del ZIP pasa el esquema oficial UBL 2.1 y su firma
verifica; el ZIP queda guardado y se puede descargar y reenviar.

**Fuera de alcance:** SMTP de producción, cliente del web service de la DIAN, notas crédito/débito, eventos
(acuse, aceptación), cambios en el frontend.

## 2. Normativa aplicada (verificada)

Anexo Técnico de Factura Electrónica de Venta **v1.9** (Resolución 000165 de 01-nov-2023), §6.4 *Contenedor
electrónico – AttachedDocument*, y **v1.8** (Resolución 000012 de 2021), §9.1 *Recepción de factura electrónica*:

- **Adjunto:** un único `.zip` con el AttachedDocument, que contiene la factura y el ApplicationResponse de la DIAN.
  El PDF es opcional y puede ir en el mismo ZIP. **Peso máximo 2 MB.**
- **Nombres de archivo: NO los reglamenta la DIAN** ("acuerdo entre las partes"). Se usa la convención común del
  mercado (ver §4.3), documentada como convención, no como regla.
- **Asunto:** `NIT;Nombre del facturador;Número;Código tipo de documento;Nombre comercial;Línea de negocio (opcional)`,
  separados por `;`, tomados del XML: `PartyTaxScheme/CompanyID`, `PartyTaxScheme/RegistrationName`, `cbc:ID`,
  `cbc:InvoiceTypeCode`, `PartyName/Name`. Ejemplo oficial: `99998888;Facturador Ejemplo;FEV500;01;Facturador Ejemplo;ContabilidadBog`.
- **Cuerpo del correo:** no reglamentado.

Cambio respecto a lo conversado: el prefijo `[PRUEBA]` **no** va en el asunto, porque rompería el formato que los
buzones de recepción de los adquirentes analizan. La marca de prueba va en el cuerpo, en el ApplicationResponse
simulado y en el PDF (que ya dice "SIN VALIDEZ FISCAL").

## 3. Flujo

```
emitir (API) ─► factura EN_PROCESO + outbox ENVIAR_DIAN
worker ENVIAR_DIAN ─► firmar factura ─► guardar xml_firmado ─► proveedor.emitir
        aceptada ─► FACTURADO + application_response guardada + outbox ENVIAR_CORREO   (una transacción)
worker ENVIAR_CORREO ─► armar AttachedDocument (firmado) + PDF ─► ZIP ─► guardar en `entrega` ─► SMTP ─► ENVIADA
```

La factura queda FACTURADA aunque el correo falle: ante la DIAN ya es válida; la entrega se sigue aparte.

## 4. Componentes

### 4.1 Firma conectada al flujo (`servicios/outbox.py`)
En `ENVIAR_DIAN`, antes de `proveedor.emitir`: `firma.firmar_xml(xml_sin_firmar, certificado)` → se guarda
`factura.xml_firmado` y se envía ese documento (reemplaza el comentario "Fase 6: aquí irá el XML FIRMADO").
Certificado desde `DIAN_CERTIFICADO_RUTA` / `DIAN_CERTIFICADO_CLAVE` (`firma.certificado_desde_entorno`).
Un reenvío tras rechazo vuelve a firmar (el XML cambia).

### 4.2 Certificado de prueba (`facturacion/certificado_prueba.py` + `python -m estacion.certificado`)
Genera un `.pfx` RSA 2048 autofirmado, válido 1 año, con `CN=<razón social> - CERTIFICADO DE PRUEBA SIN VALIDEZ`,
en la ruta indicada (fuera del repositorio; `*.pfx` ya está en `.gitignore`), con la clave que se le pase por
teclado o `--clave-stdin`. Imprime las dos variables a configurar. Nunca imprime la clave.

### 4.3 Nombres (`facturacion/paquete.py`)
Convención: `<prefijo><NIT 10 dígitos><PPP 000><AA><consecutivo hex 8>` con prefijos `ad` (AttachedDocument, .xml),
`fv` (PDF de factura de venta) y `z` (.zip). El consecutivo sale de una secuencia en la base **por año**, asignada en
la misma transacción que guarda la entrega (sin huecos ni repetidos). Ejemplo: `z0900123456000260000000a.zip`.

### 4.4 ApplicationResponse simulado (`facturacion/respuesta_simulada.py`)
El proveedor simulado no devuelve un ApplicationResponse. Se genera uno mínimo (UBL ApplicationResponse, código de
respuesta `02` = documento validado, referencia al CUFE y al número), con `cbc:Note` = "SIMULADO – banco de
pruebas, no emitido por la DIAN". `ProveedorFacturacion.emitir` pasa a poder devolverlo en `ResultadoEmision`
(`application_response: bytes | None`); el simulado lo llena con este generador. Se guarda en
`factura.application_response`.

### 4.5 AttachedDocument (`facturacion/attached.py`)
Según §6.4 del anexo 1.9 (campos AE01–AE55), construido **a partir del XML firmado** (igual que el PDF: el
contenedor no puede decir algo distinto de la factura):

- `UBLExtensions` con un `UBLExtension` para `ds:Signature` (firmado con `firma.py`, misma política XAdES).
  **[A confirmar en habilitación]** el anexo pide `UBLExtension 2..N` con DianExtensions y Signature; el ejemplo
  oficial no muestra DianExtensions. Se implementa como el ejemplo (solo la firma) y queda anotado.
- `UBLVersionID` "UBL 2.1", `CustomizationID` "Documentos adjuntos", `ProfileID` "Factura Electrónica de Venta",
  `ProfileExecutionID` = el de la factura, `ID` = consecutivo de la entrega, `IssueDate`/`IssueTime` = generación
  (≥ los de la factura), `DocumentType` "Contenedor de Factura Electrónica", `ParentDocumentID` = número de la factura.
- `SenderParty/PartyTaxScheme`: datos del emisor tomados del XML (razón social, NIT con DV y `schemeName="31"`,
  `TaxLevelCode`, `TaxScheme`).
- `ReceiverParty/PartyTaxScheme`: datos del comprador tomados del XML (nombre, documento con su `schemeName`, p. ej.
  `13` para cédula). `TaxLevelCode` es 1..1: la factura no trae la responsabilidad del comprador, así que se usa
  `R-99-PN` (no aplica – otros) **[a confirmar con el contador]**.
- `Attachment/ExternalReference`: `MimeCode` text/xml, `EncodingCode` UTF-8, `Description` = factura firmada en
  `CDATA`, byte a byte.
- `ParentDocumentLineReference`: `LineID` 1; `DocumentReference` con `ID` (número), `UUID schemeName="CUFE-SHA384"`,
  `IssueDate`, `DocumentType` "ApplicationResponse", el ApplicationResponse en `CDATA` y `ResultOfVerification`
  (`ValidatorID` "Unidad Especial Dirección de Impuestos y Aduanas Nacionales", `ValidationResultCode` 02,
  `ValidationDate`/`ValidationTime` de la respuesta).

### 4.6 Paquete (`facturacion/paquete.py`)
`armar(xml_firmado, application_response, pdf, consecutivo, ahora) -> Paquete(nombre_zip, zip_bytes, asunto)`.
ZIP en memoria (`zipfile`, DEFLATE) con exactamente dos archivos: `ad….xml` y `fv….pdf`. Si pasa de 2 MB →
`PaqueteDemasiadoGrande`. El asunto se arma desde el XML (§2).

### 4.7 Correo (`entrega/correo.py`)
Interfaz `EnviadorCorreo.enviar(mensaje)`. Implementaciones: `EnviadorSmtp` (stdlib `smtplib` + `email`, host/puerto
desde `ESTACION_SMTP_HOST`/`ESTACION_SMTP_PUERTO`, por defecto Mailpit `localhost:1025`, sin TLS ni usuario en el
banco de pruebas) y `EnviadorEnMemoria` (pruebas). Distingue `CorreoTemporal` (conexión, 4xx) de `CorreoRechazado`
(5xx, destinatario inválido). Remitente: correo de la estación; destinatario: `cliente.email`. Cuerpo texto + HTML
corto con la marca de banco de pruebas mientras `DIAN_AMBIENTE=2`.

### 4.8 Datos (migración `0005_entrega`)
- `factura.application_response TEXT`.
- Tabla `entrega`: `id`, `factura_id` (FK, UNIQUE: una entrega vigente por factura), `consecutivo` (por año),
  `anio`, `nombre_zip`, `zip BYTEA`, `estado` (`PENDIENTE`, `ENVIADA`, `FALLIDA`), `intentos`, `ultimo_error`,
  `creada_en`, `enviada_en`. El destinatario NO se copia (se lee del cliente al enviar; Ley 1581).
- Secuencia por año para el consecutivo (tabla `entrega_consecutivo(anio PK, siguiente)` con `FOR UPDATE`).
- `outbox.tipo` admite `ENVIAR_CORREO`.

### 4.9 API
- `GET /facturas/{id}`: agrega `entrega: {estado, nombre_zip, enviada_en} | null`.
- `GET /facturas/{id}/zip` (bombero y admin): descarga el ZIP guardado. 404 si no hay entrega.
- `POST /facturas/{id}/reenviar` (admin): encola otro `ENVIAR_CORREO` **con el mismo ZIP guardado** (no regenera).
  409 si la factura no está FACTURADA.

### 4.10 Infraestructura
Servicio `mailpit` (imagen `axllent/mailpit`, versión fija) en `infra/docker-compose.yml`: SMTP 1025 y web 8025
solo en `127.0.0.1`. `make mail` lo levanta solo. Variables nuevas documentadas en `.env.example`.

## 5. Manejo de errores

| Situación | Resultado |
|---|---|
| Sin certificado, clave mala o vencido | `ENVIAR_DIAN` FALLIDO con "configure DIAN_CERTIFICADO_* o corra `python -m estacion.certificado`"; factura sigue EN_PROCESO; auditoría. Nunca se envía sin firma |
| SMTP caído / 4xx | reintento con espera creciente (30 s → 1 h, 10 intentos); luego `entrega` FALLIDA |
| 5xx / destinatario inválido | FALLIDA sin reintentar; admin corrige el correo y usa `reenviar` |
| ZIP > 2 MB | FALLIDA + auditoría |
| `reenviar` de factura no FACTURADA | 409 · `GET /zip` sin entrega → 404 |

Errores y logs con identificadores únicamente: nunca correo, cédula ni nombre del cliente.

## 6. Pruebas (TDD)

- **Unitarias:** nombres con NIT/año/consecutivo de ejemplo; asunto desde el XML igual al formato del anexo;
  AttachedDocument válido contra `UBL-AttachedDocument-2.1.xsd` (OASIS), firma válida con `signxml`, factura en
  CDATA idéntica byte a byte; ApplicationResponse válido contra `UBL-ApplicationResponse-2.1.xsd`; ZIP con exactamente
  2 archivos; límite de 2 MB; certificado de prueba se genera, carga y firma.
- **Cola** (PostgreSQL real + `EnviadorEnMemoria` + `ProveedorSimulado`): emitir → FACTURADO → correo enviado con el
  ZIP; temporal reintenta; permanente queda FALLIDA; sin certificado falla cerrado; reenviar reutiliza el mismo ZIP;
  dos workers no envían dos veces (`SKIP LOCKED`).
- **API:** `GET /zip`, `reenviar`, permisos.
- **Manual de punta a punta:** Mailpit + API + worker + simulador; abrir el correo en `localhost:8025`.

## 7. Riesgos abiertos

- `DianExtensions` en el AttachedDocument y el `TaxLevelCode` del receptor persona natural: confirmar en habilitación.
- El ApplicationResponse es simulado: la estructura real la entrega la DIAN; al conectar el web service se guarda el
  real y este generador deja de usarse.
- Guardar el ZIP en la base crece ~50–100 KB por factura; aceptable para una estación, revisar si se pasa a archivos.
