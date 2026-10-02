"""Ajustes de la API, leídos del entorno. Fallar cerrado: sin base de datos o sin un secreto JWT fuerte, la API
no arranca. Los mensajes nombran la variable que falta, nunca su valor."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .captcha import Captcha, CaptchaMalConfigurado

LARGO_MINIMO_SECRETO = 32


class AjustesInvalidos(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Ajustes:
    database_url: str
    jwt_secreto: str
    jwt_minutos: int = 60  # token corto; la pantalla del bombero vuelve a pedir el PIN al vencer
    max_intentos_pin: int = 5  # fallos seguidos antes de bloquear al usuario
    bloqueo_minutos: int = 15
    login_por_minuto: int = 10  # por IP, además del bloqueo por usuario
    registro_por_minuto: int = 5  # registro público por QR, por IP
    captcha: Captcha | None = None  # None = registro público sin captcha (solo banco de pruebas)

    def __post_init__(self) -> None:
        if not self.database_url.strip():
            raise AjustesInvalidos("falta DATABASE_URL")
        if len(self.jwt_secreto) < LARGO_MINIMO_SECRETO:
            raise AjustesInvalidos(f"JWT_SECRETO falta o es corto (mínimo {LARGO_MINIMO_SECRETO} caracteres)")
        if self.jwt_minutos <= 0 or self.max_intentos_pin <= 0 or self.bloqueo_minutos <= 0:
            raise AjustesInvalidos("JWT_MINUTOS, el máximo de intentos y el bloqueo deben ser positivos")

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] | None = None) -> Ajustes:
        entorno = os.environ if entorno is None else entorno
        try:
            minutos = int(entorno.get("JWT_MINUTOS", "60"))
        except ValueError:
            raise AjustesInvalidos("JWT_MINUTOS debe ser un número entero de minutos") from None
        try:
            captcha = Captcha.desde_entorno(entorno)
        except CaptchaMalConfigurado as e:
            raise AjustesInvalidos(str(e)) from None
        return cls(
            database_url=entorno.get("DATABASE_URL", ""),
            jwt_secreto=entorno.get("JWT_SECRETO", ""),
            jwt_minutos=minutos,
            captcha=captcha,
        )
