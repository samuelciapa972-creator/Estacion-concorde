# Cliente del web service de la DIAN — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que la cola de envíos firme y mande las facturas al web service real de la DIAN (ambiente de habilitación), reciba la validación y concilie los casos inciertos, y que un comando permita pasar el set de pruebas con el flujo real del sistema.

**Architecture:** Mensajes SOAP 1.2 armados a mano y firmados con WS-Security (`facturacion/dian/sobre.py`), un cliente sin dependencias nuevas con transporte inyectable (`facturacion/dian/cliente.py`) y un `ProveedorDian` que implementa `ProveedorFacturacion`. La interfaz del proveedor cambia: `emitir` puede devolver `EnvioPendiente` (envío asíncrono del set) y la conciliación pasa de la clave interna a `consultar(...)` por CUFE / track id. La cola (`servicios/outbox.py`) aprende los estados nuevos; el worker elige proveedor con `--proveedor dian`; `python -m estacion.habilitacion` prueba la conexión, carga la numeración SETP, crea facturas de prueba y muestra su estado.

**Tech Stack:** Python 3.12, lxml, cryptography, urllib (librería estándar), psycopg 3, Alembic, pytest.

**Spec:** `docs/superpowers/specs/2026-10-13-cliente-dian-design.md`

## Global Constraints

- Ningún envío real a la DIAN desde pruebas: todo test usa transporte falso o `ProveedorSimulado`; la red real solo la usa el usuario al correr el comando o el worker.
- Sin dependencias nuevas: HTTP con `urllib.request`, XML con lxml, firma con cryptography.
- `DIAN_AMBIENTE` solo puede ser `"2"` (habilitación); producción sigue bloqueada en `servicios/configuracion.py` (`AMBIENTES_PERMITIDOS = {"2"}`) y no se toca.
- Secretos (`DIAN_SOFTWARE_PIN`, `DIAN_CLAVE_TECNICA`, `DIAN_CERTIFICADO_CLAVE`, `DIAN_TEST_SET_ID`) solo por variables de entorno; nunca en código, logs, mensajes de error ni fixtures. El log solo lleva identificadores, estados y tipos de error.
- XML de terceros (respuestas de la DIAN) se parsea con `etree.XMLParser(resolve_entities=False, no_network=True)`.
- Dinero con `Decimal`, nunca `float`. Fechas locales sin zona (`ahora_local()`).
- Estilo: dominio y mensajes en español, `dataclass(frozen=True, slots=True)`, `from __future__ import annotations`, líneas ≤ 120 (ruff), mypy limpio.
- Comandos de verificación (desde `backend/`, con un venv de Linux en `$V`, ver README): `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest -q`, `$V/bin/python -m ruff check src tests migrations`, `$V/bin/python -m ruff format --check src tests migrations`, `$V/bin/python -m mypy`.
- Commits: el usuario no ha autorizado commits en este proyecto. Los pasos "Commit" se ejecutan solo si lo autoriza; si no, se deja el cambio listo y se sigue.

## Review Focus

1. **Reintento tras una caída a mitad de envío:** el worker muere después de `SendTestSetAsync`/`SendBillSync` y antes del commit; el reintento reenvía el mismo documento y la DIAN responde "documento procesado anteriormente". Debe tratarse como incierto (conciliar por CUFE), nunca como rechazo. → prueba en Task 5.
2. **Reloj del servidor desfasado:** la DIAN responde Fault `MessageExpired`/`InvalidSecurity`. Debe quedar como problema de configuración (reintento + auditoría `DIAN_SEGURIDAD`), sin tocar el estado de la factura. → pruebas en Task 3 y Task 4.
3. **Respuesta que no es SOAP** (página HTML de un proxy, 503, cuerpo vacío, XML con entidades): `ErrorTransporteDian` sin excepción inesperada ni resolución de entidades. → prueba en Task 3.
4. **`configurar-numeracion` corrido dos veces** o con otra numeración activa: idempotente, una sola numeración activa, no reinicia `siguiente`. → prueba en Task 7.
5. **DIAN acepta pero no devuelve `XmlBase64Bytes`:** la factura queda FACTURADA igual (es válida) y la entrega falla de forma controlada. → prueba en Task 5.

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `backend/tests/fixtures/dian/WcfDianCustomerServices.wsdl` (crear) | WSDL oficial fijado; fuente de verdad de nombres de operaciones y parámetros |
| `backend/tests/test_wsdl_dian.py` (crear) | Que nuestras operaciones y parámetros existen en el WSDL |
| `backend/src/estacion/facturacion/xmldsig.py` (crear) | Utilidades XML-DSig compartidas (`sub`, `b64`, `sha256_b64`, `firmar_rsa_sha256`, `verificar_rsa_sha256`) |
| `backend/src/estacion/facturacion/firma.py` (modificar) | Usa `xmldsig` en vez de sus copias privadas |
| `backend/src/estacion/facturacion/dian/__init__.py` (crear) | Paquete |
| `backend/src/estacion/facturacion/dian/sobre.py` (crear) | Envelope SOAP 1.2 + WS-Addressing + WS-Security; `verificar_sobre` |
| `backend/src/estacion/facturacion/dian/cliente.py` (crear) | `ClienteDian`, transporte, parseo de respuestas, errores |
| `backend/src/estacion/facturacion/dian/proveedor.py` (crear) | `ProveedorDian` (modos `set_pruebas` y `normal`) |
| `backend/src/estacion/facturacion/modelos.py` (modificar) | `EnvioPendiente` |
| `backend/src/estacion/facturacion/proveedor.py` (modificar) | Interfaz nueva, `ProveedorNoConfigurado`, simulado asíncrono |
| `backend/migrations/versions/0006_cliente_dian.py` (crear) | `factura.dian_track_id` |
| `backend/src/estacion/servicios/outbox.py` (modificar) | Caminos `EnvioPendiente`, `ProveedorNoConfigurado`, rechazo en consulta |
| `backend/src/estacion/servicios/emision.py` (modificar) | Reenvío limpia `dian_track_id` |
| `backend/src/estacion/worker.py` (modificar) | `--proveedor dian` |
| `backend/src/estacion/habilitacion.py` (crear) | Comando `python -m estacion.habilitacion` |
| `backend/tests/test_sobre_dian.py`, `test_cliente_dian.py`, `test_proveedor_dian.py`, `test_habilitacion.py` (crear) | Pruebas |
| `backend/tests/test_outbox.py`, `test_migraciones.py` (modificar) | Pruebas de la cola y de la migración |
| `.env.example`, `README.md` (modificar) | `DIAN_TEST_SET_ID`, pasos de habilitación |

---

### Task 1: Fijar el WSDL oficial

**Files:**
- Create: `backend/tests/fixtures/dian/WcfDianCustomerServices.wsdl`
- Test: `backend/tests/test_wsdl_dian.py`

**Interfaces:**
- Produces: `OPERACIONES: dict[str, tuple[str, ...]]` en `test_wsdl_dian.py` — lo copian las tareas siguientes como fuente de nombres. Si el WSDL dice otra cosa que este plan, **manda el WSDL**: corregir los nombres en las tareas 2–5 antes de implementarlas.

- [ ] **Step 1: Descargar el WSDL (GET público, sin credenciales; es la única llamada de red de este plan hecha por el implementador)**

```bash
mkdir -p backend/tests/fixtures/dian
curl -fsS "https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc?singleWsdl" \
  -o backend/tests/fixtures/dian/WcfDianCustomerServices.wsdl \
  || curl -fsS "https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc?wsdl" \
  -o backend/tests/fixtures/dian/WcfDianCustomerServices.wsdl
grep -c "wsdl:operation" backend/tests/fixtures/dian/WcfDianCustomerServices.wsdl
```
Expected: un número > 5. Si `?wsdl` trae `xsd:import` a otros archivos (`?xsd=xsd0`, `xsd1`, `xsd2`), descargarlos también a `backend/tests/fixtures/dian/` con el mismo nombre que indica `schemaLocation` (reemplazando `?` y `=` por `_`).

- [ ] **Step 2: Escribir la prueba**

```python
"""El WSDL oficial de la DIAN (fijado en tests/fixtures/dian) trae las operaciones y parámetros que usamos."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dian"
OPERACIONES: dict[str, tuple[str, ...]] = {
    "GetNumberingRange": ("accountCode", "accountCodeT", "softwareCode"),
    "SendTestSetAsync": ("fileName", "contentFile", "testSetId"),
    "GetStatusZip": ("trackId",),
    "GetStatus": ("trackId",),
    "SendBillSync": ("fileName", "contentFile"),
}


def _documentos() -> list[etree._Element]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return [etree.parse(str(p), parser).getroot() for p in sorted(FIXTURES.iterdir()) if p.is_file()]


def test_operaciones_y_parametros_existen_en_el_wsdl():
    docs = _documentos()
    nombres = {
        e.get("name")
        for d in docs
        for e in d.iter("{http://www.w3.org/2001/XMLSchema}element", "{http://schemas.xmlsoap.org/wsdl/}operation")
    }
    for operacion, parametros in OPERACIONES.items():
        assert operacion in nombres, operacion
        for p in parametros:
            assert p in nombres, f"{operacion}.{p}"


def test_espacio_de_nombres_de_las_operaciones():
    raiz = _documentos()[0]
    assert raiz.get("targetNamespace") in {"http://wcf.dian.colombia", "http://tempuri.org/"}
    texto = b"".join(etree.tostring(d) for d in _documentos())
    assert b"http://wcf.dian.colombia/IWcfDianCustomerServices/SendTestSetAsync" in texto
```

- [ ] **Step 3: Correr la prueba**

Run: `$V/bin/python -m pytest tests/test_wsdl_dian.py -v`
Expected: PASS. Si falla un nombre, anotar el nombre real y corregirlo en `OPERACIONES` **y** en las tareas 2–5 de este plan antes de seguir.

- [ ] **Step 4: Guardar respuestas de ejemplo del WSDL**

Revisar en el WSDL/XSD los nombres de los campos de `DianResponse`, `UploadDocumentResponse` y `NumberRangeResponseList`. Este plan usa: `IsValid`, `StatusCode`, `StatusDescription`, `StatusMessage`, `ErrorMessage` (lista de `string`), `XmlBase64Bytes`, `XmlDocumentKey`, `XmlFileName`; `ZipKey`, `ErrorMessageList` → `XmlParamsResponseTrackId` → `ProcessedMessage`; `NumberRangeResponse` → `ResolutionNumber`, `Prefix`, `FromNumber`, `ToNumber`, `ValidDateFrom`, `ValidDateTo`, `TechnicalKey`, y `OperationCode`/`OperationDescription`. Si difieren, corregir `cliente.py` (Task 3).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/fixtures/dian backend/tests/test_wsdl_dian.py
git commit -m "test: fijar el WSDL oficial de la DIAN"
```

---

### Task 2: Mensaje SOAP firmado con WS-Security

**Files:**
- Create: `backend/src/estacion/facturacion/xmldsig.py`
- Modify: `backend/src/estacion/facturacion/firma.py` (quitar `_sub`, `_b64`, `_sha256_b64` y usar los de `xmldsig`)
- Create: `backend/src/estacion/facturacion/dian/__init__.py`, `backend/src/estacion/facturacion/dian/sobre.py`
- Test: `backend/tests/test_sobre_dian.py`

**Interfaces:**
- Consumes: `Certificado` (`facturacion/firma.py`: `.clave: rsa.RSAPrivateKey`, `.cert: x509.Certificate`).
- Produces:
  - `xmldsig.sub(padre, etiqueta, texto=None, **atributos) -> etree._Element`, `xmldsig.b64(bytes) -> str`, `xmldsig.sha256_b64(bytes) -> str`, `xmldsig.firmar_rsa_sha256(clave, datos: bytes) -> str` (base64), `xmldsig.verificar_rsa_sha256(cert, datos: bytes, firma_b64: str) -> bool`.
  - `sobre.URL_HABILITACION: str`, `sobre.accion(operacion: str) -> str`, `sobre.sobre(operacion: str, parametros: Sequence[tuple[str, str]], *, destino: str, certificado: Certificado, ahora: datetime) -> bytes`, `sobre.verificar_sobre(datos: bytes, *, ahora: datetime) -> None` (lanza `SobreInvalido`).

- [ ] **Step 1: Extraer las utilidades compartidas**

Crear `backend/src/estacion/facturacion/xmldsig.py`:

```python
"""Piezas de XML-DSig que comparten la firma XAdES de la factura (`firma.py`) y la firma WS-Security de los
mensajes al web service de la DIAN (`dian/sobre.py`)."""

from __future__ import annotations

import base64
import hashlib

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

NS_DS = "http://www.w3.org/2000/09/xmldsig#"
RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"


def sub(padre: etree._Element, etiqueta: str, texto: str | None = None, **atributos: str) -> etree._Element:
    el = etree.SubElement(padre, etiqueta, attrib=atributos)
    if texto is not None:
        el.text = texto
    return el


def b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode("ascii")


def sha256_b64(datos: bytes) -> str:
    return b64(hashlib.sha256(datos).digest())


def firmar_rsa_sha256(clave: rsa.RSAPrivateKey, datos: bytes) -> str:
    return b64(clave.sign(datos, padding.PKCS1v15(), hashes.SHA256()))


def verificar_rsa_sha256(cert: x509.Certificate, datos: bytes, firma_b64: str) -> bool:
    publica = cert.public_key()
    assert isinstance(publica, rsa.RSAPublicKey)
    try:
        publica.verify(base64.b64decode(firma_b64), datos, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        return False
    return True
```

En `firma.py`: borrar las definiciones de `_sub`, `_b64`, `_sha256_b64`; agregar
`from .xmldsig import NS_DS, RSA_SHA256, SHA256, b64 as _b64, firmar_rsa_sha256, sha256_b64 as _sha256_b64, sub as _sub`;
borrar las constantes `NS_DS`, `RSA_SHA256`, `SHA256` duplicadas; reemplazar
`valor.text = _b64(certificado.clave.sign(_c14n(info), padding.PKCS1v15(), hashes.SHA256()))` por
`valor.text = firmar_rsa_sha256(certificado.clave, _c14n(info))`; quitar los imports de `base64`, `hashlib`, `hashes`, `padding` que queden sin uso (ruff los marca).

- [ ] **Step 2: Correr las pruebas de la firma (no deben cambiar)**

Run: `$V/bin/python -m pytest tests/test_firma.py tests/test_attached.py tests/test_paquete.py -q`
Expected: PASS (mismo número que antes).

- [ ] **Step 3: Escribir las pruebas del sobre**

`backend/tests/test_sobre_dian.py`:

```python
"""Mensajes SOAP firmados con WS-Security para el web service de la DIAN. Sin red."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from lxml import etree

from estacion.facturacion.dian.sobre import (
    NS_SOAP,
    NS_WCF,
    NS_WSA,
    NS_WSSE,
    NS_WSU,
    URL_HABILITACION,
    SobreInvalido,
    accion,
    sobre,
    verificar_sobre,
)

AHORA = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)


def _armar(certificado, **kw):
    return sobre(
        "GetStatus", [("trackId", "abc123")], destino=URL_HABILITACION, certificado=certificado, ahora=AHORA, **kw
    )


def test_estructura_del_sobre(certificado):
    raiz = etree.fromstring(_armar(certificado))
    assert raiz.tag == f"{{{NS_SOAP}}}Envelope"
    assert raiz.findtext(f".//{{{NS_WSA}}}Action") == accion("GetStatus")
    assert accion("GetStatus") == "http://wcf.dian.colombia/IWcfDianCustomerServices/GetStatus"
    to = raiz.find(f".//{{{NS_WSA}}}To")
    assert to.text == URL_HABILITACION and to.get(f"{{{NS_WSU}}}Id")
    seg = raiz.find(f".//{{{NS_WSSE}}}Security")
    assert seg.findtext(f"{{{NS_WSU}}}Timestamp/{{{NS_WSU}}}Created") == "2026-10-13T15:00:00.000Z"
    assert seg.findtext(f"{{{NS_WSU}}}Timestamp/{{{NS_WSU}}}Expires") == "2026-10-13T15:01:00.000Z"
    assert seg.find(f"{{{NS_WSSE}}}BinarySecurityToken").text
    assert raiz.findtext(f".//{{{NS_WCF}}}GetStatus/{{{NS_WCF}}}trackId") == "abc123"


def test_la_firma_verifica(certificado):
    verificar_sobre(_armar(certificado), ahora=AHORA + timedelta(seconds=30))


def test_cambiar_el_destino_rompe_la_firma(certificado):
    datos = _armar(certificado).replace(URL_HABILITACION.encode(), b"https://otro.example/x")
    with pytest.raises(SobreInvalido, match="digest"):
        verificar_sobre(datos, ahora=AHORA)


def test_cambiar_la_firma_la_invalida(certificado):
    raiz = etree.fromstring(_armar(certificado))
    valor = raiz.find(".//{http://www.w3.org/2000/09/xmldsig#}SignatureValue")
    valor.text = "A" + valor.text[1:]
    with pytest.raises(SobreInvalido, match="firma"):
        verificar_sobre(etree.tostring(raiz), ahora=AHORA)


def test_sobre_vencido(certificado):
    with pytest.raises(SobreInvalido, match="venció"):
        verificar_sobre(_armar(certificado), ahora=AHORA + timedelta(seconds=61))


def test_los_parametros_se_escapan(certificado):
    raiz = etree.fromstring(
        sobre("GetStatus", [("trackId", "<x>&")], destino=URL_HABILITACION, certificado=certificado, ahora=AHORA)
    )
    assert raiz.findtext(f".//{{{NS_WCF}}}trackId") == "<x>&"
```

- [ ] **Step 4: Correr para ver que falla**

Run: `$V/bin/python -m pytest tests/test_sobre_dian.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'estacion.facturacion.dian'`.

- [ ] **Step 5: Implementar**

`backend/src/estacion/facturacion/dian/__init__.py`:

```python
"""Conexión con el web service de la DIAN (WcfDianCustomerServices)."""
```

`backend/src/estacion/facturacion/dian/sobre.py`:

```python
"""Mensaje SOAP 1.2 firmado con WS-Security (X.509) para el web service de la DIAN. Función pura: no hay red.

Estructura (SIN VERIFICAR del todo: la confirma `python -m estacion.habilitacion probar-conexion`):
    soap:Header
        wsse:Security  → wsu:Timestamp (60 s), wsse:BinarySecurityToken (el certificado), ds:Signature
        wsa:Action     → http://wcf.dian.colombia/IWcfDianCustomerServices/<Operación>
        wsa:To         → la URL del servicio, con wsu:Id; es lo que firma la ds:Signature
    soap:Body → wcf:<Operación> con un hijo por parámetro
Firma: C14N exclusiva, RSA-SHA256, una referencia a wsa:To (digest SHA-256); KeyInfo apunta al token.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree

from ..firma import Certificado
from ..xmldsig import NS_DS, RSA_SHA256, SHA256, b64, firmar_rsa_sha256, sha256_b64, sub, verificar_rsa_sha256

URL_HABILITACION = "https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc"

NS_SOAP = "http://www.w3.org/2003/05/soap-envelope"
NS_WSA = "http://www.w3.org/2005/08/addressing"
NS_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
NS_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
NS_WCF = "http://wcf.dian.colombia"
NS_EC = "http://www.w3.org/2001/10/xml-exc-c14n#"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
X509V3 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3"
BASE64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"
VIGENCIA = timedelta(seconds=60)
_PREFIJOS_TO = ["soap", "wcf"]
_PREFIJOS_INFO = ["wsa", "soap", "wcf"]
_NSMAP = {"soap": NS_SOAP, "wcf": NS_WCF}


class SobreInvalido(Exception):
    """El mensaje firmado no verifica (solo lo usan las pruebas y el diagnóstico)."""


def accion(operacion: str) -> str:
    return f"http://wcf.dian.colombia/IWcfDianCustomerServices/{operacion}"


def _q(ns: str, nombre: str) -> str:
    return f"{{{ns}}}{nombre}"


def _hora(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _exc_c14n(el: etree._Element, prefijos: list[str]) -> bytes:
    return etree.tostring(el, method="c14n", exclusive=True, with_comments=False, inclusive_ns_prefixes=prefijos)


def sobre(
    operacion: str,
    parametros: Sequence[tuple[str, str]],
    *,
    destino: str,
    certificado: Certificado,
    ahora: datetime,
) -> bytes:
    sufijo = uuid.uuid4().hex
    id_to, id_token, id_ts = f"id-{sufijo}", f"X509-{sufijo}", f"TS-{sufijo}"

    raiz = etree.Element(_q(NS_SOAP, "Envelope"), nsmap=_NSMAP)
    cabecera = etree.SubElement(raiz, _q(NS_SOAP, "Header"), nsmap={"wsa": NS_WSA})
    seguridad = etree.SubElement(
        cabecera, _q(NS_WSSE, "Security"), nsmap={"wsse": NS_WSSE, "wsu": NS_WSU}
    )
    seguridad.set(_q(NS_SOAP, "mustUnderstand"), "true")
    ts = sub(seguridad, _q(NS_WSU, "Timestamp"))
    ts.set(_q(NS_WSU, "Id"), id_ts)
    sub(ts, _q(NS_WSU, "Created"), _hora(ahora))
    sub(ts, _q(NS_WSU, "Expires"), _hora(ahora + VIGENCIA))
    token = sub(
        seguridad, _q(NS_WSSE, "BinarySecurityToken"),
        b64(certificado.cert.public_bytes(Encoding.DER)), EncodingType=BASE64, ValueType=X509V3,
    )  # fmt: skip
    token.set(_q(NS_WSU, "Id"), id_token)

    firma = etree.SubElement(seguridad, _q(NS_DS, "Signature"), nsmap={"ds": NS_DS}, Id=f"SIG-{sufijo}")
    info = sub(firma, _q(NS_DS, "SignedInfo"))
    canon = sub(info, _q(NS_DS, "CanonicalizationMethod"), Algorithm=EXC_C14N)
    etree.SubElement(canon, _q(NS_EC, "InclusiveNamespaces"), nsmap={"ec": NS_EC}, PrefixList=" ".join(_PREFIJOS_INFO))
    sub(info, _q(NS_DS, "SignatureMethod"), Algorithm=RSA_SHA256)
    ref = sub(info, _q(NS_DS, "Reference"), URI=f"#{id_to}")
    transf = sub(sub(ref, _q(NS_DS, "Transforms")), _q(NS_DS, "Transform"), Algorithm=EXC_C14N)
    etree.SubElement(transf, _q(NS_EC, "InclusiveNamespaces"), nsmap={"ec": NS_EC}, PrefixList=" ".join(_PREFIJOS_TO))
    sub(ref, _q(NS_DS, "DigestMethod"), Algorithm=SHA256)
    digest = sub(ref, _q(NS_DS, "DigestValue"), "")
    valor = sub(firma, _q(NS_DS, "SignatureValue"))
    keyinfo = sub(firma, _q(NS_DS, "KeyInfo"), Id=f"KI-{sufijo}")
    str_ = sub(keyinfo, _q(NS_WSSE, "SecurityTokenReference"))
    str_.set(_q(NS_WSU, "Id"), f"STR-{sufijo}")
    sub(str_, _q(NS_WSSE, "Reference"), URI=f"#{id_token}", ValueType=X509V3)

    sub(cabecera, _q(NS_WSA, "Action"), accion(operacion))
    to = sub(cabecera, _q(NS_WSA, "To"), destino)
    to.set(_q(NS_WSU, "Id"), id_to)

    cuerpo = etree.SubElement(raiz, _q(NS_SOAP, "Body"))
    op = sub(cuerpo, _q(NS_WCF, operacion))
    for nombre, texto in parametros:
        sub(op, _q(NS_WCF, nombre), texto)

    # Con los nodos ya en su lugar (la C14N exclusiva con InclusiveNamespaces depende de los ancestros).
    digest.text = sha256_b64(_exc_c14n(to, _PREFIJOS_TO))
    valor.text = firmar_rsa_sha256(certificado.clave, _exc_c14n(info, _PREFIJOS_INFO))
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")


def verificar_sobre(datos: bytes, *, ahora: datetime) -> None:
    raiz = etree.fromstring(datos, etree.XMLParser(resolve_entities=False, no_network=True))
    ns = {"ds": NS_DS, "wsse": NS_WSSE, "wsu": NS_WSU, "wsa": NS_WSA}
    expira = raiz.findtext(".//wsu:Timestamp/wsu:Expires", namespaces=ns)
    if expira is None or datetime.fromisoformat(expira.replace("Z", "+00:00")) < ahora:
        raise SobreInvalido("el sobre venció (Timestamp/Expires)")
    ref = raiz.find(".//ds:SignedInfo/ds:Reference", namespaces=ns)
    to = raiz.find(".//wsa:To", namespaces=ns)
    if ref is None or to is None or ref.get("URI") != f"#{to.get(_q(NS_WSU, 'Id'))}":
        raise SobreInvalido("la firma no apunta a wsa:To")
    if ref.findtext("ds:DigestValue", namespaces=ns) != sha256_b64(_exc_c14n(to, _PREFIJOS_TO)):
        raise SobreInvalido("el digest de wsa:To no coincide")
    token = raiz.findtext(".//wsse:BinarySecurityToken", namespaces=ns) or ""
    cert = x509.load_der_x509_certificate(base64.b64decode(token))
    info = raiz.find(".//ds:SignedInfo", namespaces=ns)
    firma = raiz.findtext(".//ds:SignatureValue", namespaces=ns) or ""
    assert info is not None
    if not verificar_rsa_sha256(cert, _exc_c14n(info, _PREFIJOS_INFO), firma):
        raise SobreInvalido("la firma del SignedInfo no verifica")
```

- [ ] **Step 6: Correr las pruebas**

Run: `$V/bin/python -m pytest tests/test_sobre_dian.py tests/test_firma.py -v`
Expected: PASS.

- [ ] **Step 7: Lint y tipos**

Run: `$V/bin/python -m ruff check src tests && $V/bin/python -m ruff format --check src tests && $V/bin/python -m mypy`
Expected: sin errores.

- [ ] **Step 8: Commit**

```bash
git add backend/src/estacion/facturacion/xmldsig.py backend/src/estacion/facturacion/firma.py \
        backend/src/estacion/facturacion/dian backend/tests/test_sobre_dian.py
git commit -m "feat(dian): mensaje SOAP firmado con WS-Security"
```

---

### Task 3: Cliente del web service

**Files:**
- Create: `backend/src/estacion/facturacion/dian/cliente.py`
- Test: `backend/tests/test_cliente_dian.py`

**Interfaces:**
- Consumes: `sobre.sobre`, `sobre.accion`, `sobre.URL_HABILITACION`, `Certificado`.
- Produces:
  - `Transporte = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]`; `transporte_urllib`.
  - `ErrorSeguridadDian(Exception)`, `ErrorTransporteDian(Exception)`.
  - `RespuestaDian(valida: bool, codigo: str, descripcion: str, errores: tuple[str, ...], application_response: bytes | None, cufe: str)` con propiedades `en_proceso: bool`, `no_encontrado: bool`.
  - `RespuestaEnvioAsincrono(zip_key: str, errores: tuple[str, ...])`.
  - `RangoNumeracion(prefijo: str, resolucion: str, desde: int, hasta: int, vigente_desde: str, vigente_hasta: str)`; `ConsultaRangos(codigo: str, descripcion: str, rangos: tuple[RangoNumeracion, ...])`.
  - `ClienteDian(url: str, certificado: Certificado, transporte: Transporte = transporte_urllib, reloj=lambda: datetime.now(UTC), timeout: float = 60)` con métodos `rangos_numeracion(nit: str, software_id: str) -> ConsultaRangos`, `enviar_set_pruebas(nombre_zip: str, zip_b64: str, test_set_id: str) -> RespuestaEnvioAsincrono`, `estado_zip(track_id: str) -> list[RespuestaDian]`, `estado(cufe: str) -> RespuestaDian`, `enviar_sincrono(nombre_zip: str, zip_b64: str) -> RespuestaDian`.

- [ ] **Step 1: Escribir las pruebas**

`backend/tests/test_cliente_dian.py`:

```python
"""ClienteDian con transporte falso: respuestas grabadas, nada sale a la red."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from estacion.facturacion.dian.cliente import ClienteDian, ErrorSeguridadDian, ErrorTransporteDian
from estacion.facturacion.dian.sobre import URL_HABILITACION, verificar_sobre

AHORA = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
ENV = (
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:a="http://www.w3.org/2005/08/addressing">'
    "<s:Header/><s:Body>{}</s:Body></s:Envelope>"
)
DR = (
    '<b:IsValid>{valida}</b:IsValid><b:StatusCode>{codigo}</b:StatusCode>'
    "<b:StatusDescription>{desc}</b:StatusDescription><b:StatusMessage/>"
    '<b:ErrorMessage xmlns:c="http://schemas.microsoft.com/2003/10/Serialization/Arrays">{errores}</b:ErrorMessage>'
    "<b:XmlBase64Bytes>{app}</b:XmlBase64Bytes><b:XmlDocumentKey>{cufe}</b:XmlDocumentKey>"
    "<b:XmlFileName>fv.xml</b:XmlFileName>"
)
NS_B = 'xmlns:b="http://schemas.datacontract.org/2004/07/DianResponse"'


def dian_response(valida="true", codigo="00", desc="Procesado Correctamente.", errores=(), app=b"<ApplicationResponse/>",
                  cufe="cufe123"):  # fmt: skip
    return DR.format(
        valida=valida, codigo=codigo, desc=desc, cufe=cufe,
        errores="".join(f"<c:string>{e}</c:string>" for e in errores),
        app=base64.b64encode(app).decode() if app else "",
    )  # fmt: skip


class Transporte:
    def __init__(self, estado: int, cuerpo: bytes | str, *, excepcion: Exception | None = None):
        self.estado, self.cuerpo, self.excepcion = estado, cuerpo, excepcion
        self.llamadas: list[tuple[str, dict, bytes]] = []

    def __call__(self, url, cabeceras, cuerpo, timeout):
        self.llamadas.append((url, dict(cabeceras), cuerpo))
        if self.excepcion:
            raise self.excepcion
        c = self.cuerpo.encode() if isinstance(self.cuerpo, str) else self.cuerpo
        return self.estado, c


def cliente(certificado, t):
    return ClienteDian(URL_HABILITACION, certificado, transporte=t, reloj=lambda: AHORA)


def test_estado_valido_decodifica_el_application_response(certificado):
    t = Transporte(200, ENV.format(f"<GetStatusResponse xmlns=\"http://wcf.dian.colombia\"><GetStatusResult {NS_B}>"
                                   f"{dian_response()}</GetStatusResult></GetStatusResponse>"))  # fmt: skip
    r = cliente(certificado, t).estado("cufe123")
    assert r.valida and r.codigo == "00" and r.cufe == "cufe123"
    assert r.application_response == b"<ApplicationResponse/>"
    url, cab, cuerpo = t.llamadas[0]
    assert url == URL_HABILITACION
    assert cab["Content-Type"].startswith("application/soap+xml")
    assert 'action="http://wcf.dian.colombia/IWcfDianCustomerServices/GetStatus"' in cab["Content-Type"]
    verificar_sobre(cuerpo, ahora=AHORA)


def test_rechazo_con_varios_errores(certificado):
    cuerpo = dian_response(valida="false", codigo="99", desc="Validación contiene errores en campos mandatorios.",
                           errores=("Regla: FAD06, Rechazo: CUFE mal calculado", "Regla: FAJ21, Rechazo: ..."),
                           app=b"")  # fmt: skip
    t = Transporte(200, ENV.format(f'<GetStatusResponse xmlns="http://wcf.dian.colombia"><GetStatusResult {NS_B}>'
                                   f"{cuerpo}</GetStatusResult></GetStatusResponse>"))  # fmt: skip
    r = cliente(certificado, t).estado("x")
    assert not r.valida and r.codigo == "99" and len(r.errores) == 2 and r.application_response is None
    assert not r.en_proceso


def test_set_de_pruebas_devuelve_zip_key(certificado):
    t = Transporte(200, ENV.format(
        '<SendTestSetAsyncResponse xmlns="http://wcf.dian.colombia"><SendTestSetAsyncResult '
        'xmlns:b="http://schemas.datacontract.org/2004/07/UploadDocumentResponse">'
        "<b:ErrorMessageList/><b:ZipKey>zip-key-1</b:ZipKey></SendTestSetAsyncResult></SendTestSetAsyncResponse>"))
    r = cliente(certificado, t).enviar_set_pruebas("z.zip", "UEsDBA==", "test-set")
    assert r.zip_key == "zip-key-1" and r.errores == ()
    assert b"<wcf:testSetId>test-set</wcf:testSetId>" in t.llamadas[0][2]


def test_estado_zip_en_proceso(certificado):
    cuerpo = dian_response(valida="false", codigo="", desc="Batch en proceso de validación.", app=b"", cufe="")
    t = Transporte(200, ENV.format(
        f'<GetStatusZipResponse xmlns="http://wcf.dian.colombia"><GetStatusZipResult {NS_B}>'
        f"<b:DianResponse>{cuerpo}</b:DianResponse></GetStatusZipResult></GetStatusZipResponse>"))
    [r] = cliente(certificado, t).estado_zip("zip-key-1")
    assert r.en_proceso and not r.valida


@pytest.mark.parametrize("subcodigo", ["a:InvalidSecurity", "a:MessageExpired", "a:FailedAuthentication"])
def test_fault_de_seguridad(certificado, subcodigo):
    t = Transporte(500, ENV.format(
        '<s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode>'
        f'<s:Value xmlns:a="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        f"{subcodigo}</s:Value></s:Subcode></s:Code><s:Reason><s:Text>error de seguridad</s:Text></s:Reason>"
        "</s:Fault>"))
    with pytest.raises(ErrorSeguridadDian):
        cliente(certificado, t).estado("x")


@pytest.mark.parametrize("estado", [401, 403])
def test_http_no_autorizado_es_seguridad(certificado, estado):
    with pytest.raises(ErrorSeguridadDian):
        cliente(certificado, Transporte(estado, b"")).estado("x")


@pytest.mark.parametrize(
    "estado,cuerpo",
    [
        (503, b"<html><body>Service Unavailable</body></html>"),
        (200, b""),
        (200, b"no es xml"),
        (500, ENV.format("<s:Fault><s:Code><s:Value>s:Receiver</s:Value></s:Code>"
                         "<s:Reason><s:Text>interno</s:Text></s:Reason></s:Fault>").encode()),
        (200, b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'),
    ],
)
def test_respuestas_que_no_sirven_son_error_de_transporte(certificado, estado, cuerpo):
    with pytest.raises(ErrorTransporteDian):
        cliente(certificado, Transporte(estado, cuerpo)).estado("x")


def test_timeout_es_error_de_transporte(certificado):
    with pytest.raises(ErrorTransporteDian, match="TimeoutError"):
        cliente(certificado, Transporte(0, b"", excepcion=TimeoutError("timed out"))).estado("x")


def test_rangos_de_numeracion(certificado):
    t = Transporte(200, ENV.format(
        '<GetNumberingRangeResponse xmlns="http://wcf.dian.colombia"><GetNumberingRangeResult '
        'xmlns:b="http://schemas.datacontract.org/2004/07/NumberRangeResponseList">'
        "<b:OperationCode>100</b:OperationCode><b:OperationDescription>Acción completada OK.</b:OperationDescription>"
        '<b:ResponseList xmlns:c="http://schemas.datacontract.org/2004/07/NumberRangeResponse"><c:NumberRangeResponse>'
        "<c:ResolutionNumber>18760000001</c:ResolutionNumber><c:Prefix>SETP</c:Prefix>"
        "<c:FromNumber>990000000</c:FromNumber><c:ToNumber>995000000</c:ToNumber>"
        "<c:ValidDateFrom>2019-01-19</c:ValidDateFrom><c:ValidDateTo>2030-01-19</c:ValidDateTo>"
        "<c:TechnicalKey>no-se-guarda</c:TechnicalKey></c:NumberRangeResponse></b:ResponseList>"
        "</GetNumberingRangeResult></GetNumberingRangeResponse>"))
    r = cliente(certificado, t).rangos_numeracion("901335327", "sw-id")
    assert r.codigo == "100" and len(r.rangos) == 1
    assert (r.rangos[0].prefijo, r.rangos[0].desde, r.rangos[0].hasta) == ("SETP", 990000000, 995000000)
    assert b"<wcf:accountCode>901335327</wcf:accountCode>" in t.llamadas[0][2]
    assert b"<wcf:softwareCode>sw-id</wcf:softwareCode>" in t.llamadas[0][2]


def test_el_error_no_lleva_el_sobre(certificado):
    with pytest.raises(ErrorTransporteDian) as e:
        cliente(certificado, Transporte(503, b"<html>caido</html>")).estado("x")
    assert "BinarySecurityToken" not in str(e.value) and len(str(e.value)) < 300
```

- [ ] **Step 2: Correr para ver que falla**

Run: `$V/bin/python -m pytest tests/test_cliente_dian.py -v`
Expected: FAIL con `ModuleNotFoundError` / `ImportError: cannot import name 'ClienteDian'`.

- [ ] **Step 3: Implementar**

`backend/src/estacion/facturacion/dian/cliente.py`:

```python
"""Operaciones del web service de la DIAN que usa el sistema, sobre un transporte HTTP inyectable.

Decide por `IsValid` y por la presencia de la respuesta; los códigos de estado se guardan tal cual y solo se usan
para reconocer "lote en proceso" y "no encontrado" (SIN VERIFICAR: códigos de memoria, se confirman con respuestas
reales en `tests/fixtures/dian/`). Los errores nunca incluyen el sobre ni el documento.
"""

from __future__ import annotations

import base64
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from lxml import etree

from ..firma import Certificado
from .sobre import accion, sobre

Transporte = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
_FAULTS_DE_SEGURIDAD = {
    "InvalidSecurity", "InvalidSecurityToken", "FailedAuthentication", "FailedCheck",
    "SecurityTokenUnavailable", "MessageExpired", "UnsupportedSecurityToken",
}  # fmt: skip
_CODIGOS_NO_ENCONTRADO = {"66", "90"}  # SIN VERIFICAR
_LARGO = 200


class ErrorSeguridadDian(Exception):
    """La DIAN no aceptó la seguridad del mensaje (certificado, firma, reloj). Es configuración, no la factura."""


class ErrorTransporteDian(Exception):
    """No hubo una respuesta utilizable (red, timeout, 5xx, cuerpo inválido): no se sabe si el envío llegó."""


@dataclass(frozen=True, slots=True)
class RespuestaDian:
    valida: bool
    codigo: str
    descripcion: str
    errores: tuple[str, ...]
    application_response: bytes | None
    cufe: str

    @property
    def no_encontrado(self) -> bool:
        return not self.valida and self.codigo in _CODIGOS_NO_ENCONTRADO

    @property
    def en_proceso(self) -> bool:
        return (
            not self.valida
            and not self.errores
            and not self.no_encontrado
            and (not self.codigo or "proceso" in self.descripcion.lower())
        )


@dataclass(frozen=True, slots=True)
class RespuestaEnvioAsincrono:
    zip_key: str
    errores: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RangoNumeracion:
    prefijo: str
    resolucion: str
    desde: int
    hasta: int
    vigente_desde: str
    vigente_hasta: str


@dataclass(frozen=True, slots=True)
class ConsultaRangos:
    codigo: str
    descripcion: str
    rangos: tuple[RangoNumeracion, ...]


def transporte_urllib(url: str, cabeceras: Mapping[str, str], cuerpo: bytes, timeout: float) -> tuple[int, bytes]:
    peticion = urllib.request.Request(url, data=cuerpo, headers=dict(cabeceras), method="POST")
    try:
        with urllib.request.urlopen(peticion, timeout=timeout, context=ssl.create_default_context()) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _hijos(el: etree._Element, nombre: str) -> list[etree._Element]:
    return [h for h in el.iter() if isinstance(h.tag, str) and etree.QName(h).localname == nombre]


def _texto(el: etree._Element, nombre: str) -> str:
    encontrados = _hijos(el, nombre)
    return (encontrados[0].text or "").strip() if encontrados else ""


def _recortar(texto: str) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= _LARGO else texto[: _LARGO - 1] + "…"


class ClienteDian:
    def __init__(
        self,
        url: str,
        certificado: Certificado,
        *,
        transporte: Transporte = transporte_urllib,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout: float = 60,
    ):
        self._url, self._certificado, self._transporte = url, certificado, transporte
        self._reloj, self._timeout = reloj, timeout

    # ------------------------------------------------------------------ operaciones
    def rangos_numeracion(self, nit: str, software_id: str) -> ConsultaRangos:
        r = self._llamar("GetNumberingRange", [("accountCode", nit), ("accountCodeT", nit), ("softwareCode", software_id)])
        rangos = tuple(
            RangoNumeracion(
                prefijo=_texto(n, "Prefix"), resolucion=_texto(n, "ResolutionNumber"),
                desde=int(_texto(n, "FromNumber") or 0), hasta=int(_texto(n, "ToNumber") or 0),
                vigente_desde=_texto(n, "ValidDateFrom"), vigente_hasta=_texto(n, "ValidDateTo"),
            )  # fmt: skip
            for n in _hijos(r, "NumberRangeResponse")
        )
        return ConsultaRangos(_texto(r, "OperationCode"), _texto(r, "OperationDescription"), rangos)

    def enviar_set_pruebas(self, nombre_zip: str, zip_b64: str, test_set_id: str) -> RespuestaEnvioAsincrono:
        r = self._llamar("SendTestSetAsync", [("fileName", nombre_zip), ("contentFile", zip_b64), ("testSetId", test_set_id)])
        errores = tuple(_recortar(m.text or "") for m in _hijos(r, "ProcessedMessage") if (m.text or "").strip())
        return RespuestaEnvioAsincrono(_texto(r, "ZipKey"), errores)

    def estado_zip(self, track_id: str) -> list[RespuestaDian]:
        r = self._llamar("GetStatusZip", [("trackId", track_id)])
        return [self._respuesta(d) for d in _hijos(r, "DianResponse")]

    def estado(self, cufe: str) -> RespuestaDian:
        return self._respuesta(self._llamar("GetStatus", [("trackId", cufe)]))

    def enviar_sincrono(self, nombre_zip: str, zip_b64: str) -> RespuestaDian:
        return self._respuesta(self._llamar("SendBillSync", [("fileName", nombre_zip), ("contentFile", zip_b64)]))

    # ------------------------------------------------------------------ transporte y parseo
    def _llamar(self, operacion: str, parametros: list[tuple[str, str]]) -> etree._Element:
        cuerpo = sobre(operacion, parametros, destino=self._url, certificado=self._certificado, ahora=self._reloj())
        cabeceras = {"Content-Type": f'application/soap+xml;charset=UTF-8;action="{accion(operacion)}"'}
        try:
            estado, respuesta = self._transporte(self._url, cabeceras, cuerpo, self._timeout)
        except (OSError, TimeoutError, urllib.error.URLError) as e:
            raise ErrorTransporteDian(f"{operacion}: sin respuesta ({type(e).__name__})") from None
        if estado in (401, 403):
            raise ErrorSeguridadDian(f"{operacion}: HTTP {estado} (certificado no aceptado)")
        try:
            raiz = etree.fromstring(respuesta, _PARSER) if respuesta.strip() else None
        except etree.XMLSyntaxError:
            raiz = None
        if raiz is None or etree.QName(raiz).localname != "Envelope":
            raise ErrorTransporteDian(f"{operacion}: respuesta que no es SOAP (HTTP {estado})")
        faults = _hijos(raiz, "Fault")
        if faults:
            subcodigos = {v.text.split(":")[-1] for v in _hijos(faults[0], "Value") if v.text}
            motivo = _recortar(_texto(faults[0], "Text"))
            if subcodigos & _FAULTS_DE_SEGURIDAD:
                raise ErrorSeguridadDian(f"{operacion}: {motivo}")
            raise ErrorTransporteDian(f"{operacion}: Fault {motivo}")
        resultados = _hijos(raiz, f"{operacion}Result")
        if not resultados:
            raise ErrorTransporteDian(f"{operacion}: la respuesta no trae {operacion}Result (HTTP {estado})")
        return resultados[0]

    @staticmethod
    def _respuesta(el: etree._Element) -> RespuestaDian:
        mensajes = _hijos(el, "ErrorMessage")
        errores = tuple(_recortar(s.text or "") for s in (_hijos(mensajes[0], "string") if mensajes else []) if s.text)
        app = _texto(el, "XmlBase64Bytes")
        return RespuestaDian(
            valida=_texto(el, "IsValid").lower() == "true",
            codigo=_texto(el, "StatusCode"),
            descripcion=_texto(el, "StatusDescription"),
            errores=errores,
            application_response=base64.b64decode(app) if app else None,
            cufe=_texto(el, "XmlDocumentKey"),
        )
```

- [ ] **Step 4: Correr las pruebas**

Run: `$V/bin/python -m pytest tests/test_cliente_dian.py -v`
Expected: PASS. Si la prueba de entidades falla porque lxml lanza `XMLSyntaxError` con DTD externo, ya queda cubierta por el `except`; si en cambio devuelve un árbol cuya raíz no es `Envelope`, también es `ErrorTransporteDian`.

- [ ] **Step 5: Lint y tipos**

Run: `$V/bin/python -m ruff check src tests && $V/bin/python -m ruff format src tests/test_cliente_dian.py && $V/bin/python -m mypy`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/src/estacion/facturacion/dian/cliente.py backend/tests/test_cliente_dian.py
git commit -m "feat(dian): cliente del web service con transporte inyectable"
```

---

### Task 4: Interfaz nueva del proveedor, migración 0006 y cola

**Files:**
- Modify: `backend/src/estacion/facturacion/modelos.py` (agregar `EnvioPendiente`)
- Modify: `backend/src/estacion/facturacion/proveedor.py` (interfaz, `ProveedorNoConfigurado`, simulado)
- Create: `backend/migrations/versions/0006_cliente_dian.py`
- Modify: `backend/src/estacion/servicios/outbox.py`
- Modify: `backend/src/estacion/servicios/emision.py:273-279` (el `UPDATE factura` del reenvío)
- Test: `backend/tests/test_outbox.py`, `backend/tests/test_migraciones.py`

**Interfaces:**
- Produces:
  - `modelos.EnvioPendiente(track_id: str)` (frozen, slots).
  - `proveedor.ProveedorNoConfigurado(Exception)`.
  - `ProveedorFacturacion.emitir(solicitud: SolicitudFactura) -> ResultadoEmision | EnvioPendiente`.
  - `ProveedorFacturacion.consultar(*, clave: str, numero: str, cufe: str, track_id: str | None) -> ResultadoEmision | EnvioPendiente | None` (reemplaza `buscar_por_clave`).
  - `ProveedorSimulado(..., asincrono: bool = False)`; atributos nuevos `en_proceso_restantes: int = 0` (cuántas consultas devuelven `EnvioPendiente` antes del resultado), `falla_seguridad: bool = False` (la próxima llamada lanza `ProveedorNoConfigurado`), `rechazo_en_consulta: bool = False` (en asíncrono, la consulta lanza `ProveedorRechazo`).
  - Columna `factura.dian_track_id TEXT NULL`.

- [ ] **Step 1: Escribir las pruebas de la cola**

En `backend/tests/test_outbox.py`, reemplazar la línea
`assert proveedor.buscar_por_clave(factura(escenario, f.id)[4]).cufe == f.cufe`
por
`assert proveedor.consultar(clave=factura(escenario, f.id)[4], numero="SETP1", cufe=f.cufe, track_id=None).cufe == f.cufe`
y agregar al final del archivo:

```python
# ------------------------------------------------------------------------------------------ envío asíncrono (set)
def track(e: Escenario, factura_id: int) -> str | None:
    return e.datos.uno("SELECT dian_track_id FROM factura WHERE id = %s", (factura_id,))


def vencer(e: Escenario) -> None:
    """Pone vencidos los trabajos reprogramados (las esperas de la política no se esperan en las pruebas)."""
    e.datos.conn.execute("UPDATE outbox SET proximo_intento = now() WHERE estado = 'PENDIENTE'")


def test_envio_asincrono_queda_en_proceso_y_la_consulta_lo_factura(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado(asincrono=True)
    proveedor.en_proceso_restantes = 1
    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert factura(escenario, f.id)[0] == "EN_PROCESO" and track(escenario, f.id) == f"sim-{factura(escenario, f.id)[4]}"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "HECHO", 1), ("CONSULTAR_DIAN", "PENDIENTE", 1)]
    vencer(escenario)
    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert factura(escenario, f.id)[0] == "FACTURADO" and origen_estado(escenario, origen) == "FACTURADO"
    assert proveedor.llamadas == 1  # un solo envío: la consulta nunca reenvía
    assert escenario.siguiente() == 2  # no se gastó otro número


def test_rechazo_llegado_por_la_consulta(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado(asincrono=True)
    proveedor.rechazo_en_consulta = True
    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert factura(escenario, f.id)[0] == "RECHAZADO" and origen_estado(escenario, origen) == "PENDIENTE"
    assert "rechazado por el simulador" in factura(escenario, f.id)[1]
    assert "FACTURA_RECHAZADA" in acciones(escenario, f.id)


def test_error_de_seguridad_no_toca_la_factura_y_reintenta(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_seguridad = True
    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert factura(escenario, f.id)[0] == "EN_PROCESO" and origen_estado(escenario, origen) == "EN_PROCESO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "PENDIENTE", 1)]
    assert "seguridad" in escenario.datos.uno("SELECT ultimo_error FROM outbox WHERE factura_id = %s", (f.id,))
    assert "DIAN_SEGURIDAD" in acciones(escenario, f.id)


def test_error_de_seguridad_agotado_queda_fallido(escenario, conn, recursos):
    f, _ = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    politica = PoliticaReintentos(max_intentos=2)  # la espera la adelanta vencer()
    for _ in range(2):
        proveedor.falla_seguridad = True
        procesar_pendientes(conn, proveedor, politica, recursos=recursos)
        vencer(escenario)
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "FALLIDO", 2)]


def test_reenvio_de_rechazada_limpia_el_track_id(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado(asincrono=True)
    proveedor.rechazo_en_consulta = True
    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert track(escenario, f.id) is not None
    f2, _ = solicitar(escenario, conn, origen)
    assert f2.numero == f.numero and track(escenario, f.id) is None
```

En `backend/tests/test_migraciones.py`: cambiar las dos aserciones `== "0005"` por `== "0006"` y agregar:

```python
# ------------------------------------------------------------------------------------------ 0006: cliente DIAN
def test_0006_track_id(bd):
    columnas = {c for (c,) in bd.consultar(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'factura'")}  # fmt: skip
    assert "dian_track_id" in columnas


def test_bajar_la_0006_quita_el_track_id(esquema_vacio):
    migrar(esquema_vacio.dsn)
    migrar(esquema_vacio.dsn, "0005", bajar=True)
    columnas = {c for (c,) in esquema_vacio.consultar(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'factura'")}  # fmt: skip
    assert "dian_track_id" not in columnas
```
(Si `test_migraciones.py` no importa `migrar`, agregar `from conftest import migrar`.)

- [ ] **Step 2: Correr para ver que fallan**

Run: `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest tests/test_outbox.py tests/test_migraciones.py -q`
Expected: FAIL (`TypeError: ProveedorSimulado.__init__() got an unexpected keyword argument 'asincrono'`, `AttributeError: 'ProveedorSimulado' object has no attribute 'consultar'`, versión `0005 != 0006`).

- [ ] **Step 3: Modelo y migración**

En `modelos.py`, después de `ResultadoEmision`:

```python
@dataclass(frozen=True, slots=True)
class EnvioPendiente:
    """El proveedor recibió el documento pero valida después (set de pruebas de la DIAN): el resultado se consulta
    con `track_id` (el ZipKey)."""

    track_id: str
```
y en `TipoTrabajo.CONSULTAR_DIAN` cambiar el comentario a `# conciliar un caso INCIERTO o un envío asíncrono`.

`backend/migrations/versions/0006_cliente_dian.py`:

```python
"""Cliente del web service de la DIAN.

  * `factura.dian_track_id`: el ZipKey de un envío asíncrono (set de pruebas de habilitación); con él se consulta el
    resultado (`GetStatusZip`). NULL en envíos síncronos y en las facturas del proveedor simulado síncrono.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE factura ADD COLUMN dian_track_id TEXT CHECK (btrim(dian_track_id) <> '')")


def downgrade() -> None:
    op.execute("ALTER TABLE factura DROP COLUMN dian_track_id")
```

- [ ] **Step 4: Interfaz y simulado**

Reemplazar en `facturacion/proveedor.py` desde `class ProveedorFacturacion` hasta el final por:

```python
class ProveedorNoConfigurado(Exception):
    """El proveedor no aceptó la conexión (certificado, firma del mensaje, reloj): es configuración corregible, no
    dice nada de la factura. La cola reintenta y avisa."""


class ProveedorFacturacion(Protocol):
    def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision | EnvioPendiente: ...

    def consultar(
        self, *, clave: str, numero: str, cufe: str, track_id: str | None
    ) -> ResultadoEmision | EnvioPendiente | None:
        """Estado de un documento ya enviado (o que pudo enviarse): el resultado, `EnvioPendiente` si sigue en
        validación, o None si el proveedor no lo tiene (se sabe que NO se emitió). Lanza ProveedorRechazo si la
        validación lo rechazó, ProveedorIncierto si no responde."""
        ...


class ProveedorSimulado:
    """Proveedor falso para pruebas y demostraciones. NO habla con la DIAN.

    `falla_siguiente` fuerza el comportamiento de la próxima llamada a `emitir`:
      "rechazo"          -> ProveedorRechazo, no emite
      "timeout_antes"    -> ProveedorIncierto, no emite (la petición nunca llegó)
      "timeout_despues"  -> ProveedorIncierto, pero SÍ emite (se perdió la respuesta)
    `falla_consulta` = True: `consultar` lanza ProveedorIncierto (servicio caído).
    `falla_seguridad` = True: la próxima llamada lanza ProveedorNoConfigurado (certificado no aceptado).
    `asincrono` = True imita el set de pruebas: `emitir` devuelve EnvioPendiente y el resultado llega por `consultar`
    (tras `en_proceso_restantes` consultas "en proceso"; con `rechazo_en_consulta`, como ProveedorRechazo).

    Si la solicitud ya trae número y CUFE (motor propio), los respeta; si no, numera con su propio contador.
    """

    def __init__(self, prefijo: str = "SETP", primer_numero: int = 1, reloj=datetime.now, *, asincrono: bool = False):
        self.prefijo = prefijo
        self._siguiente = primer_numero
        self._emitidas: dict[str, ResultadoEmision] = {}
        self._reloj = reloj
        self._lock = threading.Lock()
        self.asincrono = asincrono
        self.falla_siguiente: str | None = None
        self.falla_consulta = False
        self.falla_seguridad = False
        self.rechazo_en_consulta = False
        self.en_proceso_restantes = 0
        self.llamadas = 0

    def _seguridad(self) -> None:
        if self.falla_seguridad:
            self.falla_seguridad = False
            raise ProveedorNoConfigurado("el proveedor no aceptó la seguridad del mensaje (simulado)")

    def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision | EnvioPendiente:
        with self._lock:
            self._seguridad()
            self.llamadas += 1
            falla, self.falla_siguiente = self.falla_siguiente, None
            if falla == "rechazo":
                raise ProveedorRechazo("documento rechazado por el simulador")
            if falla == "timeout_antes":
                raise ProveedorIncierto("timeout antes de enviar")
            previa = self._emitidas.get(solicitud.clave)
            if previa is None:
                previa = self._emitidas[solicitud.clave] = self._emitida(solicitud)
            if falla == "timeout_despues":
                raise ProveedorIncierto("timeout esperando la respuesta")
            return EnvioPendiente(f"sim-{solicitud.clave}") if self.asincrono else previa

    def _emitida(self, solicitud: SolicitudFactura) -> ResultadoEmision:
        if solicitud.numero is not None and solicitud.cufe is not None:
            numero, cufe = solicitud.numero, solicitud.cufe
        else:
            numero = str(self._siguiente)
            self._siguiente += 1
            cufe = hashlib.sha384(f"{self.prefijo}{numero}{solicitud.total}".encode()).hexdigest()
        emitida_en = self._reloj()
        return ResultadoEmision(
            clave=solicitud.clave,
            prefijo=self.prefijo,
            numero=numero,
            cufe=cufe,
            emitida_en=emitida_en,
            detalle={"simulado": True},
            # Con el documento (motor propio) devuelve la "validación", SIMULADA y marcada como tal.
            application_response=None
            if solicitud.documento is None
            else generar_respuesta(solicitud.documento, validada_en=emitida_en),
        )

    def consultar(
        self, *, clave: str, numero: str, cufe: str, track_id: str | None
    ) -> ResultadoEmision | EnvioPendiente | None:
        with self._lock:
            self._seguridad()
            if self.falla_consulta:
                raise ProveedorIncierto("el servicio de consulta no responde (simulado)")
            emitida = self._emitidas.get(clave)
            if emitida is not None and self.asincrono:
                if self.en_proceso_restantes > 0:
                    self.en_proceso_restantes -= 1
                    return EnvioPendiente(track_id or f"sim-{clave}")
                if self.rechazo_en_consulta:
                    del self._emitidas[clave]
                    raise ProveedorRechazo("documento rechazado por el simulador")
            return emitida
```
y ajustar el import: `from .modelos import EnvioPendiente, ResultadoEmision, SolicitudFactura`.

- [ ] **Step 5: Cola**

En `servicios/outbox.py`:

1. Imports: `from ..facturacion.modelos import (EnvioPendiente, EstadoEntrega, EstadoFactura, EstadoFacturacion, ResultadoEmision, SolicitudFactura, TipoTrabajo)` y `from ..facturacion.proveedor import ProveedorFacturacion, ProveedorNoConfigurado, ProveedorRechazo`.
2. `_Factura`: agregar campo `track_id: str | None` al final; en `_leer_factura` agregar `dian_track_id` al final del `SELECT` y al desempaque, y pasarlo al construir `_Factura(..., xml, track_id)`.
3. Reemplazar el bloque `try: resultado = proveedor.emitir(solicitud) ... return _aceptada(conn, trabajo_id, f, resultado)` de `_enviar` por:

```python
    try:
        resultado = proveedor.emitir(solicitud)
    except ProveedorRechazo as e:
        return _rechazada(conn, trabajo_id, f, e)
    except ProveedorNoConfigurado as e:
        return _no_configurado(conn, politica, trabajo_id, intentos, f, e)
    except Exception as e:  # timeout, red, error inesperado: NO se sabe si quedó emitida
        _incierta(conn, trabajo_id, f, e)
        return EstadoFactura.INCIERTO
    if isinstance(resultado, EnvioPendiente):
        conn.execute("UPDATE factura SET dian_track_id = %s, actualizada_en = now() WHERE id = %s",
                     (resultado.track_id, f.id))  # fmt: skip
        _terminar(conn, trabajo_id, "HECHO")
        encolar(conn, f.id, TipoTrabajo.CONSULTAR_DIAN)
        auditar(conn, "FACTURA_ENVIADA_EN_VALIDACION", "factura", f.id, detalle={"numero": f.numero})
        return EstadoFactura.EN_PROCESO
    return _aceptada(conn, trabajo_id, f, resultado)
```

4. Reemplazar el cuerpo de `_consultar` desde `try:` por:

```python
    try:
        resultado = proveedor.consultar(clave=f.clave, numero=f.numero, cufe=f.cufe, track_id=f.track_id)
    except ProveedorRechazo as e:
        return _rechazada(conn, trabajo_id, f, e)
    except ProveedorNoConfigurado as e:
        return _no_configurado(conn, politica, trabajo_id, intentos, f, e)
    except Exception as e:
        if _reprogramar(conn, politica, trabajo_id, intentos, f"consulta sin respuesta: {type(e).__name__}: {e}"):
            auditar(conn, "CONCILIACION_FALLIDA", "factura", f.id, detalle={"numero": f.numero, "intentos": intentos})
        return f.estado
    if isinstance(resultado, EnvioPendiente):
        if _reprogramar(conn, politica, trabajo_id, intentos, "la DIAN sigue validando el envío"):
            auditar(conn, "VALIDACION_SIN_RESULTADO", "factura", f.id, detalle={"numero": f.numero})
        return f.estado
    if resultado is not None:
        return _aceptada(conn, trabajo_id, f, resultado)
    # Se sabe que NO quedó emitida: se reenvía la misma factura (mismo número, misma clave).
    _marcar(conn, f, EstadoFactura.EN_PROCESO, EstadoFacturacion.EN_PROCESO)
    _terminar(conn, trabajo_id, "HECHO")
    encolar(conn, f.id, TipoTrabajo.ENVIAR_DIAN)
    auditar(conn, "FACTURA_NO_EMITIDA_REENVIO", "factura", f.id, detalle={"numero": f.numero})
    return EstadoFactura.EN_PROCESO
```
Nota: antes la rama "sin respuesta" devolvía siempre `EstadoFactura.INCIERTO`; ahora devuelve `f.estado` porque una factura asíncrona en consulta está `EN_PROCESO`, no INCIERTO. Verificar que `test_consulta_sin_respuesta_reintenta_con_espera_y_luego_falla` siga pasando (allí la factura ya está INCIERTO).

5. Agregar junto a `_incierta`:

```python
def _rechazada(conn: Conexion, trabajo_id: int, f: _Factura, e: ProveedorRechazo) -> EstadoFactura:
    _marcar(conn, f, EstadoFactura.RECHAZADO, EstadoFacturacion.PENDIENTE, error=str(e))
    _terminar(conn, trabajo_id, "HECHO", error=f"rechazo: {e}")
    auditar(conn, "FACTURA_RECHAZADA", "factura", f.id, detalle={"numero": f.numero})
    return EstadoFactura.RECHAZADO


def _no_configurado(
    conn: Conexion, politica: PoliticaReintentos, trabajo_id: int, intentos: int, f: _Factura, e: Exception
) -> EstadoFactura:
    """Certificado o firma del mensaje no aceptados: la factura no cambia; se reintenta (es configuración)."""
    _reprogramar(conn, politica, trabajo_id, intentos, f"la DIAN no aceptó la seguridad del mensaje: {e}")
    auditar(conn, "DIAN_SEGURIDAD", "factura", f.id, detalle={"numero": f.numero, "intentos": intentos})
    return f.estado
```
y borrar del docstring del módulo la frase "se concilia por la clave"; documentar los caminos nuevos en la tabla del docstring (EnvioPendiente → `CONSULTAR_DIAN`; seguridad → reintento).

- [ ] **Step 6: Reenvío limpia el track id**

En `servicios/emision.py`, en el `UPDATE factura SET clave_emision = %s, estado = 'EN_PROCESO', ...` del reenvío, agregar `dian_track_id = NULL,` junto a `xml_firmado = NULL`.

- [ ] **Step 7: Correr las pruebas**

Run: `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest tests/test_outbox.py tests/test_migraciones.py tests/test_emision.py tests/test_entrega.py -q`
Expected: PASS.

- [ ] **Step 8: Suite completa, lint y tipos**

Run: `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest -q && $V/bin/python -m ruff check src tests migrations && $V/bin/python -m mypy`
Expected: todo pasa (447 + las nuevas).

- [ ] **Step 9: Commit**

```bash
git add backend/src/estacion/facturacion/modelos.py backend/src/estacion/facturacion/proveedor.py \
        backend/migrations/versions/0006_cliente_dian.py backend/src/estacion/servicios/outbox.py \
        backend/src/estacion/servicios/emision.py backend/tests/test_outbox.py backend/tests/test_migraciones.py
git commit -m "feat(cola): envío asíncrono, conciliación por CUFE y errores de seguridad del proveedor"
```

---

### Task 5: `ProveedorDian`

**Files:**
- Create: `backend/src/estacion/facturacion/dian/proveedor.py`
- Test: `backend/tests/test_proveedor_dian.py`

**Interfaces:**
- Consumes: `ClienteDian` y sus tipos (Task 3); `EnvioPendiente`, `ProveedorNoConfigurado`, interfaz (Task 4); `paquete.nombre_archivo(prefijo, nit, anio, consecutivo, ext)`.
- Produces: `ProveedorDian(cliente: ClienteDian, *, nit_emisor: str, test_set_id: str | None, reloj: Callable[[], datetime] = ahora_local)` con `emitir` y `consultar` según la interfaz; `ProveedorDian.modo -> Literal["set_pruebas", "normal"]`.

- [ ] **Step 1: Escribir las pruebas**

`backend/tests/test_proveedor_dian.py`:

```python
"""ProveedorDian con un ClienteDian falso: mapeo de respuestas a la interfaz de la cola. Sin red."""

from __future__ import annotations

import base64
import io
import zipfile
from datetime import datetime
from decimal import Decimal

import pytest

from estacion.facturacion.dian.cliente import (
    ErrorSeguridadDian,
    ErrorTransporteDian,
    RespuestaDian,
    RespuestaEnvioAsincrono,
)
from estacion.facturacion.dian.proveedor import ProveedorDian
from estacion.facturacion.modelos import Cliente, EnvioPendiente, LineaFactura, SolicitudFactura
from estacion.facturacion.proveedor import ProveedorIncierto, ProveedorNoConfigurado, ProveedorRechazo

AHORA = datetime(2026, 10, 13, 15, 0)
VALIDA = RespuestaDian(True, "00", "Procesado Correctamente.", (), b"<ApplicationResponse/>", "cufe-1")


class ClienteFalso:
    def __init__(self, **respuestas):
        self.r = respuestas
        self.llamadas: list[tuple] = []

    def _dar(self, nombre, *args):
        self.llamadas.append((nombre, *args))
        r = self.r[nombre]
        if isinstance(r, Exception):
            raise r
        return r

    def enviar_set_pruebas(self, nombre_zip, zip_b64, test_set_id):
        return self._dar("enviar_set_pruebas", nombre_zip, zip_b64, test_set_id)

    def enviar_sincrono(self, nombre_zip, zip_b64):
        return self._dar("enviar_sincrono", nombre_zip, zip_b64)

    def estado_zip(self, track_id):
        return self._dar("estado_zip", track_id)

    def estado(self, cufe):
        return self._dar("estado", cufe)


def solicitud() -> SolicitudFactura:
    linea = LineaFactura("DIESEL", "Diesel", "GLL", Decimal("1.861"), Decimal("15990"), Decimal("29757"),
                         Decimal("0"), "S:1")  # fmt: skip
    return SolicitudFactura(
        clave="k1", cliente=Cliente("CC", "123", "ANA"), lineas=(linea,), forma_pago="CONTADO", medio_pago="EFECTIVO",
        fecha_emision=AHORA, numero="SETP990000000", cufe="cufe-1", documento=b"<Invoice/>",
    )  # fmt: skip


def proveedor(cliente, test_set_id="set-1"):
    return ProveedorDian(cliente, nit_emisor="901335327", test_set_id=test_set_id, reloj=lambda: AHORA)


def test_set_de_pruebas_envia_el_zip_y_queda_pendiente():
    c = ClienteFalso(enviar_set_pruebas=RespuestaEnvioAsincrono("zip-1", ()))
    r = proveedor(c).emitir(solicitud())
    assert r == EnvioPendiente("zip-1")
    _, nombre_zip, zip_b64, test_set = c.llamadas[0]
    assert nombre_zip == "z0901335327000263b023380.zip" and test_set == "set-1"
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(zip_b64))) as z:
        assert z.namelist() == ["fv0901335327000263b023380.xml"]
        assert z.read("fv0901335327000263b023380.xml") == b"<Invoice/>"


def test_set_sin_zip_key_con_errores_es_rechazo():
    c = ClienteFalso(enviar_set_pruebas=RespuestaEnvioAsincrono("", ("archivo mal formado",)))
    with pytest.raises(ProveedorRechazo, match="archivo mal formado"):
        proveedor(c).emitir(solicitud())


def test_modo_normal_acepta():
    c = ClienteFalso(enviar_sincrono=VALIDA)
    p = proveedor(c, test_set_id=None)
    assert p.modo == "normal"
    r = p.emitir(solicitud())
    assert (r.cufe, r.numero, r.prefijo, r.application_response) == ("cufe-1", "SETP990000000", "SETP",
                                                                     b"<ApplicationResponse/>")  # fmt: skip
    assert r.detalle["codigo_dian"] == "00"


def test_rechazo_lleva_los_mensajes():
    c = ClienteFalso(enviar_sincrono=RespuestaDian(False, "99", "Errores", ("FAD06 CUFE", "FAJ21 NIT"), None, ""))
    with pytest.raises(ProveedorRechazo, match="FAD06 CUFE; FAJ21 NIT"):
        proveedor(c, test_set_id=None).emitir(solicitud())


def test_documento_procesado_anteriormente_es_incierto():
    # Review Focus 1: un reintento tras una caída reenvía lo mismo; no es un rechazo, se concilia por CUFE.
    c = ClienteFalso(enviar_sincrono=RespuestaDian(False, "99", "Errores",
                                                   ("Regla: 90, Rechazo: Documento procesado anteriormente.",),
                                                   None, ""))  # fmt: skip
    with pytest.raises(ProveedorIncierto, match="procesado anteriormente"):
        proveedor(c, test_set_id=None).emitir(solicitud())


def test_valida_sin_application_response_no_falla():
    # Review Focus 5: la factura es válida aunque falte el XML de respuesta; la entrega lo manejará.
    c = ClienteFalso(enviar_sincrono=RespuestaDian(True, "00", "OK", (), None, "cufe-1"))
    assert proveedor(c, test_set_id=None).emitir(solicitud()).application_response is None


def test_cufe_vacio_en_la_respuesta_usa_el_de_la_factura():
    c = ClienteFalso(enviar_sincrono=RespuestaDian(True, "00", "OK", (), b"<A/>", ""))
    assert proveedor(c, test_set_id=None).emitir(solicitud()).cufe == "cufe-1"


@pytest.mark.parametrize(
    "error,esperado", [(ErrorTransporteDian("timeout"), ProveedorIncierto), (ErrorSeguridadDian("x"), ProveedorNoConfigurado)]
)
def test_errores_del_cliente(error, esperado):
    with pytest.raises(esperado):
        proveedor(ClienteFalso(enviar_set_pruebas=error)).emitir(solicitud())


def test_consultar_con_track_id_usa_get_status_zip():
    en_proceso = RespuestaDian(False, "", "Batch en proceso de validación.", (), None, "")
    c = ClienteFalso(estado_zip=[en_proceso])
    assert proveedor(c).consultar(clave="k1", numero="SETP990000000", cufe="cufe-1", track_id="zip-1") == (
        EnvioPendiente("zip-1")
    )
    c.r["estado_zip"] = [VALIDA]
    r = proveedor(c).consultar(clave="k1", numero="SETP990000000", cufe="cufe-1", track_id="zip-1")
    assert r.cufe == "cufe-1" and c.llamadas[-1] == ("estado_zip", "zip-1")


def test_consultar_sin_track_id_usa_get_status_por_cufe():
    c = ClienteFalso(estado=RespuestaDian(False, "66", "NSU no encontrado", (), None, ""))
    assert proveedor(c).consultar(clave="k1", numero="SETP990000000", cufe="cufe-1", track_id=None) is None
    assert c.llamadas == [("estado", "cufe-1")]


def test_consultar_lista_vacia_es_no_encontrado():
    c = ClienteFalso(estado_zip=[])
    assert proveedor(c).consultar(clave="k1", numero="SETP990000000", cufe="c", track_id="zip-1") is None


def test_consultar_rechazo():
    c = ClienteFalso(estado_zip=[RespuestaDian(False, "99", "Errores", ("FAD06",), None, "")])
    with pytest.raises(ProveedorRechazo, match="FAD06"):
        proveedor(c).consultar(clave="k1", numero="SETP990000000", cufe="c", track_id="zip-1")
```

Nota sobre los nombres esperados: NIT `901335327` → `0901335327`; `000` software propio; año 26; consecutivo 990000000 = `3b023380` en hex.

- [ ] **Step 2: Correr para ver que falla**

Run: `$V/bin/python -m pytest tests/test_proveedor_dian.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'estacion.facturacion.dian.proveedor'`.

- [ ] **Step 3: Implementar**

`backend/src/estacion/facturacion/dian/proveedor.py`:

```python
"""`ProveedorFacturacion` que habla con la DIAN de verdad (ambiente según la URL del `ClienteDian`).

Modos:
    set_pruebas (hay TestSetId): SendTestSetAsync -> EnvioPendiente(ZipKey); el resultado llega con GetStatusZip.
    normal: SendBillSync -> resultado inmediato.
Conciliación: con track id, GetStatusZip; sin él, GetStatus por CUFE.
"Documento procesado anteriormente" (un reintento tras una caída) NO es un rechazo: es incierto y se concilia.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from ...servicios._bd import ahora_local
from ..modelos import EnvioPendiente, ResultadoEmision, SolicitudFactura
from ..paquete import nombre_archivo
from ..proveedor import ProveedorIncierto, ProveedorNoConfigurado, ProveedorRechazo
from .cliente import ClienteDian, ErrorSeguridadDian, ErrorTransporteDian, RespuestaDian

_YA_PROCESADO = re.compile(r"procesado anteriormente", re.IGNORECASE)
_NUMERO = re.compile(r"^([A-Z0-9]*?)(\d+)$")


class ProveedorDian:
    def __init__(
        self,
        cliente: ClienteDian,
        *,
        nit_emisor: str,
        test_set_id: str | None,
        reloj: Callable[[], datetime] = ahora_local,
    ):
        self._cliente, self._nit, self._test_set_id, self._reloj = cliente, nit_emisor, test_set_id, reloj

    @property
    def modo(self) -> Literal["set_pruebas", "normal"]:
        return "set_pruebas" if self._test_set_id else "normal"

    def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision | EnvioPendiente:
        if solicitud.documento is None or solicitud.numero is None or solicitud.cufe is None:
            raise ValueError("ProveedorDian necesita el documento firmado, el número y el CUFE")
        nombre_zip, contenido = self._zip(solicitud)
        try:
            if self._test_set_id:
                envio = self._cliente.enviar_set_pruebas(nombre_zip, contenido, self._test_set_id)
                if not envio.zip_key:
                    raise ProveedorRechazo("; ".join(envio.errores) or "la DIAN no devolvió ZipKey")
                return EnvioPendiente(envio.zip_key)
            respuesta = self._cliente.enviar_sincrono(nombre_zip, contenido)
        except ErrorTransporteDian as e:
            raise ProveedorIncierto(str(e)) from None
        except ErrorSeguridadDian as e:
            raise ProveedorNoConfigurado(str(e)) from None
        resultado = self._resultado(respuesta, solicitud.clave, solicitud.numero, solicitud.cufe, None)
        if resultado is None:  # "no encontrado" justo después de enviar: no se sabe qué pasó
            raise ProveedorIncierto(f"la DIAN respondió {respuesta.codigo} al envío")
        return resultado

    def consultar(
        self, *, clave: str, numero: str, cufe: str, track_id: str | None
    ) -> ResultadoEmision | EnvioPendiente | None:
        try:
            if track_id:
                respuestas = self._cliente.estado_zip(track_id)
                if not respuestas:
                    return None
                respuesta = respuestas[0]
            else:
                respuesta = self._cliente.estado(cufe)
        except ErrorTransporteDian as e:
            raise ProveedorIncierto(str(e)) from None
        except ErrorSeguridadDian as e:
            raise ProveedorNoConfigurado(str(e)) from None
        return self._resultado(respuesta, clave, numero, cufe, track_id)

    # ------------------------------------------------------------------ apoyo
    def _zip(self, s: SolicitudFactura) -> tuple[str, str]:
        assert s.numero is not None and s.documento is not None
        m = _NUMERO.match(s.numero)
        if m is None:
            raise ValueError(f"número de factura inesperado: {s.numero!r}")
        consecutivo, anio = int(m.group(2)), s.fecha_emision.year
        nombre_xml = nombre_archivo("fv", self._nit, anio, consecutivo, "xml")
        nombre_zip = nombre_archivo("z", self._nit, anio, consecutivo, "zip")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(nombre_xml, s.documento)
        return nombre_zip, base64.b64encode(buffer.getvalue()).decode("ascii")

    def _resultado(
        self, r: RespuestaDian, clave: str, numero: str, cufe: str, track_id: str | None
    ) -> ResultadoEmision | EnvioPendiente | None:
        if r.valida:
            m = _NUMERO.match(numero)
            prefijo = m.group(1) if m else ""
            return ResultadoEmision(
                clave=clave, prefijo=prefijo, numero=numero, cufe=r.cufe or cufe, emitida_en=self._reloj(),
                estado="VALIDADA_DIAN",
                detalle={"codigo_dian": r.codigo, "descripcion_dian": r.descripcion, "modo": self.modo},
                application_response=r.application_response,
            )  # fmt: skip
        if r.no_encontrado:
            return None
        if r.en_proceso:
            return EnvioPendiente(track_id or "")
        mensajes = "; ".join(r.errores) or f"{r.codigo} {r.descripcion}".strip()
        if _YA_PROCESADO.search(mensajes):
            raise ProveedorIncierto(f"la DIAN dice que el documento ya se procesó: {mensajes}")
        raise ProveedorRechazo(mensajes)
```

- [ ] **Step 4: Correr las pruebas**

Run: `$V/bin/python -m pytest tests/test_proveedor_dian.py -v`
Expected: PASS.

- [ ] **Step 5: Lint y tipos**

Run: `$V/bin/python -m ruff check src tests && $V/bin/python -m ruff format src tests/test_proveedor_dian.py && $V/bin/python -m mypy`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/src/estacion/facturacion/dian/proveedor.py backend/tests/test_proveedor_dian.py
git commit -m "feat(dian): ProveedorDian con set de pruebas y envío síncrono"
```

---

### Task 6: Worker con `--proveedor dian`

**Files:**
- Modify: `backend/src/estacion/worker.py`
- Modify: `.env.example` (agregar `DIAN_TEST_SET_ID=` debajo de `DIAN_CLAVE_TECNICA=` con el comentario `# Set de pruebas de habilitación (portal DIAN). Con valor: el worker usa SendTestSetAsync.`)
- Test: `backend/tests/test_outbox.py` (pruebas del worker)

**Interfaces:**
- Consumes: `ClienteDian`, `URL_HABILITACION`, `ProveedorDian`, `ConfiguracionIncompleta`, `AMBIENTES_PERMITIDOS`.
- Produces: `worker.construir_proveedor(nombre: str, conn, entorno: Mapping[str, str], certificado: Certificado | None) -> ProveedorFacturacion` (lanza `ConfiguracionIncompleta` con mensaje sin secretos).

- [ ] **Step 1: Escribir las pruebas**

Agregar a `backend/tests/test_outbox.py`:

```python
def test_worker_dian_sin_certificado_no_arranca(escenario, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", escenario.datos.bd.dsn)
    monkeypatch.delenv("DIAN_CERTIFICADO_RUTA", raising=False)
    assert worker.main(["--una-vez", "--proveedor", "dian"]) == 2


def test_construir_proveedor_dian(escenario, conn, certificado):
    from estacion.facturacion.dian.proveedor import ProveedorDian
    from estacion.servicios.configuracion import ConfiguracionIncompleta

    entorno = {**ENTORNO_DIAN, "DIAN_TEST_SET_ID": "set-1"}
    p = worker.construir_proveedor("dian", conn, entorno, certificado)
    assert isinstance(p, ProveedorDian) and p.modo == "set_pruebas"
    with pytest.raises(ConfiguracionIncompleta, match="DIAN_AMBIENTE"):
        worker.construir_proveedor("dian", conn, {**entorno, "DIAN_AMBIENTE": "1"}, certificado)
    with pytest.raises(ConfiguracionIncompleta, match="certificado"):
        worker.construir_proveedor("dian", conn, entorno, None)
    assert isinstance(worker.construir_proveedor("simulado", conn, entorno, None), ProveedorSimulado)
```

- [ ] **Step 2: Correr para ver que falla**

Run: `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest tests/test_outbox.py -k "worker or construir" -v`
Expected: FAIL (`AttributeError: module 'estacion.worker' has no attribute 'construir_proveedor'`; `--proveedor dian` no es una opción válida → código 2 por argparse pero la segunda prueba falla).

- [ ] **Step 3: Implementar**

En `worker.py`:
- docstring: reemplazar "HOY SOLO EXISTE EL PROVEEDOR SIMULADO..." y "El cliente de habilitación llega en la Fase 6" por: "`--proveedor simulado` (por defecto) no envía nada; `--proveedor dian` envía al ambiente de HABILITACIÓN (único permitido) con el certificado de DIAN_CERTIFICADO_*; con DIAN_TEST_SET_ID usa el set de pruebas (envío asíncrono)."
- `parser.add_argument("--proveedor", choices=["simulado", "dian"], default="simulado")`.
- Agregar:

```python
def construir_proveedor(
    nombre: str, conn: psycopg.Connection, entorno: Mapping[str, str], certificado: Certificado | None
) -> ProveedorFacturacion:
    if nombre == "simulado":
        return ProveedorSimulado()
    ambiente = entorno.get("DIAN_AMBIENTE", "").strip()
    if ambiente not in AMBIENTES_PERMITIDOS:
        raise ConfiguracionIncompleta(f"DIAN_AMBIENTE={ambiente!r} no permitido: solo habilitación (\"2\")")
    if certificado is None:
        raise ConfiguracionIncompleta("sin certificado de firma no se puede hablar con la DIAN")
    fila = conn.execute("SELECT nit FROM estacion WHERE activa").fetchone()
    if fila is None:
        raise ConfiguracionIncompleta("no hay una estación (emisor) activa configurada")
    return ProveedorDian(
        ClienteDian(URL_HABILITACION, certificado),
        nit_emisor=fila[0],
        test_set_id=entorno.get("DIAN_TEST_SET_ID", "").strip() or None,
    )
```
- En `main`, dentro de `with psycopg.connect(dsn, autocommit=True) as conn:` y antes del bucle, reemplazar `proveedor = ProveedorSimulado()` + el `log.warning` por:

```python
        try:
            proveedor = construir_proveedor(args.proveedor, conn, os.environ, certificado)
        except ConfiguracionIncompleta as e:
            log.error("%s", e)
            return 2
        if isinstance(proveedor, ProveedorDian):
            log.warning("proveedor DIAN, ambiente de HABILITACIÓN, modo %s", proveedor.modo)
        else:
            log.warning("proveedor SIMULADO: las facturas NO se envían a la DIAN")
```
- Imports: `from collections.abc import Mapping`; `from .facturacion.dian.cliente import ClienteDian`; `from .facturacion.dian.proveedor import ProveedorDian`; `from .facturacion.dian.sobre import URL_HABILITACION`; `from .facturacion.proveedor import ProveedorFacturacion, ProveedorSimulado`; `from .servicios.configuracion import AMBIENTES_PERMITIDOS, ConfiguracionIncompleta`.

- [ ] **Step 4: Correr las pruebas**

Run: `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest tests/test_outbox.py -v`
Expected: PASS (incluidas `test_worker_una_vez` y `test_worker_sin_certificado_no_envia`, que siguen con el simulado).

- [ ] **Step 5: Commit**

```bash
git add backend/src/estacion/worker.py .env.example backend/tests/test_outbox.py
git commit -m "feat(worker): --proveedor dian (solo habilitación)"
```

---

### Task 7: Comando `python -m estacion.habilitacion`

**Files:**
- Create: `backend/src/estacion/habilitacion.py`
- Test: `backend/tests/test_habilitacion.py`

**Interfaces:**
- Consumes: `ClienteDian.rangos_numeracion`, `URL_HABILITACION`, `certificado_desde_entorno`, `registrar_venta_manual(conn, *, usuario_id, surtidor_id, lado, producto, volumen, valor, ppu=None, forma_pago="CONTADO")`, `clientes.registrar(conn, Cliente, *, canal, usuario_id)`, `ClienteYaExiste`, `ServicioEmision(entorno=...).solicitar(conn, *, clave, origen, cliente_id, usuario_id, medio_pago)`, `Origen("venta_manual", id)`.
- Produces: `habilitacion.main(argv: list[str] | None = None, *, entorno: Mapping[str, str] | None = None, confirmar: Callable[[str], str] = input, cliente_dian: ClienteDian | None = None) -> int`; constantes `NUMERACION_SET = dict(prefijo="SETP", numero_resolucion="18760000001", desde=990000000, hasta=995000000, vigente_desde=date(2019, 1, 19), vigente_hasta=date(2030, 1, 19))`.

- [ ] **Step 1: Escribir las pruebas**

`backend/tests/test_habilitacion.py`:

```python
"""Comando de habilitación. Sin red: la DIAN es un cliente falso y el envío lo haría el worker."""

from __future__ import annotations

import pytest

from conftest import ENTORNO_DIAN
from estacion import habilitacion
from estacion.facturacion.dian.cliente import ConsultaRangos, ErrorSeguridadDian, RangoNumeracion

pytestmark = pytest.mark.bd


def entorno(escenario, **extra):
    return {**ENTORNO_DIAN, "DATABASE_URL": escenario.datos.bd.dsn, **extra}


class DianFalsa:
    def __init__(self, resultado):
        self.resultado, self.llamadas = resultado, []

    def rangos_numeracion(self, nit, software_id):
        self.llamadas.append((nit, software_id))
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


def test_probar_conexion_ok(escenario, capsys):
    dian = DianFalsa(ConsultaRangos("100", "OK", (RangoNumeracion("SETP", "18760000001", 990000000, 995000000,
                                                                  "2019-01-19", "2030-01-19"),)))  # fmt: skip
    assert habilitacion.main(["probar-conexion"], entorno=entorno(escenario), cliente_dian=dian) == 0
    assert dian.llamadas == [("900123456", ENTORNO_DIAN["DIAN_SOFTWARE_ID"])]
    salida = capsys.readouterr().out
    assert "SETP 990000000-995000000" in salida and ENTORNO_DIAN["DIAN_SOFTWARE_PIN"] not in salida


def test_probar_conexion_error_de_seguridad(escenario, capsys):
    dian = DianFalsa(ErrorSeguridadDian("GetNumberingRange: MessageExpired"))
    assert habilitacion.main(["probar-conexion"], entorno=entorno(escenario), cliente_dian=dian) == 1
    assert "reloj" in capsys.readouterr().err  # sugiere revisar la hora del servidor


def sin_conflicto(escenario):
    """El escenario trae una SETP 1-5000 con la MISMA resolución del set; en la base real no existe."""
    escenario.datos.conn.execute("UPDATE numeracion SET numero_resolucion = '1' WHERE id = %s", (escenario.numeracion,))


def admin(escenario) -> str:
    escenario.datos.conn.execute("UPDATE usuario SET rol = 'ADMIN' WHERE id = %s", (escenario.usuario,))
    escenario.datos.venta_manual(escenario.usuario)  # asegura un surtidor y un producto
    return escenario.datos.uno("SELECT usuario FROM usuario WHERE id = %s", (escenario.usuario,))


def test_configurar_numeracion_es_idempotente(escenario):
    # Review Focus 4: dos corridas, una sola numeración activa, sin reiniciar el consecutivo.
    sin_conflicto(escenario)
    e = entorno(escenario)
    assert habilitacion.main(["configurar-numeracion"], entorno=e, confirmar=lambda _: "SI") == 0
    escenario.datos.conn.execute("UPDATE numeracion SET siguiente = 990000005 WHERE desde = 990000000")
    assert habilitacion.main(["configurar-numeracion"], entorno=e, confirmar=lambda _: "SI") == 0
    filas = escenario.datos.conn.execute(
        "SELECT prefijo, desde, siguiente, activa FROM numeracion ORDER BY id").fetchall()
    assert filas == [("SETP", 1, 1, False), ("SETP", 990000000, 990000005, True)]


def test_configurar_numeracion_con_conflicto(escenario):
    # Misma resolución SETP con otro rango: no se adivina, se detiene sin cambiar nada.
    assert habilitacion.main(["configurar-numeracion"], entorno=entorno(escenario), confirmar=lambda _: "SI") == 1
    assert escenario.datos.conn.execute("SELECT desde, activa FROM numeracion").fetchall() == [(1, True)]


def test_configurar_numeracion_sin_confirmar_no_cambia_nada(escenario):
    assert habilitacion.main(["configurar-numeracion"], entorno=entorno(escenario), confirmar=lambda _: "no") == 1
    assert escenario.datos.uno("SELECT count(*) FROM numeracion") == 1


def test_enviar_crea_facturas_setp_consecutivas(escenario):
    sin_conflicto(escenario)
    e = entorno(escenario)
    login = admin(escenario)
    habilitacion.main(["configurar-numeracion"], entorno=e, confirmar=lambda _: "SI")
    assert habilitacion.main(["enviar", "--cantidad", "2", "--usuario", login], entorno=e,
                             confirmar=lambda _: "ENVIAR") == 0  # fmt: skip
    numeros = [n for (n,) in escenario.datos.conn.execute(
        "SELECT numero FROM factura ORDER BY id").fetchall()]  # fmt: skip
    assert numeros == ["SETP990000000", "SETP990000001"]
    assert escenario.datos.uno("SELECT count(*) FROM outbox WHERE tipo = 'ENVIAR_DIAN'") == 2


def test_enviar_sin_confirmacion_no_crea_nada(escenario):
    e = entorno(escenario)
    login = admin(escenario)
    assert habilitacion.main(["enviar", "--usuario", login], entorno=e, confirmar=lambda _: "si") == 1
    assert escenario.datos.uno("SELECT count(*) FROM factura") == 0


def test_enviar_exige_admin(escenario):
    login = escenario.datos.uno("SELECT usuario FROM usuario WHERE id = %s", (escenario.usuario,))  # BOMBERO
    assert habilitacion.main(["enviar", "--usuario", login], entorno=entorno(escenario),
                             confirmar=lambda _: "ENVIAR") == 2  # fmt: skip


def test_todo_exige_ambiente_de_habilitacion(escenario):
    e = entorno(escenario, DIAN_AMBIENTE="1")
    assert habilitacion.main(["estado"], entorno=e) == 2


def test_estado_lista_las_facturas_setp(escenario, capsys):
    assert habilitacion.main(["estado"], entorno=entorno(escenario)) == 0
    assert "sin facturas SETP" in capsys.readouterr().out
```

- [ ] **Step 2: Correr para ver que falla**

Run: `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest tests/test_habilitacion.py -v`
Expected: FAIL con `ImportError: cannot import name 'habilitacion'`.

- [ ] **Step 3: Implementar**

`backend/src/estacion/habilitacion.py`:

```python
"""Habilitación ante la DIAN (software propio): `python -m estacion.habilitacion <acción>`.

Orden recomendado (todo contra el ambiente de HABILITACIÓN; producción está bloqueada):
    probar-conexion         GetNumberingRange: certificado, firma del mensaje y red. No escribe nada.
    configurar-numeracion   carga la numeración del set (SETP 990000000-995000000) como la activa.
    enviar --usuario admin  crea N ventas de prueba y sus facturas; el ENVÍO lo hace el worker:
                            `python -m estacion.worker --proveedor dian`
    estado                  facturas SETP con su estado y lo que respondió la DIAN.
Lee DATABASE_URL, DIAN_* y el certificado del entorno. Nunca imprime el PIN, la clave técnica ni la del certificado.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal

import psycopg

from .facturacion.dian.cliente import ClienteDian, ErrorSeguridadDian, ErrorTransporteDian
from .facturacion.dian.sobre import URL_HABILITACION
from .facturacion.firma import FirmaError, certificado_desde_entorno
from .facturacion.modelos import Cliente
from .servicios import clientes
from .servicios._bd import auditar
from .servicios.configuracion import AMBIENTES_PERMITIDOS
from .servicios.emision import Origen, ServicioEmision
from .servicios.ventas import registrar_venta_manual

NUMERACION_SET = dict(
    prefijo="SETP", numero_resolucion="18760000001", desde=990000000, hasta=995000000,
    vigente_desde=date(2019, 1, 19), vigente_hasta=date(2030, 1, 19),
)  # fmt: skip
# Datos SINTÉTICOS de la venta de prueba (el ejemplo de CLAUDE.md: 1,861 gal a $15.990 = $29.757).
VOLUMEN, PPU, VALOR = Decimal("1.861"), Decimal("15990"), Decimal("29757")
DOCUMENTO_PRUEBA = "1000000001"


def _err(texto: str) -> None:
    print(texto, file=sys.stderr)


def main(
    argv: list[str] | None = None,
    *,
    entorno: Mapping[str, str] | None = None,
    confirmar: Callable[[str], str] = input,
    cliente_dian: ClienteDian | None = None,
) -> int:
    ap = argparse.ArgumentParser(prog="python -m estacion.habilitacion", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    sub = ap.add_subparsers(dest="accion", required=True)
    sub.add_parser("probar-conexion")
    sub.add_parser("configurar-numeracion")
    env = sub.add_parser("enviar")
    env.add_argument("--cantidad", type=int, default=1, choices=range(1, 31), metavar="1..30")
    env.add_argument("--usuario", required=True, help="login de un usuario ADMIN (queda en la auditoría)")
    sub.add_parser("estado")
    args = ap.parse_args(argv)

    entorno = os.environ if entorno is None else entorno
    if entorno.get("DIAN_AMBIENTE", "").strip() not in AMBIENTES_PERMITIDOS:
        _err('DIAN_AMBIENTE debe ser "2" (habilitación); producción está bloqueada')
        return 2
    dsn = entorno.get("DATABASE_URL", "").strip()
    if not dsn:
        _err("falta la variable de entorno DATABASE_URL")
        return 2
    with psycopg.connect(dsn, autocommit=True) as conn:
        estacion = conn.execute("SELECT id, nit, email FROM estacion WHERE activa").fetchone()
        if estacion is None:
            _err("no hay una estación (emisor) activa configurada")
            return 2
        if args.accion == "probar-conexion":
            return _probar(entorno, estacion[1], cliente_dian)
        if args.accion == "configurar-numeracion":
            return _configurar(conn, estacion[0], confirmar)
        if args.accion == "enviar":
            return _enviar(conn, entorno, estacion, args.cantidad, args.usuario, confirmar)
        return _estado(conn)


def _probar(entorno: Mapping[str, str], nit: str, cliente_dian: ClienteDian | None) -> int:
    software_id = entorno.get("DIAN_SOFTWARE_ID", "").strip()
    if not software_id:
        _err("falta DIAN_SOFTWARE_ID")
        return 2
    if cliente_dian is None:
        try:
            cliente_dian = ClienteDian(URL_HABILITACION, certificado_desde_entorno(entorno))
        except FirmaError as e:
            _err(f"certificado: {e}")
            return 2
    try:
        r = cliente_dian.rangos_numeracion(nit, software_id)
    except ErrorSeguridadDian as e:
        _err(f"la DIAN rechazó la seguridad del mensaje: {e}")
        _err("revise que el certificado sea el de la empresa y esté vigente, y que el reloj del servidor esté en hora")
        return 1
    except ErrorTransporteDian as e:
        _err(f"sin respuesta útil de la DIAN: {e}")
        return 1
    print(f"Conexión OK con la DIAN (habilitación): {r.codigo} {r.descripcion}")
    for rango in r.rangos:
        print(f"  {rango.prefijo} {rango.desde}-{rango.hasta} resolución {rango.resolucion} "
              f"({rango.vigente_desde} a {rango.vigente_hasta})")  # fmt: skip
    return 0


def _configurar(conn: psycopg.Connection, estacion_id: int, confirmar: Callable[[str], str]) -> int:
    n = NUMERACION_SET
    if confirmar(f"Activar la numeración del set {n['prefijo']} {n['desde']}-{n['hasta']}? Escriba SI: ").strip() != "SI":
        _err("cancelado: no se cambió nada")
        return 1
    with conn.transaction():
        fila = conn.execute(
            "SELECT id, desde, hasta FROM numeracion WHERE prefijo = %s AND numero_resolucion = %s",
            (n["prefijo"], n["numero_resolucion"]),
        ).fetchone()
        if fila is not None and (fila[1], fila[2]) != (n["desde"], n["hasta"]):
            _err(f"ya existe la numeración {n['prefijo']}/{n['numero_resolucion']} con otro rango "
                 f"({fila[1]}-{fila[2]}); revísela antes de continuar")  # fmt: skip
            return 1  # todavía no se modificó nada
        conn.execute("UPDATE numeracion SET activa = FALSE WHERE estacion_id = %s AND activa", (estacion_id,))
        if fila is None:
            conn.execute(
                """INSERT INTO numeracion (estacion_id, prefijo, numero_resolucion, desde, hasta, siguiente,
                                           vigente_desde, vigente_hasta, activa)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)""",
                (estacion_id, n["prefijo"], n["numero_resolucion"], n["desde"], n["hasta"], n["desde"],
                 n["vigente_desde"], n["vigente_hasta"]),
            )  # fmt: skip
        else:  # ya existe la del set: se reactiva SIN tocar `siguiente`
            conn.execute("UPDATE numeracion SET activa = TRUE WHERE id = %s", (fila[0],))
        auditar(conn, "NUMERACION_SET_ACTIVADA", "estacion", estacion_id, detalle={"prefijo": n["prefijo"]})
    print(f"Numeración {n['prefijo']} {n['desde']}-{n['hasta']} activa.")
    return 0
```

La restricción `numeracion_prefijo_numero_resolucion_key` impide dos filas con `('SETP', '18760000001')`: por eso una
existente con otro rango es un conflicto que se reporta, no se modifica.

Continuar el archivo:

```python
def _enviar(
    conn: psycopg.Connection,
    entorno: Mapping[str, str],
    estacion: tuple,
    cantidad: int,
    login: str,
    confirmar: Callable[[str], str],
) -> int:
    usuario = conn.execute(
        "SELECT id FROM usuario WHERE usuario = %s AND activo AND rol = 'ADMIN'", (login.strip().lower(),)
    ).fetchone()
    if usuario is None:
        _err(f"{login!r} no es un usuario ADMIN activo")
        return 2
    surtidor = conn.execute("SELECT id FROM surtidor WHERE activo ORDER BY id LIMIT 1").fetchone()
    producto = conn.execute("SELECT codigo FROM producto ORDER BY id LIMIT 1").fetchone()
    if surtidor is None or producto is None:
        _err("se necesita al menos un surtidor activo y un producto")
        return 2
    texto = f"Se crearán {cantidad} facturas para el ambiente de HABILITACIÓN de la DIAN. Escriba ENVIAR: "
    if confirmar(texto).strip() != "ENVIAR":
        _err("cancelado: no se creó nada")
        return 1
    cliente_id = _cliente_de_prueba(conn, estacion[2], usuario[0])
    servicio = ServicioEmision(entorno=entorno)
    for _ in range(cantidad):
        venta = registrar_venta_manual(
            conn, usuario_id=usuario[0], surtidor_id=surtidor[0], lado="A", producto=producto[0],
            volumen=VOLUMEN, valor=VALOR, ppu=PPU,
        )  # fmt: skip
        f = servicio.solicitar(
            conn, clave=uuid.uuid4().hex, origen=Origen("venta_manual", venta), cliente_id=cliente_id,
            usuario_id=usuario[0], medio_pago="EFECTIVO",
        )  # fmt: skip
        print(f"  {f.numero} en cola (factura {f.id})")
    print("Ahora corra el worker: python -m estacion.worker --proveedor dian ; y luego: estado")
    return 0


def _cliente_de_prueba(conn: psycopg.Connection, email_estacion: str, usuario_id: int) -> int:
    """Cliente SINTÉTICO; el correo es el de la propia estación (la entrega no le llega a nadie más)."""
    existente = conn.execute(
        "SELECT id FROM cliente WHERE tipo_documento = 'CC' AND numero_documento = %s", (DOCUMENTO_PRUEBA,)
    ).fetchone()
    if existente is not None:
        return existente[0]
    registrado = clientes.registrar(
        conn,
        Cliente("CC", DOCUMENTO_PRUEBA, "CLIENTE DE PRUEBA", "HABILITACION", email_estacion,
                autoriza_tratamiento_datos=True),  # fmt: skip
        canal="PISTA",
        usuario_id=usuario_id,
    )
    return registrado.id


def _estado(conn: psycopg.Connection) -> int:
    filas = conn.execute(
        """SELECT numero, estado, dian_track_id, respuesta_dian->>'codigo_dian', error
           FROM factura WHERE prefijo = 'SETP' AND consecutivo >= 990000000 ORDER BY consecutivo"""
    ).fetchall()
    if not filas:
        print("sin facturas SETP del set de pruebas")
        return 0
    for numero, estado, track, codigo, error in filas:
        print(f"{numero}  {estado:<10}  track={track or '-'}  código={codigo or '-'}  {error or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Verificar en `servicios/clientes.py` que `registrar` acepta `canal="PISTA"` y que `ClienteRegistrado` tiene `.id` (sí: `RETURNING {_COLUMNAS}` empieza por id). Si `respuesta_dian` no guarda `codigo_dian` en la raíz: `_aceptada` guarda `{"estado", "emitida_en", **resultado.detalle}`, y `ProveedorDian` pone `codigo_dian` en `detalle`, así que sí queda en la raíz.

- [ ] **Step 4: Correr las pruebas**

Run: `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest tests/test_habilitacion.py -v`
Expected: PASS.

- [ ] **Step 5: Suite completa, lint y tipos**

Run: `ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest -q && $V/bin/python -m ruff check src tests migrations && $V/bin/python -m ruff format --check src tests migrations && $V/bin/python -m mypy`
Expected: todo pasa.

- [ ] **Step 6: Commit**

```bash
git add backend/src/estacion/habilitacion.py backend/tests/test_habilitacion.py
git commit -m "feat: comando de habilitación ante la DIAN"
```

---

### Task 8: Documentación y verificación final

**Files:**
- Modify: `README.md` (sección nueva "Habilitación ante la DIAN" después de "Entrega de facturas por correo")
- Modify: `Makefile` (objetivos `habilitacion-probar`, `worker-dian`)

- [ ] **Step 1: README**

Agregar:

````markdown
## Habilitación ante la DIAN (set de pruebas)

Requisitos en el `.env`: `DIAN_AMBIENTE=2`, `DIAN_SOFTWARE_ID`, `DIAN_SOFTWARE_PIN`, `DIAN_CLAVE_TECNICA` (la del
rango de pruebas), `DIAN_TEST_SET_ID` (portal de habilitación) y el certificado REAL de la empresa en
`DIAN_CERTIFICADO_RUTA` / `DIAN_CERTIFICADO_CLAVE`. Producción está bloqueada en el código.

```bash
cd backend
.venv/bin/python -m estacion.habilitacion probar-conexion          # 1. certificado, firma y red (no envía nada)
.venv/bin/python -m estacion.habilitacion configurar-numeracion    # 2. SETP 990000000-995000000 activa
.venv/bin/python -m estacion.habilitacion enviar --usuario admin1  # 3. una factura de prueba en cola
.venv/bin/python -m estacion.worker --proveedor dian               # 4. la envía y consulta el resultado
.venv/bin/python -m estacion.habilitacion estado                   # 5. qué respondió la DIAN
```
Después, revisar el portal: el set se aprueba con 1 factura aceptada. Si sigue "En proceso", enviar más con
`enviar --cantidad N`.
````

- [ ] **Step 2: Makefile**

Agregar a `.PHONY` `habilitacion-probar worker-dian`, a la ayuda del inicio las dos líneas, y:

```make
habilitacion-probar:
	cd $(BACK) && $(PY) -m estacion.habilitacion probar-conexion

worker-dian:
	cd $(BACK) && $(PY) -m estacion.worker --proveedor dian
```

- [ ] **Step 3: Verificación completa**

Run (desde `backend/`):
```bash
ESTACION_TEST_DSN=postgresql://estacion:estacion@localhost:5432/estacion $V/bin/python -m pytest -q
$V/bin/python -m ruff check src tests migrations
$V/bin/python -m ruff format --check src tests migrations
$V/bin/python -m mypy
cd ../Frontend && npm run lint && npm test && npm run build   # el frontend no cambia; confirma que nada se rompió
```
Expected: todo pasa; el número de pruebas del backend es 447 + las de este plan.

- [ ] **Step 4: Commit**

```bash
git add README.md Makefile
git commit -m "docs: pasos de habilitación ante la DIAN"
```

---

## Después del plan (lo ejecuta el usuario, con su autorización en el momento)

1. Poner en el `.env` el certificado real y `DIAN_TEST_SET_ID`; `make habilitacion-probar`. Si falla por seguridad, revisar reloj y certificado; si falla por formato, comparar el sobre con el WSDL (Task 1) y ajustar `sobre.py`.
2. `configurar-numeracion` en la base de la máquina que envía.
3. `enviar --cantidad 1`, `make worker-dian`, `estado`. Si la DIAN rechaza, los mensajes dicen la regla (p. ej. `FAD06` CUFE, `ZB01` firma). Se corrige el código y se vuelve a facturar **la misma venta** (queda PENDIENTE): así se reutiliza el mismo número SETP. No usar `enviar` de nuevo para eso, porque crearía otra venta y gastaría otro número.
4. Revisar el portal de habilitación.
