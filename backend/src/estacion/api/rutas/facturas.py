"""Emisión y consulta de facturas. La respuesta de emitir NO espera a la DIAN: la factura queda EN_PROCESO y
el worker la envía; la pantalla consulta `GET /facturas/{id}` hasta ver FACTURADO, RECHAZADO o INCIERTO."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response, status

from ...facturacion.representacion import RepresentacionInvalida, generar_pdf, leer_xml
from ...servicios import clientes
from ...servicios._bd import auditar
from ...servicios.emision import Origen
from ..dependencias import Conn, SesionActual
from ..esquemas import ClienteFactura, Error, FacturaEntrada, FacturaSalida, TipoDocumento

router = APIRouter(prefix="/facturas", tags=["facturas"])


def leer_factura(conn: Conn, factura_id: int) -> FacturaSalida | None:
    fila = conn.execute(
        """SELECT id, numero, estado, total, cufe, fecha_emision, medio_pago, despacho_id, venta_manual_id,
                  cliente_id, error
           FROM factura WHERE id = %s""",
        (factura_id,),
    ).fetchone()
    if fila is None:
        return None
    fid, numero, estado, total, cufe, fecha, medio, despacho_id, venta_id, cliente_id, error = fila
    cliente = clientes.obtener(conn, cliente_id)
    assert cliente is not None  # FK
    return FacturaSalida(
        id=fid, numero=numero, estado=estado, total=total, cufe=cufe, fecha_emision=fecha, medio_pago=medio,
        origen_tipo="despacho" if despacho_id is not None else "venta_manual",
        origen_id=despacho_id if despacho_id is not None else venta_id,
        cliente=ClienteFactura(id=cliente.id, tipo_documento=cast(TipoDocumento, cliente.tipo_documento),
                               nombre_mostrar=cliente.nombre_mostrar),
        error=error,
    )  # fmt: skip


@router.post(
    "",
    response_model=FacturaSalida,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": FacturaSalida, "description": "la clave ya tenía factura (doble clic o reintento)"},
        409: {"model": Error, "description": "la venta ya no está pendiente o la clave se usó para otra venta"},
        422: {"model": Error, "description": "datos inválidos o caso todavía no soportado"},
        503: {"model": Error, "description": "falta configuración fiscal o numeración: no se emite"},
    },
    summary="Emitir la factura de un despacho o venta manual (idempotente por `clave`)",
)
def emitir(datos: FacturaEntrada, request: Request, response: Response, conn: Conn, sesion: SesionActual):
    registrada = request.app.state.emision.solicitar(
        conn,
        clave=datos.clave,
        origen=Origen(datos.origen_tipo, datos.origen_id),
        cliente_id=datos.cliente_id,
        usuario_id=sesion.usuario_id,
        medio_pago=datos.medio_pago,
    )
    if not registrada.nueva:
        response.status_code = status.HTTP_200_OK
    factura = leer_factura(conn, registrada.id)
    assert factura is not None
    return factura.model_copy(update={"nueva": registrada.nueva})


@router.get(
    "/{factura_id}", response_model=FacturaSalida, responses={404: {"model": Error}}, summary="Estado de una factura"
)
def consultar(factura_id: int, conn: Conn, _: SesionActual) -> FacturaSalida:
    factura = leer_factura(conn, factura_id)
    if factura is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe esa factura")
    return factura


def texto_validacion(respuesta: dict[str, object] | None) -> str:
    """Lo que el PDF dice de la validación. Con el proveedor simulado, lo dice claramente; nunca inventa una fecha."""
    if respuesta and respuesta.get("simulado"):
        return "SIMULADA (banco de pruebas): no se envió a la DIAN"
    fecha = (respuesta or {}).get("fecha_validacion")
    return str(fecha) if fecha else "SIN DATO DE VALIDACIÓN"


@router.get(
    "/{factura_id}/pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "representación gráfica (PDF)"},
        404: {"model": Error},
        409: {"model": Error, "description": "la factura todavía no está validada"},
        422: {"model": Error, "description": "el XML no permite armar la representación"},
    },
    summary="Representación gráfica (PDF) de una factura validada, armada desde su XML",
)
def pdf(factura_id: int, conn: Conn, sesion: SesionActual) -> Response:
    fila = conn.execute(
        "SELECT numero, estado, coalesce(xml_firmado, xml_sin_firmar), respuesta_dian FROM factura WHERE id = %s",
        (factura_id,),
    ).fetchone()
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe esa factura")
    numero, estado, xml, respuesta = fila
    if estado != "FACTURADO" or not xml:
        raise HTTPException(status.HTTP_409_CONFLICT, f"la factura está {estado}: solo una factura validada tiene PDF")
    try:
        contenido = generar_pdf(leer_xml(xml.encode("utf-8")), validacion=texto_validacion(respuesta))
    except RepresentacionInvalida as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"no se pudo armar el PDF: {e}") from None
    # El PDF lleva datos personales del comprador: queda en la auditoría quién lo descargó (sin esos datos).
    auditar(conn, "FACTURA_PDF", "factura", factura_id, usuario_id=sesion.usuario_id)
    return Response(
        contenido,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="factura_{numero}.pdf"',
            "Cache-Control": "no-store",
        },
    )
