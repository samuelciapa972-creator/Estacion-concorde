"""El contrato con el frontend: web/openapi.json debe ser exactamente el esquema actual de la API."""

from __future__ import annotations

from pathlib import Path

from estacion.api.openapi import como_texto, esquema

ARCHIVO_WEB = Path(__file__).resolve().parents[2] / "web" / "openapi.json"


def test_openapi_al_dia():
    assert ARCHIVO_WEB.read_text(encoding="utf-8") == como_texto(), (
        "web/openapi.json está desactualizado: corra `make tipos` y revise el cambio en el frontend"
    )


def test_valores_cerrados_salen_como_enum():
    s = esquema()["components"]["schemas"]
    assert s["UsuarioSalida"]["properties"]["rol"]["enum"] == ["BOMBERO", "ADMIN"]
    assert s["FacturaSalida"]["properties"]["estado"]["enum"] == ["EN_PROCESO", "FACTURADO", "RECHAZADO", "INCIERTO"]
    # Dinero y galones salen como texto decimal, nunca como número
    assert s["PendienteSalida"]["properties"]["valor"]["type"] == "string"
