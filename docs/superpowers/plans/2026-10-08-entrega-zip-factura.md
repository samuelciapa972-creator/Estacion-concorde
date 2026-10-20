# Entrega de la factura (ZIP con AttachedDocument + PDF, por correo) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada factura FACTURADA se entregue al cliente como un facturador real: correo con un único ZIP que contiene el AttachedDocument firmado y el PDF, guardado en la base y reenviable.

**Architecture:** La firma se conecta en el trabajo `ENVIAR_DIAN` de la cola; al quedar FACTURADA se encola `ENVIAR_CORREO`, que arma el AttachedDocument (desde el XML firmado + ApplicationResponse), el PDF y el ZIP, los guarda en `entrega` y los envía por SMTP (Mailpit en el banco de pruebas). Unidades pequeñas en `facturacion/` (documentos) y `entrega/` (correo); la cola recibe sus dependencias en un objeto `Recursos`.

**Tech Stack:** Python 3.12, PostgreSQL 16, psycopg 3, lxml, cryptography, reportlab, stdlib (`zipfile`, `smtplib`, `email`), pytest, Mailpit.

**Spec:** `docs/superpowers/specs/2026-10-08-entrega-zip-factura-design.md`

## Global Constraints

- Sin dependencias nuevas de Python (solo stdlib + las ya declaradas).
- Asunto: `NIT;RazónSocial;Número;InvoiceTypeCode;NombreComercial` tomados del XML firmado, separados por `;`, sin prefijos.
- Un único adjunto `.zip` ≤ 2 MB (2 * 1024 * 1024 bytes) con exactamente `ad….xml` y `fv….pdf`.
- Nombres: `<prefijo><NIT 10 dígitos><000><AA><consecutivo hex 8 minúsculas>`; prefijos `ad`, `fv`, `z`.
- Nunca enviar a la DIAN un XML sin firmar: sin certificado, `ENVIAR_DIAN` termina FALLIDO.
- Logs, errores y auditoría sin correo, cédula ni nombre del cliente (Ley 1581).
- Dinero con `Decimal`; fechas locales sin zona en la base, `-05:00` en los XML.
- Commits solo si el usuario los autoriza (los pasos "Commit" quedan como punto de control).
- Pruebas: `cd backend && PYTHONPATH=src ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:55432/estacion .venv/bin/python -m pytest …`

## Review Focus

1. Factura RECHAZADA y reenviada: debe volver a firmarse (el XML cambia) y no reutilizar el `xml_firmado` viejo → prueba en Task 7.
2. Correo del cliente corregido después del envío: `reenviar` usa el correo ACTUAL del cliente con el MISMO ZIP → prueba en Task 8.
3. Dos workers a la vez sobre el mismo `ENVIAR_CORREO`: un solo envío (`SKIP LOCKED`) → prueba en Task 7.
4. `reenviar` mientras ya hay un envío pendiente: 409, sin duplicar trabajos (índice `ux_outbox_un_trabajo_vivo`) → prueba en Task 8.
5. Certificado vencido o clave errada: falla cerrado con mensaje que no incluye la clave → prueba en Task 1 y Task 7.

---

### Task 1: Certificado de prueba, firma en cualquier extensión y esquemas UBL para pruebas

**Files:**
- Create: `backend/src/estacion/facturacion/certificado_prueba.py`, `backend/src/estacion/certificado.py`
- Modify: `backend/src/estacion/facturacion/firma.py` (parámetro `extension` en `firmar`/`firmar_xml`/`_hueco_de_la_firma`)
- Create: `backend/tests/fixtures/ubl21/` (copia de `xsd/common/*` y de `xsd/maindoc/UBL-{Invoice,AttachedDocument,ApplicationResponse}-2.1.xsd` del paquete OASIS `https://docs.oasis-open.org/ubl/os-UBL-2.1/UBL-2.1.zip`)
- Modify: `backend/tests/conftest.py` (fixtures `certificado`, `esquema_ubl`)
- Test: `backend/tests/test_certificado.py`

**Interfaces:**
- Produces: `generar_certificado_prueba(nombre: str, *, dias: int = 365, ahora: datetime | None = None) -> Certificado`; `guardar_pfx(cert: Certificado, ruta: Path, clave: str) -> None`; `firmar(arbol, certificado, *, ahora=None, extension: int = 2) -> bytes`; `firmar_xml(documento, certificado, *, ahora=None, extension: int = 2) -> bytes`; fixture `certificado: Certificado`; fixture `esquema_ubl(nombre: str) -> etree.XMLSchema` (nombre: "Invoice" | "AttachedDocument" | "ApplicationResponse").

- [ ] **Step 1: Pruebas que fallan** (`tests/test_certificado.py`)

```python
from datetime import datetime, timedelta, timezone
import pytest
from estacion import certificado as cli
from estacion.facturacion.certificado_prueba import generar_certificado_prueba, guardar_pfx
from estacion.facturacion.firma import FirmaError, cargar_certificado, firmar_xml
from test_xml_factura import construir

def test_genera_guarda_y_carga(tmp_path):
    c = generar_certificado_prueba("EDS PRUEBA SAS")
    assert "SIN VALIDEZ" in c.cert.subject.rfc4514_string()
    ruta = tmp_path / "prueba.pfx"
    guardar_pfx(c, ruta, "clave-123")
    leido = cargar_certificado(str(ruta), "clave-123")
    assert leido.cert.serial_number == c.cert.serial_number
    with pytest.raises(FirmaError) as e:
        cargar_certificado(str(ruta), "otra")
    assert "clave-123" not in str(e.value) and "otra" not in str(e.value)

def test_certificado_vencido_falla_cerrado():
    viejo = generar_certificado_prueba("X", dias=1, ahora=datetime.now(timezone.utc) - timedelta(days=10))
    with pytest.raises(FirmaError, match="venció"):
        firmar_xml(construir().xml, viejo)

def test_firma_en_la_primera_extension(certificado):
    # un documento con una sola extensión vacía (como el AttachedDocument)
    doc = b'<D xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"><ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent/></ext:UBLExtension></ext:UBLExtensions><x>1</x></D>'
    assert b"SignatureValue" in firmar_xml(doc, certificado, extension=1)

def test_cli_genera_el_pfx(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("clave-123\n"))
    ruta = tmp_path / "c.pfx"
    assert cli.main(["--salida", str(ruta), "--nombre", "EDS PRUEBA SAS", "--clave-stdin"]) == 0
    salida = capsys.readouterr().out
    assert "DIAN_CERTIFICADO_RUTA" in salida and "clave-123" not in salida
    assert cargar_certificado(str(ruta), "clave-123")
```

- [ ] **Step 2:** `pytest tests/test_certificado.py -v` → FAIL (`ModuleNotFoundError: estacion.facturacion.certificado_prueba`).

- [ ] **Step 3: Implementación**

`facturacion/certificado_prueba.py`:
```python
"""Certificado autofirmado SOLO para el banco de pruebas: no lo emite una entidad de certificación y la DIAN lo
rechazaría. Sirve para conectar la firma al flujo antes de tener el certificado real de la empresa."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from .firma import Certificado

MARCA = "CERTIFICADO DE PRUEBA SIN VALIDEZ"

def generar_certificado_prueba(nombre: str, *, dias: int = 365, ahora: datetime | None = None) -> Certificado:
    ahora = ahora or datetime.now(timezone.utc)
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"{nombre} - {MARCA}"[:64]),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CO"),
    ])
    cert = (x509.CertificateBuilder().subject_name(sujeto).issuer_name(sujeto).public_key(clave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora - timedelta(minutes=5))
            .not_valid_after(ahora + timedelta(days=dias)).sign(clave, hashes.SHA256()))
    return Certificado(clave, cert)

def guardar_pfx(c: Certificado, ruta: Path, clave: str) -> None:
    datos = pkcs12.serialize_key_and_certificates(b"prueba", c.clave, c.cert, None,
                                                  BestAvailableEncryption(clave.encode("utf-8")))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos)
    ruta.chmod(0o600)
```

`estacion/certificado.py` (CLI `python -m estacion.certificado --salida RUTA [--nombre N] [--clave-stdin]`): lee la clave con `getpass` dos veces (o una línea de stdin), llama `generar_certificado_prueba` + `guardar_pfx`, imprime:
```
Certificado de PRUEBA (sin validez) guardado en <ruta>. Configure:
  DIAN_CERTIFICADO_RUTA=<ruta absoluta>
  DIAN_CERTIFICADO_CLAVE=<la clave que digitó>
```
Devuelve 0; clave vacía → 2 con mensaje. Nunca imprime la clave.

`firma.py`: `_hueco_de_la_firma(raiz, extension)` usa `f"ext:UBLExtensions/ext:UBLExtension[{extension}]/ext:ExtensionContent"`; `firmar(..., extension: int = 2)` y `firmar_xml(..., extension: int = 2)` lo pasan; mensaje de error: `f"el documento no tiene el lugar de la firma (ext:UBLExtension[{extension}])"`.

`conftest.py`:
```python
from lxml import etree
from estacion.facturacion.certificado_prueba import generar_certificado_prueba
XSD = Path(__file__).resolve().parent / "fixtures" / "ubl21" / "maindoc"

@pytest.fixture(scope="session")
def certificado():
    return generar_certificado_prueba("ESTACION DE PRUEBA S A S")

@pytest.fixture(scope="session")
def esquema_ubl():
    cache: dict[str, etree.XMLSchema] = {}
    def cargar(nombre: str) -> etree.XMLSchema:
        if nombre not in cache:
            cache[nombre] = etree.XMLSchema(etree.parse(str(XSD / f"UBL-{nombre}-2.1.xsd")))
        return cache[nombre]
    return cargar
```

- [ ] **Step 4:** `pytest tests/test_certificado.py tests/test_firma.py -v` → PASS.
- [ ] **Step 5: Commit** (si se autoriza) `feat: certificado de prueba y firma en cualquier extensión`

---

### Task 2: Migración 0005 (entrega, consecutivo, application_response, ENVIAR_CORREO)

**Files:**
- Create: `backend/migrations/versions/0005_entrega.py`
- Modify: `backend/tests/test_migraciones.py` (versión `0005`, tablas nuevas, prueba de bajar)
- Modify: `backend/src/estacion/facturacion/modelos.py` (`TipoTrabajo.ENVIAR_CORREO`, `EstadoEntrega`)

**Interfaces:**
- Produces: tabla `entrega(id, factura_id UNIQUE, anio, consecutivo, nombre_zip, zip BYTEA, estado, intentos, ultimo_error, creada_en, enviada_en)`; tabla `entrega_consecutivo(anio PK, siguiente)`; `factura.application_response TEXT`; `TipoTrabajo.ENVIAR_CORREO = "ENVIAR_CORREO"`; `EstadoEntrega` (`PENDIENTE`, `ENVIADA`, `FALLIDA`).

- [ ] **Step 1: Pruebas que fallan** — en `test_migraciones.py`: cambiar las dos aserciones `== "0004"` por `== "0005"`, agregar `"entrega", "entrega_consecutivo"` al conjunto de tablas nuevas, y:

```python
def test_0005_entrega(bd):
    bd.ejecutar("INSERT INTO entrega_consecutivo (anio, siguiente) VALUES (2026, 1)")
    with pytest.raises(Exception, match="entrega_estado_check"):
        bd.ejecutar("""INSERT INTO entrega (factura_id, anio, consecutivo, nombre_zip, zip, estado)
                       VALUES (1, 2026, 1, 'z.zip', '\\x00', 'OTRO')""")

def test_bajar_la_0005(esquema_vacio):
    migrar(esquema_vacio.dsn)
    migrar(esquema_vacio.dsn, "0004", bajar=True)
    assert "entrega" not in tablas(esquema_vacio)
```

- [ ] **Step 2:** `pytest tests/test_migraciones.py -v` → FAIL (versión 0004).

- [ ] **Step 3: Implementación** (`0005_entrega.py`, mismo patrón `_sql` que la 0004):

```sql
-- UPGRADE
ALTER TABLE factura ADD COLUMN application_response TEXT;
ALTER TABLE outbox DROP CONSTRAINT outbox_tipo_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_tipo_check
    CHECK (tipo IN ('ENVIAR_DIAN', 'CONSULTAR_DIAN', 'ENVIAR_CORREO'));
CREATE TABLE entrega_consecutivo (
    anio      SMALLINT PRIMARY KEY CHECK (anio BETWEEN 2000 AND 2099),
    siguiente BIGINT   NOT NULL CHECK (siguiente >= 1)
);
CREATE TABLE entrega (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    factura_id   BIGINT NOT NULL UNIQUE REFERENCES factura (id),
    anio         SMALLINT NOT NULL,
    consecutivo  BIGINT NOT NULL CHECK (consecutivo BETWEEN 1 AND 4294967295),  -- 8 dígitos hex
    nombre_zip   TEXT NOT NULL CHECK (nombre_zip ~ '^z[0-9]{10}000[0-9]{2}[0-9a-f]{8}\.zip$'),
    zip          BYTEA NOT NULL CHECK (octet_length(zip) <= 2097152),
    estado       TEXT NOT NULL DEFAULT 'PENDIENTE',
    intentos     SMALLINT NOT NULL DEFAULT 0,
    ultimo_error TEXT,
    creada_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    enviada_en   TIMESTAMPTZ,
    CONSTRAINT entrega_estado_check CHECK (estado IN ('PENDIENTE', 'ENVIADA', 'FALLIDA')),
    CONSTRAINT entrega_consecutivo_unico UNIQUE (anio, consecutivo)
);
-- DOWNGRADE
DROP TABLE entrega; DROP TABLE entrega_consecutivo;
DELETE FROM outbox WHERE tipo = 'ENVIAR_CORREO';
ALTER TABLE outbox DROP CONSTRAINT outbox_tipo_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_tipo_check CHECK (tipo IN ('ENVIAR_DIAN', 'CONSULTAR_DIAN'));
ALTER TABLE factura DROP COLUMN application_response;
```
Verificar con `\d outbox` que el CHECK de la 0002 se llama `outbox_tipo_check`; si no, usar el nombre real.

`modelos.py`: `ENVIAR_CORREO = "ENVIAR_CORREO"  # entregar el ZIP al cliente` y `class EstadoEntrega(StrEnum): PENDIENTE, ENVIADA, FALLIDA`.

- [ ] **Step 4:** `pytest tests/test_migraciones.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: migración 0005 de la entrega al cliente`

---

### Task 3: ApplicationResponse simulado y su paso por el proveedor

**Files:**
- Create: `backend/src/estacion/facturacion/respuesta_simulada.py`
- Modify: `backend/src/estacion/facturacion/modelos.py` (`ResultadoEmision.application_response`), `facturacion/proveedor.py`
- Test: `backend/tests/test_respuesta_simulada.py`

**Interfaces:**
- Consumes: XML de factura (bytes) de `xml_factura.construir_factura` (firmado o no).
- Produces: `generar_respuesta(factura_xml: bytes, *, validada_en: datetime) -> bytes`; `ResultadoEmision.application_response: bytes | None = field(default=None, compare=False)`; `NOTA_SIMULADA = "SIMULADO – banco de pruebas, no emitido por la DIAN"`.

- [ ] **Step 1: Pruebas que fallan**

```python
from datetime import datetime
from lxml import etree
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.facturacion.respuesta_simulada import NOTA_SIMULADA, generar_respuesta
from test_xml_factura import construir

NS = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
      "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"}

def test_respuesta_valida_y_marcada(esquema_ubl):
    f = construir()
    r = etree.fromstring(generar_respuesta(f.xml, validada_en=datetime(2026, 9, 29, 10, 25)))
    assert esquema_ubl("ApplicationResponse").validate(r), esquema_ubl("ApplicationResponse").error_log
    assert r.findtext("cbc:Note", namespaces=NS) == NOTA_SIMULADA
    assert r.findtext("cac:DocumentResponse/cac:Response/cbc:ResponseCode", namespaces=NS) == "02"
    assert r.findtext("cac:DocumentResponse/cac:DocumentReference/cbc:UUID", namespaces=NS) == f.cufe
    assert r.findtext("cbc:IssueDate", namespaces=NS) == "2026-09-29"

def test_el_simulado_la_devuelve_si_hay_documento():
    from estacion.facturacion.modelos import SolicitudFactura
    f = construir()
    sol = construir_solicitud_con_documento(f)  # helper: SolicitudFactura(..., numero=f.numero, cufe=f.cufe, documento=f.xml)
    assert ProveedorSimulado().emitir(sol).application_response.startswith(b"<?xml")
```
(`construir_solicitud_con_documento` se define en el mismo archivo a partir de `test_xml_factura.SOLICITUD` con `dataclasses.replace`.)

- [ ] **Step 2:** FAIL (módulo no existe).

- [ ] **Step 3: Implementación** — `generar_respuesta` lee del XML: `cbc:ID`, `cbc:UUID` (CUFE), `cbc:ProfileExecutionID`, emisor (`AccountingSupplierParty/Party/PartyTaxScheme`: `RegistrationName`, `CompanyID` con sus atributos). Construye en este orden (UBL 2.1):

```
ApplicationResponse (ns urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2)
  cbc:UBLVersionID "UBL 2.1" · cbc:CustomizationID "1" · cbc:ProfileID "DIAN 2.1"
  cbc:ProfileExecutionID <de la factura>
  cbc:ID "SIM" + numero · cbc:UUID schemeName="CUDE-SHA384" = sha384(numero+cufe+fecha).hexdigest()
  cbc:IssueDate / cbc:IssueTime (validada_en, hora + "-05:00")
  cbc:Note NOTA_SIMULADA
  cac:SenderParty/cac:PartyTaxScheme: RegistrationName "Unidad Especial Dirección de Impuestos y Aduanas Nacionales",
      CompanyID 800197268 (schemeAgencyID 195, schemeID 4, schemeName 31), cac:TaxScheme(ID 01, Name IVA)
  cac:ReceiverParty/cac:PartyTaxScheme: los del emisor de la factura, cac:TaxScheme(ID 01, Name IVA)
  cac:DocumentResponse
     cac:Response: cbc:ResponseCode "02", cbc:Description "Documento validado por la DIAN (SIMULADO)"
     cac:DocumentReference: cbc:ID numero, cbc:UUID schemeName="CUFE-SHA384" cufe
```
Serializa con `xml_declaration=True, encoding="UTF-8"`.

`modelos.py`: `application_response: bytes | None = field(default=None, compare=False)` en `ResultadoEmision`.
`proveedor.py` (`ProveedorSimulado.emitir`): si `solicitud.documento` no es None, `application_response=generar_respuesta(solicitud.documento, validada_en=self._reloj())`; `detalle={"simulado": True}` se mantiene.

- [ ] **Step 4:** `pytest tests/test_respuesta_simulada.py tests/test_outbox.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: ApplicationResponse simulado`

---

### Task 4: AttachedDocument

**Files:**
- Create: `backend/src/estacion/facturacion/attached.py`
- Test: `backend/tests/test_attached.py`

**Interfaces:**
- Consumes: `firma.firmar(arbol, certificado, ahora=..., extension=1)`; `respuesta_simulada.generar_respuesta`.
- Produces: `construir_attached(factura_firmada: bytes, application_response: bytes, *, consecutivo: int, ahora: datetime, certificado: Certificado) -> bytes`; `class AttachedInvalido(ValueError)`; constante `RESPONSABILIDAD_RECEPTOR_SIN_DATO = "R-99-PN"`.

- [ ] **Step 1: Pruebas que fallan**

```python
from datetime import datetime
from lxml import etree
from signxml import XMLVerifier
from estacion.facturacion.attached import AttachedInvalido, construir_attached
from estacion.facturacion.firma import firmar_xml
from estacion.facturacion.respuesta_simulada import generar_respuesta
from test_xml_factura import construir
import pytest

AHORA = datetime(2026, 9, 29, 10, 30)
NS = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
      "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"}

@pytest.fixture
def piezas(certificado):
    f = construir()
    firmada = firmar_xml(f.xml, certificado)
    return f, firmada, generar_respuesta(firmada, validada_en=datetime(2026, 9, 29, 10, 25))

def test_valido_firmado_y_con_la_factura_byte_a_byte(piezas, certificado, esquema_ubl):
    f, firmada, ar = piezas
    xml = construir_attached(firmada, ar, consecutivo=10, ahora=AHORA, certificado=certificado)
    raiz = etree.fromstring(xml)
    assert esquema_ubl("AttachedDocument").validate(raiz), esquema_ubl("AttachedDocument").error_log
    XMLVerifier().verify(xml, x509_cert=certificado.cert, expect_references=3)
    t = lambda p: raiz.findtext(p, namespaces=NS)
    assert t("cbc:CustomizationID") == "Documentos adjuntos"
    assert t("cbc:ProfileID") == "Factura Electrónica de Venta"
    assert t("cbc:DocumentType") == "Contenedor de Factura Electrónica"
    assert t("cbc:ParentDocumentID") == f.numero and t("cbc:ID") == "10"
    assert t("cac:Attachment/cac:ExternalReference/cbc:Description").encode("utf-8") == firmada
    ref = "cac:ParentDocumentLineReference/cac:DocumentReference/"
    assert t(ref + "cbc:UUID") == f.cufe and t(ref + "cbc:DocumentType") == "ApplicationResponse"
    assert t(ref + "cac:Attachment/cac:ExternalReference/cbc:Description").encode("utf-8") == ar
    assert t(ref + "cac:ResultOfVerification/cbc:ValidationResultCode") == "02"
    assert t("cac:ReceiverParty/cac:PartyTaxScheme/cbc:TaxLevelCode") == "R-99-PN"

def test_rechaza_factura_sin_firmar(piezas, certificado):
    f, _, ar = piezas
    with pytest.raises(AttachedInvalido, match="sin firmar"):
        construir_attached(f.xml, ar, consecutivo=1, ahora=AHORA, certificado=certificado)

def test_fecha_del_contenedor_no_anterior_a_la_factura(piezas, certificado):
    _, firmada, ar = piezas
    with pytest.raises(AttachedInvalido, match="anterior"):
        construir_attached(firmada, ar, consecutivo=1, ahora=datetime(2020, 1, 1), certificado=certificado)
```

- [ ] **Step 2:** FAIL.

- [ ] **Step 3: Implementación** (`attached.py`): parsea la factura con `XMLParser(resolve_entities=False, no_network=True)`; exige `ds:Signature` (si no → `AttachedInvalido("la factura está sin firmar")`); exige `ahora.date() >= IssueDate` (si no → `AttachedInvalido("la fecha del contenedor es anterior a la factura")`). Construye, en el orden del XSD y del ejemplo del anexo 1.9 §6.4:

```
AttachedDocument (ns urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2; nsmap: cac, cbc, ext, ds, xades, xades141)
  ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent   (vacío: lo llena firmar(..., extension=1))
  cbc:UBLVersionID "UBL 2.1" · cbc:CustomizationID "Documentos adjuntos" · cbc:ProfileID "Factura Electrónica de Venta"
  cbc:ProfileExecutionID (de la factura) · cbc:ID str(consecutivo) · cbc:IssueDate · cbc:IssueTime (+"-05:00")
  cbc:DocumentType "Contenedor de Factura Electrónica" · cbc:ParentDocumentID (cbc:ID de la factura)
  cac:SenderParty/cac:PartyTaxScheme   ← copia de AccountingSupplierParty/Party/PartyTaxScheme:
        RegistrationName, CompanyID (mismos atributos), TaxLevelCode (texto; listName "No aplica"), TaxScheme(ID, Name)
  cac:ReceiverParty/cac:PartyTaxScheme ← de AccountingCustomerParty/Party/PartyTaxScheme:
        RegistrationName, CompanyID (mismos atributos), TaxLevelCode = el de la factura o R-99-PN, TaxScheme(ID, Name)
  cac:Attachment/cac:ExternalReference: cbc:MimeCode "text/xml", cbc:EncodingCode "UTF-8",
        cbc:Description = etree.CDATA(factura_firmada.decode("utf-8"))
  cac:ParentDocumentLineReference
     cbc:LineID "1"
     cac:DocumentReference: cbc:ID numero · cbc:UUID schemeName="CUFE-SHA384" · cbc:IssueDate (de la factura)
        · cbc:DocumentType "ApplicationResponse"
        · cac:Attachment/cac:ExternalReference (MimeCode, EncodingCode, Description = CDATA(ar))
        · cac:ResultOfVerification: ValidatorID "Unidad Especial Dirección de Impuestos y Aduanas Nacionales",
          ValidationResultCode (= ResponseCode del AR), ValidationDate/ValidationTime (= IssueDate/IssueTime del AR)
```
Devuelve `firmar(raiz, certificado, ahora=<ahora con zona -05:00>, extension=1)`. Comentario en el módulo: "[A confirmar en habilitación] DianExtensions en el contenedor; TaxLevelCode del receptor persona natural."

- [ ] **Step 4:** `pytest tests/test_attached.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: AttachedDocument firmado`

---

### Task 5: Paquete ZIP, nombres y asunto

**Files:**
- Create: `backend/src/estacion/facturacion/paquete.py`
- Modify: `backend/src/estacion/facturacion/representacion.py` (mover aquí `texto_validacion` desde `api/rutas/facturas.py`), `backend/src/estacion/api/rutas/facturas.py` (importarlo)
- Test: `backend/tests/test_paquete.py`

**Interfaces:**
- Consumes: `construir_attached`, `representacion.generar_pdf`, `leer_xml`, `texto_validacion`.
- Produces: `nombre_archivo(prefijo: Literal["ad","fv","z"], nit: str, anio: int, consecutivo: int, ext: str) -> str`; `asunto(factura_firmada: bytes) -> str`; `@dataclass Paquete(nombre_zip: str, zip: bytes, asunto: str)`; `armar(factura_firmada: bytes, application_response: bytes, *, consecutivo: int, ahora: datetime, certificado: Certificado, validacion: str) -> Paquete`; `class PaqueteDemasiadoGrande(ValueError)`; `LIMITE_ZIP = 2 * 1024 * 1024`.

- [ ] **Step 1: Pruebas que fallan**

```python
import io, zipfile
from datetime import datetime
import pytest
from estacion.facturacion import paquete
from estacion.facturacion.firma import firmar_xml
from estacion.facturacion.respuesta_simulada import generar_respuesta
from test_xml_factura import construir

def test_nombre_archivo():
    assert paquete.nombre_archivo("z", "900123456", 2026, 10, "zip") == "z0900123456000260000000a.zip"
    assert paquete.nombre_archivo("ad", "900123456", 2026, 4294967295, "xml") == "ad090012345600026ffffffff.xml"
    with pytest.raises(ValueError):
        paquete.nombre_archivo("z", "900123456", 2026, 0, "zip")

def test_asunto_desde_el_xml(certificado):
    firmada = firmar_xml(construir().xml, certificado)
    # NIT;RazónSocial;Número;Tipo;NombreComercial (anexo v1.8 §9.1)
    assert paquete.asunto(firmada) == "900123456;ESTACION DE PRUEBA S A S;SETP215802;01;EDS PRUEBA"

def test_zip_con_dos_archivos(certificado):
    firmada = firmar_xml(construir().xml, certificado)
    ar = generar_respuesta(firmada, validada_en=datetime(2026, 9, 29, 10, 25))
    p = paquete.armar(firmada, ar, consecutivo=10, ahora=datetime(2026, 9, 29, 10, 30), certificado=certificado,
                      validacion="SIMULADA")
    assert p.nombre_zip == "z0900123456000260000000a.zip"
    with zipfile.ZipFile(io.BytesIO(p.zip)) as z:
        assert sorted(z.namelist()) == ["ad0900123456000260000000a.xml", "fv0900123456000260000000a.pdf"]
        assert z.read("fv0900123456000260000000a.pdf").startswith(b"%PDF")
    assert len(p.zip) <= paquete.LIMITE_ZIP

def test_zip_demasiado_grande(certificado, monkeypatch):
    monkeypatch.setattr(paquete, "LIMITE_ZIP", 100)
    firmada = firmar_xml(construir().xml, certificado)
    ar = generar_respuesta(firmada, validada_en=datetime(2026, 9, 29, 10, 25))
    with pytest.raises(paquete.PaqueteDemasiadoGrande):
        paquete.armar(firmada, ar, consecutivo=1, ahora=datetime(2026, 9, 29, 10, 30), certificado=certificado,
                      validacion="x")
```
(Los valores exactos de razón social y nombre comercial salen del `EMISOR` de `test_xml_factura.py`; ajustar el literal del asunto a ese emisor al escribir la prueba.)

- [ ] **Step 2:** FAIL.

- [ ] **Step 3: Implementación**

```python
def nombre_archivo(prefijo, nit, anio, consecutivo, ext):
    if not 1 <= consecutivo <= 0xFFFFFFFF:
        raise ValueError("consecutivo fuera de rango (1 a FFFFFFFF)")
    return f"{prefijo}{int(nit):010d}000{anio % 100:02d}{consecutivo:08x}.{ext}"
```
`asunto`: lee del XML `PartyTaxScheme/CompanyID`, `PartyTaxScheme/RegistrationName`, `cbc:ID`, `cbc:InvoiceTypeCode`, `PartyName/Name` (si falta, RegistrationName) y los une con `";"`; quita `;` y saltos de línea de cada campo (`replace(";", ",")`).
`armar`: `ad = construir_attached(...)`; `pdf = generar_pdf(leer_xml(factura_firmada), validacion=validacion)`; ZIP con `zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)`, fecha fija de los miembros = `ahora` (`ZipInfo(date_time=ahora.timetuple()[:6])`); si `len > LIMITE_ZIP` → `PaqueteDemasiadoGrande(f"el ZIP pesa {len} bytes; el máximo es {LIMITE_ZIP}")`.
`texto_validacion` se mueve tal cual a `representacion.py`; `facturas.py` lo importa de ahí.

- [ ] **Step 4:** `pytest tests/test_paquete.py tests/test_representacion.py tests/test_api.py -k "pdf or paquete" -v` → PASS.
- [ ] **Step 5: Commit** `feat: paquete ZIP de la factura`

---

### Task 6: Envío de correo

**Files:**
- Create: `backend/src/estacion/entrega/__init__.py`, `backend/src/estacion/entrega/correo.py`
- Test: `backend/tests/test_correo.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) Mensaje(remitente: str, destinatario: str, asunto: str, texto: str, html: str, adjunto_nombre: str, adjunto: bytes)`; `class EnviadorCorreo(Protocol): def enviar(self, m: Mensaje) -> None`; `class CorreoTemporal(Exception)`; `class CorreoRechazado(Exception)`; `EnviadorSmtp(host: str, puerto: int, *, timeout: float = 10)`; `EnviadorSmtp.desde_entorno(entorno: Mapping[str, str] | None = None)` (lee `ESTACION_SMTP_HOST` def. `localhost`, `ESTACION_SMTP_PUERTO` def. `1025`); `EnviadorEnMemoria` con `.enviados: list[Mensaje]` y `.falla_siguiente: Literal["temporal","rechazo"] | None`; `mensaje_factura(*, remitente, destinatario, asunto, nombre_zip, zip, prueba: bool) -> Mensaje`.

- [ ] **Step 1: Pruebas que fallan**

```python
import smtplib, threading
import pytest
from estacion.entrega.correo import (CorreoRechazado, CorreoTemporal, EnviadorEnMemoria, EnviadorSmtp,
                                     mensaje_factura)

M = mensaje_factura(remitente="estacion@example.com", destinatario="ana@example.com", asunto="1;A;B;01;C",
                    nombre_zip="z.zip", zip=b"PK\x03\x04", prueba=True)

def test_mensaje_de_prueba_lo_dice_en_el_cuerpo():
    assert "banco de pruebas" in M.texto.lower() and "banco de pruebas" in M.html.lower()
    assert M.asunto == "1;A;B;01;C"

def test_smtp_entrega_el_adjunto(servidor_smtp):  # fixture: aiosmtpd NO; usar smtpd mínimo con socketserver
    host, puerto, recibidos = servidor_smtp
    EnviadorSmtp(host, puerto).enviar(M)
    (crudo,) = recibidos
    assert b"Subject: 1;A;B;01;C" in crudo and b'filename="z.zip"' in crudo

def test_smtp_caido_es_temporal():
    with pytest.raises(CorreoTemporal):
        EnviadorSmtp("127.0.0.1", 1, timeout=1).enviar(M)

def test_5xx_es_rechazo(monkeypatch):
    class Falso:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def send_message(self, m): raise smtplib.SMTPRecipientsRefused({"ana@example.com": (550, b"no existe")})
    monkeypatch.setattr(smtplib, "SMTP", Falso)
    with pytest.raises(CorreoRechazado) as e:
        EnviadorSmtp("x", 25).enviar(M)
    assert "ana@example.com" not in str(e.value)  # Ley 1581

def test_en_memoria():
    e = EnviadorEnMemoria()
    e.enviar(M)
    assert e.enviados == [M]
    e.falla_siguiente = "temporal"
    with pytest.raises(CorreoTemporal):
        e.enviar(M)
```
Fixture `servidor_smtp` (en `test_correo.py`): servidor SMTP mínimo con `socketserver.ThreadingTCPServer` en `127.0.0.1:0` que responde `220/250/354/250/221` y guarda los bytes entre `DATA` y `.`; se apaga al terminar.

- [ ] **Step 2:** FAIL.

- [ ] **Step 3: Implementación** — `EnviadorSmtp.enviar`: arma `email.message.EmailMessage` (From, To, Subject, `set_content(texto)`, `add_alternative(html, subtype="html")`, `add_attachment(adjunto, maintype="application", subtype="zip", filename=adjunto_nombre)`), `with smtplib.SMTP(host, puerto, timeout=timeout) as s: s.send_message(msg)`. Clasificación:
  - `OSError`, `smtplib.SMTPServerDisconnected`, `smtplib.SMTPConnectError`, y `SMTPResponseException` con código 4xx → `CorreoTemporal(f"servidor de correo no disponible: {type(e).__name__}")`.
  - `SMTPRecipientsRefused`, `SMTPSenderRefused`, `SMTPDataError`/`SMTPResponseException` 5xx → `CorreoRechazado(f"el servidor rechazó el correo (código {codigo})")`.
  - Los mensajes nunca incluyen direcciones.
`mensaje_factura`: texto "Adjuntamos su factura electrónica <número>…" (el número sale del asunto, campo 3); si `prueba`, primera línea: "BANCO DE PRUEBAS: esta factura no tiene validez fiscal."

- [ ] **Step 4:** `pytest tests/test_correo.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: envío de correo con adjunto`

---

### Task 7: Cola — firmar antes de emitir, guardar el AR, entregar por correo

**Files:**
- Modify: `backend/src/estacion/servicios/outbox.py`, `backend/src/estacion/worker.py`
- Create: `backend/src/estacion/servicios/entrega.py` (consecutivo y guardado de la entrega)
- Modify: `backend/tests/conftest.py` (fixture `recursos`), `backend/tests/test_outbox.py`, `backend/tests/test_api.py`, `backend/tests/test_ventas.py` (pasar `recursos=`; ajustar aserciones que contaban un solo trabajo)
- Test: `backend/tests/test_entrega.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: `@dataclass(frozen=True) Recursos(certificado: Certificado | None, enviador: EnviadorCorreo, error_certificado: str = "")`; `procesar_siguiente(conn, proveedor, politica=None, *, recursos: Recursos)`; `procesar_pendientes(conn, proveedor, politica=None, limite=100, *, recursos: Recursos)`; `servicios/entrega.py`: `reservar_consecutivo(conn, anio: int) -> int`, `obtener(conn, factura_id: int) -> EntregaGuardada | None`, `guardar(conn, factura_id, anio, consecutivo, nombre_zip, zip) -> EntregaGuardada`, `marcar(conn, factura_id, estado: EstadoEntrega, *, error: str | None = None)`; `@dataclass EntregaGuardada(id, factura_id, nombre_zip, zip, estado, enviada_en)`.

- [ ] **Step 1: Pruebas que fallan** (`tests/test_entrega.py`)

```python
import io, zipfile
from conftest import ENTORNO_DIAN
from estacion.entrega.correo import EnviadorEnMemoria
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.servicios.outbox import PoliticaReintentos, Recursos, procesar_pendientes
from test_outbox import cola, factura, solicitar
import pytest
pytestmark = pytest.mark.bd

def test_facturada_se_entrega_por_correo(escenario, conn, certificado):
    f, _ = solicitar(escenario, conn)
    enviador = EnviadorEnMemoria()
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, enviador))
    assert factura(escenario, f.id)[0] == "FACTURADO"
    assert [t for t, *_ in cola(escenario, f.id)] == ["ENVIAR_DIAN", "ENVIAR_CORREO"]
    (m,) = enviador.enviados
    assert m.destinatario == "ana@example.com" and m.asunto.split(";")[2] == f.numero
    with zipfile.ZipFile(io.BytesIO(m.adjunto)) as z:
        assert len(z.namelist()) == 2
    d = escenario.datos
    assert d.uno("SELECT estado FROM entrega WHERE factura_id = %s", (f.id,)) == "ENVIADA"
    assert d.uno("SELECT xml_firmado LIKE '%%SignatureValue%%' FROM factura WHERE id = %s", (f.id,))

def test_sin_certificado_falla_cerrado(escenario, conn):
    f, _ = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    procesar_pendientes(conn, proveedor, recursos=Recursos(None, EnviadorEnMemoria(), "falta DIAN_CERTIFICADO_RUTA"))
    assert proveedor.llamadas == 0
    assert factura(escenario, f.id)[0] == "EN_PROCESO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "FALLIDO", 1)]
    assert "python -m estacion.certificado" in escenario.datos.uno("SELECT ultimo_error FROM outbox")

def test_correo_temporal_reintenta_y_rechazo_no(escenario, conn, certificado):
    f, _ = solicitar(escenario, conn)
    e = EnviadorEnMemoria(); e.falla_siguiente = "temporal"
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "PENDIENTE", 1)
    escenario.datos.conn.execute("UPDATE outbox SET proximo_intento = now()")
    e.falla_siguiente = "rechazo"
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "FALLIDO", 2)
    assert escenario.datos.uno("SELECT estado FROM entrega WHERE factura_id = %s", (f.id,)) == "FALLIDA"
    assert factura(escenario, f.id)[0] == "FACTURADO"  # la factura sigue válida

def test_reintento_reutiliza_el_mismo_zip(escenario, conn, certificado):
    f, _ = solicitar(escenario, conn)
    e = EnviadorEnMemoria(); e.falla_siguiente = "temporal"
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    primero = escenario.datos.uno("SELECT zip FROM entrega WHERE factura_id = %s", (f.id,))
    escenario.datos.conn.execute("UPDATE outbox SET proximo_intento = now()")
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    assert e.enviados[0].adjunto == bytes(primero)

def test_rechazada_y_reenviada_se_vuelve_a_firmar(escenario, conn, certificado):
    f, origen = solicitar(escenario, conn)
    p = ProveedorSimulado(); p.falla_siguiente = "rechazo"
    procesar_pendientes(conn, p, recursos=Recursos(certificado, EnviadorEnMemoria()))
    primera = escenario.datos.uno("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,))
    solicitar(escenario, conn, origen)  # reenvío con el mismo número
    assert escenario.datos.uno("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,)) is None
    procesar_pendientes(conn, p, recursos=Recursos(certificado, EnviadorEnMemoria()))
    assert escenario.datos.uno("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,)) != primera
```

- [ ] **Step 2:** FAIL (`ImportError: Recursos`).

- [ ] **Step 3: Implementación**
  - `outbox.py`: agregar `Recursos`; firmar en `_enviar` antes de emitir:
    ```python
    if recursos.certificado is None:
        _terminar(conn, trabajo_id, "FALLIDO", error=_recortar(
            f"no se puede firmar: {recursos.error_certificado or 'sin certificado'}. Configure DIAN_CERTIFICADO_RUTA "
            "y DIAN_CERTIFICADO_CLAVE, o genere uno de prueba con `python -m estacion.certificado`"))
        auditar(conn, "FIRMA_NO_DISPONIBLE", "factura", f.id, detalle={"numero": f.numero})
        return f.estado
    try:
        firmado = firmar_xml(f.xml.encode("utf-8"), recursos.certificado)
    except FirmaError as e:   # vencido, documento dañado: mismo tratamiento
        ... FALLIDO + auditoría "FIRMA_FALLIDA", return f.estado
    conn.execute("UPDATE factura SET xml_firmado = %s WHERE id = %s", (firmado.decode("utf-8"), f.id))
    ```
    y `documento=firmado` en la solicitud. `_aceptada`: guarda `application_response` (`resultado.application_response.decode()` si no es None) y encola `ENVIAR_CORREO` en la misma transacción.
  - Nuevo `_entregar(conn, politica, trabajo_id, intentos, f, recursos)`:
    1. `e = entrega.obtener(conn, f.id)`; si None: lee `xml_firmado`, `application_response`, `respuesta_dian`; si falta alguno → FALLIDO "falta el XML firmado o la respuesta de la DIAN"; si no, `anio = fecha_emision.year`, `c = entrega.reservar_consecutivo(conn, anio)`, `p = paquete.armar(..., validacion=texto_validacion(respuesta_dian), ahora=ahora_local())`, `e = entrega.guardar(...)`. Errores de armado (`PaqueteDemasiadoGrande`, `AttachedInvalido`, `RepresentacionInvalida`, `FirmaError`) → outbox FALLIDO, `entrega` sin crear, auditoría `ENTREGA_NO_ARMADA`.
    2. Destinatario = `leer_cliente(conn, f.cliente_id, solo_activos=False).email`; remitente = `estacion.email` (consulta a `estacion` activa); asunto = `paquete.asunto(xml_firmado)`.
    3. `recursos.enviador.enviar(mensaje_factura(..., prueba=software ambiente "2"))` (ambiente: `ProfileExecutionID` del XML).
    4. OK → `entrega.marcar(ENVIADA)` + `enviada_en=now()`, outbox HECHO, auditoría `FACTURA_ENTREGADA`. `CorreoTemporal` → si `intentos >= politica.max_intentos`: FALLIDO + `marcar(FALLIDA)`; si no: `proximo_intento = now() + politica.espera(intentos)`. `CorreoRechazado` → FALLIDO + `marcar(FALLIDA, error=...)`.
  - `servicios/entrega.py`: `reservar_consecutivo` = `INSERT INTO entrega_consecutivo (anio, siguiente) VALUES (%s, 1) ON CONFLICT DO NOTHING` + `SELECT siguiente … FOR UPDATE` + `UPDATE … siguiente + 1`.
  - `emision.py` (reenvío tras rechazo): el `UPDATE factura` ya pone `xml_firmado = NULL`; agregar `application_response = NULL`.
  - `worker.py`: construir `Recursos`:
    ```python
    try:
        cert, error_cert = certificado_desde_entorno(), ""
    except FirmaError as e:
        cert, error_cert = None, str(e)
        log.error("sin certificado de firma: %s (las facturas no se enviarán)", e)
    recursos = Recursos(cert, EnviadorSmtp.desde_entorno(), error_cert)
    ```
  - `conftest.py`: `@pytest.fixture def recursos(certificado): return Recursos(certificado, EnviadorEnMemoria())`; en `test_outbox.py`, `test_api.py`, `test_ventas.py` pasar `recursos=recursos` (o `Recursos(certificado, EnviadorEnMemoria())`) a cada `procesar_*`; las aserciones que comparaban la lista completa de trabajos/acciones ahora incluyen `ENVIAR_CORREO` / `FACTURA_ENTREGADA` (`test_envio_aceptado`: `cola == [("ENVIAR_DIAN","HECHO",1), ("ENVIAR_CORREO","HECHO",1)]`); `test_ventas.py` `(hecho,) = …` → `hecho = …[0]`; `test_worker_una_vez` define `DIAN_CERTIFICADO_RUTA/CLAVE` con un `.pfx` de `guardar_pfx` en `tmp_path` y `ESTACION_SMTP_PUERTO=1` (correo queda reintentando; la factura FACTURADA). Agregar `test_dos_workers_un_solo_correo` con dos conexiones: la primera toma `ENVIAR_CORREO` dentro de una transacción abierta y la segunda `procesar_siguiente` devuelve None.

- [ ] **Step 4:** `pytest -q` (suite completa) → PASS.
- [ ] **Step 5: Commit** `feat: la cola firma, guarda la validación y entrega el ZIP por correo`

---

### Task 8: API — estado de la entrega, descarga y reenvío

**Files:**
- Modify: `backend/src/estacion/api/esquemas.py` (`EntregaSalida`, campo `entrega` en `FacturaSalida`), `backend/src/estacion/api/rutas/facturas.py`
- Modify: `Frontend/openapi.json` (regenerado con `make tipos`; solo el contrato)
- Test: `backend/tests/test_api_entrega.py`

**Interfaces:**
- Consumes: `servicios.entrega.obtener`, `TipoTrabajo.ENVIAR_CORREO`.
- Produces: `EntregaSalida(estado: Literal["PENDIENTE","ENVIADA","FALLIDA"], nombre_zip: str, enviada_en: datetime | None)`; `GET /facturas/{id}/zip` (`application/zip`, `Content-Disposition: attachment; filename="<nombre_zip>"`); `POST /facturas/{id}/reenviar` → 202 `{"mensaje": "reenvío en cola"}`.

- [ ] **Step 1: Pruebas que fallan**

```python
import pytest
from conftest import clave
from estacion.entrega.correo import EnviadorEnMemoria
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.servicios.outbox import Recursos, procesar_pendientes
pytestmark = pytest.mark.bd

def emitida_y_entregada(api, certificado, enviador):
    d = api.e.datos
    r = api.post("/facturas", {"clave": clave(), "origen_tipo": "despacho", "origen_id": d.despacho(),
                               "cliente_id": api.e.cliente, "medio_pago": "EFECTIVO"}, como=api.bombero)
    procesar_pendientes(d.conn, ProveedorSimulado(), recursos=Recursos(certificado, enviador))
    return r.json()["id"]

def test_estado_y_descarga(api, certificado):
    fid = emitida_y_entregada(api, certificado, EnviadorEnMemoria())
    f = api.get(f"/facturas/{fid}", como=api.bombero).json()
    assert f["entrega"]["estado"] == "ENVIADA" and f["entrega"]["nombre_zip"].startswith("z")
    z = api.get(f"/facturas/{fid}/zip", como=api.bombero)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert z.content.startswith(b"PK")

def test_reenviar_con_el_correo_corregido(api, certificado):
    e = EnviadorEnMemoria()
    fid = emitida_y_entregada(api, certificado, e)
    api.e.datos.conn.execute("UPDATE cliente SET email = 'nuevo@example.com' WHERE id = %s", (api.e.cliente,))
    assert api.post(f"/facturas/{fid}/reenviar", {}, como=api.admin).status_code == 202
    assert api.post(f"/facturas/{fid}/reenviar", {}, como=api.admin).status_code == 409  # ya hay uno en cola
    procesar_pendientes(api.e.datos.conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    assert [m.destinatario for m in e.enviados] == ["ana@example.com", "nuevo@example.com"]
    assert e.enviados[0].adjunto == e.enviados[1].adjunto  # mismo ZIP

def test_permisos_y_errores(api, certificado):
    assert api.get("/facturas/999/zip", como=api.bombero).status_code == 404
    fid = emitida_y_entregada(api, certificado, EnviadorEnMemoria())
    assert api.post(f"/facturas/{fid}/reenviar", {}, como=api.bombero).status_code == 403
    d = api.e.datos
    r = api.post("/facturas", {"clave": clave(), "origen_tipo": "despacho", "origen_id": d.despacho(),
                               "cliente_id": api.e.cliente, "medio_pago": "EFECTIVO"}, como=api.bombero)
    assert api.post(f"/facturas/{r.json()['id']}/reenviar", {}, como=api.admin).status_code == 409  # no FACTURADA
    assert api.get(f"/facturas/{r.json()['id']}/zip", como=api.bombero).status_code == 404
```

- [ ] **Step 2:** FAIL.

- [ ] **Step 3: Implementación** — `leer_factura` agrega `entrega=` desde `servicios.entrega.obtener` (sin el zip). `GET /zip`: 404 si no hay entrega; auditoría `FACTURA_ZIP` con `usuario_id`; `Cache-Control: no-store`. `POST /reenviar` (`SesionAdmin`): 404 si no existe; 409 si `estado != FACTURADO` o si no hay entrega armada todavía; `INSERT INTO outbox (factura_id, tipo) VALUES (%s, 'ENVIAR_CORREO')` capturando `psycopg.errors.UniqueViolation` → 409 "ya hay un envío en curso"; `entrega.marcar(PENDIENTE)`; auditoría `FACTURA_REENVIO`. Regenerar el contrato: `cd backend && PYTHONPATH=src python -m estacion.api.openapi > ../Frontend/openapi.json`.

- [ ] **Step 4:** `pytest tests/test_api_entrega.py tests/test_openapi.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: descarga y reenvío del ZIP de la factura`

---

### Task 9: Mailpit, configuración y prueba de punta a punta

**Files:**
- Modify: `infra/docker-compose.yml` (servicio `mailpit`; `worker` con `ESTACION_SMTP_HOST: mailpit`, `ESTACION_SMTP_PUERTO: "1025"`, `DIAN_*` y `DIAN_CERTIFICADO_*` montando el `.pfx` de solo lectura), `Makefile` (`mail`, `certificado`), `.env.example` (ESTACION_SMTP_*, quitar "(Fase 6, todavía no las lee el código)"), `../Docs/README_EXECUTE.md` (sección "Correo de prueba")

- [ ] **Step 1:** Elegir una etiqueta de Mailpit existente y con más de dos semanas (`docker pull axllent/mailpit:<tag>`), y agregar:

```yaml
  mailpit:
    image: axllent/mailpit:<tag verificado>
    restart: unless-stopped
    ports: ["127.0.0.1:1025:1025", "127.0.0.1:8025:8025"]   # SMTP y buzón web, solo en este equipo
```
`Makefile`: `mail:` → `$(COMPOSE) up -d mailpit` y `certificado:` → `cd $(BACK) && $(PY) -m estacion.certificado --salida ../datos/certificado-prueba.pfx` (`datos/` ya está en `.gitignore`).

- [ ] **Step 2: Punta a punta manual** (base temporal, datos sintéticos): `make mail`; generar el certificado de prueba; migrar; estación + numeración; usuarios; simulador `--cantidad 3`; API; registrar cliente con correo `@example.com`; emitir; `python -m estacion.worker --una-vez`; abrir `http://localhost:8025` (o `GET /api/v1/messages` de Mailpit) y comprobar: asunto con 5 campos `;`, adjunto `z….zip` con `ad….xml` (pasa el XSD AttachedDocument, firma válida con signxml) y `fv….pdf`. `GET /facturas/{id}/zip` devuelve el mismo ZIP.

- [ ] **Step 3:** `ruff check src tests migrations`, `ruff format --check …`, `mypy`, `pytest` completo → todo limpio.
- [ ] **Step 4: Commit** `feat: buzón de pruebas Mailpit y documentación de la entrega`
