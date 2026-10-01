"""Parser del archivo exportado por el surtidor Speed Solutions.

El archivo se llama `.xls` pero es XML plano:

    <catalog><book ID-DESPACHO="36015"><ID-CIERRE>3392</ID-CIERRE>...</book>...</catalog>

Se lee en streaming (iterparse), una fila `<book>` a la vez. Una fila dañada se
rechaza y se reporta; un archivo truncado o mal formado aborta TODO (no se
importa nada a medias).
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..modelos import ArchivoInvalido, Despacho, RechazoFila, ResultadoLectura
from ..normalizacion import (
    normalizar_forma_pago,
    normalizar_kilometraje,
    normalizar_placa,
)

MARCA = "SPEED_SOLUTIONS"
_FORMATO_FECHA = "%Y/%m/%d %H:%M:%S"
_OBLIGATORIOS = (
    "ID-DESPACHO",
    "ID-CIERRE",
    "ID-SURTIDOR",
    "LADO",
    "PISTOLA",
    "PRD",
    "TIME-INI",
    "TIME-FIN",
    "VOLG",
    "VOLN",
    "MONEY",
    "PPU",
    "FORMA-PAGO",
)


def leer(ruta: str | Path) -> ResultadoLectura:
    ruta = Path(ruta)
    despachos: list[Despacho] = []
    rechazos: list[RechazoFila] = []

    try:
        for _, elem in ET.iterparse(ruta, events=("end",)):
            if elem.tag != "book":
                continue
            crudo = {"ID-DESPACHO": elem.get("ID-DESPACHO") or ""}
            crudo.update({hijo.tag: (hijo.text or "").strip() for hijo in elem})
            elem.clear()  # no acumular memoria
            try:
                despachos.append(_normalizar(crudo))
            except (KeyError, ValueError, ArithmeticError) as e:
                # ArithmeticError cubre decimal.InvalidOperation
                id_txt = crudo.get("ID-DESPACHO", "")
                rechazos.append(
                    RechazoFila(
                        id_externo=int(id_txt) if id_txt.isdigit() else None,
                        motivo=str(e) or e.__class__.__name__,
                        crudo=crudo,
                    )
                )
    except ET.ParseError as e:
        raise ArchivoInvalido(f"XML mal formado o truncado: {e}") from e

    return ResultadoLectura(MARCA, _sha256(ruta), despachos, rechazos)


def _normalizar(crudo: dict[str, str]) -> Despacho:
    faltan = [k for k in _OBLIGATORIOS if not crudo.get(k)]
    if faltan:
        raise ValueError(f"campos vacíos o ausentes: {', '.join(faltan)}")

    pistola = int(crudo["PISTOLA"])
    inicio = datetime.strptime(crudo["TIME-INI"], _FORMATO_FECHA)
    fin = datetime.strptime(crudo["TIME-FIN"], _FORMATO_FECHA)
    volumen_bruto = Decimal(crudo["VOLG"])
    volumen_neto = Decimal(crudo["VOLN"])
    valor = Decimal(crudo["MONEY"])
    ppu = Decimal(crudo["PPU"])
    if min(volumen_bruto, volumen_neto, valor) < 0 or ppu <= 0:
        raise ValueError("volumen, valor o precio negativo")

    tipo_placa, ref = normalizar_placa(crudo.get("INFO-PLACA"))
    tot_vol, tot_valor = _totalizador(crudo, pistola)

    return Despacho(
        surtidor_marca=MARCA,
        codigo_surtidor=crudo["ID-SURTIDOR"],
        id_externo=int(crudo["ID-DESPACHO"]),
        id_cierre=int(crudo["ID-CIERRE"]),
        lado=crudo["LADO"].strip().upper(),
        pistola=pistola,
        producto=crudo["PRD"].strip().upper(),
        inicio_reloj=inicio,
        fin_reloj=fin,
        inicio=inicio,
        fin=fin,
        volumen_bruto=volumen_bruto,
        volumen_neto=volumen_neto,
        valor=valor,
        ppu=ppu,
        forma_pago=normalizar_forma_pago(crudo["FORMA-PAGO"]),
        placa_raw=crudo.get("INFO-PLACA", ""),
        placa_tipo=tipo_placa,
        ref_vehiculo=ref,
        kilometraje_raw=crudo.get("INFO-KILOMETRAJE", ""),
        kilometraje=normalizar_kilometraje(crudo.get("INFO-KILOMETRAJE")),
        totalizador_vol=tot_vol,
        totalizador_valor=tot_valor,
        hash_contenido=hashlib.sha256(json.dumps(crudo, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        crudo=crudo,
    )


def _totalizador(crudo: dict[str, str], pistola: int) -> tuple[Decimal | None, Decimal | None]:
    """Lectura del contador acumulado tras el despacho.

    SUPUESTO (a confirmar con un despacho de pistola 2+): el ranura TOT-*-N
    corresponde a la pistola N. En el archivo real solo se usa N=1 y coincide
    con el 100 % de los despachos (delta del contador == VOLG y == MONEY).
    """
    if not 1 <= pistola <= 4:
        return None, None
    bruto = crudo.get(f"TOT-GROS-{pistola}")
    dinero = crudo.get(f"TOT-MONEY-{pistola}")
    if not bruto or not dinero:
        return None, None
    return Decimal(bruto), Decimal(dinero)


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()
