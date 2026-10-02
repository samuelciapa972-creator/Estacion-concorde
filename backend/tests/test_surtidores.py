"""Fuentes de despachos: simulador de surtidor, archivo de Speed Solutions e ingesta a PostgreSQL."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import count

import psycopg
import pytest

from estacion import simulador as comando
from estacion.calidad import TOLERANCIA_VALOR, analizar
from estacion.modelos import FormaPago, MarcaSurtidor, PlacaTipo
from estacion.parsers import speed_solutions
from estacion.surtidores import ArchivoSpeedSolutions, SimuladorSurtidor, ingerir


def reloj_que_avanza(inicio: datetime = datetime(2026, 9, 30, 6, 0)):
    pasos = count()
    return lambda: inicio + timedelta(minutes=3 * next(pasos))


def simulador(**kw) -> SimuladorSurtidor:
    return SimuladorSurtidor(semilla=kw.pop("semilla", 42), reloj=kw.pop("reloj", reloj_que_avanza()), **kw)


def lote(sim: SimuladorSurtidor):
    (unico,) = list(sim.leer())
    return unico


# ------------------------------------------------------------------------------------------ simulador
def test_misma_semilla_mismos_despachos():
    a, b = simulador(), simulador()
    assert [a.despachar() for _ in range(20)] == [b.despachar() for _ in range(20)]


def test_despachos_limpios_no_generan_anomalias():
    sim = simulador()
    for _ in range(300):
        sim.despachar()
    despachos = lote(sim).despachos
    limpios, anomalias, turnos = analizar(list(despachos))
    assert len(limpios) == 300 and anomalias == []
    assert [t.estado for t in turnos] == ["ABIERTO"]
    for d in despachos:
        assert d.surtidor_marca == MarcaSurtidor.SIMULADOR
        assert d.volumen_bruto == d.volumen_bruto.quantize(Decimal("0.001")) and d.volumen_neto <= d.volumen_bruto
        assert abs(d.valor - d.volumen_bruto * d.ppu) <= TOLERANCIA_VALOR
        assert d.forma_pago is FormaPago.CONTADO or d.placa_tipo is PlacaTipo.PLACA  # crédito siempre con placa


def test_variedad_realista():
    sim = simulador()
    despachos = [sim.despachar() for _ in range(400)]
    normalizados = lote(sim).despachos
    assert {f["LADO"] for f in despachos} == {"A", "B"}
    assert {f["PRD"] for f in despachos} == {"CORRIENTE", "DIESEL"}
    assert {d.forma_pago for d in normalizados} == {FormaPago.CONTADO, FormaPago.CREDITO}
    assert set(Counter(d.placa_tipo for d in normalizados)) == set(PlacaTipo)
    assert any(d.valor % 10_000 == 0 for d in normalizados)  # ventas "por plata"
    assert any(d.valor != (d.volumen_bruto * d.ppu).quantize(Decimal(1)) for d in normalizados)  # redondeo


def test_los_contadores_suben_lo_despachado():
    sim = simulador(lados=("A",))
    a = sim.despachar(pistola=1, galones=Decimal("3.120"))
    b = sim.despachar(pistola=1, galones=Decimal("1.861"))
    assert Decimal(b["TOT-GROS-1"]) - Decimal(a["TOT-GROS-1"]) == Decimal("1.861")
    assert Decimal(b["TOT-MONEY-1"]) - Decimal(a["TOT-MONEY-1"]) == Decimal(b["MONEY"])
    assert int(b["ID-DESPACHO"]) == int(a["ID-DESPACHO"]) + 1


def test_venta_por_plata_y_por_galones():
    sim = simulador()
    plata = sim.despachar(pistola=2, valor=Decimal(50_000))
    galones = sim.despachar(pistola=2, galones=Decimal("1.861"))
    assert (plata["MONEY"], plata["VOLG"]) == ("50000", "3.127")  # 50.000 / 15.990 = 3,1269...
    assert (galones["MONEY"], galones["PPU"]) == ("29757", "15990")  # el caso del riesgo de decimales


def test_falla_salto_deja_un_hueco_que_calidad_detecta():
    sim = simulador()
    sim.despachar("A", pistola=1)
    hueco = sim.despachar("A", pistola=1, falla="salto")
    assert hueco["ID-DESPACHO"] == "3"  # el 2 se perdió
    _, anomalias, _ = analizar(list(lote(sim).despachos))
    assert [(a.tipo, a.id_despacho) for a in anomalias] == [("GAP_TOTALIZADOR", 3)]


def test_falla_repetido_reenvia_la_misma_fila():
    sim = simulador()
    original = sim.despachar()
    assert sim.despachar(falla="repetido") == original
    limpios, anomalias, _ = analizar(list(lote(sim).despachos))
    assert len(limpios) == 1 and anomalias == []


@pytest.mark.parametrize(
    ("llamada", "mensaje"),
    [
        (lambda s: s.despachar(falla="repetido"), "no hay un despacho anterior"),
        (lambda s: s.despachar(falla="incendio"), "falla desconocida"),
        (lambda s: s.despachar("C"), "no existe"),
    ],
)
def test_usos_invalidos(llamada, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        llamada(simulador())


def test_leer_sin_confirmar_vuelve_a_entregar_y_confirmar_respeta_lo_nuevo():
    sim = simulador()
    sim.despachar()
    sim.despachar()
    primero = lote(sim)
    assert lote(sim).sha256 == primero.sha256  # sin confirmar: el mismo lote
    sim.despachar()  # llega mientras se guardaba el primero
    sim.confirmar(primero)
    assert [f["ID-DESPACHO"] for f in sim.pendientes] == ["3"]
    sim.confirmar(lote(sim))
    assert list(sim.leer()) == []


def test_cierre_de_turno():
    sim = simulador()
    sim.despachar()
    sim.cerrar_turno()
    sim.despachar()
    _, _, turnos = analizar(list(lote(sim).despachos))
    assert [(t.id_cierre, t.estado) for t in turnos] == [(1, "CERRADO"), (2, "ABIERTO")]


def test_retomar_continua_ids_turno_y_contadores():
    sim = simulador(lados=("A",))
    sim.retomar(ultimo_id=99, id_cierre=7, contadores={("A", 1): (Decimal("1000.000"), Decimal(16_000_000))})
    fila = sim.despachar("A", pistola=1, galones=Decimal("2.000"))
    assert (fila["ID-DESPACHO"], fila["ID-CIERRE"], fila["TOT-GROS-1"]) == ("100", "7", "1002.000")


# ------------------------------------------------------------------------------------------ archivo
def test_archivo_speed_solutions_lee_lo_que_escribe_el_simulador(tmp_path):
    sim = simulador()
    for _ in range(10):
        sim.despachar()
    ruta = tmp_path / "D261001_120000.xls"
    speed_solutions.escribir(ruta, sim.pendientes)

    fuente = ArchivoSpeedSolutions(ruta)
    (leido,) = list(fuente.leer())
    assert leido.marca == "SPEED_SOLUTIONS" and leido.nombre == ruta.name and leido.rechazos == ()
    simulados = lote(sim).despachos
    assert [(d.id_externo, d.valor, d.volumen_bruto, d.placa_tipo) for d in leido.despachos] == [
        (d.id_externo, d.valor, d.volumen_bruto, d.placa_tipo) for d in simulados
    ]
    fuente.confirmar(leido)
    assert list(fuente.leer()) == []


# ------------------------------------------------------------------------------------------ ingesta (PostgreSQL)
def despachos_en_bd(dsn: str) -> list[tuple]:
    with psycopg.connect(dsn) as c:
        return c.execute(
            """SELECT d.id_externo, s.marca, d.estado_facturacion FROM despacho d
               JOIN surtidor s ON s.id = d.surtidor_id ORDER BY d.id_externo"""
        ).fetchall()


def anomalias_en_bd(dsn: str) -> list[tuple]:
    """Anomalías de datos. Se omite VEHICULO_SIN_CLIENTE: es correcta (crédito a placas que en la prueba no
    tienen cliente registrado) y no habla de la calidad del surtidor."""
    with psycopg.connect(dsn) as c:
        return c.execute(
            """SELECT a.tipo, d.id_externo FROM anomalia a JOIN despacho d ON d.id = a.despacho_id
               WHERE a.tipo <> 'VEHICULO_SIN_CLIENTE' ORDER BY d.id_externo, a.tipo"""
        ).fetchall()


@pytest.mark.bd
def test_el_sistema_recibe_ventas_del_simulador(bd):
    sim = simulador()
    for _ in range(4):  # varios lotes, como llegarían en vivo
        for _ in range(5):
            sim.despachar()
        (r,) = ingerir(bd.dsn, sim)
        assert (r.resumen.nuevos, r.resumen.duplicados, r.resumen.conflictos) == (5, 0, 0)
    assert sim.pendientes == []
    filas = despachos_en_bd(bd.dsn)
    assert [f[0] for f in filas] == list(range(1, 21))
    assert {(f[1], f[2]) for f in filas} == {("SIMULADOR", "PENDIENTE")}
    assert anomalias_en_bd(bd.dsn) == []
    assert ingerir(bd.dsn, sim) == []  # nada pendiente


@pytest.mark.bd
def test_salto_de_contador_entre_lotes_se_detecta_en_la_base(bd):
    sim = simulador()
    sim.despachar("A", pistola=2)
    ingerir(bd.dsn, sim)
    sim.despachar("A", pistola=2, falla="salto")  # lote de UN despacho: calidad sola no puede verlo
    ingerir(bd.dsn, sim)
    assert anomalias_en_bd(bd.dsn) == [("GAP_TOTALIZADOR", 3)]


@pytest.mark.bd
def test_despacho_repetido_en_otro_lote_no_se_duplica(bd):
    sim = simulador()
    sim.despachar()
    ingerir(bd.dsn, sim)
    sim.despachar(falla="repetido")
    sim.despachar()
    (r,) = ingerir(bd.dsn, sim)
    assert (r.resumen.nuevos, r.resumen.duplicados) == (1, 1)
    assert len(despachos_en_bd(bd.dsn)) == 2


@pytest.mark.bd
def test_un_lote_identico_a_uno_ya_guardado_se_ignora(bd):
    sim = simulador()
    sim.despachar()
    ingerir(bd.dsn, sim)
    sim.despachar(falla="repetido")  # el lote solo trae la fila repetida: mismo contenido, mismo sha256
    (r,) = ingerir(bd.dsn, sim)
    assert r.resumen.archivo_repetido and sim.pendientes == []


@pytest.mark.bd
def test_si_la_carga_falla_el_lote_no_se_pierde(bd):
    sim = simulador()
    sim.despachar()
    with pytest.raises(psycopg.OperationalError):
        ingerir("postgresql://nadie@127.0.0.1:1/ninguna?connect_timeout=1", sim)
    assert len(sim.pendientes) == 1
    sim.despachar()
    (r,) = ingerir(bd.dsn, sim)
    assert r.resumen.nuevos == 2


@pytest.mark.bd
def test_archivo_se_ingiere_una_sola_vez(bd, tmp_path):
    sim = simulador()
    for _ in range(6):
        sim.despachar()
    ruta = tmp_path / "export.xls"
    speed_solutions.escribir(ruta, sim.pendientes)
    (r,) = ingerir(bd.dsn, ArchivoSpeedSolutions(ruta))
    assert r.resumen.nuevos == 6
    (otra,) = ingerir(bd.dsn, ArchivoSpeedSolutions(ruta))  # otra instancia: la base lo reconoce
    assert otra.resumen.archivo_repetido
    assert {f[1] for f in despachos_en_bd(bd.dsn)} == {"SPEED_SOLUTIONS"}


# ------------------------------------------------------------------------------------------ comando
def test_comando_en_seco_no_necesita_base(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert comando.main(["--cantidad", "3", "--cada", "0", "--en-seco", "--semilla", "1"]) == 0


def test_comando_sin_base_y_sin_en_seco(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert comando.main(["--cantidad", "1", "--cada", "0"]) == 2


@pytest.mark.bd
def test_comando_guarda_y_retoma_donde_quedo(bd, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", bd.dsn)
    assert comando.main(["--cantidad", "5", "--cada", "0", "--semilla", "1"]) == 0
    assert comando.main(["--cantidad", "5", "--cada", "0", "--semilla", "2", "--lado", "A"]) == 0
    assert [f[0] for f in despachos_en_bd(bd.dsn)] == list(range(1, 11))
    assert anomalias_en_bd(bd.dsn) == []  # sin choques de id ni saltos de contador entre corridas


def test_duraciones():
    assert comando.segundos("2m") == 120
    with pytest.raises(Exception, match="duración inválida"):
        comando.segundos("pronto")
