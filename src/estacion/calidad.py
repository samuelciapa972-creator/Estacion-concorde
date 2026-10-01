"""Validación y limpieza de despachos ya normalizados (independiente de la marca).

Entrada: lista de `Despacho`. Salida: despachos (con fechas corregidas si hizo
falta), anomalías para revisión humana y los turnos derivados.

Reglas
  1. Duplicados dentro del archivo (mismo id): idéntico -> se descarta; distinto -> conflicto.
  2. Fechas malas del reloj del surtidor. Los ids son cronológicos, así que el tiempo debe
     avanzar con el id. Se parte la secuencia en "bloques continuos" y se conserva la cadena
     de bloques coherente más larga; los demás son islas con fecha mala. Casos reales:
       - 1 despacho con fecha 2005 (reloj reiniciado)             -> estimación por interpolación
       - 47 despachos del 31-ago grabados como 1-ago               -> desplazamiento de 30 días
     El desplazamiento solo se aplica si existe UN ÚNICO número entero de días que deja la
     isla entre el bloque anterior y el siguiente; si no, no se inventa nada y se avisa.
     El valor original siempre se conserva en `inicio_reloj/fin_reloj`.
  3. Valor vs volumen bruto * precio (el surtidor calcula el valor con el bruto).
  4. Continuidad del contador acumulado por lado/pistola: si el contador subió más de lo
     despachado, falta un despacho en el archivo (o hubo consumo sin registrar).
  5. Crédito sin placa: no se puede facturar a un cliente.
  6. Turnos: el de mayor id_cierre se considera ABIERTO.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from .modelos import (
    Anomalia,
    Despacho,
    FormaPago,
    PlacaTipo,
    Severidad,
    TurnoDerivado,
)

TOLERANCIA_VALOR = Decimal("10")  # pesos. En los datos reales el máximo fue 5,3
TOLERANCIA_TOTALIZADOR = Decimal("0.005")  # galones (el equipo maneja 3 decimales)
SALTO_MAX = timedelta(days=2)  # más que esto entre despachos consecutivos = quiebre
SOLAPE_MAX = timedelta(hours=3)  # un despacho puede empezar antes de que termine el anterior (otro lado)
DURACION_MAX_PLAUSIBLE = timedelta(hours=3)
MAX_FILAS_INTERPOLABLES = 5  # islas más grandes sin desplazamiento exacto no se estiman


def analizar(
    despachos: list[Despacho],
) -> tuple[list[Despacho], list[Anomalia], list[TurnoDerivado]]:
    anomalias: list[Anomalia] = []
    lista = _deduplicar(despachos, anomalias)
    lista = _corregir_fechas(lista, anomalias)
    _validar_valor_y_pago(lista, anomalias)
    _validar_totalizadores(lista, anomalias)
    return lista, anomalias, derivar_turnos(lista)


# --------------------------------------------------------------------------
def _deduplicar(despachos: list[Despacho], anomalias: list[Anomalia]) -> list[Despacho]:
    vistos: dict[int, Despacho] = {}
    for d in sorted(despachos, key=lambda x: x.id_externo):
        previo = vistos.get(d.id_externo)
        if previo is None:
            vistos[d.id_externo] = d
        elif previo.hash_contenido != d.hash_contenido:
            anomalias.append(
                Anomalia(
                    "DUPLICADO_CONFLICTIVO",
                    Severidad.ALTA,
                    id_despacho=d.id_externo,
                    detalle={"nota": "mismo ID-DESPACHO con contenido distinto en el archivo"},
                )
            )
    return list(vistos.values())


def _bloques_continuos(lista: list[Despacho]) -> list[tuple[int, int]]:
    """Índices (inicio, fin) inclusive de tramos donde el tiempo avanza con el id."""
    bloques, desde = [], 0
    for i in range(1, len(lista)):
        salto = lista[i].inicio_reloj - lista[i - 1].fin_reloj
        if not (-SOLAPE_MAX <= salto <= SALTO_MAX):
            bloques.append((desde, i - 1))
            desde = i
    bloques.append((desde, len(lista) - 1))
    return bloques


def _bloques_confiables(lista: list[Despacho], bloques: list[tuple[int, int]]) -> set[int]:
    """Cadena de bloques cronológicamente coherente con más despachos (LIS ponderado)."""
    t0 = [lista[a].inicio_reloj for a, _ in bloques]
    t1 = [max(d.fin_reloj for d in lista[a : b + 1]) for a, b in bloques]
    peso = [b - a + 1 for a, b in bloques]
    mejor, padre = peso[:], [-1] * len(bloques)
    for i in range(len(bloques)):
        for j in range(i):
            if t1[j] <= t0[i] + SOLAPE_MAX and mejor[j] + peso[i] > mejor[i]:
                mejor[i], padre[i] = mejor[j] + peso[i], j
    k = max(range(len(bloques)), key=lambda i: mejor[i])
    cadena: set[int] = set()
    while k != -1:
        cadena.add(k)
        k = padre[k]
    return cadena


def _corregir_fechas(lista: list[Despacho], anomalias: list[Anomalia]) -> list[Despacho]:
    for d in lista:
        if d.fin_reloj < d.inicio_reloj:
            anomalias.append(
                Anomalia(
                    "DURACION_NEGATIVA",
                    Severidad.AVISO,
                    id_despacho=d.id_externo,
                    detalle={"inicio_reloj": d.inicio_reloj.isoformat(), "fin_reloj": d.fin_reloj.isoformat()},
                )
            )
    if len(lista) < 2:
        return lista
    bloques = _bloques_continuos(lista)
    if len(bloques) == 1:
        return lista

    confiables = _bloques_confiables(lista, bloques)
    resultado = list(lista)
    for b, (a, z) in enumerate(bloques):
        if b in confiables:
            continue
        previo = next((lista[bloques[r][1]] for r in range(b - 1, -1, -1) if r in confiables), None)
        siguiente = next((lista[bloques[r][0]] for r in range(b + 1, len(bloques)) if r in confiables), None)
        for i, nueva in _estimar_isla(lista, a, z, previo, siguiente, anomalias):
            resultado[i] = nueva
    return resultado


def _estimar_isla(
    lista: list[Despacho],
    a: int,
    z: int,
    previo: Despacho | None,
    siguiente: Despacho | None,
    anomalias: list[Anomalia],
) -> list[tuple[int, Despacho]]:
    filas = lista[a : z + 1]

    def no_corregible(motivo: str) -> list[tuple[int, Despacho]]:
        for d in filas:
            anomalias.append(
                Anomalia(
                    "FECHA_INVALIDA_NO_CORREGIBLE",
                    Severidad.ALTA,
                    id_despacho=d.id_externo,
                    detalle={"motivo": motivo, "inicio_reloj": d.inicio_reloj.isoformat()},
                )
            )
        return []

    if previo is None or siguiente is None:
        return no_corregible("la isla está en el borde del archivo, sin vecino confiable de un lado")

    # 1) Desplazamiento por días enteros (caso "31-ago grabado como 1-ago").
    un_dia = timedelta(days=1)
    desde = min(d.inicio_reloj for d in filas)
    hasta = max(d.fin_reloj for d in filas)
    k_min = math.ceil((previo.fin_reloj - desde) / un_dia)
    k_max = math.floor((siguiente.inicio_reloj - hasta) / un_dia)
    if k_min == k_max:
        salida = []
        for i, d in enumerate(filas, start=a):
            nueva = replace(
                d, inicio=d.inicio_reloj + k_min * un_dia, fin=d.fin_reloj + k_min * un_dia, fecha_corregida=True
            )
            anomalias.append(
                Anomalia(
                    "FECHA_CORREGIDA",
                    Severidad.AVISO,
                    id_despacho=d.id_externo,
                    detalle={
                        "metodo": "DESPLAZAMIENTO_DIAS",
                        "dias": k_min,
                        "inicio_reloj": d.inicio_reloj.isoformat(),
                        "inicio_efectivo": nueva.inicio.isoformat(),
                        "nota": "único desplazamiento entero de días que encaja entre los bloques vecinos",
                    },
                )
            )
            salida.append((i, nueva))
        return salida

    # 2) Pocas filas sin desplazamiento exacto: interpolar entre los vecinos (estimación).
    if len(filas) <= MAX_FILAS_INTERPOLABLES:
        bajo = previo.fin_reloj
        alto = max(siguiente.inicio_reloj, bajo)
        salida = []
        for j, (i, d) in enumerate(zip(range(a, z + 1), filas, strict=True)):
            fin_est = bajo + (alto - bajo) * (j + 1) / (len(filas) + 1)
            dur = d.fin_reloj - d.inicio_reloj
            if not timedelta(0) <= dur <= DURACION_MAX_PLAUSIBLE:
                dur = timedelta(0)
            nueva = replace(d, inicio=fin_est - dur, fin=fin_est, fecha_corregida=True)
            anomalias.append(
                Anomalia(
                    "FECHA_CORREGIDA",
                    Severidad.AVISO,
                    id_despacho=d.id_externo,
                    detalle={
                        "metodo": "INTERPOLACION",
                        "inicio_reloj": d.inicio_reloj.isoformat(),
                        "inicio_efectivo": nueva.inicio.isoformat(),
                        "nota": "ESTIMADA entre el despacho anterior y el siguiente; confirmar con el turno",
                    },
                )
            )
            salida.append((i, nueva))
        return salida

    return no_corregible("ningún desplazamiento entero de días encaja y la isla es demasiado grande para interpolar")


def _validar_valor_y_pago(lista: list[Despacho], anomalias: list[Anomalia]) -> None:
    for d in lista:
        esperado = d.volumen_bruto * d.ppu
        if abs(d.valor - esperado) > TOLERANCIA_VALOR:
            anomalias.append(
                Anomalia(
                    "VALOR_INCONSISTENTE",
                    Severidad.AVISO,
                    id_despacho=d.id_externo,
                    detalle={"valor": str(d.valor), "volumen_bruto_x_ppu": str(esperado.quantize(Decimal("0.01")))},
                )
            )
        if d.forma_pago is FormaPago.OTRO:
            anomalias.append(
                Anomalia(
                    "FORMA_PAGO_DESCONOCIDA",
                    Severidad.AVISO,
                    id_despacho=d.id_externo,
                    detalle={"forma_pago": d.crudo.get("FORMA-PAGO", "")},
                )
            )
        if d.forma_pago is FormaPago.CREDITO and d.placa_tipo is PlacaTipo.SIN_PLACA:
            anomalias.append(
                Anomalia(
                    "CREDITO_SIN_IDENTIFICACION",
                    Severidad.ALTA,
                    id_despacho=d.id_externo,
                    detalle={"placa_raw": d.placa_raw, "valor": str(d.valor)},
                )
            )


def _validar_totalizadores(lista: list[Despacho], anomalias: list[Anomalia]) -> None:
    """El contador acumulado de cada lado/pistola debe subir exactamente lo despachado."""
    # (despacho, contador de volumen, contador de dinero) del último despacho con lectura, por lado/pistola
    ultimo: dict[tuple[str, int], tuple[Despacho, Decimal, Decimal]] = {}
    for d in lista:
        if d.totalizador_vol is None or d.totalizador_valor is None:
            continue
        anterior = ultimo.get((d.lado, d.pistola))
        if anterior is not None:
            previo, previo_vol, previo_valor = anterior
            dif_vol = d.totalizador_vol - (previo_vol + d.volumen_bruto)
            if abs(dif_vol) > TOLERANCIA_TOTALIZADOR:
                dif_valor = d.totalizador_valor - (previo_valor + d.valor)
                anomalias.append(
                    Anomalia(
                        "GAP_TOTALIZADOR" if dif_vol > 0 else "RETROCESO_TOTALIZADOR",
                        Severidad.ALTA,
                        id_despacho=d.id_externo,
                        detalle={
                            "lado": d.lado,
                            "pistola": d.pistola,
                            "despacho_previo": previo.id_externo,
                            "diferencia_galones": str(dif_vol),
                            "diferencia_pesos": str(dif_valor),
                            "nota": "faltan despachos en el archivo"
                            if dif_vol > 0
                            else "el contador retrocedió (¿reinicio o cambio de equipo?)",
                        },
                    )
                )
        ultimo[(d.lado, d.pistola)] = (d, d.totalizador_vol, d.totalizador_valor)


def derivar_turnos(lista: list[Despacho]) -> list[TurnoDerivado]:
    por_cierre: dict[int, list[Despacho]] = defaultdict(list)
    for d in lista:
        por_cierre[d.id_cierre].append(d)
    if not por_cierre:
        return []
    ultimo = max(por_cierre)
    return [
        TurnoDerivado(
            id_cierre=c,
            inicio=min(d.inicio for d in ds),
            fin=max(d.fin for d in ds),
            estado="ABIERTO" if c == ultimo else "CERRADO",
            despachos=len(ds),
            volumen_bruto=sum((d.volumen_bruto for d in ds), Decimal(0)),
            valor=sum((d.valor for d in ds), Decimal(0)),
        )
        for c, ds in sorted(por_cierre.items())
    ]
