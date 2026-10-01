"""Esquema inicial: el db/schema.sql original, con las dos inconsistencias conocidas corregidas.

  * despacho.estado_facturacion: los 5 estados de `EstadoFacturacion` (antes el CHECK tenía 3).
  * cliente.tipo_documento: los 6 tipos de `documentos.TIPOS_DOCUMENTO` (antes TI, RC y NUIP en lugar de
    NIT_EXTERIOR y PPT).
La fuente de verdad de esos valores es el código Python; `tests/test_migraciones.py` comprueba que los CHECK
de la base coincidan con él.

Convenciones (del schema.sql original)
  * Fechas/horas del negocio: TIMESTAMP *sin* zona = hora local de la estación (Colombia no tiene horario
    de verano). Marcas técnicas (creado_en...): TIMESTAMPTZ.
  * Dinero en pesos (NUMERIC); volúmenes en galones con 3 decimales. Nunca float.

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
-- ---------------------------------------------------------------------
-- Catálogos
-- ---------------------------------------------------------------------
CREATE TABLE surtidor (
    id              SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    marca           TEXT     NOT NULL CHECK (marca IN ('SPEED_SOLUTIONS', 'WAYNE')),
    codigo_externo  TEXT     NOT NULL,             -- ID-SURTIDOR del archivo
    descripcion     TEXT,
    activo          BOOLEAN  NOT NULL DEFAULT TRUE,
    UNIQUE (marca, codigo_externo)
);

CREATE TABLE producto (
    id      SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo  TEXT NOT NULL UNIQUE,                  -- 'DIESEL', 'CORRIENTE', ...
    nombre  TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Trazabilidad de importaciones
-- ---------------------------------------------------------------------
CREATE TABLE importacion (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surtidor_id       SMALLINT    NOT NULL REFERENCES surtidor (id),
    archivo_nombre    TEXT        NOT NULL,
    archivo_sha256    CHAR(64)    NOT NULL,
    iniciada_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    terminada_en      TIMESTAMPTZ,
    filas_leidas      INTEGER,
    filas_nuevas      INTEGER,
    filas_duplicadas  INTEGER,
    filas_conflicto   INTEGER,
    filas_rechazadas  INTEGER,
    -- el mismo archivo exacto no se procesa dos veces
    UNIQUE (surtidor_id, archivo_sha256)
);

-- ---------------------------------------------------------------------
-- Turnos (ID-CIERRE del surtidor)
-- ---------------------------------------------------------------------
CREATE TABLE turno (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surtidor_id  SMALLINT  NOT NULL REFERENCES surtidor (id),
    id_cierre    INTEGER   NOT NULL,
    inicio       TIMESTAMP NOT NULL,               -- primer despacho del turno
    fin          TIMESTAMP NOT NULL,               -- último despacho conocido
    -- El archivo no trae la hora de cierre: un turno es ABIERTO mientras sea
    -- el de mayor id_cierre del surtidor; pasa a CERRADO cuando aparece uno mayor.
    estado       TEXT      NOT NULL DEFAULT 'ABIERTO'
                 CHECK (estado IN ('ABIERTO', 'CERRADO')),
    UNIQUE (surtidor_id, id_cierre)
);

-- ---------------------------------------------------------------------
-- Clientes y su mapeo desde placa / alias de equipo
-- (datos personales: Ley 1581 de 2012 -> se registra la autorización)
-- ---------------------------------------------------------------------
CREATE TABLE cliente (
    id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo_documento           TEXT NOT NULL CHECK (tipo_documento IN ('CC', 'NIT', 'NIT_EXTERIOR', 'CE', 'PA', 'PPT')),
    numero_documento         TEXT NOT NULL,
    digito_verificacion      CHAR(1),
    razon_social             TEXT NOT NULL,
    email                    TEXT,
    telefono                 TEXT,
    direccion                TEXT,
    municipio_codigo         TEXT,                 -- código DANE, lo pide la factura
    autoriza_tratamiento     BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_autorizacion       TIMESTAMPTZ,
    activo                   BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tipo_documento, numero_documento),
    CHECK (NOT autoriza_tratamiento OR fecha_autorizacion IS NOT NULL)
);

-- Un vehículo (placa) o un equipo de obra ("BOBCAT", "MOTONIVELADORA"...)
-- pertenece a un cliente. En el archivo real ~10 % de las etiquetas no son
-- placas sino nombres de maquinaria, por eso `tipo`.
-- v1: una placa pertenece a un solo cliente a la vez (sin histórico de dueños).
CREATE TABLE vehiculo (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    identificador  TEXT NOT NULL UNIQUE,           -- placa normalizada o alias en mayúsculas
    tipo           TEXT NOT NULL CHECK (tipo IN ('PLACA', 'EQUIPO_TEXTO')),
    cliente_id     BIGINT NOT NULL REFERENCES cliente (id),
    descripcion    TEXT,
    activo         BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX ix_vehiculo_cliente ON vehiculo (cliente_id);

-- ---------------------------------------------------------------------
-- Despachos (formato ya normalizado, común a Speed Solutions y Wayne)
-- ---------------------------------------------------------------------
CREATE TABLE despacho (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surtidor_id         SMALLINT  NOT NULL REFERENCES surtidor (id),
    id_externo          BIGINT    NOT NULL,        -- ID-DESPACHO del equipo
    turno_id            BIGINT    NOT NULL REFERENCES turno (id),
    lado                CHAR(1)   NOT NULL,
    pistola             SMALLINT  NOT NULL,
    producto_id         SMALLINT  NOT NULL REFERENCES producto (id),

    -- Tiempos: `inicio/fin` son los efectivos (corregidos si el reloj falló);
    -- `*_reloj` es lo que reportó el surtidor, sin tocar.
    inicio              TIMESTAMP NOT NULL,
    fin                 TIMESTAMP NOT NULL,
    inicio_reloj        TIMESTAMP NOT NULL,
    fin_reloj           TIMESTAMP NOT NULL,
    fecha_corregida     BOOLEAN   NOT NULL DEFAULT FALSE,
    fecha_operativa     DATE GENERATED ALWAYS AS (inicio::date) STORED,

    -- El surtidor calcula el valor con el volumen BRUTO (verificado en los datos:
    -- error máx. 5,3 pesos con VOLG vs 21,75 con VOLN). Se factura volumen_bruto.
    volumen_bruto       NUMERIC(12,3) NOT NULL CHECK (volumen_bruto >= 0),
    volumen_neto        NUMERIC(12,3) NOT NULL CHECK (volumen_neto  >= 0),
    valor               NUMERIC(14,0) NOT NULL CHECK (valor >= 0),
    ppu                 NUMERIC(10,2) NOT NULL CHECK (ppu > 0),
    forma_pago          TEXT NOT NULL CHECK (forma_pago IN ('CONTADO', 'CREDITO', 'OTRO')),

    -- Identificación del vehículo tal como la digitó el operador
    placa_raw           TEXT NOT NULL DEFAULT '',
    placa_tipo          TEXT NOT NULL CHECK (placa_tipo IN ('PLACA', 'SIN_PLACA', 'EQUIPO_TEXTO')),
    ref_vehiculo        TEXT,                      -- normalizada; NULL si SIN_PLACA
    vehiculo_id         BIGINT REFERENCES vehiculo (id),
    kilometraje_raw     TEXT NOT NULL DEFAULT '',
    kilometraje         INTEGER,                   -- NULL si el dato es basura (0, 1, texto...)

    -- Lectura del contador acumulado del surtidor DESPUÉS de este despacho
    -- (por lado/pistola). Sirve para detectar despachos faltantes.
    totalizador_vol     NUMERIC(14,3),
    totalizador_valor   NUMERIC(18,0),

    estado_facturacion  TEXT NOT NULL DEFAULT 'PENDIENTE'
                        CHECK (estado_facturacion IN ('PENDIENTE', 'EN_PROCESO', 'FACTURADO', 'INCIERTO', 'NO_FACTURABLE')),

    hash_contenido      TEXT  NOT NULL,            -- detecta re-exportaciones con datos distintos
    crudo               JSONB NOT NULL,
    importacion_id      BIGINT NOT NULL REFERENCES importacion (id),

    UNIQUE (surtidor_id, id_externo)
);
CREATE INDEX ix_despacho_turno      ON despacho (turno_id);
CREATE INDEX ix_despacho_fecha      ON despacho (surtidor_id, fecha_operativa);
CREATE INDEX ix_despacho_ref        ON despacho (ref_vehiculo) WHERE ref_vehiculo IS NOT NULL;
CREATE INDEX ix_despacho_pendientes ON despacho (id) WHERE estado_facturacion = 'PENDIENTE';

-- ---------------------------------------------------------------------
-- Anomalías detectadas (para revisión humana) y filas ilegibles
-- ---------------------------------------------------------------------
CREATE TABLE anomalia (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    importacion_id  BIGINT NOT NULL REFERENCES importacion (id),
    despacho_id     BIGINT REFERENCES despacho (id),
    turno_id        BIGINT REFERENCES turno (id),
    tipo            TEXT NOT NULL,
    severidad       TEXT NOT NULL CHECK (severidad IN ('INFO', 'AVISO', 'ALTA')),
    detalle         JSONB NOT NULL DEFAULT '{}',
    creada_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resuelta        BOOLEAN NOT NULL DEFAULT FALSE,
    resuelta_en     TIMESTAMPTZ,
    nota            TEXT
);
-- Las exportaciones del surtidor se solapan: evita re-registrar la misma anomalía.
CREATE UNIQUE INDEX ux_anomalia_despacho_tipo ON anomalia (despacho_id, tipo)
    WHERE despacho_id IS NOT NULL;
CREATE INDEX ix_anomalia_abiertas ON anomalia (severidad, tipo) WHERE NOT resuelta;

CREATE TABLE fila_rechazada (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    importacion_id  BIGINT NOT NULL REFERENCES importacion (id),
    id_externo      BIGINT,
    motivo          TEXT   NOT NULL,
    crudo           JSONB  NOT NULL
);

-- ---------------------------------------------------------------------
-- Vistas base para los reportes de la Fase 4
-- ---------------------------------------------------------------------
CREATE VIEW v_ventas_diarias AS
SELECT d.surtidor_id,
       d.fecha_operativa            AS fecha,
       p.codigo                     AS producto,
       d.forma_pago,
       count(*)                     AS despachos,
       sum(d.volumen_bruto)         AS galones,
       sum(d.valor)                 AS valor
FROM despacho d
JOIN producto p ON p.id = d.producto_id
GROUP BY d.surtidor_id, d.fecha_operativa, p.codigo, d.forma_pago;

CREATE VIEW v_ventas_mensuales AS
SELECT d.surtidor_id,
       date_trunc('month', d.fecha_operativa)::date AS mes,
       p.codigo                     AS producto,
       d.forma_pago,
       count(*)                     AS despachos,
       sum(d.volumen_bruto)         AS galones,
       sum(d.valor)                 AS valor
FROM despacho d
JOIN producto p ON p.id = d.producto_id
GROUP BY d.surtidor_id, date_trunc('month', d.fecha_operativa), p.codigo, d.forma_pago;

-- Resumen por turno y lado. El contador es acumulado, por eso max() = lectura final.
CREATE VIEW v_turno_resumen AS
SELECT t.id                         AS turno_id,
       t.surtidor_id,
       t.id_cierre,
       t.estado,
       t.inicio,
       t.fin,
       d.lado,
       count(*)                     AS despachos,
       sum(d.volumen_bruto)         AS galones,
       sum(d.valor)                 AS valor,
       max(d.totalizador_vol)       AS lectura_final_vol,
       max(d.totalizador_valor)     AS lectura_final_valor
FROM turno t
JOIN despacho d ON d.turno_id = t.id
GROUP BY t.id, t.surtidor_id, t.id_cierre, t.estado, t.inicio, t.fin, d.lado;
"""

DOWNGRADE = """
DROP VIEW IF EXISTS v_turno_resumen, v_ventas_mensuales, v_ventas_diarias;
DROP TABLE IF EXISTS fila_rechazada, anomalia, despacho, vehiculo, cliente, turno, importacion, producto, surtidor;
"""


def _sql(texto: str) -> None:
    # SQL crudo por psycopg, sin parámetros: admite varias sentencias y '%' en los comentarios.
    op.get_bind().connection.driver_connection.execute(texto)  # type: ignore[union-attr]


def upgrade() -> None:
    _sql(UPGRADE)


def downgrade() -> None:
    _sql(DOWNGRADE)
