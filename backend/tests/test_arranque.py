"""Arranque de la API detrás del proxy (sin base de datos)."""

from __future__ import annotations

from typing import Any

import pytest

from estacion.api import __main__ as arranque


@pytest.fixture
def corrida(monkeypatch):
    llamadas: list[dict[str, Any]] = []
    monkeypatch.setattr(arranque.uvicorn, "run", lambda *a, **kw: llamadas.append(kw))
    monkeypatch.setenv("DATABASE_URL", "postgresql://sin-conexion/x")
    monkeypatch.setenv("JWT_SECRETO", "s" * 40)
    monkeypatch.delenv("ESTACION_PROXY_CONFIABLE", raising=False)
    return llamadas


def test_por_defecto_no_confia_en_cabeceras_de_terceros(corrida):
    assert arranque.main([]) == 0
    assert corrida[0]["proxy_headers"] is True
    assert corrida[0]["forwarded_allow_ips"] is None  # uvicorn: solo 127.0.0.1


def test_proxy_confiable_desde_el_entorno(corrida, monkeypatch):
    monkeypatch.setenv("ESTACION_PROXY_CONFIABLE", "*")
    assert arranque.main(["--host", "0.0.0.0"]) == 0
    assert corrida[0]["forwarded_allow_ips"] == "*" and corrida[0]["host"] == "0.0.0.0"


def test_sin_secreto_no_arranca(corrida, monkeypatch, capsys):
    monkeypatch.delenv("JWT_SECRETO")
    assert arranque.main([]) == 2
    assert corrida == [] and "JWT_SECRETO" in capsys.readouterr().err
