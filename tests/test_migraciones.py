"""Migraciones de Alembic contra PostgreSQL real: subir y bajar desde cero, coherencia de los CHECK con el
código (una sola fuente de verdad) y restricciones que impiden duplicados y datos inválidos.

Todos los datos son sintéticos (NIT, nombres y correos de mentira).
"""

from __future__ import annotations

import random
import re
import uuid
from typing import Any

import pytest
from psycopg import errors

from conftest import BaseDePrueba, migrar
from estacion.facturacion.documentos import TIPOS_DOCUMENTO, calcular_dv
from estacion.facturacion.modelos import EstadoFactura, EstadoFacturacion, EstadoOutbox, TipoTrabajo
from estacion.modelos import FormaPago, PlacaTipo, Rol, Severidad

pytestmark = pytest.mark.bd

TABLAS_NUEVAS = {"estacion", "numeracion", "usuario", "venta_manual", "factura", "outbox", "auditoria"}
TABLAS_INICIALES = {
    "surtidor", "producto", "importacion", "turno", "cliente", "vehiculo", "despacho", "anomalia", "fila_rechazada",
}  # fmt: skip


def tablas(bd: BaseDePrueba) -> set[str]:
    return {f[0] for f in bd.consultar("SELECT tablename FROM pg_tables WHERE schemaname = %s", (bd.esquema,))}


# ------------------------------------------------------------------------------------------ subir y bajar
def test_upgrade_desde_cero_downgrade_a_base_y_otra_vez_upgrade(esquema_vacio):
    bd = esquema_vacio
    migrar(bd.dsn)
    assert tablas(bd) == TABLAS_INICIALES | TABLAS_NUEVAS | {"alembic_version"}
    assert bd.valor("SELECT version_num FROM alembic_version") == "0002"

    migrar(bd.dsn, "base", bajar=True)
    assert tablas(bd) == {"alembic_version"}
    assert bd.valor("SELECT count(*) FROM pg_views WHERE schemaname = %s", (bd.esquema,)) == 0
    assert bd.valor(
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = %s",
        (bd.esquema,),
    ) == 0  # fmt: skip

    migrar(bd.dsn)
    assert bd.valor("SELECT version_num FROM alembic_version") == "0002"


def test_bajar_solo_la_0002_deja_el_esquema_inicial(esquema_vacio):
    bd = esquema_vacio
    migrar(bd.dsn)
    migrar(bd.dsn, "0001", bajar=True)
    assert tablas(bd) == TABLAS_INICIALES | {"alembic_version"}
    columnas = {f[0] for f in bd.consultar(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'cliente'",
        (bd.esquema,))}  # fmt: skip
    assert "razon_social" in columnas and "apellidos" not in columnas


# ------------------------------------------------------------------------------------------ fuente de verdad
def valores_check(bd: BaseDePrueba, tabla: str, columna: str) -> set[str]:
    """Valores permitidos por el CHECK ... IN (...) de una sola columna."""
    definiciones = [f[0] for f in bd.consultar(
        """SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
           JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
           WHERE c.contype = 'c' AND c.conrelid = %s::regclass AND a.attname = %s
             AND cardinality(c.conkey) = 1""",
        (f"{bd.esquema}.{tabla}", columna))]  # fmt: skip
    con_lista = [d for d in definiciones if "ARRAY[" in d]
    assert len(con_lista) == 1, f"{tabla}.{columna}: se esperaba un CHECK con lista, hay {definiciones}"
    return set(re.findall(r"'([^']*)'::text", con_lista[0]))


@pytest.mark.parametrize(
    ("tabla", "columna", "esperado"),
    [
        ("despacho", "estado_facturacion", set(EstadoFacturacion)),
        ("venta_manual", "estado_facturacion", set(EstadoFacturacion)),
        ("cliente", "tipo_documento", TIPOS_DOCUMENTO),
        ("factura", "estado", set(EstadoFactura)),
        ("outbox", "estado", set(EstadoOutbox)),
        ("outbox", "tipo", set(TipoTrabajo)),
        ("usuario", "rol", set(Rol)),
        ("despacho", "forma_pago", set(FormaPago)),
        ("despacho", "placa_tipo", set(PlacaTipo)),
        ("anomalia", "severidad", set(Severidad)),
        # factura y venta manual: solo lo que se puede facturar (OTRO es "desconocido en el archivo")
        ("factura", "forma_pago", {FormaPago.CONTADO, FormaPago.CREDITO}),
        ("venta_manual", "forma_pago", {FormaPago.CONTADO, FormaPago.CREDITO}),
    ],
)
def test_los_check_de_la_base_coinciden_con_el_codigo(bd, tabla, columna, esperado):
    assert valores_check(bd, tabla, columna) == {str(v) for v in esperado}


def test_dv_nit_de_la_base_es_el_mismo_de_python(bd):
    azar = random.Random(20260930)
    nits = ["900123456", "800197268", "860034313", "1", "12345678901"] + [
        str(azar.randrange(10_000_000, 9_999_999_999)) for _ in range(300)
    ]
    en_bd = dict(bd.consultar("SELECT n, dv_nit(n) FROM unnest(%s::text[]) AS n", (nits,)))
    assert en_bd == {n: calcular_dv(n) for n in nits}


@pytest.fixture
def emision(datos):
    """Lo mínimo para una factura: estación, numeración, usuario, cliente y un despacho."""
    estacion = datos.estacion()
    return dict(
        datos=datos,
        numeracion=datos.numeracion(estacion),
        usuario=datos.usuario(),
        cliente=datos.cliente(),
        despacho=datos.despacho(),
    )


def factura(e: dict[str, Any], **cambios: Any) -> int:
    campos = dict(
        clave_emision=uuid.uuid4().hex, despacho_id=e["despacho"], cliente_id=e["cliente"], usuario_id=e["usuario"],
        numeracion_id=e["numeracion"], prefijo="SETP", consecutivo=1, forma_pago="CONTADO", medio_pago="EFECTIVO",
        total=29757, fecha_emision="2026-09-30 10:05",
    )  # fmt: skip
    return e["datos"].insertar("factura", **{**campos, **cambios})


# ------------------------------------------------------------------------------------------ estación
def test_estacion_valida_se_guarda(datos):
    assert datos.estacion() > 0


@pytest.mark.parametrize(
    ("cambio", "error"),
    [
        ({"dv": "3"}, errors.CheckViolation),  # el DV de 900123456 es 8
        ({"nit": "90012345A"}, errors.CheckViolation),
        ({"responsabilidades": None}, errors.NotNullViolation),  # nada por defecto: fallar cerrado
        ({"responsabilidades": []}, errors.CheckViolation),
        ({"responsabilidades": ["R99PN"]}, errors.CheckViolation),
        ({"actividades_ciiu": ["47"]}, errors.CheckViolation),
        ({"responsable_iva": None}, errors.NotNullViolation),
        ({"codigo_municipio_dane": "1500"}, errors.CheckViolation),
        ({"codigo_departamento": "25"}, errors.CheckViolation),  # 15001 es de Boyacá (15)
        ({"direccion_establecimiento": "  "}, errors.CheckViolation),
        ({"email": "sin-arroba"}, errors.CheckViolation),
    ],
)
def test_estacion_rechaza_datos_fiscales_invalidos_o_faltantes(datos, cambio, error):
    with pytest.raises(error):
        datos.estacion(**cambio)


def test_solo_una_estacion_activa(datos):
    datos.estacion()
    with pytest.raises(errors.UniqueViolation):
        datos.estacion(razon_social="OTRA ESTACION S A S")
    datos.estacion(activa=False)  # una inactiva (histórica) sí se permite


# ------------------------------------------------------------------------------------------ numeración
@pytest.mark.parametrize(
    "cambio",
    [
        {"hasta": 0},  # hasta < desde
        {"desde": 0},
        {"siguiente": 5002},  # fuera de [desde, hasta + 1]
        {"siguiente": 0},
        {"vigente_hasta": "2025-12-31"},
        {"prefijo": "setp"},
        {"prefijo": "SETPX"},
    ],
)
def test_numeracion_rechaza_rangos_invalidos(datos, cambio):
    estacion = datos.estacion()
    with pytest.raises(errors.CheckViolation):
        datos.numeracion(estacion, **cambio)


def test_numeracion_agotada_es_hasta_mas_uno_y_solo_una_activa(datos):
    estacion = datos.estacion()
    datos.numeracion(estacion, siguiente=5001)
    with pytest.raises(errors.UniqueViolation):
        datos.numeracion(estacion, prefijo="FE", numero_resolucion="18760000002")
    datos.numeracion(estacion, prefijo="FE", numero_resolucion="18760000002", activa=False)


# ------------------------------------------------------------------------------------------ usuarios y clientes
@pytest.mark.parametrize("pin", ["1234", "sha256:abcd", "$1$md5viejo$", ""])
def test_usuario_no_acepta_pin_en_claro_ni_hashes_debiles(datos, pin):
    with pytest.raises(errors.CheckViolation):
        datos.usuario(pin_hash=pin)


def test_usuario_acepta_argon2_y_bcrypt_y_el_login_es_unico(datos):
    datos.usuario(usuario="ana.perez")
    datos.usuario(pin_hash="$2b$12$" + "a" * 53)
    with pytest.raises(errors.UniqueViolation):
        datos.usuario(usuario="ana.perez")
    with pytest.raises(errors.CheckViolation):
        datos.usuario(rol="CAJERO")


@pytest.mark.parametrize(
    ("cambio", "error"),
    [
        ({"autoriza_tratamiento": False}, errors.CheckViolation),  # Ley 1581: sin autorización no hay cliente
        ({"fecha_autorizacion": None}, errors.CheckViolation),
        ({"autoriza_tratamiento": None}, errors.NotNullViolation),
        ({"nombres": " "}, errors.CheckViolation),
        ({"tipo_documento": "TI"}, errors.CheckViolation),
        ({"tipo_documento": "NIT", "numero_documento": "900123456", "digito_verificacion": "3"}, errors.CheckViolation),
        ({"tipo_documento": "NIT", "numero_documento": "900123456", "digito_verificacion": None},
         errors.CheckViolation),
        ({"numero_documento": "12-34"}, errors.CheckViolation),
    ],
)  # fmt: skip
def test_cliente_rechaza_datos_invalidos(datos, cambio, error):
    with pytest.raises(error):
        datos.cliente(**cambio)


def test_cliente_nit_con_dv_correcto_y_documento_unico(datos):
    datos.cliente(tipo_documento="NIT", numero_documento="900123456", digito_verificacion="8",
                  nombres="TRANSPORTES DE PRUEBA S A S", apellidos="")  # fmt: skip
    with pytest.raises(errors.UniqueViolation):
        datos.cliente(tipo_documento="NIT", numero_documento="900123456", digito_verificacion="8")


# ------------------------------------------------------------------------------------------ facturas
def test_factura_valida_y_su_numero(emision):
    f = factura(emision, consecutivo=215802)
    assert emision["datos"].uno("SELECT numero FROM factura WHERE id = %s", (f,)) == "SETP215802"


def test_un_despacho_no_puede_tener_dos_facturas(emision):
    factura(emision, consecutivo=1)
    with pytest.raises(errors.UniqueViolation, match="despacho_id"):
        factura(emision, consecutivo=2)


def test_un_consecutivo_no_se_repite(emision):
    factura(emision, consecutivo=7)
    otro = emision["datos"].despacho()
    with pytest.raises(errors.UniqueViolation, match="factura_numero_unico"):
        factura(emision, consecutivo=7, despacho_id=otro)


def test_la_clave_de_emision_no_se_repite(emision):
    factura(emision, clave_emision="clave0123456789abcdef")
    otro = emision["datos"].despacho()
    with pytest.raises(errors.UniqueViolation, match="clave_emision"):
        factura(emision, clave_emision="clave0123456789abcdef", consecutivo=2, despacho_id=otro)


def test_una_venta_manual_no_puede_tener_dos_facturas(emision):
    venta = emision["datos"].venta_manual(emision["usuario"])
    factura(emision, despacho_id=None, venta_manual_id=venta, consecutivo=1)
    with pytest.raises(errors.UniqueViolation, match="venta_manual_id"):
        factura(emision, despacho_id=None, venta_manual_id=venta, consecutivo=2)


@pytest.mark.parametrize("origen", ["ninguno", "ambos"])
def test_la_factura_tiene_exactamente_un_origen(emision, origen):
    if origen == "ninguno":
        cambio = {"despacho_id": None}
    else:
        cambio = {"venta_manual_id": emision["datos"].venta_manual(emision["usuario"])}
    with pytest.raises(errors.CheckViolation, match="factura_origen_check"):
        factura(emision, **cambio)


def test_el_prefijo_debe_ser_el_de_su_numeracion(emision):
    with pytest.raises(errors.ForeignKeyViolation, match="factura_numeracion_fk"):
        factura(emision, prefijo="OTRO")


@pytest.mark.parametrize(
    ("cambio", "restriccion"),
    [
        ({"estado": "FACTURADO"}, "factura_facturado_check"),  # facturada sin CUFE
        ({"estado": "RECHAZADO"}, "factura_rechazado_check"),  # rechazada sin el error
        ({"estado": "ANULADA"}, "factura_estado_check"),
        ({"cufe": "no-es-sha384"}, "factura_cufe_check"),
        ({"total": 0}, "factura_total_check"),
        ({"medio_pago": "CHEQUE"}, "factura_medio_pago_check"),
        ({"clave_emision": "con-guion-0123456789"}, "factura_clave_emision_check"),
    ],
)
def test_factura_rechaza_estados_incompletos_y_valores_invalidos(emision, cambio, restriccion):
    with pytest.raises(errors.CheckViolation, match=restriccion):
        factura(emision, **cambio)


def test_factura_facturada_con_cufe(emision):
    factura(emision, estado="FACTURADO", cufe="ab" * 48)


def test_una_venta_manual_se_enlaza_a_un_solo_despacho(emision):
    d = emision["datos"]
    d.venta_manual(emision["usuario"], despacho_id=emision["despacho"])
    with pytest.raises(errors.UniqueViolation):
        d.venta_manual(emision["usuario"], despacho_id=emision["despacho"])


# ------------------------------------------------------------------------------------------ outbox y auditoría
def test_outbox_un_solo_trabajo_vivo_por_factura_y_tipo(emision):
    d = emision["datos"]
    f = factura(emision)
    trabajo = d.insertar("outbox", factura_id=f, tipo="ENVIAR_DIAN")
    with pytest.raises(errors.UniqueViolation, match="ux_outbox_un_trabajo_vivo"):
        d.insertar("outbox", factura_id=f, tipo="ENVIAR_DIAN")
    d.insertar("outbox", factura_id=f, tipo="CONSULTAR_DIAN")  # otro tipo sí

    with pytest.raises(errors.CheckViolation, match="outbox_terminado_check"):
        d.conn.execute("UPDATE outbox SET estado = 'HECHO' WHERE id = %s", (trabajo,))  # sin terminado_en
    d.conn.execute("UPDATE outbox SET estado = 'HECHO', terminado_en = now() WHERE id = %s", (trabajo,))
    d.insertar("outbox", factura_id=f, tipo="ENVIAR_DIAN")  # terminado el anterior, se puede encolar otro


def test_la_auditoria_solo_admite_agregar(datos):
    u = datos.usuario()
    a = datos.insertar("auditoria", usuario_id=u, accion="FACTURA_SOLICITADA", entidad="factura", entidad_id="1")
    for sql in (
        "UPDATE auditoria SET accion = 'OTRA' WHERE id = %s",
        "DELETE FROM auditoria WHERE id = %s",
    ):
        with pytest.raises(errors.RaiseException, match="solo admite agregar"):
            datos.conn.execute(sql, (a,))
    with pytest.raises(errors.RaiseException, match="TRUNCATE"):
        datos.conn.execute("TRUNCATE auditoria")
    assert datos.uno("SELECT count(*) FROM auditoria") == 1
