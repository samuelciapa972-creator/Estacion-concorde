"""ClienteDian con transporte falso: respuestas grabadas con la forma del WSDL; nada sale a la red."""

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
NS_B = 'xmlns:b="http://schemas.datacontract.org/2004/07/DianResponse"'
NS_C = 'xmlns:c="http://schemas.microsoft.com/2003/10/Serialization/Arrays"'


def dian_response(
    valida="true", codigo="00", desc="Procesado Correctamente.", errores=(), app=b"<ApplicationResponse/>",
    cufe="cufe123",
) -> str:  # fmt: skip
    mensajes = "".join(f"<c:string>{e}</c:string>" for e in errores)
    return (
        f"<b:ErrorMessage {NS_C}>{mensajes}</b:ErrorMessage><b:IsValid>{valida}</b:IsValid>"
        f"<b:StatusCode>{codigo}</b:StatusCode><b:StatusDescription>{desc}</b:StatusDescription><b:StatusMessage/>"
        f"<b:XmlBase64Bytes>{base64.b64encode(app).decode() if app else ''}</b:XmlBase64Bytes>"
        f"<b:XmlDocumentKey>{cufe}</b:XmlDocumentKey><b:XmlFileName>fv.xml</b:XmlFileName>"
    )


def resultado(operacion: str, contenido: str, ns: str = NS_B) -> str:
    return ENV.format(
        f'<{operacion}Response xmlns="http://wcf.dian.colombia"><{operacion}Result {ns}>{contenido}'
        f"</{operacion}Result></{operacion}Response>"
    )


def fault(subcodigo: str | None, texto: str = "error") -> str:
    sub = (
        '<s:Subcode><s:Value xmlns:a="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        f"{subcodigo}</s:Value></s:Subcode>"
        if subcodigo
        else ""
    )
    return ENV.format(
        f"<s:Fault><s:Code><s:Value>s:Sender</s:Value>{sub}</s:Code><s:Reason><s:Text>{texto}</s:Text></s:Reason>"
        "</s:Fault>"
    )


class Transporte:
    def __init__(self, estado: int, cuerpo: bytes | str, *, excepcion: Exception | None = None):
        self.estado, self.cuerpo, self.excepcion = estado, cuerpo, excepcion
        self.llamadas: list[tuple[str, dict, bytes]] = []

    def __call__(self, url, cabeceras, cuerpo, timeout):
        self.llamadas.append((url, dict(cabeceras), cuerpo))
        if self.excepcion:
            raise self.excepcion
        return self.estado, self.cuerpo.encode() if isinstance(self.cuerpo, str) else self.cuerpo


def cliente(certificado, t):
    return ClienteDian(URL_HABILITACION, certificado, transporte=t, reloj=lambda: AHORA)


def test_estado_valido_decodifica_el_application_response(certificado):
    t = Transporte(200, resultado("GetStatus", dian_response()))
    r = cliente(certificado, t).estado("cufe123")
    assert r.valida and r.codigo == "00" and r.cufe == "cufe123" and r.errores == ()
    assert r.application_response == b"<ApplicationResponse/>"
    url, cab, cuerpo = t.llamadas[0]
    assert url == URL_HABILITACION
    assert cab["Content-Type"].startswith("application/soap+xml")
    assert 'action="http://wcf.dian.colombia/IWcfDianCustomerServices/GetStatus"' in cab["Content-Type"]
    verificar_sobre(cuerpo, ahora=AHORA)


def test_rechazo_con_varios_errores(certificado):
    cuerpo = dian_response(
        valida="false", codigo="99", desc="Validación contiene errores en campos mandatorios.",
        errores=("Regla: FAD06, Rechazo: CUFE mal calculado", "Regla: FAJ21, Rechazo: NIT"), app=b"",
    )  # fmt: skip
    r = cliente(certificado, Transporte(200, resultado("GetStatus", cuerpo))).estado("x")
    assert not r.valida and r.codigo == "99" and r.application_response is None
    assert r.errores == ("Regla: FAD06, Rechazo: CUFE mal calculado", "Regla: FAJ21, Rechazo: NIT")
    assert not r.en_proceso and not r.no_encontrado


def test_set_de_pruebas_devuelve_zip_key(certificado):
    ns = 'xmlns:b="http://schemas.datacontract.org/2004/07/UploadDocumentResponse"'
    t = Transporte(200, resultado("SendTestSetAsync", "<b:ErrorMessageList/><b:ZipKey>zip-key-1</b:ZipKey>", ns))
    r = cliente(certificado, t).enviar_set_pruebas("z.zip", "UEsDBA==", "test-set")
    assert r.zip_key == "zip-key-1" and r.errores == ()
    assert b"<wcf:testSetId>test-set</wcf:testSetId>" in t.llamadas[0][2]
    assert b"<wcf:fileName>z.zip</wcf:fileName>" in t.llamadas[0][2]


def test_set_de_pruebas_con_errores_de_carga(certificado):
    ns = 'xmlns:b="http://schemas.datacontract.org/2004/07/UploadDocumentResponse"'
    lista = (
        '<b:ErrorMessageList xmlns:c="http://schemas.datacontract.org/2004/07/XmlParamsResponseTrackId">'
        "<c:XmlParamsResponseTrackId><c:ProcessedMessage>Nombre de archivo inválido</c:ProcessedMessage>"
        "<c:Success>false</c:Success></c:XmlParamsResponseTrackId></b:ErrorMessageList><b:ZipKey/>"
    )
    r = cliente(certificado, Transporte(200, resultado("SendTestSetAsync", lista, ns))).enviar_set_pruebas("z", "", "t")
    assert r.zip_key == "" and r.errores == ("Nombre de archivo inválido",)


def test_estado_zip_en_proceso(certificado):
    cuerpo = dian_response(valida="false", codigo="", desc="Batch en proceso de validación.", app=b"", cufe="")
    t = Transporte(200, resultado("GetStatusZip", f"<b:DianResponse>{cuerpo}</b:DianResponse>"))
    [r] = cliente(certificado, t).estado_zip("zip-key-1")
    assert r.en_proceso and not r.valida


@pytest.mark.parametrize("subcodigo", ["a:InvalidSecurity", "a:MessageExpired", "a:FailedAuthentication"])
def test_fault_de_seguridad(certificado, subcodigo):
    with pytest.raises(ErrorSeguridadDian):
        cliente(certificado, Transporte(500, fault(subcodigo))).estado("x")


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
        (500, fault(None, "interno")),
        (200, b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'),
        (200, ENV.format("<otra/>")),
    ],
)
def test_respuestas_que_no_sirven_son_error_de_transporte(certificado, estado, cuerpo):
    with pytest.raises(ErrorTransporteDian):
        cliente(certificado, Transporte(estado, cuerpo)).estado("x")


def test_timeout_es_error_de_transporte(certificado):
    with pytest.raises(ErrorTransporteDian, match="TimeoutError"):
        cliente(certificado, Transporte(0, b"", excepcion=TimeoutError("timed out"))).estado("x")


def test_rangos_de_numeracion(certificado):
    ns = 'xmlns:b="http://schemas.datacontract.org/2004/07/NumberRangeResponseList"'
    contenido = (
        "<b:OperationCode>100</b:OperationCode><b:OperationDescription>Acción completada OK.</b:OperationDescription>"
        '<b:ResponseList xmlns:c="http://schemas.datacontract.org/2004/07/NumberRangeResponse"><c:NumberRangeResponse>'
        "<c:ResolutionNumber>18760000001</c:ResolutionNumber><c:Prefix>SETP</c:Prefix>"
        "<c:FromNumber>990000000</c:FromNumber><c:ToNumber>995000000</c:ToNumber>"
        "<c:ValidDateFrom>2019-01-19</c:ValidDateFrom><c:ValidDateTo>2030-01-19</c:ValidDateTo>"
        "<c:TechnicalKey>no-se-guarda</c:TechnicalKey></c:NumberRangeResponse></b:ResponseList>"
    )
    t = Transporte(200, resultado("GetNumberingRange", contenido, ns))
    r = cliente(certificado, t).rangos_numeracion("901335327", "sw-id")
    assert r.codigo == "100" and len(r.rangos) == 1
    assert (r.rangos[0].prefijo, r.rangos[0].desde, r.rangos[0].hasta) == ("SETP", 990000000, 995000000)
    assert r.rangos[0].clave_tecnica == "no-se-guarda"  # se conserva para compararla con la configurada...
    assert "no-se-guarda" not in repr(r)  # ...pero nunca sale en el repr (logs, trazas)
    assert b"<wcf:accountCode>901335327</wcf:accountCode>" in t.llamadas[0][2]
    assert b"<wcf:softwareCode>sw-id</wcf:softwareCode>" in t.llamadas[0][2]


def test_el_error_no_lleva_el_sobre(certificado):
    with pytest.raises(ErrorTransporteDian) as e:
        cliente(certificado, Transporte(503, b"<html>caido</html>")).estado("x")
    assert "BinarySecurityToken" not in str(e.value) and len(str(e.value)) < 300


def test_application_response_con_saltos_de_linea(certificado):
    # Revisión final: un base64 partido en líneas es válido; descartarlo dejaría sin entregar una factura aceptada.
    b64 = base64.b64encode(b"<ApplicationResponse>" + b"x" * 200 + b"</ApplicationResponse>").decode()
    partido = "\n".join(b64[i : i + 76] for i in range(0, len(b64), 76))
    cuerpo = dian_response().replace(base64.b64encode(b"<ApplicationResponse/>").decode(), partido)
    r = cliente(certificado, Transporte(200, resultado("GetStatus", cuerpo))).estado("x")
    assert r.application_response is not None and r.application_response.startswith(b"<ApplicationResponse>")
