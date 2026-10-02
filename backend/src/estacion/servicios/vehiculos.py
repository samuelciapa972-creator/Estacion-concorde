"""Vehículos (placas) y equipos de obra de los clientes: alta, listado y baja lógica.

El identificador se normaliza igual que la placa que digita el operador en el surtidor (`normalizar_placa`), así
los despachos se pueden asociar al vehículo. Un valor que en el surtidor sería "sin dato" ("0", "1"...) no es un
vehículo. v1: una placa pertenece a un solo cliente a la vez (restricción única de la migración 0001).
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import errors

from ..modelos import PlacaTipo
from ..normalizacion import normalizar_placa
from ._bd import Conexion, auditar, exigir_autocommit


class VehiculoInvalido(ValueError):
    pass


class VehiculoYaExiste(Exception):
    def __init__(self, vehiculo_id: int):
        super().__init__("esa placa o equipo ya está registrado")
        self.vehiculo_id = vehiculo_id


@dataclass(frozen=True, slots=True)
class VehiculoRegistrado:
    id: int
    identificador: str
    tipo: str  # PLACA | EQUIPO_TEXTO
    cliente_id: int
    cliente_nombre: str
    descripcion: str | None
    activo: bool


_SELECT = """SELECT v.id, v.identificador, v.tipo, v.cliente_id,
                    CASE WHEN c.tipo_documento IN ('NIT', 'NIT_EXTERIOR') THEN c.nombres
                         ELSE btrim(c.nombres || ' ' || c.apellidos) END,
                    v.descripcion, v.activo
             FROM vehiculo v JOIN cliente c ON c.id = v.cliente_id"""


def identificador(texto: str) -> tuple[str, str]:
    """(identificador normalizado, tipo). Lanza VehiculoInvalido si no identifica nada.

    >>> identificador("gzy-848")
    ('GZY848', 'PLACA')
    >>> identificador("Bobcat")
    ('BOBCAT', 'EQUIPO_TEXTO')
    """
    tipo, ref = normalizar_placa(texto)
    if tipo is PlacaTipo.SIN_PLACA or ref is None:
        raise VehiculoInvalido("la placa está vacía o es un relleno ('0', '1'...)")
    if len(ref) > 40:
        raise VehiculoInvalido("la placa o el nombre del equipo es demasiado largo (máximo 40)")
    return ref, tipo.value


def obtener(conn: Conexion, vehiculo_id: int) -> VehiculoRegistrado | None:
    fila = conn.execute(f"{_SELECT} WHERE v.id = %s", (vehiculo_id,)).fetchone()
    return None if fila is None else VehiculoRegistrado(*fila)


def registrar(
    conn: Conexion, texto: str, cliente_id: int, *, descripcion: str | None = None, usuario_id: int
) -> VehiculoRegistrado:
    exigir_autocommit(conn)
    ref, tipo = identificador(texto)
    desc = (descripcion or "").strip() or None
    try:
        with conn.transaction():
            activo = conn.execute("SELECT activo FROM cliente WHERE id = %s FOR SHARE", (cliente_id,)).fetchone()
            if activo is None or not activo[0]:
                raise VehiculoInvalido("el cliente no existe o está inactivo")
            fila = conn.execute(
                """INSERT INTO vehiculo (identificador, tipo, cliente_id, descripcion) VALUES (%s, %s, %s, %s)
                   RETURNING id""",
                (ref, tipo, cliente_id, desc),
            ).fetchone()
            assert fila is not None
            auditar(conn, "VEHICULO_REGISTRADO", "vehiculo", fila[0], usuario_id=usuario_id,
                    detalle={"tipo": tipo, "cliente_id": cliente_id})  # fmt: skip
    except errors.UniqueViolation:
        existente = conn.execute("SELECT id FROM vehiculo WHERE identificador = %s", (ref,)).fetchone()
        assert existente is not None
        raise VehiculoYaExiste(existente[0]) from None
    v = obtener(conn, fila[0])
    assert v is not None
    return v


def listar(
    conn: Conexion,
    *,
    texto: str | None = None,
    cliente_id: int | None = None,
    activo: bool | None = None,
    limite: int = 50,
    desplazamiento: int = 0,
) -> tuple[list[VehiculoRegistrado], int]:
    ref = None
    if texto and texto.strip():
        tipo, ref = normalizar_placa(texto)
        ref = ref or texto.strip().upper()
    filtro = """WHERE (%(ref)s::text IS NULL OR v.identificador LIKE '%%' || %(ref)s || '%%')
                  AND (%(cliente)s::bigint IS NULL OR v.cliente_id = %(cliente)s)
                  AND (%(activo)s::boolean IS NULL OR v.activo = %(activo)s)"""
    p = {"ref": ref, "cliente": cliente_id, "activo": activo, "limite": limite, "desplazamiento": desplazamiento}
    total = conn.execute(f"SELECT count(*) FROM vehiculo v {filtro}", p).fetchone()
    filas = conn.execute(
        f"{_SELECT} {filtro} ORDER BY v.identificador LIMIT %(limite)s OFFSET %(desplazamiento)s", p
    ).fetchall()
    return [VehiculoRegistrado(*f) for f in filas], int(total[0]) if total else 0


def cambiar_activo(conn: Conexion, vehiculo_id: int, activo: bool, *, usuario_id: int) -> VehiculoRegistrado | None:
    exigir_autocommit(conn)
    with conn.transaction():
        fila = conn.execute("UPDATE vehiculo SET activo = %s WHERE id = %s RETURNING id", (activo, vehiculo_id))
        if fila.fetchone() is None:
            return None
        auditar(conn, "VEHICULO_ACTIVADO" if activo else "VEHICULO_DESACTIVADO", "vehiculo", vehiculo_id,
                usuario_id=usuario_id)  # fmt: skip
    return obtener(conn, vehiculo_id)
