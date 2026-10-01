"""Simulador de surtidor para el banco de pruebas: despachos realistas sin ningún equipo real.

Genera filas en el MISMO formato del export de Speed Solutions y las normaliza con el mismo código del parser
(`speed_solutions.normalizar_fila`), con marca SIMULADOR para que nunca se confundan con ventas reales.

Lo que imita (de los hallazgos sobre el export real, ver CLAUDE.md):
  * lados con dos pistolas: 1 = gasolina corriente, 2 = diésel;
  * galones con 3 decimales; venta "por plata" (valor redondo, galones redondeados a 3 decimales) o "por
    galones/tanqueo" (valor = galones × precio redondeado al peso): valor y galones × precio difieren unos
    pesos (máx. precio × 0,0005 ≈ 8; en el export real el máximo fue 5,3);
  * volumen neto un poco menor que el bruto (compensación de temperatura); se factura el bruto;
  * contadores acumulados por lado/pistola que suben exactamente lo despachado;
  * placas válidas, sucias ("abc-123"), "sin dato" ("0", "1"), nombres de maquinaria y cédulas digitadas;
    kilometraje casi siempre de relleno;
  * contado y crédito (crédito: diésel con placa).
Fallas a pedido (`falla=`): "repetido" (el equipo reenvía el último despacho) y "salto" (se pierde un
despacho: el id y el contador avanzan sin que llegue la fila).

Todos los datos son SINTÉTICOS y los precios son de ejemplo (no son la lista de precios de la estación).
"""

from __future__ import annotations

import hashlib
import json
import random
import string
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from ..modelos import Despacho, MarcaSurtidor, RechazoFila
from ..parsers.speed_solutions import normalizar_fila
from .fuente import Lote

Falla = Literal["repetido", "salto"]
FALLAS: tuple[Falla, ...] = ("repetido", "salto")
PRODUCTO_POR_PISTOLA = {1: "CORRIENTE", 2: "DIESEL"}
PRECIOS_EJEMPLO = {"CORRIENTE": Decimal("16025"), "DIESEL": Decimal("15990")}
_FORMATO_FECHA = "%Y/%m/%d %H:%M:%S"
_MIL = Decimal("0.001")


def _ahora_local() -> datetime:
    return datetime.now(ZoneInfo("America/Bogota")).replace(tzinfo=None, microsecond=0)


class SimuladorSurtidor:
    """Fuente de despachos simulados (implementa `FuenteDespachos`).

    `despachar()` produce un despacho terminado "ahora" y lo deja pendiente; `leer()` entrega los pendientes
    como un lote y `confirmar()` los descarta cuando ya están guardados.
    """

    def __init__(
        self,
        *,
        codigo: str = "SIM1",
        lados: Sequence[str] = ("A", "B"),
        precios: dict[str, Decimal] | None = None,
        semilla: int | None = None,
        reloj: Callable[[], datetime] = _ahora_local,
        primer_id: int = 1,
        id_cierre: int = 1,
    ):
        if not lados:
            raise ValueError("el simulador necesita al menos un lado")
        self.codigo = codigo
        self.lados = tuple(lado.upper() for lado in lados)
        self.precios = dict(PRECIOS_EJEMPLO if precios is None else precios)
        self.id_cierre = id_cierre
        self._azar = random.Random(semilla)
        self._reloj = reloj
        self._ultimo_id = primer_id - 1
        self._contadores = {
            (lado, p): [Decimal(self._azar.randrange(10_000, 900_000)), Decimal(0)]
            for lado in self.lados
            for p in PRODUCTO_POR_PISTOLA
        }
        for c in self._contadores.values():
            c[1] = (c[0] * Decimal(15_000)).quantize(Decimal(1))
        self._pendientes: list[dict[str, str]] = []
        self._ultima: dict[str, str] | None = None

    # ------------------------------------------------------------------ generar
    def despachar(
        self,
        lado: str | None = None,
        *,
        pistola: int | None = None,
        galones: Decimal | None = None,
        valor: Decimal | None = None,
        forma_pago: str | None = None,
        placa: str | None = None,
        falla: Falla | None = None,
    ) -> dict[str, str]:
        """Genera un despacho (los campos no dados se sortean) y devuelve su fila tal como la exporta el equipo."""
        if falla == "repetido":
            if self._ultima is None:
                raise ValueError("no hay un despacho anterior para repetir")
            fila = dict(self._ultima)
            self._pendientes.append(fila)
            return fila
        if falla == "salto":
            # El despacho perdido (misma manguera que el siguiente): avanza id y contador, pero no llega.
            lado = lado or self._azar.choice(self.lados)
            pistola = pistola or self._azar.choice(list(PRODUCTO_POR_PISTOLA))
            self._generar(lado, pistola, None, None, None, None)
            self._pendientes.pop()
        elif falla is not None:
            raise ValueError(f"falla desconocida: {falla!r} (opciones: {', '.join(FALLAS)})")
        return self._generar(lado, pistola, galones, valor, forma_pago, placa)

    def sortear_falla(self, probabilidad: float) -> Falla | None:
        """Una falla al azar con la probabilidad dada (para el modo continuo del comando)."""
        return self._azar.choice(FALLAS) if self._azar.random() < probabilidad else None

    def retomar(
        self, *, ultimo_id: int, id_cierre: int, contadores: dict[tuple[str, int], tuple[Decimal, Decimal]]
    ) -> None:
        """Continúa donde quedó una corrida anterior (último id, turno y contadores guardados en la base), para
        que los ids no choquen y los contadores no salten."""
        self._ultimo_id = ultimo_id
        self.id_cierre = id_cierre
        for clave, (vol, dinero) in contadores.items():
            if clave in self._contadores:
                self._contadores[clave] = [vol, dinero]

    def cerrar_turno(self) -> int:
        """Cierre de turno en el equipo: los despachos siguientes llevan el nuevo ID-CIERRE."""
        self.id_cierre += 1
        return self.id_cierre

    def _generar(
        self,
        lado: str | None,
        pistola: int | None,
        galones: Decimal | None,
        valor: Decimal | None,
        forma_pago: str | None,
        placa: str | None,
    ) -> dict[str, str]:
        az = self._azar
        lado = (lado or az.choice(self.lados)).upper()
        if lado not in self.lados:
            raise ValueError(f"el lado {lado!r} no existe en este surtidor ({', '.join(self.lados)})")
        pistola = pistola or az.choice(list(PRODUCTO_POR_PISTOLA))
        producto = PRODUCTO_POR_PISTOLA[pistola]
        ppu = self.precios[producto]
        galones, valor = self._cantidades(producto, ppu, galones, valor)
        if forma_pago is None:
            forma_pago = "CREDITO" if producto == "DIESEL" and az.random() < 0.5 else "CONTADO"
        if placa is None:
            placa = self._placa(credito=forma_pago == "CREDITO")

        contador = self._contadores[(lado, pistola)]
        contador[0] += galones
        contador[1] += valor
        self._ultimo_id += 1
        fin = self._reloj().replace(microsecond=0)
        inicio = fin - timedelta(seconds=az.randrange(40, 360))
        neto = (galones * Decimal("0.9975")).quantize(_MIL)
        fila = {
            "ID-DESPACHO": str(self._ultimo_id),
            "ID-CIERRE": str(self.id_cierre),
            "ID-SURTIDOR": self.codigo,
            "LADO": lado,
            "PISTOLA": str(pistola),
            "PRD": producto,
            "TIME-INI": inicio.strftime(_FORMATO_FECHA),
            "TIME-FIN": fin.strftime(_FORMATO_FECHA),
            "VOLG": str(galones),
            "VOLN": str(neto),
            "MONEY": str(valor),
            "PPU": str(ppu),
            "FORMA-PAGO": forma_pago,
            "INFO-PLACA": placa,
            "INFO-KILOMETRAJE": az.choice(["0", "1", "0", "1", "0", str(az.randrange(10_000, 900_000))]),
            f"TOT-GROS-{pistola}": str(contador[0]),
            f"TOT-NET-{pistola}": str(contador[0]),
            f"TOT-MONEY-{pistola}": str(contador[1]),
        }
        self._pendientes.append(fila)
        self._ultima = fila
        return fila

    def _cantidades(
        self, producto: str, ppu: Decimal, galones: Decimal | None, valor: Decimal | None
    ) -> tuple[Decimal, Decimal]:
        if galones is not None and valor is not None:
            return galones.quantize(_MIL), valor.quantize(Decimal(1))
        if valor is None and galones is None and self._azar.random() < 0.4:
            valor = Decimal(self._azar.choice([10_000, 20_000, 30_000, 50_000, 100_000]))
        if valor is not None:  # venta "por plata": el equipo corta al llegar al valor
            return (valor / ppu).quantize(_MIL, rounding=ROUND_HALF_UP), valor.quantize(Decimal(1))
        if galones is None:
            tope = 15 if producto == "CORRIENTE" else 70
            galones = Decimal(self._azar.randrange(500, tope * 1000)) / 1000
        galones = galones.quantize(_MIL)
        return galones, (galones * ppu).quantize(Decimal(1), rounding=ROUND_HALF_UP)

    def _placa(self, *, credito: bool) -> str:
        az = self._azar
        valida = "".join(az.choices(string.ascii_uppercase, k=3)) + f"{az.randrange(1000):03d}"
        if credito:
            return valida
        return az.choices(
            [valida, f"{valida[:3].lower()}-{valida[3:]}", "0", "1", "BOBCAT", str(az.randrange(10**7, 10**10))],
            weights=[60, 8, 15, 8, 4, 5],
        )[0]

    # ------------------------------------------------------------------ FuenteDespachos
    @property
    def pendientes(self) -> list[dict[str, str]]:
        """Filas generadas que todavía no se confirmaron (copia)."""
        return [dict(f) for f in self._pendientes]

    def leer(self) -> Iterator[Lote]:
        if not self._pendientes:
            return
        filas = list(self._pendientes)
        despachos: list[Despacho] = []
        rechazos: list[RechazoFila] = []
        for fila in filas:
            try:
                despachos.append(normalizar_fila(fila, marca=MarcaSurtidor.SIMULADOR))
            except (KeyError, ValueError, ArithmeticError) as e:
                rechazos.append(RechazoFila(int(fila["ID-DESPACHO"]), str(e) or type(e).__name__, fila))
        contenido = json.dumps([self.codigo, filas], sort_keys=True).encode()
        yield Lote(
            marca=MarcaSurtidor.SIMULADOR,
            nombre=f"simulador-{self.codigo}-{filas[0]['ID-DESPACHO']}-{filas[-1]['ID-DESPACHO']}",
            sha256=hashlib.sha256(contenido).hexdigest(),
            despachos=tuple(despachos),
            rechazos=tuple(rechazos),
        )

    def confirmar(self, lote: Lote) -> None:
        # Las filas del lote son las primeras pendientes (las generadas después siguen pendientes).
        del self._pendientes[: len(lote.despachos) + len(lote.rechazos)]
