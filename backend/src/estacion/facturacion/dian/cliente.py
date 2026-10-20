"""Operaciones del web service de la DIAN que usa el sistema, sobre un transporte HTTP inyectable.

Nombres de operaciones, parámetros y campos: los del WSDL oficial (fijado en tests/fixtures/dian). Las decisiones
se toman por `IsValid` y por la presencia de la respuesta; los códigos de estado se guardan tal cual y solo se
usan para reconocer "no encontrado" y "lote en proceso" (SIN VERIFICAR: códigos de memoria; se confirman con las
primeras respuestas reales). Los errores nunca incluyen el sobre, el certificado ni el documento.
"""

from __future__ import annotations

import base64
import binascii
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
_LARGO = 300


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
    """POST por HTTPS con verificación del certificado del servidor. Un error HTTP se devuelve con su cuerpo."""
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


def _entero(texto: str) -> int:
    try:
        return int(texto)
    except ValueError:
        return 0


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
        r = self._llamar(
            "GetNumberingRange", [("accountCode", nit), ("accountCodeT", nit), ("softwareCode", software_id)]
        )
        rangos = tuple(
            RangoNumeracion(
                prefijo=_texto(n, "Prefix"),
                resolucion=_texto(n, "ResolutionNumber"),
                desde=_entero(_texto(n, "FromNumber")),
                hasta=_entero(_texto(n, "ToNumber")),
                vigente_desde=_texto(n, "ValidDateFrom"),
                vigente_hasta=_texto(n, "ValidDateTo"),
            )  # fmt: skip
            for n in _hijos(r, "NumberRangeResponse")
        )
        return ConsultaRangos(_texto(r, "OperationCode"), _texto(r, "OperationDescription"), rangos)

    def enviar_set_pruebas(self, nombre_zip: str, zip_b64: str, test_set_id: str) -> RespuestaEnvioAsincrono:
        r = self._llamar(
            "SendTestSetAsync", [("fileName", nombre_zip), ("contentFile", zip_b64), ("testSetId", test_set_id)]
        )
        errores = tuple(
            _recortar(_texto(item, "ProcessedMessage"))
            for item in _hijos(r, "XmlParamsResponseTrackId")
            if _texto(item, "Success").lower() != "true" and _texto(item, "ProcessedMessage")
        )
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
        except OSError as e:  # incluye URLError, TimeoutError y errores de TLS
            raise ErrorTransporteDian(f"{operacion}: sin respuesta ({type(e).__name__})") from None
        if estado in (401, 403):
            raise ErrorSeguridadDian(f"{operacion}: HTTP {estado} (la DIAN no aceptó el certificado)")
        raiz: etree._Element | None = None
        if respuesta.strip():
            try:
                raiz = etree.fromstring(respuesta, _PARSER)
            except etree.XMLSyntaxError:
                raiz = None
        if raiz is None or etree.QName(raiz).localname != "Envelope":
            raise ErrorTransporteDian(f"{operacion}: respuesta que no es SOAP (HTTP {estado})")
        faults = _hijos(raiz, "Fault")
        if faults:
            subcodigos = {v.text.strip().split(":")[-1] for v in _hijos(faults[0], "Value") if v.text}
            motivo = _recortar(_texto(faults[0], "Text"))
            if subcodigos & _FAULTS_DE_SEGURIDAD:
                raise ErrorSeguridadDian(f"{operacion}: {motivo}")
            raise ErrorTransporteDian(f"{operacion}: Fault {motivo} (HTTP {estado})")
        resultados = _hijos(raiz, f"{operacion}Result")
        if not resultados:
            raise ErrorTransporteDian(f"{operacion}: la respuesta no trae {operacion}Result (HTTP {estado})")
        return resultados[0]

    @staticmethod
    def _respuesta(el: etree._Element) -> RespuestaDian:
        mensajes = _hijos(el, "ErrorMessage")
        errores = tuple(
            _recortar(s.text) for s in (_hijos(mensajes[0], "string") if mensajes else []) if s.text and s.text.strip()
        )
        try:
            # base64 válido puede venir partido en líneas: se quitan los espacios antes de validar.
            app = base64.b64decode("".join(_texto(el, "XmlBase64Bytes").split()), validate=True) or None
        except (binascii.Error, ValueError):
            app = None
        return RespuestaDian(
            valida=_texto(el, "IsValid").lower() == "true",
            codigo=_texto(el, "StatusCode"),
            descripcion=_texto(el, "StatusDescription"),
            errores=errores,
            application_response=app,
            cufe=_texto(el, "XmlDocumentKey"),
        )
