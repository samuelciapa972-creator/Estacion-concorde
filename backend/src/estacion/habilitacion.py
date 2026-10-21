"""Habilitación ante la DIAN (software propio): `python -m estacion.habilitacion <acción>`.

Orden recomendado (todo contra el ambiente de HABILITACIÓN):
    probar-conexion         GetNumberingRange: certificado, firma del mensaje y red. No escribe nada.
    configurar-numeracion   carga la numeración del set (SETP 990000000-995000000) como la activa.
    enviar --usuario admin  crea N ventas de prueba y sus facturas; el ENVÍO lo hace el worker:
                            `python -m estacion.worker --proveedor dian`
    estado                  facturas del set con su estado y lo que respondió la DIAN.
Lee DATABASE_URL, DIAN_* y el certificado del entorno. Nunca imprime el PIN, la clave técnica ni la del certificado.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg

from .facturacion.dian.cliente import ClienteDian, ErrorSeguridadDian, ErrorTransporteDian
from .facturacion.dian.sobre import URL_HABILITACION
from .facturacion.documentos import ClienteInvalido
from .facturacion.firma import FirmaError, certificado_desde_entorno
from .facturacion.modelos import Cliente
from .servicios import clientes
from .servicios._bd import auditar
from .servicios.emision import Origen, ServicioEmision
from .servicios.ventas import registrar_venta_manual

Conexion = psycopg.Connection[Any]
NUMERACION_SET: dict[str, Any] = dict(
    prefijo="SETP", numero_resolucion="18760000001", desde=990000000, hasta=995000000,
    vigente_desde=date(2019, 1, 19), vigente_hasta=date(2030, 1, 19),
)  # fmt: skip
# Venta SINTÉTICA de prueba (el ejemplo real analizado: 1,861 gal a $15.990 = $29.757 cobrados).
VOLUMEN, PPU, VALOR = Decimal("1.861"), Decimal("15990"), Decimal("29757")
DOCUMENTO_PRUEBA = "1000000001"


def _err(texto: str) -> None:
    print(texto, file=sys.stderr)


def main(
    argv: list[str] | None = None,
    *,
    entorno: Mapping[str, str] | None = None,
    confirmar: Callable[[str], str] = input,
    cliente_dian: ClienteDian | None = None,
) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m estacion.habilitacion",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="accion", required=True)
    sub.add_parser("probar-conexion", help="prueba certificado, firma y red (no envía documentos)")
    sub.add_parser("configurar-numeracion", help="activa la numeración SETP del set de pruebas")
    env = sub.add_parser("enviar", help="crea facturas de prueba en la cola de envíos")
    env.add_argument("--cantidad", type=int, default=1, choices=range(1, 31), metavar="1..30")
    env.add_argument("--usuario", required=True, help="login de un usuario ADMIN (queda en la auditoría)")
    env.add_argument("--correo", help="a dónde llega el ZIP de las facturas de prueba (por defecto, el de la estación)")
    sub.add_parser("estado", help="facturas del set y lo que respondió la DIAN")
    args = ap.parse_args(argv)

    entorno = os.environ if entorno is None else entorno
    if entorno.get("DIAN_AMBIENTE", "").strip() != "2":  # esta herramienta es solo del set de pruebas
        _err('DIAN_AMBIENTE debe ser "2" (habilitación)')
        return 2
    dsn = entorno.get("DATABASE_URL", "").strip()
    if not dsn:
        _err("falta la variable de entorno DATABASE_URL")
        return 2
    with psycopg.connect(dsn, autocommit=True) as conn:
        estacion = conn.execute("SELECT id, nit, email FROM estacion WHERE activa").fetchone()
        if estacion is None:
            _err("no hay una estación (emisor) activa configurada")
            return 2
        if args.accion == "probar-conexion":
            return _probar(entorno, estacion[1], cliente_dian)
        if args.accion == "configurar-numeracion":
            return _configurar(conn, estacion[0], confirmar)
        if args.accion == "enviar":
            return _enviar(conn, entorno, args.correo or estacion[2], args.cantidad, args.usuario, confirmar)
        return _estado(conn)


def _probar(entorno: Mapping[str, str], nit: str, cliente_dian: ClienteDian | None) -> int:
    software_id = entorno.get("DIAN_SOFTWARE_ID", "").strip()
    if not software_id:
        _err("falta DIAN_SOFTWARE_ID")
        return 2
    if cliente_dian is None:
        try:
            cliente_dian = ClienteDian(URL_HABILITACION, certificado_desde_entorno(entorno))
        except FirmaError as e:
            _err(f"certificado: {e}")
            return 2
    try:
        r = cliente_dian.rangos_numeracion(nit, software_id)
    except ErrorSeguridadDian as e:
        _err(f"la DIAN rechazó la seguridad del mensaje: {e}")
        _err("revise que el certificado sea el de la empresa y esté vigente, y que el reloj del servidor esté en hora")
        return 1
    except ErrorTransporteDian as e:
        _err(f"sin respuesta útil de la DIAN: {e}")
        return 1
    print(f"Conexión OK con la DIAN (habilitación): {r.codigo} {r.descripcion}")
    for rango in r.rangos:
        print(
            f"  {rango.prefijo} {rango.desde}-{rango.hasta} resolución {rango.resolucion} "
            f"({rango.vigente_desde} a {rango.vigente_hasta})"
        )
    return 0


def _configurar(conn: Conexion, estacion_id: int, confirmar: Callable[[str], str]) -> int:
    n = NUMERACION_SET
    pregunta = f"Activar la numeración del set {n['prefijo']} {n['desde']}-{n['hasta']}? Escriba SI: "
    if confirmar(pregunta).strip() != "SI":
        _err("cancelado: no se cambió nada")
        return 1
    with conn.transaction():
        fila = conn.execute(
            "SELECT id, desde, hasta FROM numeracion WHERE prefijo = %s AND numero_resolucion = %s",
            (n["prefijo"], n["numero_resolucion"]),
        ).fetchone()
        if fila is not None and (fila[1], fila[2]) != (n["desde"], n["hasta"]):
            _err(
                f"ya existe la numeración {n['prefijo']}/{n['numero_resolucion']} con otro rango "
                f"({fila[1]}-{fila[2]}); revísela antes de continuar"
            )
            return 1  # todavía no se modificó nada
        conn.execute("UPDATE numeracion SET activa = FALSE WHERE estacion_id = %s AND activa", (estacion_id,))
        if fila is None:
            conn.execute(
                """INSERT INTO numeracion (estacion_id, prefijo, numero_resolucion, desde, hasta, siguiente,
                                           vigente_desde, vigente_hasta, activa)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)""",
                (estacion_id, n["prefijo"], n["numero_resolucion"], n["desde"], n["hasta"], n["desde"],
                 n["vigente_desde"], n["vigente_hasta"]),
            )  # fmt: skip
        else:  # ya existe la del set: se reactiva SIN tocar `siguiente`
            conn.execute("UPDATE numeracion SET activa = TRUE WHERE id = %s", (fila[0],))
        auditar(conn, "NUMERACION_SET_ACTIVADA", "estacion", estacion_id, detalle={"prefijo": n["prefijo"]})
    print(f"Numeración {n['prefijo']} {n['desde']}-{n['hasta']} activa.")
    return 0


def _enviar(
    conn: Conexion,
    entorno: Mapping[str, str],
    correo: str,
    cantidad: int,
    login: str,
    confirmar: Callable[[str], str],
) -> int:
    usuario = conn.execute(
        "SELECT id FROM usuario WHERE usuario = %s AND activo AND rol = 'ADMIN'", (login.strip().lower(),)
    ).fetchone()
    if usuario is None:
        _err(f"{login!r} no es un usuario ADMIN activo")
        return 2
    surtidor = conn.execute("SELECT id FROM surtidor WHERE activo ORDER BY id LIMIT 1").fetchone()
    producto = conn.execute("SELECT codigo FROM producto ORDER BY id LIMIT 1").fetchone()
    if surtidor is None or producto is None:
        _err("se necesita al menos un surtidor activo y un producto")
        return 2
    try:
        cliente = _datos_cliente_de_prueba(correo)
    except ClienteInvalido as e:
        _err(f"correo inválido para el cliente de prueba: {e}")
        return 2
    texto = f"Se crearán {cantidad} factura(s) para el ambiente de HABILITACIÓN de la DIAN. Escriba ENVIAR: "
    if confirmar(texto).strip() != "ENVIAR":
        _err("cancelado: no se creó nada")
        return 1
    cliente_id = _cliente_de_prueba(conn, cliente, usuario[0])
    if cliente_id is None:
        _err(f"el cliente de prueba CC {DOCUMENTO_PRUEBA} está inactivo: actívelo en Clientes")
        return 2
    servicio = ServicioEmision(entorno=entorno)
    for _ in range(cantidad):
        venta = registrar_venta_manual(
            conn, usuario_id=usuario[0], surtidor_id=surtidor[0], lado="A", producto=producto[0],
            volumen=VOLUMEN, valor=VALOR, ppu=PPU,
        )  # fmt: skip
        f = servicio.solicitar(
            conn, clave=uuid.uuid4().hex, origen=Origen("venta_manual", venta), cliente_id=cliente_id,
            usuario_id=usuario[0], medio_pago="EFECTIVO",
        )  # fmt: skip
        print(f"  {f.numero} en cola (factura {f.id})")
    print("Ahora corra el worker: python -m estacion.worker --proveedor dian ; y luego: estado")
    return 0


def _datos_cliente_de_prueba(correo: str) -> Cliente:
    """Cliente SINTÉTICO; recibe el ZIP en `correo` (el de la estación o el que se indique, nunca el de un tercero)."""
    return clientes.normalizar(
        tipo_documento="CC", numero_documento=DOCUMENTO_PRUEBA, nombres="CLIENTE DE PRUEBA",
        apellidos="HABILITACION", email=correo, autoriza_tratamiento=True,
    )  # fmt: skip


def _cliente_de_prueba(conn: Conexion, cliente: Cliente, usuario_id: int) -> int | None:
    """El id del cliente de prueba, con el correo pedido. None si existe pero está inactivo."""
    existente = clientes.buscar(conn, "CC", DOCUMENTO_PRUEBA)
    if existente is None:
        return clientes.registrar(conn, cliente, canal="PISTA", usuario_id=usuario_id).id
    if not existente.activo:
        return None
    if existente.email != cliente.email:
        with conn.transaction():
            conn.execute("UPDATE cliente SET email = %s WHERE id = %s", (cliente.email, existente.id))
            auditar(conn, "CLIENTE_CORREO_CAMBIADO", "cliente", existente.id, usuario_id=usuario_id)
    return existente.id


def _estado(conn: Conexion) -> int:
    n = NUMERACION_SET
    filas = conn.execute(
        """SELECT numero, estado, dian_track_id, respuesta_dian->>'codigo_dian', error
           FROM factura WHERE prefijo = %s AND consecutivo BETWEEN %s AND %s ORDER BY consecutivo""",
        (n["prefijo"], n["desde"], n["hasta"]),
    ).fetchall()
    if not filas:
        print("sin facturas SETP del set de pruebas")
        return 0
    for numero, estado, track, codigo, error in filas:
        print(f"{numero}  {estado:<10}  track={track or '-'}  código={codigo or '-'}  {error or ''}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
