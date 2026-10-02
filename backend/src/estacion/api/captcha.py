"""Captcha del registro público por QR: Cloudflare Turnstile o hCaptcha (se elige por entorno).

Los dos verifican igual: el navegador obtiene un token del widget y la API lo confirma con un POST a `siteverify`
(secreto + token + IP) que responde `{"success": true|false}`. **Falla cerrado:** si el proveedor no responde, tarda
o contesta algo raro, el registro se rechaza. Los registros de log no llevan el token ni la IP (Ley 1581).

SIN VERIFICAR contra los servicios reales: las URLs y el formato son los de la documentación pública de cada
proveedor; las pruebas usan un `enviar` falso. Antes de publicar el QR, probar con las claves de prueba del
proveedor (Turnstile publica unas que siempre aprueban o siempre rechazan).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)

NombreProveedor = Literal["turnstile", "hcaptcha"]
URL_VERIFICAR: dict[str, str] = {
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "hcaptcha": "https://api.hcaptcha.com/siteverify",
}
SIN_CAPTCHA = {"", "ninguno"}

Enviar = Callable[[str, Mapping[str, str], float], Any]


class CaptchaMalConfigurado(ValueError):
    pass


def _enviar(url: str, datos: Mapping[str, str], timeout: float) -> Any:
    cuerpo = urllib.parse.urlencode(datos).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=cuerpo, method="POST"), timeout=timeout) as r:
        return json.load(r)


@dataclass(frozen=True, slots=True)
class Captcha:
    proveedor: NombreProveedor
    clave_sitio: str  # pública: la usa el widget en el navegador
    secreto: str = field(repr=False)
    timeout: float = 5.0

    def __post_init__(self) -> None:
        if self.proveedor not in URL_VERIFICAR:
            raise CaptchaMalConfigurado(f"proveedor de captcha desconocido: {self.proveedor!r}")
        if not self.clave_sitio.strip() or not self.secreto.strip():
            raise CaptchaMalConfigurado("faltan ESTACION_CAPTCHA_CLAVE_SITIO o ESTACION_CAPTCHA_SECRETO")

    def verificar(self, token: str | None, ip: str | None, enviar: Enviar | None = None) -> bool:
        if not token:
            return False
        datos = {"secret": self.secreto, "response": token}
        if ip and ip != "desconocida":
            datos["remoteip"] = ip
        try:
            respuesta = (enviar or _enviar)(URL_VERIFICAR[self.proveedor], datos, self.timeout)
        except Exception as e:  # red, tiempo de espera, JSON inválido: fallar cerrado
            log.warning("captcha %s sin respuesta válida (%s): registro rechazado", self.proveedor, type(e).__name__)
            return False
        return isinstance(respuesta, dict) and respuesta.get("success") is True

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> Captcha | None:
        """None = sin captcha (solo banco de pruebas). Un proveedor a medio configurar NO arranca."""
        proveedor = entorno.get("ESTACION_CAPTCHA_PROVEEDOR", "").strip().lower()
        if proveedor in SIN_CAPTCHA:
            return None
        return cls(
            proveedor=proveedor,  # type: ignore[arg-type]  # __post_init__ lo valida
            clave_sitio=entorno.get("ESTACION_CAPTCHA_CLAVE_SITIO", "").strip(),
            secreto=entorno.get("ESTACION_CAPTCHA_SECRETO", "").strip(),
        )
