"""Clientes: búsqueda por documento y registro con autorización de tratamiento de datos (Ley 1581 de 2012).

Normalización: nombres y apellidos en MAYÚSCULAS sin espacios repetidos, correo en minúsculas. Para un NIT,
`nombres` es la RAZÓN SOCIAL y es lo que se muestra siempre (la falla actual de Nexus es no mostrarla).
Si el NIT llega sin dígito de verificación, se calcula; si llega con uno, se verifica.

La auditoría guarda el tipo de documento y el canal, nunca el número, el nombre ni el correo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from psycopg import errors

from ..facturacion.documentos import ClienteInvalido, calcular_dv, validar_cliente
from ..facturacion.modelos import Cliente
from ._bd import Conexion, auditar, exigir_autocommit

_ESPACIOS = re.compile(r"\s+")


class ClienteYaExiste(Exception):
    def __init__(self, cliente_id: int):
        super().__init__("ya existe un cliente con ese tipo y número de documento")
        self.cliente_id = cliente_id


def nombre_para_mostrar(tipo_documento: str, nombres: str, apellidos: str) -> str:
    """Razón social para un NIT; nombres y apellidos para una persona."""
    if tipo_documento in ("NIT", "NIT_EXTERIOR"):
        return nombres
    return f"{nombres} {apellidos}".strip()


@dataclass(frozen=True, slots=True)
class ClienteRegistrado:
    id: int
    tipo_documento: str
    numero_documento: str
    digito_verificacion: str | None
    nombres: str
    apellidos: str
    email: str
    activo: bool

    @property
    def nombre_mostrar(self) -> str:
        return nombre_para_mostrar(self.tipo_documento, self.nombres, self.apellidos)


def _limpio(texto: str) -> str:
    return _ESPACIOS.sub(" ", texto).strip().upper()


def normalizar(
    *,
    tipo_documento: str,
    numero_documento: str,
    nombres: str,
    apellidos: str = "",
    email: str,
    digito_verificacion: str | None = None,
    autoriza_tratamiento: bool,
) -> Cliente:
    """Arma y valida el cliente. Lanza ClienteInvalido con TODOS los problemas."""
    tipo = tipo_documento.strip().upper()
    numero = re.sub(r"[\s.\-]", "", numero_documento).upper()
    dv = (digito_verificacion or "").strip() or None
    if tipo == "NIT" and dv is None and numero.isdigit() and 8 <= len(numero) <= 10:
        dv = str(calcular_dv(numero))
    cliente = Cliente(
        tipo_documento=tipo,
        numero_documento=numero,
        nombres=_limpio(nombres),
        apellidos="" if tipo == "NIT" else _limpio(apellidos),
        email=email.strip().lower(),
        digito_verificacion=dv if tipo == "NIT" else None,
        autoriza_tratamiento_datos=autoriza_tratamiento,
    )
    validar_cliente(cliente)
    return cliente


_COLUMNAS = "id, tipo_documento, numero_documento, digito_verificacion, nombres, apellidos, coalesce(email, ''), activo"


def buscar(conn: Conexion, tipo_documento: str, numero_documento: str) -> ClienteRegistrado | None:
    numero = re.sub(r"[\s.\-]", "", numero_documento).upper()
    fila = conn.execute(
        f"SELECT {_COLUMNAS} FROM cliente WHERE tipo_documento = %s AND numero_documento = %s",
        (tipo_documento.strip().upper(), numero),
    ).fetchone()
    return None if fila is None else ClienteRegistrado(*fila)


def obtener(conn: Conexion, cliente_id: int) -> ClienteRegistrado | None:
    fila = conn.execute(f"SELECT {_COLUMNAS} FROM cliente WHERE id = %s", (cliente_id,)).fetchone()
    return None if fila is None else ClienteRegistrado(*fila)


def registrar(
    conn: Conexion,
    cliente: Cliente,
    *,
    canal: str,
    usuario_id: int | None = None,
    fecha_autorizacion: datetime | None = None,
) -> ClienteRegistrado:
    """Guarda un cliente ya validado (`normalizar`). `canal`: PISTA (lo registra el bombero), PUBLICO (QR) o
    IMPORTACION (migración desde el sistema anterior).

    La fecha de autorización es la del servidor de base de datos (con zona), en el momento de guardar; en una
    importación es la que trae el archivo (cuándo autorizó el cliente en el sistema anterior).
    Lanza ClienteYaExiste si el documento ya está registrado (no se sobrescriben sus datos).
    """
    exigir_autocommit(conn)
    if not cliente.autoriza_tratamiento_datos:  # validar_cliente ya lo exige; doble llave por ser un dato legal
        raise ClienteInvalido(["falta la autorización de tratamiento de datos (Ley 1581 de 2012)"])
    try:
        with conn.transaction():
            fila = conn.execute(
                f"""INSERT INTO cliente (tipo_documento, numero_documento, digito_verificacion, nombres, apellidos,
                                         email, autoriza_tratamiento, fecha_autorizacion)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, coalesce(%s, now()))
                    RETURNING {_COLUMNAS}""",
                (cliente.tipo_documento, cliente.numero_documento, cliente.digito_verificacion, cliente.nombres,
                 cliente.apellidos, cliente.email, fecha_autorizacion),
            ).fetchone()  # fmt: skip
            assert fila is not None
            registrado = ClienteRegistrado(*fila)
            auditar(
                conn,
                "CLIENTE_REGISTRADO",
                "cliente",
                registrado.id,
                usuario_id=usuario_id,
                detalle={"tipo_documento": registrado.tipo_documento, "canal": canal},
            )
            return registrado
    except errors.UniqueViolation:
        existente = buscar(conn, cliente.tipo_documento, cliente.numero_documento)
        assert existente is not None
        raise ClienteYaExiste(existente.id) from None


def listar(
    conn: Conexion,
    *,
    texto: str | None = None,
    activo: bool | None = None,
    limite: int = 50,
    desplazamiento: int = 0,
) -> tuple[list[ClienteRegistrado], int]:
    """Clientes que coinciden con `texto` (inicio del documento, o parte del nombre), y el total sin paginar."""
    t = _ESPACIOS.sub(" ", texto or "").strip().upper() or None
    doc = re.sub(r"[\s.\-]", "", t or "") or None
    filtro = """FROM cliente
                WHERE (%(t)s::text IS NULL OR numero_documento LIKE %(doc)s || '%%'
                       OR (nombres || ' ' || apellidos) LIKE '%%' || %(t)s || '%%')
                  AND (%(activo)s::boolean IS NULL OR activo = %(activo)s)"""
    p = {"t": t, "doc": doc, "activo": activo, "limite": limite, "desplazamiento": desplazamiento}
    total = conn.execute(f"SELECT count(*) {filtro}", p).fetchone()
    filas = conn.execute(
        f"SELECT {_COLUMNAS} {filtro} ORDER BY nombres, apellidos, id LIMIT %(limite)s OFFSET %(desplazamiento)s", p
    ).fetchall()
    return [ClienteRegistrado(*f) for f in filas], int(total[0]) if total else 0


def cambiar_activo(conn: Conexion, cliente_id: int, activo: bool, *, usuario_id: int) -> ClienteRegistrado | None:
    """Baja lógica (o reactivación). Un cliente inactivo no aparece en la pista y no se le puede facturar; su
    historial y sus facturas se conservan. None si no existe."""
    exigir_autocommit(conn)
    with conn.transaction():
        fila = conn.execute(
            f"UPDATE cliente SET activo = %s WHERE id = %s RETURNING {_COLUMNAS}", (activo, cliente_id)
        ).fetchone()
        if fila is None:
            return None
        auditar(conn, "CLIENTE_ACTIVADO" if activo else "CLIENTE_DESACTIVADO", "cliente", cliente_id,
                usuario_id=usuario_id)  # fmt: skip
        return ClienteRegistrado(*fila)
