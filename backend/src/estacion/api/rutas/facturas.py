"""Emisión y consulta de facturas. La respuesta de emitir NO espera a la DIAN: la factura queda EN_PROCESO y
el worker la envía; la pantalla consulta `GET /facturas/{id}` hasta ver FACTURADO, RECHAZADO o INCIERTO."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response, status
from psycopg import errors

from ...facturacion.modelos import EstadoEntrega, TipoTrabajo
from ...facturacion.recibo import generar_recibo
from ...facturacion.representacion import RepresentacionInvalida, generar_pdf, leer_xml, texto_validacion
from ...servicios import clientes, entrega
from ...servicios._bd import auditar, encolar, exigir_autocommit
from ...servicios.anulacion import FacturaNoEncontrada, es_anulable
from ...servicios.emision import Origen
from ..dependencias import Conn, SesionActual, SesionAdmin
from ..esquemas import (
    AnulacionEntrada,
    ClienteFactura,
    EntregaSalida,
    Error,
    FacturaEntrada,
    FacturaSalida,
    TipoDocumento,
)

router = APIRouter(prefix="/facturas", tags=["facturas"])


def leer_factura(conn: Conn, factura_id: int, ambiente_actual: str) -> FacturaSalida | None:
    """La factura o nota con su estado de anulación. Una nota hereda el origen y el cliente de la factura que anula."""
    fila = conn.execute(
        """SELECT f.id, f.numero, f.estado, f.total, f.cufe, f.fecha_emision, f.medio_pago,
                  coalesce(f.despacho_id, r.despacho_id), coalesce(f.venta_manual_id, r.venta_manual_id),
                  f.cliente_id, f.error, f.tipo, f.ambiente, f.venta_manual_id, r.numero, n.numero, n.estado
           FROM factura f
           LEFT JOIN factura r ON r.id = f.referencia_id  -- la factura que anula esta nota
           LEFT JOIN factura n ON n.referencia_id = f.id  -- la nota de esta factura (única por factura)
           WHERE f.id = %s""",
        (factura_id,),
    ).fetchone()
    if fila is None:
        return None
    (fid, numero, estado, total, cufe, fecha, medio, despacho_id, venta_id, cliente_id, error, tipo, ambiente,
     venta_propia, referencia, nota_numero, nota_estado) = fila  # fmt: skip
    cliente = clientes.obtener(conn, cliente_id)
    assert cliente is not None  # FK
    e = entrega.obtener(conn, fid)
    return FacturaSalida(
        id=fid, tipo=tipo, ambiente=ambiente, referencia=referencia,
        anulada_por=nota_numero if nota_estado == "FACTURADO" else None,
        anulable=es_anulable(tipo=tipo, estado=estado, venta_manual_id=venta_propia, ambiente=ambiente,
                             ambiente_actual=ambiente_actual, estado_nota=nota_estado),
        numero=numero, estado=estado, total=total, cufe=cufe, fecha_emision=fecha, medio_pago=medio,
        origen_tipo="despacho" if despacho_id is not None else "venta_manual",
        origen_id=despacho_id if despacho_id is not None else venta_id,
        cliente=ClienteFactura(id=cliente.id, tipo_documento=cast(TipoDocumento, cliente.tipo_documento),
                               nombre_mostrar=cliente.nombre_mostrar),
        error=error,
        entrega=None if e is None else EntregaSalida(
            estado=e.estado.value,
            nombre_zip=e.nombre_zip,
            enviada_en=e.enviada_en,
        ),
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
    factura = leer_factura(conn, registrada.id, request.app.state.anulacion.ambiente_actual)
    assert factura is not None
    return factura.model_copy(update={"nueva": registrada.nueva})


@router.post(
    "/{factura_id}/anular",
    response_model=FacturaSalida,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": Error, "description": "no existe la factura"},
        409: {"model": Error, "description": "la factura no se puede anular o la clave se usó para otra operación"},
        422: {"model": Error, "description": "datos inválidos"},
        503: {"model": Error, "description": "falta configuración fiscal o numeración: no se emite"},
    },
    summary="Anular una factura con una nota crédito total (idempotente por `clave`). Solo administradores",
)
def anular(factura_id: int, datos: AnulacionEntrada, request: Request, conn: Conn, sesion: SesionAdmin):
    # Como `emitir`, no espera a la DIAN: la nota queda EN_PROCESO y el worker la envía. El motivo no se repite
    # en ningún mensaje de error: puede llevar datos personales.
    servicio = request.app.state.anulacion
    try:
        registrada = servicio.solicitar(
            conn, clave=datos.clave, factura_id=factura_id, usuario_id=sesion.usuario_id, motivo=datos.motivo
        )
    except FacturaNoEncontrada:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe esa factura") from None
    nota = leer_factura(conn, registrada.id, servicio.ambiente_actual)
    assert nota is not None
    return nota.model_copy(update={"nueva": registrada.nueva})


@router.get(
    "/{factura_id}", response_model=FacturaSalida, responses={404: {"model": Error}}, summary="Estado de una factura"
)
def consultar(factura_id: int, request: Request, conn: Conn, _: SesionActual) -> FacturaSalida:
    factura = leer_factura(conn, factura_id, request.app.state.anulacion.ambiente_actual)
    if factura is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe esa factura")
    return factura


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
        """SELECT numero, estado, coalesce(xml_firmado, xml_sin_firmar), respuesta_dian, application_response
           FROM factura WHERE id = %s""",
        (factura_id,),
    ).fetchone()
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe esa factura")
    numero, estado, xml, respuesta, application_response = fila
    if estado != "FACTURADO" or not xml:
        raise HTTPException(status.HTTP_409_CONFLICT, f"la factura está {estado}: solo una factura validada tiene PDF")
    try:
        contenido = generar_pdf(
            leer_xml(xml.encode("utf-8")), validacion=texto_validacion(respuesta, application_response)
        )
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


def _placa(conn: Conn, factura_id: int) -> str | None:
    """La placa normalizada del despacho o, en una venta manual, la que digitó el bombero (opcional). Una nota la
    toma de la factura que anula."""
    fila = conn.execute(
        """SELECT coalesce(d.ref_vehiculo, v.placa)
           FROM factura f
           LEFT JOIN factura r ON r.id = f.referencia_id
           LEFT JOIN venta_manual v ON v.id = coalesce(f.venta_manual_id, r.venta_manual_id)
           LEFT JOIN despacho d ON d.id = coalesce(f.despacho_id, r.despacho_id, v.despacho_id)
           WHERE f.id = %s""",
        (factura_id,),
    ).fetchone()
    return None if fila is None or not fila[0] else str(fila[0])


@router.get(
    "/{factura_id}/recibo",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "comandos ESC/POS (impresora de 58 mm)"},
        404: {"model": Error},
        409: {"model": Error, "description": "la factura todavía no está validada"},
        422: {"model": Error, "description": "el XML no permite armar el recibo"},
    },
    summary="Recibo para la impresora térmica (ESC/POS), armado desde el XML como el PDF",
)
def recibo(factura_id: int, conn: Conn, sesion: SesionActual) -> Response:
    fila = conn.execute(
        """SELECT numero, estado, coalesce(xml_firmado, xml_sin_firmar), respuesta_dian, application_response
           FROM factura WHERE id = %s""",
        (factura_id,),
    ).fetchone()
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe esa factura")
    numero, estado, xml, respuesta, application_response = fila
    if estado != "FACTURADO" or not xml:
        raise HTTPException(status.HTTP_409_CONFLICT, f"la factura está {estado}: solo se imprime una validada")
    try:
        contenido = generar_recibo(
            leer_xml(xml.encode("utf-8")),
            placa=_placa(conn, factura_id),
            validacion=texto_validacion(respuesta, application_response),
        )
    except RepresentacionInvalida as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"no se pudo armar el recibo: {e}") from None
    # Como el PDF: lleva datos del comprador, queda en la auditoría quién lo imprimió (sin esos datos).
    auditar(conn, "FACTURA_RECIBO", "factura", factura_id, usuario_id=sesion.usuario_id)
    return Response(
        contenido,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="recibo_{numero}.bin"',
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/{factura_id}/zip",
    response_class=Response,
    responses={
        200: {"content": {"application/zip": {}}, "description": "el ZIP entregado (AttachedDocument + PDF)"},
        404: {"model": Error, "description": "no existe la factura o todavía no tiene entrega"},
    },
    summary="Descargar el ZIP que se le entregó al cliente (el mismo archivo del correo)",
)
def descargar_zip(factura_id: int, conn: Conn, sesion: SesionActual) -> Response:
    e = entrega.obtener(conn, factura_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "la factura no existe o todavía no tiene entrega")
    # Lleva datos personales del comprador: queda en la auditoría quién lo descargó (sin esos datos).
    auditar(conn, "FACTURA_ZIP", "factura", factura_id, usuario_id=sesion.usuario_id)
    return Response(
        e.zip,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{e.nombre_zip}"', "Cache-Control": "no-store"},
    )


@router.post(
    "/{factura_id}/reenviar",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": Error},
        409: {"model": Error, "description": "no está FACTURADA o ya hay un envío en curso"},
    },
    summary="Volver a enviar al cliente el MISMO ZIP (a su correo actual). Solo administradores",
)
def reenviar(factura_id: int, conn: Conn, sesion: SesionAdmin) -> dict[str, str]:
    exigir_autocommit(conn)
    with conn.transaction():
        fila = conn.execute("SELECT estado FROM factura WHERE id = %s FOR UPDATE", (factura_id,)).fetchone()
        if fila is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe esa factura")
        if fila[0] != "FACTURADO":
            raise HTTPException(status.HTTP_409_CONFLICT, f"la factura está {fila[0]}: solo se reenvía una FACTURADA")
        try:
            with conn.transaction():  # punto de guardado: el choque con el índice no aborta la transacción
                encolar(conn, factura_id, TipoTrabajo.ENVIAR_CORREO)
        except errors.UniqueViolation:  # ux_outbox_un_trabajo_vivo: ya hay un envío pendiente
            raise HTTPException(status.HTTP_409_CONFLICT, "ya hay un envío de esta factura en curso") from None
        if entrega.obtener(conn, factura_id) is not None:  # sin entrega (no se pudo armar): el worker la vuelve a armar
            entrega.marcar(conn, factura_id, EstadoEntrega.PENDIENTE, intento=False)
        auditar(conn, "FACTURA_REENVIO", "factura", factura_id, usuario_id=sesion.usuario_id)
    return {"mensaje": "reenvío en cola"}
