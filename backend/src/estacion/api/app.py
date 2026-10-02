"""Aplicación FastAPI del banco de pruebas: `python -m estacion.api` (ver `__main__.py`).

`crear_app()` lee los ajustes del entorno y falla al arrancar si falta algo (DATABASE_URL, JWT_SECRETO).
La emisión usa las variables DIAN_* del entorno: sin ellas, emitir responde 503 (fallar cerrado).
Hoy el worker solo tiene el proveedor SIMULADO: nada se envía a la DIAN.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool, PoolTimeout

from ..facturacion.documentos import ClienteInvalido
from ..facturacion.xml_factura import FacturaNoSoportada, NumeracionInvalida
from ..servicios.clientes import ClienteYaExiste
from ..servicios.configuracion import ConfiguracionIncompleta
from ..servicios.emision import ClaveReutilizada, ClienteNoDisponible, OrigenNoDisponible, ServicioEmision
from ..servicios.importacion_csv import ArchivoInvalido
from ..servicios.numeracion import NumeracionNoDisponible
from ..servicios.vehiculos import VehiculoInvalido, VehiculoYaExiste
from ..servicios.ventas import VentaInvalida
from .ajustes import Ajustes
from .esquemas import Error, SaludSalida
from .limites import LimitadorTasa
from .rutas import admin, clientes, facturas, pista, reportes, sesion

log = logging.getLogger(__name__)

# Excepción de dominio -> (código HTTP, prefijo del mensaje). El mensaje del dominio no lleva secretos ni datos
# personales (regla de los servicios), así que se puede mostrar.
ERRORES: dict[type[Exception], tuple[int, str]] = {
    OrigenNoDisponible: (status.HTTP_409_CONFLICT, ""),
    ClaveReutilizada: (status.HTTP_409_CONFLICT, ""),
    ClienteYaExiste: (status.HTTP_409_CONFLICT, ""),
    ClienteNoDisponible: (status.HTTP_422_UNPROCESSABLE_CONTENT, ""),
    VentaInvalida: (status.HTTP_422_UNPROCESSABLE_CONTENT, ""),
    VehiculoInvalido: (status.HTTP_422_UNPROCESSABLE_CONTENT, ""),
    VehiculoYaExiste: (status.HTTP_409_CONFLICT, ""),
    ArchivoInvalido: (status.HTTP_422_UNPROCESSABLE_CONTENT, "archivo inválido: "),
    FacturaNoSoportada: (status.HTTP_422_UNPROCESSABLE_CONTENT, "caso todavía no soportado: "),
    NumeracionInvalida: (status.HTTP_422_UNPROCESSABLE_CONTENT, ""),
    ConfiguracionIncompleta: (status.HTTP_503_SERVICE_UNAVAILABLE, "no se puede facturar: "),
    NumeracionNoDisponible: (status.HTTP_503_SERVICE_UNAVAILABLE, "no se puede facturar: "),
}


def crear_app(
    ajustes: Ajustes | None = None,
    *,
    entorno_dian: Mapping[str, str] | None = None,
) -> FastAPI:
    aj = ajustes or Ajustes.desde_entorno()

    @asynccontextmanager
    async def ciclo(app: FastAPI) -> AsyncIterator[None]:
        pool = ConnectionPool(aj.database_url, kwargs={"autocommit": True}, min_size=1, max_size=10, open=False)
        pool.open(wait=True, timeout=10)
        app.state.pool = pool
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(
        title="Estación de servicio — banco de pruebas",
        version="0.1.0",
        description="API del sistema que reemplaza a Nexus. **Banco de pruebas: no envía nada a la DIAN.** "
        "Dinero y galones viajan como texto decimal.",
        lifespan=ciclo,
    )
    app.state.ajustes = aj
    if aj.captcha is None:
        log.warning("registro público SIN captcha (ESTACION_CAPTCHA_PROVEEDOR vacío): solo para el banco de pruebas")
    app.state.emision = ServicioEmision(entorno=entorno_dian)
    app.state.limite_login = LimitadorTasa(aj.login_por_minuto)
    app.state.limite_registro = LimitadorTasa(aj.registro_por_minuto)

    for tipo, (codigo, prefijo) in ERRORES.items():
        app.add_exception_handler(tipo, _manejador(codigo, prefijo))
    app.add_exception_handler(ClienteInvalido, _cliente_invalido)

    for r in (sesion.router, pista.router, clientes.router, facturas.router, reportes.router, admin.router):
        app.include_router(r)

    @app.get(
        "/salud",
        response_model=SaludSalida,
        responses={503: {"model": Error}},
        tags=["salud"],
        summary="La API y la base responden (y la base tiene las migraciones)",
    )
    def salud(request: Request):
        try:
            with request.app.state.pool.connection(timeout=5) as conn:
                fila = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except psycopg.errors.UndefinedTable:
            return _no_disponible("la base no tiene las migraciones: corra `make migrate`")
        except (psycopg.Error, PoolTimeout):
            return _no_disponible("no hay conexión con la base de datos")
        return SaludSalida(estado="ok", base_de_datos="ok", migracion=None if fila is None else fila[0])

    return app


def _no_disponible(mensaje: str) -> JSONResponse:
    return JSONResponse({"detail": mensaje}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


def _manejador(codigo: int, prefijo: str):
    async def manejar(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": f"{prefijo}{exc}"}, status_code=codigo)

    return manejar


async def _cliente_invalido(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ClienteInvalido)
    return JSONResponse(
        {"detail": {"mensaje": "datos del cliente inválidos", "problemas": exc.problemas}},
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )
