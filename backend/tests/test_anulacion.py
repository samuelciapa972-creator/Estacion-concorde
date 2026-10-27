"""Servicio de anulación (`servicios/anulacion.py`) contra PostgreSQL real: numera, guarda y encola la nota crédito.

La factura anulada se lleva a FACTURADO con `procesar_pendientes` + `ProveedorSimulado`: nada sale hacia la DIAN.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from lxml import etree

from conftest import ENTORNO_DIAN, Escenario
from estacion.facturacion.modelos import EstadoFactura, SolicitudFactura
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.facturacion.xml_nota import ReferenciaFactura, construir_nota_credito
from estacion.servicios.anulacion import (
    PREFIJO_NOTA,
    FacturaNoAnulable,
    ServicioAnulacion,
    asignar_consecutivo_nota,
)
from estacion.servicios.configuracion import ConfiguracionIncompleta, cargar_configuracion
from estacion.servicios.emision import ClaveReutilizada, Origen, ServicioEmision, leer_cliente, leer_origen
from estacion.servicios.outbox import procesar_pendientes

pytestmark = pytest.mark.bd

AHORA = datetime(2026, 9, 30, 10, 5)
AHORA_NOTA = datetime(2026, 10, 1, 8, 30, 15)
MOTIVO = "Error en los datos del cliente"
ENTORNO_PRODUCCION = {**ENTORNO_DIAN, "DIAN_AMBIENTE": "1"}
NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def anulador(entorno: dict[str, str] | None = None, reloj=lambda: AHORA_NOTA) -> ServicioAnulacion:
    return ServicioAnulacion(entorno=ENTORNO_DIAN if entorno is None else entorno, reloj=reloj)


def facturar(e: Escenario, conn, recursos, *, origen: Origen | None = None, entorno=None, aceptar=True) -> int:
    """Emite una factura (por omisión de una venta manual) y, si `aceptar`, la deja FACTURADO."""
    origen = origen or Origen("venta_manual", e.datos.venta_manual(e.usuario))
    f = ServicioEmision(entorno=ENTORNO_DIAN if entorno is None else entorno, reloj=lambda: AHORA).solicitar(
        conn, clave=uuid.uuid4().hex, origen=origen, cliente_id=e.cliente, usuario_id=e.usuario,
        medio_pago="EFECTIVO",
    )  # fmt: skip
    if aceptar:
        if entorno is None:
            procesar_pendientes(conn, ProveedorSimulado(), recursos=recursos)
        else:  # otro ambiente: el proveedor simulado no importa, se marca aceptada directamente
            e.datos.conn.execute("UPDATE factura SET estado = 'FACTURADO' WHERE id = %s", (f.id,))
    return f.id


def anular(e: Escenario, conn, factura_id: int, *, k: str | None = None, motivo: str = MOTIVO, **kw):
    return anulador(**kw).solicitar(
        conn, clave=k or uuid.uuid4().hex, factura_id=factura_id, usuario_id=e.usuario, motivo=motivo
    )


def contar(e: Escenario, tabla: str, donde: str = "TRUE") -> int:
    return e.datos.uno(f"SELECT count(*) FROM {tabla} WHERE {donde}")


def siguiente_nota(e: Escenario) -> int | None:
    return e.datos.uno("SELECT siguiente FROM numeracion_nota ORDER BY id LIMIT 1")


def test_anular_crea_nota_en_proceso_y_encola(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)
    nota = anular(escenario, conn, factura_id)

    assert (nota.numero, nota.estado, nota.nueva) == ("NCP1", EstadoFactura.EN_PROCESO, True)
    tipo, estado, referencia, motivo, ambiente, numero, total, xml = escenario.datos.conn.execute(
        "SELECT tipo, estado, referencia_id, motivo, ambiente, numero, total, xml_sin_firmar "
        "FROM factura WHERE id = %s",
        (nota.id,),
    ).fetchone()
    assert (tipo, estado, referencia, motivo, ambiente, numero) == (
        "NOTA_CREDITO", "EN_PROCESO", factura_id, MOTIVO, "2", "NCP1",
    )  # fmt: skip
    assert total == escenario.datos.uno("SELECT total FROM factura WHERE id = %s", (factura_id,))
    assert "<cbc:CreditNoteTypeCode>91</cbc:CreditNoteTypeCode>" in xml
    trabajos = escenario.datos.conn.execute(
        "SELECT tipo, estado FROM outbox WHERE factura_id = %s", (nota.id,)
    ).fetchall()
    assert trabajos == [("ENVIAR_DIAN", "PENDIENTE")]
    # La venta sigue FACTURADO: solo la nota aceptada la saca de los reportes.
    assert escenario.datos.uno("SELECT estado FROM factura WHERE id = %s", (factura_id,)) == "FACTURADO"
    assert siguiente_nota(escenario) == 2
    (detalle,) = escenario.datos.conn.execute(
        "SELECT detalle FROM auditoria WHERE accion = 'ANULACION_SOLICITADA' AND entidad_id = %s", (str(nota.id),)
    ).fetchone()
    assert detalle["nota"] == "NCP1" and detalle["motivo_largo"] == len(MOTIVO)
    assert MOTIVO not in str(detalle)


def test_misma_clave_devuelve_la_misma_nota(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)
    k = uuid.uuid4().hex
    primera = anular(escenario, conn, factura_id, k=k)
    segunda = anular(escenario, conn, factura_id, k=k)

    assert (segunda.id, segunda.numero, segunda.nueva) == (primera.id, primera.numero, False)
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 1
    assert contar(escenario, "outbox", f"factura_id = {primera.id}") == 1
    assert siguiente_nota(escenario) == 2


def test_misma_clave_para_otra_factura_se_rechaza(escenario, conn, recursos):
    k = uuid.uuid4().hex
    anular(escenario, conn, facturar(escenario, conn, recursos), k=k)
    otra = facturar(escenario, conn, recursos)
    with pytest.raises(ClaveReutilizada):
        anular(escenario, conn, otra, k=k)
    assert siguiente_nota(escenario) == 2


def test_otra_clave_con_nota_viva_es_rechazada(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)
    anular(escenario, conn, factura_id)
    with pytest.raises(FacturaNoAnulable, match="ya tiene una nota"):
        anular(escenario, conn, factura_id)
    assert siguiente_nota(escenario) == 2


@pytest.mark.parametrize("estado", ["EN_PROCESO", "INCIERTO", "RECHAZADO"])
def test_no_se_anula_si_no_esta_facturada(escenario, conn, recursos, estado):
    factura_id = facturar(escenario, conn, recursos, aceptar=False)
    escenario.datos.conn.execute("UPDATE factura SET estado = %s, error = 'x' WHERE id = %s", (estado, factura_id))
    with pytest.raises(FacturaNoAnulable, match="no está aceptada"):
        anular(escenario, conn, factura_id)
    assert siguiente_nota(escenario) is None  # ni siquiera se creó la serie: no se gastó número
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 0


def test_factura_inexistente(escenario, conn):
    with pytest.raises(FacturaNoAnulable, match="no existe"):
        anular(escenario, conn, 999999)


def test_no_se_anula_una_nota(escenario, conn, recursos):
    nota = anular(escenario, conn, facturar(escenario, conn, recursos))
    with pytest.raises(FacturaNoAnulable, match="solo se anulan facturas"):
        anular(escenario, conn, nota.id)


def test_no_se_anula_factura_de_despacho(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos, origen=Origen("despacho", escenario.datos.despacho()))
    with pytest.raises(FacturaNoAnulable, match="ventas manuales"):
        anular(escenario, conn, factura_id)
    assert siguiente_nota(escenario) is None


def test_no_se_anula_factura_de_otro_ambiente(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)  # ambiente '2'
    with pytest.raises(FacturaNoAnulable, match="otro ambiente"):
        anular(escenario, conn, factura_id, entorno=ENTORNO_PRODUCCION)
    assert siguiente_nota(escenario) is None


@pytest.mark.parametrize("motivo", ["", "   \n"])
def test_motivo_vacio(escenario, conn, recursos, motivo):
    factura_id = facturar(escenario, conn, recursos)
    with pytest.raises(ValueError, match="motivo"):
        anular(escenario, conn, factura_id, motivo=motivo)
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 0


def test_motivo_se_recorta_a_500(escenario, conn, recursos):
    nota = anular(escenario, conn, facturar(escenario, conn, recursos), motivo="x" * 800)
    assert escenario.datos.uno("SELECT length(motivo) FROM factura WHERE id = %s", (nota.id,)) == 500


def test_series_por_ambiente(escenario, conn, recursos):
    assert anular(escenario, conn, facturar(escenario, conn, recursos)).numero == "NCP1"

    # La estación pasa a producción: otra resolución, otro ambiente, otra serie de notas.
    escenario.datos.conn.execute("UPDATE numeracion SET activa = FALSE WHERE id = %s", (escenario.numeracion,))
    escenario.datos.numeracion(escenario.estacion, prefijo="EDSC", ambiente="1", numero_resolucion="18764117468740")
    en_produccion = facturar(escenario, conn, recursos, entorno=ENTORNO_PRODUCCION)
    nota = anular(escenario, conn, en_produccion, entorno=ENTORNO_PRODUCCION)

    assert nota.numero == "NCE1"
    series = escenario.datos.conn.execute("SELECT ambiente, prefijo, siguiente FROM numeracion_nota ORDER BY ambiente")
    assert series.fetchall() == [("1", "NCE", 2), ("2", "NCP", 2)]


def test_nota_rechazada_se_reenvia_con_el_mismo_numero(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)
    primera = anular(escenario, conn, factura_id)
    # Como lo deja el worker ante un rechazo: la factura RECHAZADA y su trabajo terminado.
    escenario.datos.conn.execute("UPDATE factura SET estado = 'RECHAZADO', error = 'x' WHERE id = %s", (primera.id,))
    escenario.datos.conn.execute(
        "UPDATE outbox SET estado = 'HECHO', terminado_en = now() WHERE factura_id = %s", (primera.id,)
    )

    segunda = anular(escenario, conn, factura_id, motivo="Motivo corregido")

    assert (segunda.id, segunda.numero, segunda.nueva) == (primera.id, "NCP1", True)
    estado, motivo, error = escenario.datos.conn.execute(
        "SELECT estado, motivo, error FROM factura WHERE id = %s", (primera.id,)
    ).fetchone()
    assert (estado, motivo, error) == ("EN_PROCESO", "Motivo corregido", None)
    assert siguiente_nota(escenario) == 2
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 1
    assert contar(escenario, "auditoria", "accion = 'NOTA_REENVIADA'") == 1


def test_prefijo_incorrecto_para_el_ambiente_falla_sin_gastar_numero(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)
    # La BD no impide un NCP en la serie del ambiente de producción (ni al revés): el servicio lo vigila.
    escenario.datos.insertar("numeracion_nota", estacion_id=escenario.estacion, ambiente="2", prefijo="NCE")
    with pytest.raises(ConfiguracionIncompleta, match="prefijo NCE, y debe ser NCP"):
        anular(escenario, conn, factura_id)
    assert siguiente_nota(escenario) == 1
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 0
    assert contar(escenario, "outbox") == 2  # solo los de la factura (envío y correo): ninguno de nota


def test_numerar_fuera_de_transaccion_se_rechaza(escenario, conn):
    with pytest.raises(RuntimeError, match="dentro de la transacción"):
        asignar_consecutivo_nota(conn, escenario.estacion, "2")


def test_sin_estacion_activa_no_se_anula(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)
    escenario.datos.conn.execute("UPDATE estacion SET activa = FALSE")
    with pytest.raises(ConfiguracionIncompleta):
        anular(escenario, conn, factura_id)
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 0


def test_la_nota_usa_la_hora_de_la_nota_y_referencia_la_de_la_factura(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)  # emitida el 2026-09-30 10:05
    nota = anular(escenario, conn, factura_id, reloj=lambda: AHORA_NOTA)

    xml, cude = escenario.datos.conn.execute(
        "SELECT xml_sin_firmar, cufe FROM factura WHERE id = %s", (nota.id,)
    ).fetchone()
    arbol = etree.fromstring(xml.encode("utf-8"))
    texto = lambda xp: arbol.xpath(xp, namespaces=NS)[0].text  # noqa: E731
    assert texto("/*/cbc:IssueDate") == "2026-10-01"
    assert texto("/*/cbc:IssueTime") == "08:30:15-05:00"
    assert texto("//cac:BillingReference//cbc:IssueDate") == "2026-09-30"
    assert escenario.datos.uno("SELECT fecha_emision FROM factura WHERE id = %s", (nota.id,)) == AHORA_NOTA

    # El CUDE guardado es el de construir la nota con la hora de la nota (y no con la de la factura).
    numero, cufe_f, fecha_f, venta, cliente_id, forma, medio = escenario.datos.conn.execute(
        """SELECT numero, cufe, fecha_emision, venta_manual_id, cliente_id, forma_pago, medio_pago
           FROM factura WHERE id = %s""",
        (factura_id,),
    ).fetchone()
    config = cargar_configuracion(conn, escenario.numeracion, ENTORNO_DIAN)

    def cude_con(momento: datetime) -> str:
        sol = SolicitudFactura(
            clave="x", cliente=leer_cliente(conn, cliente_id),
            lineas=(leer_origen(conn, Origen("venta_manual", venta), bloquear=False).linea,),
            forma_pago=forma, medio_pago=medio, fecha_emision=momento,
        )  # fmt: skip
        return construir_nota_credito(
            sol, prefijo="NCP", consecutivo=1, referencia=ReferenciaFactura(numero, cufe_f, fecha_f),
            motivo=MOTIVO, emisor=config.emisor, software=config.software,
        ).cufe  # fmt: skip

    assert cude == cude_con(AHORA_NOTA)
    assert cude != cude_con(fecha_f)


def test_prefijos_por_ambiente():
    assert PREFIJO_NOTA == {"1": "NCE", "2": "NCP"}


def test_venta_modificada_tras_facturar_no_se_anula(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)
    venta = escenario.datos.uno("SELECT venta_manual_id FROM factura WHERE id = %s", (factura_id,))
    escenario.datos.conn.execute("UPDATE venta_manual SET valor = valor + 1000 WHERE id = %s", (venta,))
    with pytest.raises(FacturaNoAnulable, match="cambió"):
        anular(escenario, conn, factura_id)
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 0
    assert siguiente_nota(escenario) is None  # el rollback deshizo también la creación de la serie


def test_reenvio_con_prefijo_incorrecto_falla(escenario, conn, recursos):
    factura_id = facturar(escenario, conn, recursos)
    nota = anular(escenario, conn, factura_id)
    escenario.datos.conn.execute(
        "UPDATE factura SET estado = 'RECHAZADO', error = 'x', prefijo = 'NCE' WHERE id = %s", (nota.id,)
    )
    escenario.datos.conn.execute(
        "UPDATE outbox SET estado = 'HECHO', terminado_en = now() WHERE factura_id = %s", (nota.id,)
    )
    with pytest.raises(ConfiguracionIncompleta, match="prefijo NCE"):
        anular(escenario, conn, factura_id)
    assert escenario.datos.uno("SELECT estado FROM factura WHERE id = %s", (nota.id,)) == "RECHAZADO"


@pytest.mark.parametrize("motivo", ["cliente\x0bequivocado", "nul\x00", "escape\x1b", "no carácter￾", "￿"])
def test_motivo_con_caracteres_que_el_xml_no_admite(escenario, conn, recursos, motivo):
    # Pegado desde Word (tabulación vertical, etc.): lxml no los acepta. Se rechaza antes de tocar la base.
    factura_id = facturar(escenario, conn, recursos)
    with pytest.raises(ValueError, match="caracteres no permitidos"):
        anular(escenario, conn, factura_id, motivo=motivo)
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 0
    assert siguiente_nota(escenario) is None


def test_motivo_con_saltos_de_linea_y_tabulaciones_se_acepta(escenario, conn, recursos):
    nota = anular(escenario, conn, facturar(escenario, conn, recursos), motivo="línea 1\n\tlínea 2\r\nfin")
    assert nota.numero == "NCP1"


def test_serie_de_notas_desfasada_falla_cerrado_sin_gastar_numero(escenario, conn, recursos):
    primera, segunda = facturar(escenario, conn, recursos), facturar(escenario, conn, recursos)
    assert anular(escenario, conn, primera).numero == "NCP1"
    # El contador quedó atrás de las notas guardadas (restauración parcial, edición a mano): NCP1 ya existe.
    escenario.datos.conn.execute("UPDATE numeracion_nota SET siguiente = 1")
    with pytest.raises(ConfiguracionIncompleta, match="la serie de notas NCP está desfasada"):
        anular(escenario, conn, segunda)
    assert siguiente_nota(escenario) == 1
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 1


def test_clave_tomada_por_otra_operacion_a_la_vez_es_clave_reutilizada(escenario, conn, recursos, monkeypatch):
    # Carrera: entre la comprobación de la clave y el INSERT, otra operación guarda la misma clave. Aquí se
    # simula dentro de la misma transacción (al construir el XML), y el INSERT choca con el índice de la clave.
    import estacion.servicios.anulacion as modulo

    otra = facturar(escenario, conn, recursos)
    factura_id = facturar(escenario, conn, recursos)
    k = uuid.uuid4().hex
    original = modulo.construir_nota_credito

    def con_carrera(*args, **kwargs):
        conn.execute("UPDATE factura SET clave_emision = %s WHERE id = %s", (k, otra))
        return original(*args, **kwargs)

    monkeypatch.setattr(modulo, "construir_nota_credito", con_carrera)
    with pytest.raises(ClaveReutilizada):
        anular(escenario, conn, factura_id, k=k)
    assert siguiente_nota(escenario) is None
    assert contar(escenario, "factura", "tipo = 'NOTA_CREDITO'") == 0
    assert contar(escenario, "factura", f"clave_emision = '{k}'") == 0  # el rollback deshizo la "otra" también
