"""Captcha del registro público (sin red: `enviar` falso)."""

from __future__ import annotations

import pytest

from estacion.api.ajustes import Ajustes, AjustesInvalidos
from estacion.api.captcha import URL_VERIFICAR, Captcha

BASE = {"DATABASE_URL": "postgresql://x/y", "JWT_SECRETO": "s" * 40}


def captcha(proveedor="turnstile") -> Captcha:
    return Captcha(proveedor=proveedor, clave_sitio="sitio-publico", secreto="secreto-de-prueba")


class Enviar:
    def __init__(self, respuesta=None, error: Exception | None = None):
        self.respuesta, self.error, self.llamadas = respuesta, error, []

    def __call__(self, url, datos, timeout):
        self.llamadas.append((url, dict(datos), timeout))
        if self.error:
            raise self.error
        return self.respuesta


@pytest.mark.parametrize("proveedor", ["turnstile", "hcaptcha"])
def test_token_valido(proveedor):
    enviar = Enviar({"success": True})
    assert captcha(proveedor).verificar("tok", "10.0.0.7", enviar)
    url, datos, _ = enviar.llamadas[0]
    assert url == URL_VERIFICAR[proveedor]
    assert datos == {"secret": "secreto-de-prueba", "response": "tok", "remoteip": "10.0.0.7"}


@pytest.mark.parametrize(
    "respuesta", [{"success": False, "error-codes": ["invalid-input-response"]}, {}, {"success": "true"}, [], None]
)
def test_respuesta_negativa_o_rara_rechaza(respuesta):
    assert not captcha().verificar("tok", None, Enviar(respuesta))


def test_sin_red_falla_cerrado(caplog):
    assert not captcha().verificar("tok-secreto", "10.0.0.7", Enviar(error=TimeoutError()))
    assert "TimeoutError" in caplog.text
    assert "tok-secreto" not in caplog.text and "10.0.0.7" not in caplog.text  # nada personal en el log


def test_sin_token_ni_siquiera_consulta():
    enviar = Enviar({"success": True})
    assert not captcha().verificar(None, "10.0.0.7", enviar) and not captcha().verificar("", None, enviar)
    assert enviar.llamadas == []


def test_el_secreto_no_sale_en_repr():
    assert "secreto-de-prueba" not in repr(captcha())


def test_entorno_sin_captcha_es_banco_de_pruebas():
    assert Ajustes.desde_entorno(BASE).captcha is None
    assert Ajustes.desde_entorno({**BASE, "ESTACION_CAPTCHA_PROVEEDOR": "ninguno"}).captcha is None


def test_entorno_con_captcha():
    c = Ajustes.desde_entorno(
        {**BASE, "ESTACION_CAPTCHA_PROVEEDOR": "Turnstile", "ESTACION_CAPTCHA_CLAVE_SITIO": "s",
         "ESTACION_CAPTCHA_SECRETO": "x"}
    ).captcha  # fmt: skip
    assert c is not None and c.proveedor == "turnstile"


@pytest.mark.parametrize(
    "extra",
    [
        {
            "ESTACION_CAPTCHA_PROVEEDOR": "recaptcha",
            "ESTACION_CAPTCHA_CLAVE_SITIO": "s",
            "ESTACION_CAPTCHA_SECRETO": "x",
        },
        {"ESTACION_CAPTCHA_PROVEEDOR": "hcaptcha", "ESTACION_CAPTCHA_CLAVE_SITIO": "s"},
        {"ESTACION_CAPTCHA_PROVEEDOR": "hcaptcha", "ESTACION_CAPTCHA_SECRETO": "x"},
    ],
)
def test_captcha_a_medio_configurar_no_arranca(extra):
    with pytest.raises(AjustesInvalidos, match="captcha|CAPTCHA"):
        Ajustes.desde_entorno({**BASE, **extra})
