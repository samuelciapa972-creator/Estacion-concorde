"""`ProveedorFacturacion` que habla con la DIAN de verdad (el ambiente lo fija la URL del `ClienteDian`).

Modos:
    set_pruebas (hay TestSetId): SendTestSetAsync -> EnvioPendiente(ZipKey); el resultado llega con GetStatusZip.
    normal: SendBillSync -> resultado inmediato.
Conciliación: con track id, GetStatusZip; sin él, GetStatus por CUFE.
"Documento procesado anteriormente" (un reintento tras una caída) NO es un rechazo: es incierto y se concilia.
"98 Intente más tarde" (la DIAN ocupada) tampoco: es incierto; la cola concilia por CUFE y reintenta con espera.
Nombres del ZIP y del XML: la convención de `paquete.nombre_archivo` con el consecutivo de la factura.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from ..firma import ZONA_COLOMBIA
from ..modelos import EnvioPendiente, ResultadoEmision, SolicitudFactura
from ..paquete import nombre_archivo
from ..proveedor import ProveedorIncierto, ProveedorNoConfigurado, ProveedorRechazo
from .cliente import ClienteDian, ErrorSeguridadDian, ErrorTransporteDian, RespuestaDian

_YA_PROCESADO = re.compile(r"procesado anteriormente", re.IGNORECASE)
_INTENTE_MAS_TARDE = re.compile(r"intente m[aá]s tarde", re.IGNORECASE)
_CODIGO_REINTENTAR = "98"  # visto en habilitación (2026-11-04) con la descripción "Intente más tarde"
_NUMERO = re.compile(r"^([A-Z0-9]*?)(\d+)$")


def _ahora_local() -> datetime:
    return datetime.now(ZONA_COLOMBIA).replace(tzinfo=None)


class ProveedorDian:
    def __init__(
        self,
        cliente: ClienteDian,
        *,
        nit_emisor: str,
        test_set_id: str | None,
        reloj: Callable[[], datetime] = _ahora_local,
    ):
        self._cliente, self._nit, self._test_set_id, self._reloj = cliente, nit_emisor, test_set_id, reloj

    @property
    def modo(self) -> Literal["set_pruebas", "normal"]:
        return "set_pruebas" if self._test_set_id else "normal"

    def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision | EnvioPendiente:
        if solicitud.documento is None or solicitud.numero is None or solicitud.cufe is None:
            raise ValueError("ProveedorDian necesita el documento firmado, el número y el CUFE")
        nombre_zip, contenido = self._zip(
            solicitud.numero, solicitud.fecha_emision, solicitud.documento, solicitud.tipo_documento
        )
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
        if not isinstance(resultado, ResultadoEmision):  # "no encontrado" o "en proceso" justo al enviar
            raise ProveedorIncierto(f"la DIAN respondió {respuesta.codigo} {respuesta.descripcion} al envío")
        return resultado

    def consultar(
        self, *, clave: str, numero: str, cufe: str, track_id: str | None
    ) -> ResultadoEmision | EnvioPendiente | None:
        try:
            if track_id:
                # Con ZipKey la DIAN SÍ recibió el lote: "vacío" o "no encontrado" es "todavía no", nunca "no existe"
                # (eso reenviaría el documento). Un zip trae una sola factura: basta la primera respuesta.
                respuestas = self._cliente.estado_zip(track_id)
                if not respuestas or respuestas[0].no_encontrado:
                    return EnvioPendiente(track_id)
                respuesta = respuestas[0]
                if not respuesta.valida and _YA_PROCESADO.search("; ".join(respuesta.errores)):
                    # Reenvío tras una caída: este lote es un duplicado; la factura original se busca por su CUFE.
                    return self._resultado(self._cliente.estado(cufe), clave, numero, cufe, None)
            else:
                respuesta = self._cliente.estado(cufe)
        except ErrorTransporteDian as e:
            raise ProveedorIncierto(str(e)) from None
        except ErrorSeguridadDian as e:
            raise ProveedorNoConfigurado(str(e)) from None
        return self._resultado(respuesta, clave, numero, cufe, track_id)

    # ------------------------------------------------------------------ apoyo
    def _zip(
        self, numero: str, fecha: datetime, documento: bytes, tipo_documento: Literal["fv", "nc"] = "fv"
    ) -> tuple[str, str]:
        m = _NUMERO.match(numero)
        if m is None:
            raise ValueError(f"número de factura inesperado: {numero!r}")
        consecutivo = int(m.group(2))
        nombre_xml = nombre_archivo(tipo_documento, self._nit, fecha.year, consecutivo, "xml")
        nombre_zip = nombre_archivo("z", self._nit, fecha.year, consecutivo, "zip")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(nombre_xml, documento)
        return nombre_zip, base64.b64encode(buffer.getvalue()).decode("ascii")

    def _resultado(
        self, r: RespuestaDian, clave: str, numero: str, cufe: str, track_id: str | None
    ) -> ResultadoEmision | EnvioPendiente | None:
        if r.valida:
            m = _NUMERO.match(numero)
            return ResultadoEmision(
                clave=clave, prefijo=m.group(1) if m else "", numero=numero, cufe=r.cufe or cufe,
                emitida_en=self._reloj(), estado="VALIDADA_DIAN",
                detalle={"codigo_dian": r.codigo, "descripcion_dian": r.descripcion, "modo": self.modo},
                application_response=r.application_response,
            )  # fmt: skip
        mensajes = "; ".join(r.errores) or f"{r.codigo} {r.descripcion}".strip()
        if _YA_PROCESADO.search(mensajes):
            raise ProveedorIncierto(f"la DIAN dice que el documento ya se procesó: {mensajes}")
        if r.codigo == _CODIGO_REINTENTAR or _INTENTE_MAS_TARDE.search(mensajes):
            raise ProveedorIncierto(f"la DIAN pide reintentar más tarde: {mensajes}")
        if r.no_encontrado:
            return None
        if r.en_proceso:
            return EnvioPendiente(track_id or "")
        raise ProveedorRechazo(mensajes)
