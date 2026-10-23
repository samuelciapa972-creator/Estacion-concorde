"""Habilitación ante la DIAN (software propio): `python -m estacion.habilitacion <acción>`.

Orden recomendado (todo contra el ambiente de HABILITACIÓN):
    probar-conexion         GetNumberingRange: certificado, firma del mensaje y red. No escribe nada.
    configurar-numeracion   carga la numeración del set (SETP 990000000-995000000) como la activa.
    enviar --usuario admin  crea N ventas de prueba y sus facturas; el ENVÍO lo hace el worker:
                            `python -m estacion.worker --proveedor dian`
    estado                  facturas del set con su estado y lo que respondió la DIAN.

Salida a producción (con DIAN_AMBIENTE=1; ver docs):
    verificar-produccion    lista con ✔/✘ lo que falta (no envía documentos); sale 0 solo si todo está ✔.
    configurar-produccion --prefijo EDSC   consulta el rango en la DIAN y lo activa como numeración de producción.
    guardar-clave-tecnica --prefijo EDSC --archivo RUTA   escribe DIAN_CLAVE_TECNICA=<clave de la DIAN> en un
                            archivo NUEVO (0600), sin imprimirla; se copia la línea al .env y se borra el archivo.
    correo-emisor [--nuevo CORREO]         muestra o cambia el correo del emisor.
Lee DATABASE_URL, DIAN_* y el certificado del entorno. Nunca imprime el PIN, la clave técnica ni la del certificado.
"""

from __future__ import annotations

import argparse
import hmac
import os
import re
import sys
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from .facturacion.dian.cliente import ClienteDian, ConsultaRangos, ErrorSeguridadDian, ErrorTransporteDian
from .facturacion.dian.sobre import URL_HABILITACION, url_servicio
from .facturacion.documentos import ClienteInvalido
from .facturacion.firma import FirmaError, certificado_desde_entorno, es_certificado_de_prueba
from .facturacion.modelos import Cliente
from .servicios import clientes
from .servicios._bd import ahora_local, auditar
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
# Mismo patrón que el CHECK de estacion.email (migración 0002).
PATRON_CORREO = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}")
ACCIONES_DE_PRODUCCION = {"configurar-produccion", "verificar-produccion", "guardar-clave-tecnica"}
# Trabajos que hablan con la DIAN: los de habilitación (ambiente 2) no pueden quedar en la cola al pasar a producción.
SQL_PENDIENTES_DE_HABILITACION = """SELECT count(*) FROM outbox o JOIN factura f ON f.id = o.factura_id
    WHERE o.estado = 'PENDIENTE' AND o.tipo IN ('ENVIAR_DIAN', 'CONSULTAR_DIAN') AND f.ambiente = '2'"""


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
    sub.add_parser("verificar-produccion", help="revisa si todo está listo para producción (no envía documentos)")
    prod = sub.add_parser("configurar-produccion", help="activa la numeración de producción que la DIAN tiene asociada")
    prod.add_argument("--prefijo", required=True, help="prefijo del rango de producción, p. ej. EDSC")
    clave = sub.add_parser("guardar-clave-tecnica", help="guarda la clave técnica del rango, sin imprimirla")
    clave.add_argument("--prefijo", required=True, help="prefijo del rango de producción, p. ej. EDSC")
    clave.add_argument("--archivo", required=True, help="archivo NUEVO donde escribirla (permisos 0600)")
    correo = sub.add_parser("correo-emisor", help="muestra o cambia el correo del emisor")
    correo.add_argument("--nuevo", help="el correo nuevo (pide confirmación)")
    args = ap.parse_args(argv)

    entorno = os.environ if entorno is None else entorno
    ambiente = entorno.get("DIAN_AMBIENTE", "").strip()
    if args.accion in ACCIONES_DE_PRODUCCION:
        if ambiente != "1":
            _err('DIAN_AMBIENTE debe ser "1" (producción) para este comando')
            return 2
    elif args.accion != "correo-emisor" and ambiente != "2":  # el resto es solo del set de pruebas
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
        if args.accion == "verificar-produccion":
            return _verificar_produccion(conn, entorno, estacion[0], estacion[1], cliente_dian)
        if args.accion == "configurar-produccion":
            return _configurar_produccion(
                conn, entorno, estacion[0], estacion[1], args.prefijo, confirmar, cliente_dian
            )
        if args.accion == "guardar-clave-tecnica":
            return _guardar_clave_tecnica(entorno, estacion[1], args.prefijo, args.archivo, cliente_dian)
        if args.accion == "correo-emisor":
            return _correo_emisor(conn, estacion[0], estacion[2], args.nuevo, confirmar)
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


def _pendientes_de_habilitacion(conn: Conexion) -> int:
    fila = conn.execute(SQL_PENDIENTES_DE_HABILITACION).fetchone()
    return int(fila[0]) if fila else 0


def _cliente_produccion(entorno: Mapping[str, str]) -> ClienteDian:
    return ClienteDian(url_servicio("1"), certificado_desde_entorno(entorno))


def _consultar_rangos(
    entorno: Mapping[str, str], nit: str, cliente_dian: ClienteDian | None
) -> tuple[ConsultaRangos | None, str]:
    """(respuesta de GetNumberingRange en producción, motivo si no se pudo). Sin secretos en el motivo."""
    software_id = entorno.get("DIAN_SOFTWARE_ID", "").strip()
    if not software_id:
        return None, "falta DIAN_SOFTWARE_ID"
    try:
        cliente = cliente_dian or _cliente_produccion(entorno)
        return cliente.rangos_numeracion(nit, software_id), ""
    except FirmaError as e:
        return None, f"certificado: {e}"
    except ErrorSeguridadDian as e:
        return None, f"la DIAN rechazó la seguridad del mensaje: {e}"
    except ErrorTransporteDian as e:
        return None, f"sin respuesta útil de la DIAN: {e}"


def _configurar_produccion(
    conn: Conexion,
    entorno: Mapping[str, str],
    estacion_id: int,
    nit: str,
    prefijo: str,
    confirmar: Callable[[str], str],
    cliente_dian: ClienteDian | None,
) -> int:
    pendientes = _pendientes_de_habilitacion(conn)
    if pendientes:
        _err(
            f"hay {pendientes} documento(s) de habilitación (ambiente 2) pendientes en la cola de envíos: "
            "no se configura producción mientras existan; resuélvalos primero"
        )
        return 1
    r, motivo = _consultar_rangos(entorno, nit, cliente_dian)
    if r is None:
        _err(motivo)
        return 1
    rango = next((x for x in r.rangos if x.prefijo == prefijo), None)
    if rango is None:
        _err(f"la DIAN no tiene un rango de producción con el prefijo {prefijo}")
        return 1
    clave = entorno.get("DIAN_CLAVE_TECNICA", "").strip()
    if not rango.clave_tecnica or not hmac.compare_digest(rango.clave_tecnica, clave):
        _err(
            f"la clave técnica del rango {prefijo} en la DIAN no coincide con DIAN_CLAVE_TECNICA: no se configuró nada"
        )
        return 1
    try:
        vigente_desde, vigente_hasta = date.fromisoformat(rango.vigente_desde), date.fromisoformat(rango.vigente_hasta)
    except ValueError:
        _err("la DIAN devolvió una vigencia que no se entiende: no se configuró nada")
        return 1
    print(
        f"Resolución {rango.resolucion}: {rango.prefijo} {rango.desde}-{rango.hasta}, "
        f"vigente del {vigente_desde} al {vigente_hasta}"
    )
    if (
        confirmar(f"Activar esta numeración de PRODUCCIÓN? Escriba el prefijo {prefijo} para confirmar: ").strip()
        != prefijo
    ):
        _err("cancelado: no se cambió nada")
        return 1
    with conn.transaction():
        fila = conn.execute(
            "SELECT id, desde, hasta, ambiente FROM numeracion WHERE prefijo = %s AND numero_resolucion = %s",
            (prefijo, rango.resolucion),
        ).fetchone()
        if fila is not None and (fila[1], fila[2], fila[3]) != (rango.desde, rango.hasta, "1"):
            _err(f"ya existe la numeración {prefijo}/{rango.resolucion} con otro rango o ambiente; revísela primero")
            return 1  # todavía no se modificó nada
        conn.execute("UPDATE numeracion SET activa = FALSE WHERE estacion_id = %s AND activa", (estacion_id,))
        if fila is None:
            conn.execute(
                """INSERT INTO numeracion (estacion_id, prefijo, numero_resolucion, desde, hasta, siguiente,
                                           vigente_desde, vigente_hasta, activa, ambiente)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, '1')""",
                (estacion_id, prefijo, rango.resolucion, rango.desde, rango.hasta, rango.desde,
                 vigente_desde, vigente_hasta),
            )  # fmt: skip
        else:  # ya existe: se reactiva SIN tocar `siguiente`
            conn.execute("UPDATE numeracion SET activa = TRUE WHERE id = %s", (fila[0],))
        auditar(conn, "NUMERACION_PRODUCCION_ACTIVADA", "estacion", estacion_id, detalle={"prefijo": prefijo})
    print(f"Numeración de producción {prefijo} {rango.desde}-{rango.hasta} activa.")
    return 0


def _guardar_clave_tecnica(
    entorno: Mapping[str, str], nit: str, prefijo: str, archivo: str, cliente_dian: ClienteDian | None
) -> int:
    """Escribe `DIAN_CLAVE_TECNICA=<clave>` (la que la DIAN tiene para el rango) en un archivo NUEVO con permisos 0600.
    La clave nunca se imprime ni se registra: es secreta."""
    if os.path.lexists(archivo):
        _err("el archivo ya existe: no se sobrescribe nada (indique uno nuevo)")
        return 1
    r, motivo = _consultar_rangos(entorno, nit, cliente_dian)
    if r is None:
        _err(motivo)
        return 1
    rango = next((x for x in r.rangos if x.prefijo == prefijo), None)
    if rango is None:
        _err(f"la DIAN no tiene un rango de producción con el prefijo {prefijo}")
        return 1
    if not rango.clave_tecnica:
        _err(f"la DIAN no devolvió clave técnica para el rango {prefijo}: no se escribió nada")
        return 1
    try:
        fd = os.open(archivo, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # EXCL: ni sigue ni pisa un archivo previo
    except OSError as e:
        _err(f"no se pudo crear el archivo ({e.strerror})")
        return 1
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"DIAN_CLAVE_TECNICA={rango.clave_tecnica}\n")
    print(
        f"Clave técnica del rango {prefijo} escrita en {archivo} (no se muestra). "
        "Copie esa línea al .env y después BORRE el archivo."
    )
    return 0


def _verificar_produccion(
    conn: Conexion, entorno: Mapping[str, str], estacion_id: int, nit: str, cliente_dian: ClienteDian | None
) -> int:
    revisiones: list[tuple[bool, str]] = []

    def revisar(ok: bool, texto: str) -> None:
        revisiones.append((ok, texto))

    faltan = [
        n for n in ("DIAN_SOFTWARE_ID", "DIAN_SOFTWARE_PIN", "DIAN_CLAVE_TECNICA") if not entorno.get(n, "").strip()
    ]
    revisar(not faltan, "variables DIAN presentes" if not faltan else f"faltan variables: {', '.join(faltan)}")
    revisar(
        not entorno.get("DIAN_TEST_SET_ID", "").strip(),
        "DIAN_TEST_SET_ID vacío" if not entorno.get("DIAN_TEST_SET_ID", "").strip()
        else "DIAN_TEST_SET_ID debe estar vacío en producción (es del set de pruebas)",
    )  # fmt: skip
    try:
        cert = certificado_desde_entorno(entorno)
    except FirmaError as e:  # el mensaje nunca lleva la clave
        revisar(False, f"certificado: {e}")
    else:
        ahora = datetime.now(UTC)
        vence = cert.cert.not_valid_after_utc
        vigente = cert.cert.not_valid_before_utc <= ahora <= vence
        revisar(vigente, f"certificado vigente, vence en {(vence - ahora).days} día(s)" if vigente
                else f"certificado fuera de vigencia (vence {vence:%Y-%m-%d})")  # fmt: skip
        de_prueba = es_certificado_de_prueba(cert)
        revisar(not de_prueba, "certificado de la empresa" if not de_prueba else "el certificado es de PRUEBA")
    activa = conn.execute(
        """SELECT prefijo, ambiente, siguiente, hasta, vigente_desde, vigente_hasta FROM numeracion
           WHERE estacion_id = %s AND activa""",
        (estacion_id,),
    ).fetchone()
    prefijo_activo = None
    if activa is None or activa[1] != "1":
        revisar(False, "no hay una numeración activa de producción (ambiente 1): use configurar-produccion")
    else:
        prefijo_activo, _, siguiente, hasta, desde_vig, hasta_vig = activa
        hoy = ahora_local().date()
        revisar(desde_vig <= hoy <= hasta_vig, f"numeración {prefijo_activo} vigente hasta {hasta_vig}"
                if desde_vig <= hoy <= hasta_vig else f"numeración {prefijo_activo} fuera de vigencia")  # fmt: skip
        revisar(siguiente <= hasta, f"numeración {prefijo_activo}: quedan {hasta - siguiente + 1} consecutivos"
                if siguiente <= hasta else f"numeración {prefijo_activo} agotada")  # fmt: skip
    r, motivo = _consultar_rangos(entorno, nit, cliente_dian)
    if r is None:
        revisar(False, f"GetNumberingRange: {motivo}")
    else:
        revisar(True, "GetNumberingRange responde")
        if prefijo_activo is not None:
            esta = any(x.prefijo == prefijo_activo for x in r.rangos)
            revisar(esta, f"la DIAN tiene el prefijo {prefijo_activo}" if esta
                    else f"la DIAN no devolvió el prefijo {prefijo_activo}")  # fmt: skip
    smtp = bool(entorno.get("ESTACION_SMTP_USUARIO", "").strip())
    revisar(smtp, "SMTP configurado" if smtp else "falta ESTACION_SMTP_USUARIO (el correo saldría sin cuenta)")
    pendientes = _pendientes_de_habilitacion(conn)
    revisar(
        pendientes == 0,
        "sin documentos de habilitación pendientes en la cola" if not pendientes
        else f"{pendientes} documento(s) de habilitación (ambiente 2) pendientes en la cola de envíos",
    )  # fmt: skip
    for ok, texto in revisiones:
        print(f"{'✔' if ok else '✘'} {texto}")
    return 0 if all(ok for ok, _ in revisiones) else 1


def _correo_emisor(
    conn: Conexion, estacion_id: int, actual: str, nuevo: str | None, confirmar: Callable[[str], str]
) -> int:
    print(f"Correo del emisor: {actual}")
    if nuevo is None:
        return 0
    nuevo = nuevo.strip().lower()
    if not PATRON_CORREO.fullmatch(nuevo):
        _err("el correo nuevo no es válido: no se cambió nada")
        return 2
    if confirmar(f"Cambiar el correo del emisor a {nuevo}? Escriba SI: ").strip() != "SI":
        _err("cancelado: no se cambió nada")
        return 1
    with conn.transaction():
        conn.execute("UPDATE estacion SET email = %s WHERE id = %s", (nuevo, estacion_id))
        auditar(conn, "EMISOR_CORREO_CAMBIADO", "estacion", estacion_id)  # sin el correo: es dato personal
    print("Correo del emisor actualizado.")
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
