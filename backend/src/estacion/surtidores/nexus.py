"""Lector de Nexus (IMED OIL): las ventas del Wayne que muestra la web de su Raspberry, como despachos.

ALCANCE (autorizado por la empresa el 2026-11-05): SOLO LECTURA, la misma consulta que hace la tablet de la isla al
tocar "Actualizar" (`POST /ver_ultimas_ventas`). Nunca se llama a Configuración, Cambio de precio, Turno ni
Imprimir. Es una fuente PROVISIONAL para probar con ventas reales mientras no existe la conexión directa con el
surtidor.

Lo que entrega Nexus (visto el 2026-11-06) y cómo se completa:
    [numero_venta, cara, placa_o_documento, producto, dinero, volumen]   solo las 3 ÚLTIMAS ventas
    · numero_venta -> ID-DESPACHO (consecutivo de Nexus; la carga no lo duplica)
    · cara 1/2 -> lado A/B; producto -> PRD y pistola (su posición en la lista de productos de `/init`)
    · precio por galón = dinero / volumen (Nexus no lo trae)
    · hora: Nexus NO la trae -> la hora en que se leyó (consultando cada 15 s, el error es de segundos)
    · turno: Nexus no lo trae -> uno por día (ID-CIERRE = AAAAMMDD)
    · forma de pago: la lista no la trae -> CONTADO (el crédito se prueba con venta manual)
Como solo muestra 3, hay que consultar seguido. Si entre dos lecturas pasaron más, las que faltan se avisan (perdidas).
Al arrancar sin historia no se cargan las ventas que ya estaban: no se sabe a qué hora fueron.

Las filas se arman en el formato del archivo de Speed Solutions y se normalizan con su mismo código
(`speed_solutions.normalizar_fila`), como hace el simulador.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from ..modelos import Despacho, MarcaSurtidor, RechazoFila
from ..parsers.speed_solutions import normalizar_fila
from .fuente import Lote

log = logging.getLogger(__name__)

CODIGO_WAYNE = "HELIX-1"  # el surtidor de la migración 0009 (el bombero ya lo usa en la venta manual)
LADOS = {1: "A", 2: "B"}
_FORMATO_FECHA = "%Y/%m/%d %H:%M:%S"
_CENTAVO = Decimal("0.01")

Consultar = Callable[[], list[list[Any]]]


def _ahora_local() -> datetime:
    return datetime.now(ZoneInfo("America/Bogota")).replace(tzinfo=None, microsecond=0)


def fila_desde_venta(venta: Sequence[Any], *, productos: Sequence[str], visto: datetime) -> dict[str, str]:
    """Una venta de Nexus -> fila con el formato del archivo de Speed Solutions. Lanza ValueError si es imposible."""
    if len(venta) < 6:
        raise ValueError(f"venta de Nexus con {len(venta)} campos (se esperaban 6)")
    numero, cara, placa, producto, dinero, volumen = venta[:6]
    if cara not in LADOS:
        raise ValueError(f"cara {cara!r} inexistente (el Wayne tiene {len(LADOS)})")
    nombres = [p.strip().upper() for p in productos]
    prd = str(producto).strip().upper()
    if prd not in nombres:
        raise ValueError(f"producto {producto!r} no anunciado por Nexus ({', '.join(productos)})")
    try:
        valor = Decimal(str(dinero))
        galones = Decimal(str(volumen))
    except InvalidOperation:
        raise ValueError("dinero o volumen no numérico") from None
    if valor <= 0 or galones <= 0:
        raise ValueError("venta sin valor o sin galones")
    instante = visto.strftime(_FORMATO_FECHA)
    return {
        "ID-DESPACHO": str(int(numero)),
        "ID-CIERRE": visto.strftime("%Y%m%d"),
        "ID-SURTIDOR": CODIGO_WAYNE,
        "LADO": LADOS[cara],
        "PISTOLA": str(nombres.index(prd) + 1),
        "PRD": prd,
        "TIME-INI": instante,
        "TIME-FIN": instante,
        "VOLG": str(galones),
        "VOLN": str(galones),
        "MONEY": str(valor.quantize(Decimal(1))),
        "PPU": str((valor / galones).quantize(_CENTAVO, rounding=ROUND_HALF_UP)),
        "FORMA-PAGO": "CONTADO",
        "INFO-PLACA": str(placa or "").strip().upper() or "0",
        "INFO-KILOMETRAJE": "0",
    }


class FuenteNexus:
    """Fuente de despachos leídos de Nexus (implementa `FuenteDespachos`).

    `iniciar(ultimo)` fija desde qué venta se cuenta (la última guardada; None = desde ahora). `sondear()` consulta
    Nexus una vez y deja pendientes las ventas nuevas; `leer()` / `confirmar()` como cualquier fuente.
    """

    def __init__(
        self,
        consultar: Consultar,
        *,
        productos: Sequence[str],
        reloj: Callable[[], datetime] = _ahora_local,
    ):
        self._consultar = consultar
        self.productos = tuple(productos)
        self._reloj = reloj
        self._ultimo: int | None = None
        self._iniciado = False
        self._pendientes: list[dict[str, str]] = []
        self._rechazadas: list[tuple[list[Any], str]] = []
        self.perdidas: list[int] = []

    def iniciar(self, ultimo: int | None) -> None:
        self._ultimo = ultimo
        self._iniciado = ultimo is not None

    def sondear(self) -> int:
        """Una consulta a Nexus. Devuelve cuántas ventas nuevas dejó pendientes (o rechazadas)."""
        ventas = sorted(self._consultar(), key=lambda v: int(v[0]))
        if not self._iniciado:
            # Primera lectura sin historia: lo que ya estaba no se carga (no se sabe a qué hora fue).
            self._ultimo = max((int(v[0]) for v in ventas), default=0)
            self._iniciado = True
            log.info("Nexus: se empieza a contar después de la venta %s", self._ultimo)
            return 0
        ultimo = self._ultimo or 0
        nuevas = [v for v in ventas if int(v[0]) > ultimo]
        if not nuevas:
            return 0
        faltan = list(range(ultimo + 1, int(nuevas[0][0]))) if self._ultimo is not None else []
        if faltan:
            self.perdidas.extend(faltan)
            log.warning(
                "Nexus: se perdieron %s venta(s) (%s): pasaron más de 3 entre dos lecturas; registrarlas a mano",
                len(faltan), ", ".join(map(str, faltan)),
            )  # fmt: skip
        visto = self._reloj()
        for v in nuevas:
            try:
                self._pendientes.append(fila_desde_venta(v, productos=self.productos, visto=visto))
            except (ValueError, TypeError) as e:
                self._rechazadas.append((list(v), str(e)))
        self._ultimo = int(nuevas[-1][0])
        return len(nuevas)

    # ------------------------------------------------------------------ FuenteDespachos
    def leer(self) -> Iterator[Lote]:
        if not self._pendientes and not self._rechazadas:
            return
        filas = list(self._pendientes)
        despachos: list[Despacho] = []
        rechazos = [
            RechazoFila(int(v[0]) if v and str(v[0]).isdigit() else None, motivo, {"venta": json.dumps(v)})
            for v, motivo in self._rechazadas
        ]
        for fila in filas:
            try:
                despachos.append(normalizar_fila(fila, marca=MarcaSurtidor.WAYNE))
            except (KeyError, ValueError, ArithmeticError) as e:
                rechazos.append(RechazoFila(int(fila["ID-DESPACHO"]), str(e) or type(e).__name__, fila))
        ids = [int(f["ID-DESPACHO"]) for f in filas] + [r.id_externo or 0 for r in rechazos]
        contenido = json.dumps([CODIGO_WAYNE, filas, [list(v) for v, _ in self._rechazadas]], sort_keys=True)
        yield Lote(
            marca=MarcaSurtidor.WAYNE,
            nombre=f"nexus-{CODIGO_WAYNE}-{min(ids)}-{max(ids)}",
            sha256=hashlib.sha256(contenido.encode()).hexdigest(),
            despachos=tuple(despachos),
            rechazos=tuple(rechazos),
        )

    def confirmar(self, lote: Lote) -> None:
        self._pendientes.clear()
        self._rechazadas.clear()


# ---------------------------------------------------------------------- la web de Nexus
def _post(url: str, datos: dict[str, str], timeout: float) -> Any:
    cuerpo = urllib.parse.urlencode(datos).encode()
    pedido = urllib.request.Request(url, data=cuerpo, method="POST")  # noqa: S310 (URL de la red local de la pista)
    with urllib.request.urlopen(pedido, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


class WebNexus:
    """Las dos consultas de SOLO LECTURA que hace la tablet: la configuración y las últimas ventas."""

    def __init__(self, base: str, *, timeout: float = 10.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def productos(self) -> list[str]:
        """Nombres de los productos configurados en Nexus (de `/init`), en el orden de sus pistolas."""
        conf = _post(f"{self.base}/init", {"init": "init"}, self.timeout)
        nombres = [p[0] if isinstance(p, list) else p for p in conf["nombre_productos"]]
        return [str(n) for n in nombres[: int(conf["numero_productos"])]]

    def ultimas_ventas(self) -> list[list[Any]]:
        return list(_post(f"{self.base}/ver_ultimas_ventas", {"venta": "venta"}, self.timeout)["ultimas_ventas"])
