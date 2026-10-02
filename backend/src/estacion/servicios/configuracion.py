"""Configuración fiscal que necesita la emisión: emisor (tabla `estacion`), numeración (tabla `numeracion`)
y los datos del software ante la DIAN (variables de entorno, porque el PIN y la clave técnica son secretos).

Fallar cerrado: si falta cualquier dato, `ConfiguracionIncompleta` y no se emite. Los mensajes nombran la
variable que falta, nunca su valor.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from ..facturacion.configuracion import Emisor, Numeracion, SoftwareDian
from ._bd import Conexion

# Banco de pruebas: solo el ambiente de habilitación de la DIAN. Producción ("1") queda bloqueada en el código.
AMBIENTES_PERMITIDOS = {"2"}


class ConfiguracionIncompleta(Exception):
    """Falta un dato fiscal o de la DIAN: el sistema se niega a emitir."""


@dataclass(frozen=True, slots=True)
class ConfiguracionFiscal:
    estacion_id: int
    emisor: Emisor
    numeracion: Numeracion
    software: SoftwareDian


def _requerida(entorno: Mapping[str, str], nombre: str, faltan: list[str]) -> str:
    valor = entorno.get(nombre, "").strip()
    if not valor:
        faltan.append(nombre)
    return valor


def software_desde_entorno(entorno: Mapping[str, str], nit_emisor: str) -> SoftwareDian:
    faltan: list[str] = []
    ambiente = _requerida(entorno, "DIAN_AMBIENTE", faltan)
    software_id = _requerida(entorno, "DIAN_SOFTWARE_ID", faltan)
    pin = _requerida(entorno, "DIAN_SOFTWARE_PIN", faltan)
    if faltan:
        raise ConfiguracionIncompleta(f"faltan variables de entorno de la DIAN: {', '.join(faltan)}")
    if ambiente not in AMBIENTES_PERMITIDOS:
        raise ConfiguracionIncompleta(
            f'DIAN_AMBIENTE={ambiente!r} no permitido: este banco de pruebas solo usa habilitación ("2")'
        )
    # Software propio: el proveedor tecnológico es la propia empresa (SIN VERIFICAR contra el anexo).
    return SoftwareDian(software_id=software_id, pin=pin, nit_proveedor=nit_emisor, ambiente=ambiente)


def cargar_configuracion(
    conn: Conexion, numeracion_id: int, entorno: Mapping[str, str] | None = None
) -> ConfiguracionFiscal:
    """Arma la configuración para emitir con la numeración `numeracion_id` (la que ya se reservó)."""
    entorno = os.environ if entorno is None else entorno
    fila = conn.execute(
        """SELECT e.id, e.nit, e.razon_social, e.direccion_establecimiento, e.telefono, e.email,
                  e.actividades_ciiu, e.responsabilidades, e.responsable_iva, e.codigo_municipio_dane,
                  e.municipio, e.codigo_departamento, e.departamento, e.nombre_comercial, e.codigo_postal,
                  e.matricula_mercantil,
                  n.prefijo, n.desde, n.hasta, n.numero_resolucion, n.vigente_desde, n.vigente_hasta
           FROM numeracion n JOIN estacion e ON e.id = n.estacion_id
           WHERE n.id = %s AND e.activa""",
        (numeracion_id,),
    ).fetchone()
    if fila is None:
        raise ConfiguracionIncompleta("no hay una estación activa con esa numeración")
    (estacion_id, nit, razon_social, direccion, telefono, email, actividades, responsabilidades, responsable_iva,
     cod_municipio, municipio, cod_depto, depto, nombre_comercial, cod_postal, matricula,
     prefijo, desde, hasta, resolucion, vigente_desde, vigente_hasta) = fila  # fmt: skip

    faltan: list[str] = []
    clave_tecnica = _requerida(entorno, "DIAN_CLAVE_TECNICA", faltan)
    for nombre in ("DIAN_AMBIENTE", "DIAN_SOFTWARE_ID", "DIAN_SOFTWARE_PIN"):  # todas las faltantes de una vez
        _requerida(entorno, nombre, faltan)
    if faltan:
        raise ConfiguracionIncompleta(f"faltan variables de entorno de la DIAN: {', '.join(faltan)}")

    emisor = Emisor(
        nit=nit,
        razon_social=razon_social,
        direccion=direccion,
        telefono=telefono,
        email=email,
        actividades_ciiu=tuple(actividades),
        responsabilidades=";".join(responsabilidades),
        responsable_iva=responsable_iva,
        codigo_municipio=cod_municipio,
        municipio=municipio,
        codigo_departamento=cod_depto,
        departamento=depto,
        nombre_comercial=nombre_comercial,
        codigo_postal=cod_postal,
        matricula_mercantil=matricula,
    )
    numeracion = Numeracion(
        prefijo=prefijo,
        desde=desde,
        hasta=hasta,
        numero_resolucion=resolucion,
        vigente_desde=vigente_desde,
        vigente_hasta=vigente_hasta,
        clave_tecnica=clave_tecnica,
    )
    return ConfiguracionFiscal(estacion_id, emisor, numeracion, software_desde_entorno(entorno, nit))
