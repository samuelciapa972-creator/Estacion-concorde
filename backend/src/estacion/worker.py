"""Worker de la cola de envíos a la DIAN: `python -m estacion.worker [--una-vez] [--espera 2]`.

Lee DATABASE_URL y DIAN_AMBIENTE ("2" habilitación, "1" producción; sin él no arranca, tampoco con el simulado: la
cola solo procesa documentos de SU ambiente). `--proveedor simulado` (por defecto) no envía nada a la DIAN;
`--proveedor dian` envía al ambiente que diga DIAN_AMBIENTE y, en habilitación con DIAN_TEST_SET_ID,
usa el set de pruebas (envío asíncrono). En producción se rechaza el proveedor simulado, DIAN_TEST_SET_ID y el
certificado de prueba.
Firma con DIAN_CERTIFICADO_RUTA / DIAN_CERTIFICADO_CLAVE (sin ellos no envía nada; las facturas esperan en la cola) y
entrega el ZIP por SMTP según ESTACION_SMTP_* (por defecto Mailpit en localhost:1025; ver .env.example).
Varios workers pueden correr a la vez: `FOR UPDATE SKIP LOCKED` evita que dos tomen el mismo trabajo.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Mapping
from typing import Any

import psycopg

from .entrega.correo import ConListaBlanca, EnviadorSmtp, enviador_desde_entorno
from .facturacion.dian.cliente import ClienteDian
from .facturacion.dian.proveedor import ProveedorDian
from .facturacion.dian.sobre import url_servicio
from .facturacion.firma import Certificado, FirmaError, certificado_desde_entorno, es_certificado_de_prueba
from .facturacion.proveedor import ProveedorFacturacion, ProveedorSimulado
from .servicios.configuracion import AMBIENTES_PERMITIDOS, ConfiguracionIncompleta
from .servicios.outbox import Recursos, procesar_pendientes

log = logging.getLogger("estacion.worker")


def _ambiente(entorno: Mapping[str, str]) -> str:
    ambiente = entorno.get("DIAN_AMBIENTE", "").strip()
    if ambiente not in AMBIENTES_PERMITIDOS:
        raise ConfiguracionIncompleta(
            f'DIAN_AMBIENTE={ambiente!r} no permitido: use "1" (producción) o "2" (habilitación); el worker no '
            "arranca sin él, ni con el proveedor simulado"
        )
    return ambiente


def construir_proveedor(
    nombre: str, conn: psycopg.Connection[Any], entorno: Mapping[str, str], certificado: Certificado | None
) -> ProveedorFacturacion:
    """El proveedor que usa la cola. Con `dian`, falla cerrado si falta algo (mensajes sin secretos)."""
    ambiente = _ambiente(entorno)
    if nombre == "simulado":
        if ambiente == "1":
            raise ConfiguracionIncompleta("en producción no se puede usar el proveedor simulado")
        return ProveedorSimulado()
    if certificado is None:
        raise ConfiguracionIncompleta("sin certificado de firma no se puede hablar con la DIAN")
    test_set_id = entorno.get("DIAN_TEST_SET_ID", "").strip() or None
    if ambiente == "1":
        if test_set_id:
            raise ConfiguracionIncompleta("en producción no se usa DIAN_TEST_SET_ID (es del set de pruebas)")
        if es_certificado_de_prueba(certificado):
            raise ConfiguracionIncompleta("en producción no se puede firmar con un certificado de prueba")
    fila = conn.execute("SELECT nit FROM estacion WHERE activa").fetchone()
    if fila is None:
        raise ConfiguracionIncompleta("no hay una estación (emisor) activa configurada")
    return ProveedorDian(
        ClienteDian(url_servicio(ambiente), certificado),
        nit_emisor=fila[0],
        test_set_id=test_set_id,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m estacion.worker", description=__doc__.splitlines()[0])
    parser.add_argument("--una-vez", action="store_true", help="procesa lo que haya en la cola y termina")
    parser.add_argument("--espera", type=float, default=2.0, help="segundos entre revisiones de la cola")
    parser.add_argument("--proveedor", choices=["simulado", "dian"], default="simulado")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        log.error("falta la variable de entorno DATABASE_URL")
        return 2
    try:
        ambiente = _ambiente(os.environ)
    except ConfiguracionIncompleta as e:
        log.error("%s", e)
        return 2
    try:
        enviador = enviador_desde_entorno()
    except ValueError as e:
        log.error("%s", e)
        return 2
    lista, smtp = "", enviador
    if isinstance(enviador, ConListaBlanca):
        lista, smtp = f", lista blanca de {len(enviador.permitidos)} dirección(es)", enviador.interno
    if isinstance(smtp, EnviadorSmtp):  # solo servidor y modo: nunca usuario, clave ni direcciones
        log.info("correo por %s:%s (%s)%s", smtp.host, smtp.puerto, smtp.seguridad, lista)
    certificado: Certificado | None = None
    error_certificado = ""
    try:
        certificado = certificado_desde_entorno()
    except FirmaError as e:  # el mensaje nunca lleva la clave
        error_certificado = str(e)
        log.error("sin certificado de firma (%s): las facturas esperan en la cola sin enviarse", e)
    recursos = Recursos(certificado, enviador, error_certificado, ambiente)

    with psycopg.connect(dsn, autocommit=True) as conn:
        try:
            proveedor = construir_proveedor(args.proveedor, conn, os.environ, certificado)
        except ConfiguracionIncompleta as e:
            log.error("%s", e)
            return 2
        if isinstance(proveedor, ProveedorDian):
            nombre_ambiente = "PRODUCCIÓN" if ambiente == "1" else "HABILITACIÓN"
            log.warning("proveedor DIAN, ambiente de %s, modo %s", nombre_ambiente, proveedor.modo)
        else:
            log.warning("proveedor SIMULADO: las facturas NO se envían a la DIAN (ambiente %s)", ambiente)
        while True:
            try:
                for hecho in procesar_pendientes(conn, proveedor, recursos=recursos):
                    # Solo identificadores y estados: nada de datos personales en el log.
                    log.info("trabajo %s (%s) factura %s -> %s", hecho.trabajo_id, hecho.tipo,
                             hecho.factura_id, hecho.estado_factura)  # fmt: skip
            except Exception:
                log.exception("error procesando la cola; se reintenta en la próxima vuelta")
                if args.una_vez:
                    return 1
            if args.una_vez:
                return 0
            time.sleep(args.espera)


if __name__ == "__main__":
    sys.exit(main())
