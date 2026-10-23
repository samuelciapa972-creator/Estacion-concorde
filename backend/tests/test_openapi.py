"""El contrato con el frontend: Frontend/openapi.json debe ser exactamente el esquema actual de la API."""

from __future__ import annotations

from pathlib import Path

from estacion.api.openapi import como_texto, esquema

ARCHIVO_WEB = Path(__file__).resolve().parents[2] / "Frontend" / "openapi.json"


def test_openapi_al_dia():
    assert ARCHIVO_WEB.read_text(encoding="utf-8") == como_texto(), (
        "Frontend/openapi.json está desactualizado: corra `make tipos` y revise el cambio en el frontend"
    )


def test_valores_cerrados_salen_como_enum():
    s = esquema()["components"]["schemas"]
    assert s["UsuarioSalida"]["properties"]["rol"]["enum"] == ["BOMBERO", "ADMIN"]
    assert s["FacturaSalida"]["properties"]["estado"]["enum"] == ["EN_PROCESO", "FACTURADO", "RECHAZADO", "INCIERTO"]
    # Dinero y galones salen como texto decimal, nunca como número
    assert s["PendienteSalida"]["properties"]["valor"]["type"] == "string"


def test_anulacion_en_el_contrato():
    s = esquema()
    anular = s["paths"]["/facturas/{factura_id}/anular"]["post"]
    assert set(anular["responses"]) >= {"202", "404", "409", "422", "503"}
    f = s["components"]["schemas"]["FacturaSalida"]["properties"]
    assert f["tipo"]["enum"] == ["FACTURA", "NOTA_CREDITO"] and f["ambiente"]["enum"] == ["1", "2"]
    assert {"referencia", "anulada_por", "anulable"} <= set(f)
    assert s["components"]["schemas"]["AnulacionEntrada"]["properties"]["motivo"]["maxLength"] == 500
