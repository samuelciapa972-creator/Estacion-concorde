"""Lector de Nexus (IMED OIL): las ventas del Wayne que muestra su web, como despachos (`surtidores/nexus.py`).

Datos SINTÉTICOS con el formato real que responde `POST /ver_ultimas_ventas` (visto el 2026-11-06):
`[numero_venta, cara, placa_o_documento, producto, dinero, volumen]`, solo las 3 últimas ventas y sin hora.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from estacion.modelos import MarcaSurtidor
from estacion.surtidores.nexus import FuenteNexus, fila_desde_venta

AHORA = datetime(2026, 11, 6, 15, 30, 0)
PRODUCTOS = ("Diesel", "Corriente")


class NexusFalso:
    """Responde como la web de Nexus: las 3 últimas ventas, la más reciente primero."""

    def __init__(self, ventas: list[list] | None = None):
        self.ventas = ventas or []
        self.consultas = 0

    def ultimas_ventas(self) -> list[list]:
        self.consultas += 1
        return self.ventas[:3]

    def vender(self, *venta) -> None:
        self.ventas.insert(0, list(venta))


def fuente(nexus: NexusFalso, ultimo: int | None = None) -> FuenteNexus:
    f = FuenteNexus(nexus.ultimas_ventas, productos=PRODUCTOS, reloj=lambda: AHORA)
    f.iniciar(ultimo)
    return f


def test_una_venta_de_nexus_es_un_despacho_del_wayne():
    fila = fila_desde_venta([198661, 1, "", "Corriente", 10000, 0.627], productos=PRODUCTOS, visto=AHORA)
    identidad = (fila["ID-DESPACHO"], fila["ID-SURTIDOR"], fila["LADO"], fila["PRD"])
    assert identidad == ("198661", "HELIX-1", "A", "CORRIENTE")
    assert (fila["PISTOLA"], fila["VOLG"], fila["MONEY"], fila["FORMA-PAGO"]) == ("2", "0.627", "10000", "CONTADO")
    assert fila["PPU"] == "15948.96"  # dinero / galones: Nexus no lo trae
    assert fila["TIME-INI"] == fila["TIME-FIN"] == "2026/11/06 15:30:00"  # Nexus no trae la hora: la de lectura
    assert fila["ID-CIERRE"] == "20261106"  # Nexus no trae el turno: uno por día
    assert fila["INFO-PLACA"] == "0"  # sin placa: "sin dato", como en el archivo del surtidor


def test_cara_2_diesel_y_placa():
    fila = fila_desde_venta([5, 2, "gty455", "Diesel", 61000, 10], productos=PRODUCTOS, visto=AHORA)
    assert (fila["LADO"], fila["PRD"], fila["PISTOLA"], fila["INFO-PLACA"], fila["PPU"]) == (
        "B", "DIESEL", "1", "GTY455", "6100.00",
    )  # fmt: skip


@pytest.mark.parametrize(
    "venta",
    [
        [1, 3, "", "Corriente", 1000, 0.1],  # el surtidor tiene 2 caras
        [1, 1, "", "Gas", 1000, 0.1],  # producto que Nexus no anunció
        [1, 1, "", "Corriente", 0, 0.1],  # sin valor
        [1, 1, "", "Corriente", 1000, 0],  # sin galones
    ],
)
def test_venta_imposible_se_rechaza(venta):
    with pytest.raises(ValueError):
        fila_desde_venta(venta, productos=PRODUCTOS, visto=AHORA)


def test_al_arrancar_sin_historia_no_carga_las_ventas_viejas():
    # Las 3 que ya estaban no se sabe a qué hora fueron: solo cuentan las que aparecen después.
    nexus = NexusFalso([[12, 1, "", "Corriente", 10000, 0.627], [11, 2, "", "Corriente", 5000, 0.312]])
    f = fuente(nexus)
    f.sondear()
    assert list(f.leer()) == []
    nexus.vender(13, 2, "", "Diesel", 20000, 3.279)
    f.sondear()
    (lote,) = f.leer()
    assert [d.id_externo for d in lote.despachos] == [13]
    assert lote.marca == MarcaSurtidor.WAYNE and lote.despachos[0].volumen_bruto == Decimal("3.279")


def test_retoma_desde_la_ultima_venta_guardada():
    nexus = NexusFalso([[12, 1, "", "Corriente", 1000, 0.1], [11, 1, "", "Corriente", 1000, 0.1]])
    f = fuente(nexus, ultimo=11)
    f.sondear()
    (lote,) = f.leer()
    assert [d.id_externo for d in lote.despachos] == [12]


def test_una_venta_no_se_carga_dos_veces_y_se_confirma():
    nexus = NexusFalso([[10, 1, "", "Corriente", 1000, 0.1]])
    f = fuente(nexus, ultimo=9)
    f.sondear()
    f.sondear()  # la misma venta sigue en la lista de Nexus
    (lote,) = f.leer()
    assert [d.id_externo for d in lote.despachos] == [10]
    f.confirmar(lote)
    f.sondear()
    assert list(f.leer()) == []


def test_varias_ventas_nuevas_salen_en_orden():
    nexus = NexusFalso()
    f = fuente(nexus, ultimo=100)
    for n in (101, 102):
        nexus.vender(n, 1, "", "Corriente", 1000, 0.1)
    f.sondear()
    (lote,) = f.leer()
    assert [d.id_externo for d in lote.despachos] == [101, 102]


def test_si_pasaron_mas_de_3_ventas_avisa_las_perdidas(caplog):
    nexus = NexusFalso()
    f = fuente(nexus, ultimo=100)
    for n in range(101, 106):  # 5 ventas entre dos lecturas: Nexus solo muestra 103, 104 y 105
        nexus.vender(n, 1, "", "Corriente", 1000, 0.1)
    f.sondear()
    (lote,) = f.leer()
    assert [d.id_externo for d in lote.despachos] == [103, 104, 105]
    assert f.perdidas == [101, 102]
    assert "101" in caplog.text and "102" in caplog.text


def test_una_venta_danada_va_a_rechazos_sin_detener_las_demas():
    nexus = NexusFalso()
    f = fuente(nexus, ultimo=1)
    nexus.vender(2, 9, "", "Corriente", 1000, 0.1)  # cara inexistente
    nexus.vender(3, 1, "", "Corriente", 1000, 0.1)
    f.sondear()
    (lote,) = f.leer()
    assert [d.id_externo for d in lote.despachos] == [3]
    assert [r.id_externo for r in lote.rechazos] == [2]


def test_el_mismo_lote_tiene_la_misma_huella():
    def un_lote():
        nexus = NexusFalso()
        f = fuente(nexus, ultimo=1)
        nexus.vender(2, 1, "", "Corriente", 1000, 0.1)
        f.sondear()
        return next(f.leer())

    assert un_lote().sha256 == un_lote().sha256


@pytest.mark.bd
def test_las_ventas_de_nexus_salen_en_la_pista_del_wayne(api):
    from estacion.surtidores import ingerir

    nexus = NexusFalso()
    f = FuenteNexus(nexus.ultimas_ventas, productos=PRODUCTOS)  # reloj real: la pista muestra lo reciente
    f.iniciar(198660)
    nexus.vender(198661, 1, "", "Corriente", 10000, 0.627)
    f.sondear()
    (r,) = ingerir(api.e.datos.bd.dsn, f)
    assert r.resumen.nuevos == 1
    f.sondear()  # Nexus la sigue mostrando: no se vuelve a guardar
    assert ingerir(api.e.datos.bd.dsn, f) == []
    surtidor = api.e.datos.uno(
        "SELECT s.codigo_externo FROM despacho d JOIN surtidor s ON s.id = d.surtidor_id WHERE d.id_externo = 198661"
    )
    assert surtidor == "HELIX-1"  # el mismo Wayne de la venta manual, no un surtidor nuevo
    pendientes = api.cliente.get("/despachos/pendientes?lado=A", headers=api.entrar(api.bombero)).json()
    del_surtidor = [(p["valor"], p["producto"]) for p in pendientes if p["origen_tipo"] == "despacho"]
    assert del_surtidor == [("10000", "CORRIENTE")]
