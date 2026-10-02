"""Importación de clientes y vehículos desde CSV (migración desde el sistema anterior u hojas de cálculo).

FORMATO PROPIO, provisional: el formato real de lo que entregue Nexus/IMED OIL todavía no se conoce (riesgo 11 del
CLAUDE.md). Se acepta separador `,` o `;` (Excel en español usa `;`) y texto UTF-8 o Windows-1252.

    clientes:   tipo_documento;numero_documento;nombres;apellidos;email;autoriza_tratamiento;fecha_autorizacion
    vehiculos:  placa;tipo_documento;numero_documento;descripcion

Reglas:
  * **Ley 1581:** un cliente sin autorización registrada (`autoriza_tratamiento` = SI y su fecha) NO se importa.
  * Lo que ya existe NO se sobrescribe (se cuenta como "existente").
  * `en_seco=True` (el valor por defecto en la API) valida y cuenta sin escribir nada.
  * Todo o nada: con un error inesperado de la base no queda una importación a medias.
  * Los problemas se reportan por número de fila, sin repetir los datos personales de la fila.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..facturacion.documentos import ClienteInvalido
from . import clientes
from ._bd import ZONA_ESTACION, Conexion, auditar, exigir_autocommit
from .vehiculos import VehiculoInvalido, identificador

MAX_FILAS = 20_000
COLUMNAS_CLIENTES = (
    "tipo_documento", "numero_documento", "nombres", "apellidos", "email", "autoriza_tratamiento", "fecha_autorizacion",
)  # fmt: skip
COLUMNAS_VEHICULOS = ("placa", "tipo_documento", "numero_documento", "descripcion")
_SI = {"SI", "SÍ", "S", "X", "1", "TRUE", "VERDADERO"}


class ArchivoInvalido(ValueError):
    """El archivo entero no sirve (codificación, encabezados, demasiadas filas)."""


@dataclass(frozen=True, slots=True)
class FilaRechazada:
    fila: int  # número de línea en el archivo (el encabezado es la 1)
    problemas: list[str]


@dataclass(slots=True)
class ResultadoImportacion:
    en_seco: bool
    filas: int = 0
    nuevas: int = 0
    existentes: int = 0
    rechazadas: list[FilaRechazada] = field(default_factory=list)


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def leer_csv(contenido: bytes, columnas: tuple[str, ...], obligatorias: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = contenido.decode("cp1252", errors="strict")
    if not texto.strip():
        raise ArchivoInvalido("el archivo está vacío")
    primera = texto.splitlines()[0]
    separador = ";" if primera.count(";") > primera.count(",") else ","
    lector = csv.DictReader(io.StringIO(texto, newline=""), delimiter=separador)
    encabezados = [_sin_tildes(h or "").strip().lower().replace(" ", "_") for h in (lector.fieldnames or [])]
    faltan = [c for c in obligatorias if c not in encabezados]
    if faltan:
        raise ArchivoInvalido(f"faltan columnas: {', '.join(faltan)} (se esperan: {', '.join(columnas)})")
    lector.fieldnames = encabezados
    filas: list[dict[str, str]] = []
    for crudo in lector:
        if len(filas) >= MAX_FILAS:
            raise ArchivoInvalido(f"el archivo tiene más de {MAX_FILAS} filas: divídalo")
        filas.append({k: (v or "").strip() for k, v in crudo.items() if k in columnas})
    return filas


def _fecha(texto: str) -> datetime | None:
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            d = datetime.strptime(texto, formato)
        except ValueError:
            continue
        return d.replace(tzinfo=ZONA_ESTACION)
    return None


def importar_clientes(
    conn: Conexion, contenido: bytes, *, en_seco: bool, usuario_id: int, hoy: date | None = None
) -> ResultadoImportacion:
    exigir_autocommit(conn)
    filas = leer_csv(contenido, COLUMNAS_CLIENTES, COLUMNAS_CLIENTES[:3] + COLUMNAS_CLIENTES[4:])
    hoy = hoy or datetime.now(ZONA_ESTACION).date()
    r = ResultadoImportacion(en_seco=en_seco, filas=len(filas))
    validas: list[tuple[int, Any, datetime]] = []
    vistos: set[tuple[str, str]] = set()
    for n, f in enumerate(filas, start=2):
        problemas: list[str] = []
        autoriza = _sin_tildes(f.get("autoriza_tratamiento", "")).upper() in {_sin_tildes(x) for x in _SI}
        fecha = _fecha(f.get("fecha_autorizacion", ""))
        if not autoriza:
            problemas.append("sin autorización de tratamiento de datos: no se importa (Ley 1581 de 2012)")
        elif fecha is None:
            problemas.append("falta la fecha de autorización (AAAA-MM-DD o DD/MM/AAAA)")
        elif fecha.date() > hoy:
            problemas.append("la fecha de autorización está en el futuro")
        try:
            cliente = clientes.normalizar(
                tipo_documento=f.get("tipo_documento", ""),
                numero_documento=f.get("numero_documento", ""),
                nombres=f.get("nombres", ""),
                apellidos=f.get("apellidos", ""),
                email=f.get("email", ""),
                autoriza_tratamiento=True,  # la autorización se revisó arriba con su fecha
            )
        except ClienteInvalido as e:
            problemas.extend(p for p in e.problemas if "autorización" not in p)
            cliente = None
        if cliente is not None:
            llave = (cliente.tipo_documento, cliente.numero_documento)
            if llave in vistos:
                problemas.append("documento repetido dentro del archivo")
            vistos.add(llave)
        if problemas or cliente is None or fecha is None:
            r.rechazadas.append(FilaRechazada(n, problemas))
        else:
            validas.append((n, cliente, fecha))

    with conn.transaction():
        for _, cliente, fecha in validas:
            if clientes.buscar(conn, cliente.tipo_documento, cliente.numero_documento) is not None:
                r.existentes += 1
                continue
            if not en_seco:
                try:
                    clientes.registrar(conn, cliente, canal="IMPORTACION", usuario_id=usuario_id,
                                       fecha_autorizacion=fecha)  # fmt: skip
                except clientes.ClienteYaExiste:  # lo registró otro proceso mientras tanto
                    r.existentes += 1
                    continue
            r.nuevas += 1
        if not en_seco:
            auditar(conn, "CLIENTES_IMPORTADOS", "cliente", None, usuario_id=usuario_id,
                    detalle={"filas": r.filas, "nuevas": r.nuevas, "existentes": r.existentes,
                             "rechazadas": len(r.rechazadas)})  # fmt: skip
    return r


def importar_vehiculos(conn: Conexion, contenido: bytes, *, en_seco: bool, usuario_id: int) -> ResultadoImportacion:
    exigir_autocommit(conn)
    filas = leer_csv(contenido, COLUMNAS_VEHICULOS, COLUMNAS_VEHICULOS[:3])
    r = ResultadoImportacion(en_seco=en_seco, filas=len(filas))
    vistos: set[str] = set()
    with conn.transaction():
        for n, f in enumerate(filas, start=2):
            problemas: list[str] = []
            try:
                ref, tipo = identificador(f.get("placa", ""))
            except VehiculoInvalido as e:
                r.rechazadas.append(FilaRechazada(n, [str(e)]))
                continue
            if ref in vistos:
                r.rechazadas.append(FilaRechazada(n, ["placa repetida dentro del archivo"]))
                continue
            vistos.add(ref)
            dueno = clientes.buscar(conn, f.get("tipo_documento", ""), f.get("numero_documento", ""))
            if dueno is None or not dueno.activo:
                problemas.append("el dueño no está registrado o está inactivo: importe primero los clientes")
            existente = conn.execute("SELECT cliente_id FROM vehiculo WHERE identificador = %s", (ref,)).fetchone()
            if existente is not None and dueno is not None and existente[0] != dueno.id:
                problemas.append("la placa ya está registrada a nombre de otro cliente")
            if problemas or dueno is None:
                r.rechazadas.append(FilaRechazada(n, problemas))
                continue
            if existente is not None:
                r.existentes += 1
                continue
            if not en_seco:  # una placa repetida por otro proceso aborta la transacción entera (todo o nada)
                conn.execute(
                    "INSERT INTO vehiculo (identificador, tipo, cliente_id, descripcion) VALUES (%s, %s, %s, %s)",
                    (ref, tipo, dueno.id, f.get("descripcion") or None),
                )
            r.nuevas += 1
        if not en_seco:
            auditar(conn, "VEHICULOS_IMPORTADOS", "vehiculo", None, usuario_id=usuario_id,
                    detalle={"filas": r.filas, "nuevas": r.nuevas, "existentes": r.existentes,
                             "rechazadas": len(r.rechazadas)})  # fmt: skip
    return r
