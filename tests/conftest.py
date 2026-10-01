"""Fixtures compartidas.

`bd`: PostgreSQL real (el del docker-compose), con un esquema temporal propio por prueba al que se le
aplican las migraciones de Alembic (`upgrade head`). Se borra al terminar: no toca las tablas de la base.
Sin ESTACION_TEST_DSN la prueba se salta y lo dice (`make test` la define; `make test-rapido` no).
"""

from __future__ import annotations

import os
import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

RAIZ = Path(__file__).resolve().parent.parent
HASH_PIN = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo"  # hash de mentira, formato argon2


def migrar(dsn: str, destino: str = "head", *, bajar: bool = False) -> None:
    """Corre Alembic contra `dsn` (que ya trae el search_path del esquema temporal)."""
    import psycopg
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    config = Config(str(RAIZ / "alembic.ini"))
    motor = create_engine("postgresql+psycopg://", creator=lambda: psycopg.connect(dsn), poolclass=NullPool)
    with motor.begin() as conexion:
        config.attributes["connection"] = conexion
        (command.downgrade if bajar else command.upgrade)(config, destino)
    motor.dispose()


@dataclass(frozen=True)
class BaseDePrueba:
    dsn: str  # apunta al esquema temporal (search_path)
    esquema: str

    def consultar(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            return conn.execute(sql, params).fetchall()

    def ejecutar(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql, params)

    def valor(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        filas = self.consultar(sql, params)
        return filas[0][0] if filas else None


@pytest.fixture
def esquema_vacio() -> Iterator[BaseDePrueba]:
    """Esquema temporal SIN migraciones (para probar `upgrade`/`downgrade` desde cero)."""
    base = os.environ.get("ESTACION_TEST_DSN", "").strip()
    if not base:
        pytest.skip("defina ESTACION_TEST_DSN (o use `make test`) para probar contra PostgreSQL")
    import psycopg
    from psycopg.conninfo import make_conninfo

    esquema = f"prueba_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {esquema}")
        try:
            yield BaseDePrueba(dsn=make_conninfo(base, options=f"-c search_path={esquema}"), esquema=esquema)
        finally:
            admin.execute(f"DROP SCHEMA {esquema} CASCADE")


@pytest.fixture
def bd(esquema_vacio: BaseDePrueba) -> BaseDePrueba:
    migrar(esquema_vacio.dsn)
    return esquema_vacio


# ------------------------------------------------------------------------------------------ datos de apoyo
class Datos:
    """Inserta filas sintéticas válidas; cada método acepta cambios para probar una restricción."""

    def __init__(self, bd: BaseDePrueba):
        import psycopg

        self.bd = bd
        self.conn = psycopg.connect(bd.dsn, autocommit=True)

    def uno(self, sql: str, params: Any = ()) -> Any:
        fila = self.conn.execute(sql, params).fetchone()
        return None if fila is None else fila[0]

    def insertar(self, tabla: str, **campos: Any) -> int:
        columnas = ", ".join(campos)
        marcas = ", ".join(["%s"] * len(campos))
        return self.uno(f"INSERT INTO {tabla} ({columnas}) VALUES ({marcas}) RETURNING id", tuple(campos.values()))

    def estacion(self, **cambios: Any) -> int:
        campos = dict(
            nit="900123456", dv="8", razon_social="ESTACION DE PRUEBA S A S",
            direccion_establecimiento="CL 1 # 2-3", municipio="TUNJA", codigo_municipio_dane="15001",
            departamento="BOYACÁ", codigo_departamento="15", telefono="3000000000", email="estacion@example.com",
            responsabilidades=["O-23", "R-99-PN"], responsable_iva=True, actividades_ciiu=["4731", "4732"],
        )  # fmt: skip
        return self.insertar("estacion", **{**campos, **cambios})

    def numeracion(self, estacion_id: int, **cambios: Any) -> int:
        campos = dict(
            estacion_id=estacion_id, prefijo="SETP", numero_resolucion="18760000001", desde=1, hasta=5000,
            vigente_desde="2026-01-01", vigente_hasta="2027-12-31", siguiente=1,
        )  # fmt: skip
        return self.insertar("numeracion", **{**campos, **cambios})

    def usuario(self, **cambios: Any) -> int:
        campos = dict(usuario=f"bombero{uuid.uuid4().hex[:6]}", nombre="Bombero Uno", rol="BOMBERO", pin_hash=HASH_PIN)
        return self.insertar("usuario", **{**campos, **cambios})

    def cliente(self, **cambios: Any) -> int:
        campos = dict(
            tipo_documento="CC", numero_documento=str(random.randrange(10**7, 10**10)), nombres="ANA MARÍA",
            apellidos="PÉREZ GÓMEZ", email="ana@example.com", autoriza_tratamiento=True,
            fecha_autorizacion="2026-09-30 10:00-05",
        )  # fmt: skip
        return self.insertar("cliente", **{**campos, **cambios})

    def despacho(self) -> int:
        s = self.uno("""INSERT INTO surtidor (marca, codigo_externo) VALUES ('SPEED_SOLUTIONS', 'S1')
                        ON CONFLICT (marca, codigo_externo) DO UPDATE SET marca = EXCLUDED.marca RETURNING id""")
        p = self.uno("""INSERT INTO producto (codigo, nombre) VALUES ('DIESEL', 'Diesel')
                        ON CONFLICT (codigo) DO UPDATE SET codigo = EXCLUDED.codigo RETURNING id""")
        i = self.insertar("importacion", surtidor_id=s, archivo_nombre="x.xls", archivo_sha256=uuid.uuid4().hex * 2)
        t = self.uno(
            """INSERT INTO turno (surtidor_id, id_cierre, inicio, fin) VALUES (%s, 1, now(), now())
                        ON CONFLICT (surtidor_id, id_cierre) DO UPDATE SET fin = EXCLUDED.fin RETURNING id""",
            (s,),
        )
        return self.insertar(
            "despacho", surtidor_id=s, id_externo=random.randrange(10**9), turno_id=t, lado="A", pistola=1,
            producto_id=p, inicio="2026-09-30 10:00", fin="2026-09-30 10:02", inicio_reloj="2026-09-30 10:00",
            fin_reloj="2026-09-30 10:02", volumen_bruto="1.861", volumen_neto="1.861", valor=29757, ppu=15990,
            forma_pago="CONTADO", placa_tipo="SIN_PLACA", hash_contenido="h", crudo="{}", importacion_id=i,
        )  # fmt: skip

    def venta_manual(self, usuario_id: int, **cambios: Any) -> int:
        s = self.uno("SELECT id FROM surtidor LIMIT 1") or self.uno(
            "INSERT INTO surtidor (marca, codigo_externo) VALUES ('WAYNE', 'W1') RETURNING id"
        )
        p = self.uno("SELECT id FROM producto LIMIT 1") or self.uno(
            "INSERT INTO producto (codigo, nombre) VALUES ('CORRIENTE', 'Corriente') RETURNING id"
        )
        campos = dict(
            surtidor_id=s, lado="B", producto_id=p, volumen="3.120", valor=50000, ppu=16025, forma_pago="CONTADO",
            usuario_id=usuario_id, registrada_en="2026-09-30 11:00",
        )  # fmt: skip
        return self.insertar("venta_manual", **{**campos, **cambios})


@pytest.fixture
def datos(bd):
    d = Datos(bd)
    yield d
    d.conn.close()


# ------------------------------------------------------------------------------------------ servicios
# Secretos de MENTIRA para el ambiente de habilitación: el generador los usa, nada sale de la máquina.
ENTORNO_DIAN = {
    "DIAN_AMBIENTE": "2",
    "DIAN_SOFTWARE_ID": "00000000-0000-4000-8000-000000000000",
    "DIAN_SOFTWARE_PIN": "12345",
    "DIAN_CLAVE_TECNICA": "clave-tecnica-de-prueba",
}


@dataclass(frozen=True)
class Escenario:
    """Estación, numeración SETP 1-5000, un bombero y un cliente con CC listos para facturar."""

    datos: Datos
    estacion: int
    numeracion: int
    usuario: int
    cliente: int

    def conectar(self) -> Any:
        import psycopg

        return psycopg.connect(self.datos.bd.dsn, autocommit=True)

    def siguiente(self) -> int:
        return self.datos.uno("SELECT siguiente FROM numeracion WHERE id = %s", (self.numeracion,))


@pytest.fixture
def escenario(datos: Datos) -> Escenario:
    estacion = datos.estacion()
    return Escenario(datos, estacion, datos.numeracion(estacion), datos.usuario(), datos.cliente())


@pytest.fixture
def conn(escenario: Escenario) -> Iterator[Any]:
    c = escenario.conectar()
    yield c
    c.close()
