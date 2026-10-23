"""API de anulación: `POST /facturas/{id}/anular` (solo administrador) y la salida con tipo y estado de anulación."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from conftest import ENTORNO_DIAN, clave
from estacion.entrega.correo import EnviadorEnMemoria
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.servicios.outbox import Recursos, procesar_pendientes

pytestmark = pytest.mark.bd

FILTRO = {
    "desde": (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"),
    "hasta": (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds"),
}
MOTIVO = "Error en los datos del cliente"


def emitir(api, origen: str = "venta_manual") -> dict:
    d = api.e.datos
    origen_id = d.venta_manual(api.e.usuario) if origen == "venta_manual" else d.despacho()
    r = api.post("/facturas", {"clave": clave(), "origen_tipo": origen, "origen_id": origen_id,
                               "cliente_id": api.e.cliente, "medio_pago": "EFECTIVO"}, como=api.bombero)  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def worker(api, certificado) -> None:
    procesar_pendientes(api.e.datos.conn, ProveedorSimulado(), recursos=Recursos(certificado, EnviadorEnMemoria()))


def facturada(api, certificado) -> dict:
    f = emitir(api)
    worker(api, certificado)
    return api.get(f"/facturas/{f['id']}", como=api.bombero).json()


def anular(api, factura_id: int, *, k: str | None = None, motivo: str = MOTIVO, como: str | None = None):
    return api.post(f"/facturas/{factura_id}/anular", {"clave": k or clave(), "motivo": motivo}, como=como or api.admin)


def test_admin_anula_y_el_worker_completa(api, certificado):
    f = facturada(api, certificado)
    assert f["anulable"] is True and f["anulada_por"] is None and f["tipo"] == "FACTURA" and f["ambiente"] == "2"

    r = anular(api, f["id"])
    assert r.status_code == 202, r.text
    nota = r.json()
    assert (nota["tipo"], nota["estado"], nota["referencia"], nota["numero"]) == (
        "NOTA_CREDITO", "EN_PROCESO", f["numero"], "NCP1")  # fmt: skip
    assert nota["anulable"] is False and nota["nueva"] is True
    assert nota["origen_id"] == f["origen_id"] and nota["total"] == f["total"]

    # mientras la nota está en curso la factura no se puede anular otra vez, pero aún no figura anulada
    en_curso = api.get(f"/facturas/{f['id']}", como=api.bombero).json()
    assert en_curso["anulable"] is False and en_curso["anulada_por"] is None

    worker(api, certificado)
    assert api.get(f"/facturas/{nota['id']}", como=api.admin).json()["estado"] == "FACTURADO"
    anulada = api.get(f"/facturas/{f['id']}", como=api.bombero).json()
    assert anulada["anulada_por"] == "NCP1" and anulada["anulable"] is False


def test_bombero_no_puede_anular(api, certificado):
    f = facturada(api, certificado)
    assert anular(api, f["id"], como=api.bombero).status_code == 403
    assert api.post(f"/facturas/{f['id']}/anular", {"clave": clave(), "motivo": MOTIVO}).status_code == 401
    assert api.e.datos.uno("SELECT count(*) FROM factura WHERE tipo = 'NOTA_CREDITO'") == 0


def test_doble_clic_misma_clave(api, certificado):
    f = facturada(api, certificado)
    k = clave()
    primera = anular(api, f["id"], k=k)
    segunda = anular(api, f["id"], k=k)
    assert segunda.status_code == 202
    assert segunda.json()["id"] == primera.json()["id"] and segunda.json()["nueva"] is False
    assert api.e.datos.uno("SELECT count(*) FROM factura WHERE tipo = 'NOTA_CREDITO'") == 1
    # otra clave sobre la misma factura: ya tiene nota viva
    r = anular(api, f["id"])
    assert r.status_code == 409 and "ya tiene una nota crédito" in r.json()["detail"]


def test_clave_de_otra_factura_409(api, certificado):
    a, b = facturada(api, certificado), facturada(api, certificado)
    k = clave()
    assert anular(api, a["id"], k=k).status_code == 202
    assert anular(api, b["id"], k=k).status_code == 409


def test_motivo_vacio_422(api, certificado):
    f = facturada(api, certificado)
    assert anular(api, f["id"], motivo="   ").status_code == 422
    assert anular(api, f["id"], motivo="x" * 501).status_code == 422
    assert anular(api, f["id"], k="corta").status_code == 422
    assert api.e.datos.uno("SELECT count(*) FROM factura WHERE tipo = 'NOTA_CREDITO'") == 0


def test_factura_de_despacho_409(api, certificado):
    f = emitir(api, "despacho")
    worker(api, certificado)
    assert api.get(f"/facturas/{f['id']}", como=api.admin).json()["anulable"] is False
    r = anular(api, f["id"], motivo="dato personal 1049612345")
    assert r.status_code == 409 and "despacho de surtidor" in r.json()["detail"]
    assert "1049612345" not in r.text  # el motivo no vuelve en los errores


def test_factura_inexistente_404(api):
    assert anular(api, 999999).status_code == 404


def test_factura_sin_aceptar_no_es_anulable(api):
    f = emitir(api)  # EN_PROCESO
    assert f["anulable"] is False
    assert anular(api, f["id"]).status_code == 409


def test_factura_de_otro_ambiente_no_es_anulable(armar, certificado):
    api = armar({**ENTORNO_DIAN, "DIAN_AMBIENTE": "1"})
    api.e.datos.conn.execute("UPDATE numeracion SET ambiente = '1'")  # la resolución de producción
    f = emitir(api)  # nace con el ambiente actual (1) y se acepta a mano: el proveedor simulado no importa
    api.e.datos.conn.execute("UPDATE factura SET estado = 'FACTURADO' WHERE id = %s", (f["id"],))
    assert api.get(f"/facturas/{f['id']}", como=api.admin).json()["anulable"] is True
    cambiada = armar()  # el sistema vuelve a habilitación (2)
    assert cambiada.get(f"/facturas/{f['id']}", como=cambiada.admin).json()["anulable"] is False
    assert anular(cambiada, f["id"]).status_code == 409


def test_listado_incluye_la_nota(api, certificado):
    f = facturada(api, certificado)
    nota = anular(api, f["id"]).json()
    worker(api, certificado)

    filas = api.get("/facturas", como=api.admin, params=FILTRO).json()
    assert filas["total"] == 2
    por_id = {x["id"]: x for x in filas["filas"]}
    assert (por_id[nota["id"]]["tipo"], por_id[nota["id"]]["referencia"], por_id[nota["id"]]["anulable"]) == (
        "NOTA_CREDITO", f["numero"], False)  # fmt: skip
    assert (por_id[f["id"]]["tipo"], por_id[f["id"]]["anulada_por"], por_id[f["id"]]["anulable"]) == (
        "FACTURA", "NCP1", False)  # fmt: skip

    solo_notas = api.get("/facturas", como=api.admin, params={**FILTRO, "tipo": "NOTA_CREDITO"}).json()
    assert [x["id"] for x in solo_notas["filas"]] == [nota["id"]] and solo_notas["total"] == 1
    solo_facturas = api.get("/facturas", como=api.admin, params={**FILTRO, "tipo": "FACTURA"}).json()
    assert [x["id"] for x in solo_facturas["filas"]] == [f["id"]]


def test_listado_marca_anulable_la_factura_aceptada(api, certificado):
    f = facturada(api, certificado)
    fila = api.get("/facturas", como=api.admin, params=FILTRO).json()["filas"][0]
    assert fila["id"] == f["id"] and fila["anulable"] is True and fila["anulada_por"] is None


def test_pdf_y_zip_de_la_nota(api, certificado):
    f = facturada(api, certificado)
    nota = anular(api, f["id"]).json()
    assert api.get(f"/facturas/{nota['id']}/pdf", como=api.bombero).status_code == 409  # aún sin validar
    worker(api, certificado)

    pdf = api.get(f"/facturas/{nota['id']}/pdf", como=api.bombero)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert f"factura_{nota['numero']}.pdf" in pdf.headers["content-disposition"]
    zip_ = api.get(f"/facturas/{nota['id']}/zip", como=api.bombero)
    assert zip_.status_code == 200 and zip_.content.startswith(b"PK")
