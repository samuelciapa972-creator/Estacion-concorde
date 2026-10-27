"""API de anulación: `POST /facturas/{id}/anular` (solo administrador) y la salida con tipo y estado de anulación."""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
import pytest
from psycopg_pool import PoolTimeout

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


@pytest.mark.parametrize("motivo", ["cliente\x0bequivocado", "nul\x00", "no carácter￿"])
def test_motivo_con_caracteres_de_control_422(api, certificado, motivo):
    # Pegado desde Word: el XML no los admite. Antes llegaban a lxml y salían como 500.
    f = facturada(api, certificado)
    r = anular(api, f["id"], motivo=motivo)
    assert r.status_code == 422, r.text
    assert api.e.datos.uno("SELECT count(*) FROM factura WHERE tipo = 'NOTA_CREDITO'") == 0


def test_motivo_con_saltos_de_linea_se_acepta(api, certificado):
    f = facturada(api, certificado)
    assert anular(api, f["id"], motivo="línea 1\n\tlínea 2").status_code == 202


def test_serie_de_notas_desfasada_503(api, certificado):
    f1, f2 = facturada(api, certificado), facturada(api, certificado)
    assert anular(api, f1["id"]).status_code == 202
    api.e.datos.conn.execute("UPDATE numeracion_nota SET siguiente = 1")  # el contador quedó atrás de NCP1
    r = anular(api, f2["id"])
    assert r.status_code == 503, r.text
    assert "la serie de notas NCP está desfasada" in r.json()["detail"]
    assert api.e.datos.uno("SELECT siguiente FROM numeracion_nota") == 1  # no se gastó número
    assert api.e.datos.uno("SELECT count(*) FROM factura WHERE tipo = 'NOTA_CREDITO'") == 1


def test_clave_tomada_a_la_vez_por_otra_factura_409(api, certificado, monkeypatch):
    import estacion.servicios.anulacion as modulo

    otra, f = facturada(api, certificado), facturada(api, certificado)
    k = clave()
    original = modulo.construir_nota_credito

    def con_carrera(*args, **kwargs):  # otra operación guarda la misma clave entre la comprobación y el INSERT
        api.e.datos.conn.execute("UPDATE factura SET clave_emision = %s WHERE id = %s", (k, otra["id"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(modulo, "construir_nota_credito", con_carrera)
    r = anular(api, f["id"], k=k)
    assert r.status_code == 409, r.text
    assert "clave de emisión ya se usó" in r.json()["detail"]
    assert api.e.datos.uno("SELECT count(*) FROM factura WHERE tipo = 'NOTA_CREDITO'") == 0


@pytest.mark.parametrize(
    "error",
    [
        psycopg.errors.DeadlockDetected("deadlock detected DETAIL: Process 123 waits for ShareLock on tx 456"),
        psycopg.errors.QueryCanceled("canceling statement due to statement timeout"),
        psycopg.OperationalError("connection to server at 10.0.0.5 failed"),
        PoolTimeout("couldn't get a connection after 30.00 sec"),
    ],
    ids=["deadlock", "timeout", "conexion", "pool"],
)
def test_error_de_base_no_mapeado_503_sin_detalles(api, certificado, monkeypatch, error):
    f = facturada(api, certificado)

    def falla(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(api.cliente.app.state.anulacion, "solicitar", falla)
    r = anular(api, f["id"])
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "la base de datos no está disponible en este momento: intente de nuevo"


def test_configuracion_incompleta_503(armar, certificado):
    api = armar()
    f = facturada(api, certificado)
    sin_pin = armar({k: v for k, v in ENTORNO_DIAN.items() if k != "DIAN_SOFTWARE_PIN"})
    r = anular(sin_pin, f["id"])
    assert r.status_code == 503, r.text
    assert r.json()["detail"].startswith("no se puede facturar:") and "DIAN_SOFTWARE_PIN" in r.json()["detail"]
    assert "12345" not in r.text  # ningún secreto en el mensaje
    assert api.e.datos.uno("SELECT count(*) FROM factura WHERE tipo = 'NOTA_CREDITO'") == 0


def test_sin_dian_ambiente_503_no_otro_ambiente(armar, certificado):
    # DIAN_AMBIENTE vacío es un problema de configuración, no una factura "de otro ambiente".
    api = armar()
    f = facturada(api, certificado)
    r = anular(armar({**ENTORNO_DIAN, "DIAN_AMBIENTE": ""}), f["id"])
    assert r.status_code == 503, r.text
    assert "DIAN_AMBIENTE" in r.json()["detail"] and "otro ambiente" not in r.json()["detail"]


def test_nota_rechazada_vuelve_a_ser_anulable_y_reusa_el_numero(api, certificado):
    f = facturada(api, certificado)
    nota = anular(api, f["id"]).json()
    # Como lo deja el worker ante un rechazo de la DIAN: la nota RECHAZADA y su trabajo terminado.
    api.e.datos.conn.execute("UPDATE factura SET estado = 'RECHAZADO', error = 'x' WHERE id = %s", (nota["id"],))
    api.e.datos.conn.execute("UPDATE outbox SET estado = 'HECHO', terminado_en = now() WHERE factura_id = %s",
                             (nota["id"],))  # fmt: skip
    factura = api.get(f"/facturas/{f['id']}", como=api.admin).json()
    assert factura["anulable"] is True and factura["anulada_por"] is None

    r = anular(api, f["id"], motivo="Motivo corregido")
    assert r.status_code == 202, r.text
    assert (r.json()["id"], r.json()["numero"], r.json()["estado"]) == (nota["id"], "NCP1", "EN_PROCESO")
    assert api.e.datos.uno("SELECT count(*) FROM factura WHERE tipo = 'NOTA_CREDITO'") == 1
    assert api.e.datos.uno("SELECT siguiente FROM numeracion_nota") == 2
