"""Facturación propia: estación (emisor), numeración, usuarios, ventas manuales, facturas, cola y auditoría.

Garantías que da la BASE (no solo el código):
  * Un despacho (o una venta manual) tiene a lo sumo UNA factura: UNIQUE(despacho_id), UNIQUE(venta_manual_id)
    y exactamente uno de los dos (CHECK num_nonnulls).
  * Ningún número se repite: UNIQUE(prefijo, consecutivo); el prefijo debe ser el de su numeración (FK compuesta).
    Sin huecos: el consecutivo se toma de `numeracion.siguiente` en la MISMA transacción que crea la factura
    (Fase 2); si algo falla, se deshacen las dos cosas. Un rechazo NO gasta el número: la misma fila se
    corrige y se reenvía (por eso UNIQUE(despacho_id) aunque haya reintentos).
  * Idempotencia: UNIQUE(clave_emision).
  * Fallar cerrado: la tabla `estacion` no tiene valores por defecto en ningún dato fiscal; el DV del NIT
    se verifica con `dv_nit()` (misma fórmula que `documentos.calcular_dv`).
  * Ley 1581: no existe un cliente sin autorización de tratamiento de datos con su fecha.
  * PIN: solo se acepta un hash argon2/bcrypt, nunca el PIN en claro.
  * Auditoría: solo se agregan filas; UPDATE, DELETE y TRUNCATE fallan.

Secretos que NO van en la base: clave técnica de la numeración, PIN del software, certificado (variables de
entorno, ver .env.example).

Pendiente para la Fase 3 (enlace de una venta manual con el despacho que luego llega del surtidor): impedir en
la base que ese despacho reciba otra factura. Hoy solo lo impide el código que se escriba entonces.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = r"""
-- ---------------------------------------------------------------------
-- Dígito de verificación del NIT (pesos oficiales de la DIAN, de derecha a izquierda).
-- NULL si el NIT no es solo dígitos: así el CHECK de formato (no un error de conversión) es el que lo rechaza.
-- ---------------------------------------------------------------------
CREATE FUNCTION dv_nit(nit TEXT) RETURNS SMALLINT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT (CASE WHEN r < 2 THEN r ELSE 11 - r END)::smallint
    FROM (SELECT sum(substr(reverse(nit), i, 1)::int
                     * ('{3,7,13,17,19,23,29,37,41,43,47,53,59,67,71}'::int[])[i]) % 11 AS r
          FROM generate_series(1, length(nit)) AS i
          WHERE nit ~ '^[0-9]{1,15}$') AS t
$$;

-- ---------------------------------------------------------------------
-- Cliente: alineado con facturacion.modelos.Cliente (nombres/apellidos; para NIT, nombres = razón social)
-- ---------------------------------------------------------------------
ALTER TABLE cliente RENAME COLUMN razon_social TO nombres;
ALTER TABLE cliente ADD COLUMN apellidos TEXT NOT NULL DEFAULT '';
ALTER TABLE cliente ALTER COLUMN autoriza_tratamiento DROP DEFAULT;
ALTER TABLE cliente DROP CONSTRAINT cliente_check;   -- "si autoriza, hay fecha": la reemplaza la de abajo
ALTER TABLE cliente ADD CONSTRAINT cliente_autorizacion_check
    CHECK (autoriza_tratamiento AND fecha_autorizacion IS NOT NULL);
ALTER TABLE cliente ADD CONSTRAINT cliente_nombres_check CHECK (btrim(nombres) <> '');
ALTER TABLE cliente ADD CONSTRAINT cliente_numero_check CHECK (numero_documento ~ '^[A-Za-z0-9]{3,20}$');
ALTER TABLE cliente ADD CONSTRAINT cliente_dv_nit_check CHECK (
    CASE WHEN tipo_documento <> 'NIT' THEN TRUE
         WHEN numero_documento !~ '^[0-9]{8,10}$' THEN FALSE
         ELSE digito_verificacion IS NOT DISTINCT FROM dv_nit(numero_documento)::text END);  -- NIT sin DV: rechazado

-- ---------------------------------------------------------------------
-- Estación = el emisor. TODO sale del RUT (hoja de ESTABLECIMIENTOS para la dirección). Sin valores
-- por defecto: si un dato no se conoce, la fila no existe y el sistema se niega a emitir.
-- ---------------------------------------------------------------------
CREATE TABLE estacion (
    id                         SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nit                        TEXT     NOT NULL CHECK (nit ~ '^[0-9]{8,10}$'),
    dv                         CHAR(1)  NOT NULL,
    razon_social               TEXT     NOT NULL CHECK (btrim(razon_social) <> ''),
    nombre_comercial           TEXT,
    direccion_establecimiento  TEXT     NOT NULL CHECK (btrim(direccion_establecimiento) <> ''),
    municipio                  TEXT     NOT NULL CHECK (btrim(municipio) <> ''),
    codigo_municipio_dane      CHAR(5)  NOT NULL CHECK (codigo_municipio_dane ~ '^[0-9]{5}$'),
    departamento               TEXT     NOT NULL CHECK (btrim(departamento) <> ''),
    codigo_departamento        CHAR(2)  NOT NULL,
    codigo_postal              CHAR(6)  CHECK (codigo_postal ~ '^[0-9]{6}$'),
    telefono                   TEXT     NOT NULL CHECK (btrim(telefono) <> ''),
    email                      TEXT     NOT NULL CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]{2,}$'),
    -- Códigos del anexo técnico (O-13, O-15, O-23, O-47, R-99-PN...). Los define el contador con el RUT.
    responsabilidades          TEXT[]   NOT NULL
        CHECK (array_to_string(responsabilidades, ',') ~ '^[A-Z]-[0-9]{2}(-[A-Z]+)?(,[A-Z]-[0-9]{2}(-[A-Z]+)?)*$'),
    responsable_iva            BOOLEAN  NOT NULL,
    actividades_ciiu           TEXT[]   NOT NULL
        CHECK (array_to_string(actividades_ciiu, ',') ~ '^[0-9]{4}(,[0-9]{4})*$'),
    matricula_mercantil        TEXT     CHECK (btrim(matricula_mercantil) <> ''),
    activa                     BOOLEAN  NOT NULL DEFAULT TRUE,
    creada_en                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT estacion_dv_check CHECK (dv = dv_nit(nit)::text),
    CONSTRAINT estacion_depto_del_municipio_check CHECK (codigo_departamento = left(codigo_municipio_dane, 2))
);
CREATE UNIQUE INDEX ux_estacion_activa ON estacion ((TRUE)) WHERE activa;   -- alcance actual: una estación

-- ---------------------------------------------------------------------
-- Resolución de numeración de la DIAN (prefijo propio de este software). La clave técnica NO va aquí.
-- `siguiente` = próximo consecutivo a asignar; hasta + 1 = rango agotado.
-- ---------------------------------------------------------------------
CREATE TABLE numeracion (
    id                 SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    estacion_id        SMALLINT NOT NULL REFERENCES estacion (id),
    prefijo            TEXT     NOT NULL CHECK (prefijo ~ '^[A-Z0-9]{1,4}$'),
    numero_resolucion  TEXT     NOT NULL CHECK (numero_resolucion ~ '^[0-9]{1,20}$'),
    desde              BIGINT   NOT NULL CHECK (desde >= 1),
    hasta              BIGINT   NOT NULL,
    vigente_desde      DATE     NOT NULL,
    vigente_hasta      DATE     NOT NULL,
    siguiente          BIGINT   NOT NULL,
    activa             BOOLEAN  NOT NULL DEFAULT TRUE,
    creada_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT numeracion_rango_check    CHECK (hasta >= desde),
    CONSTRAINT numeracion_vigencia_check CHECK (vigente_hasta >= vigente_desde),
    CONSTRAINT numeracion_siguiente_check CHECK (siguiente BETWEEN desde AND hasta + 1),
    UNIQUE (prefijo, numero_resolucion),
    UNIQUE (id, prefijo)                                  -- destino de la FK compuesta de factura
);
CREATE UNIQUE INDEX ux_numeracion_activa ON numeracion (estacion_id) WHERE activa;

-- ---------------------------------------------------------------------
-- Usuarios (bombero/vendedor y administrador). PIN solo como hash argon2 o bcrypt.
-- ---------------------------------------------------------------------
CREATE TABLE usuario (
    id                 SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario            TEXT     NOT NULL UNIQUE CHECK (usuario ~ '^[a-z0-9._-]{3,40}$'),
    nombre             TEXT     NOT NULL CHECK (btrim(nombre) <> ''),
    rol                TEXT     NOT NULL CHECK (rol IN ('BOMBERO', 'ADMIN')),
    pin_hash           TEXT     NOT NULL CHECK (pin_hash ~ '^\$(argon2(id|i|d)|2[aby])\$'),
    activo             BOOLEAN  NOT NULL DEFAULT TRUE,
    intentos_fallidos  SMALLINT NOT NULL DEFAULT 0 CHECK (intentos_fallidos >= 0),
    bloqueado_hasta    TIMESTAMPTZ,
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Venta digitada por el bombero (modo manual, sin datos del surtidor)
-- ---------------------------------------------------------------------
CREATE TABLE venta_manual (
    id                  BIGINT   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surtidor_id         SMALLINT NOT NULL REFERENCES surtidor (id),
    lado                CHAR(1)  NOT NULL CHECK (lado ~ '^[A-Z0-9]$'),
    producto_id         SMALLINT NOT NULL REFERENCES producto (id),
    volumen             NUMERIC(12,3) NOT NULL CHECK (volumen > 0),
    valor               NUMERIC(14,0) NOT NULL CHECK (valor > 0),
    ppu                 NUMERIC(10,2) NOT NULL CHECK (ppu > 0),
    forma_pago          TEXT     NOT NULL CHECK (forma_pago IN ('CONTADO', 'CREDITO')),
    usuario_id          SMALLINT NOT NULL REFERENCES usuario (id),
    registrada_en       TIMESTAMP NOT NULL,                    -- hora local de la estación
    -- Despacho del surtidor al que corresponde, cuando llegue (Fase 3).
    despacho_id         BIGINT   UNIQUE REFERENCES despacho (id),
    estado_facturacion  TEXT     NOT NULL DEFAULT 'PENDIENTE'
                        CHECK (estado_facturacion IN ('PENDIENTE', 'EN_PROCESO', 'FACTURADO',
                                                       'INCIERTO', 'NO_FACTURABLE')),
    creada_en           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Facturas electrónicas (una fila por documento; los reintentos reutilizan la fila y su número)
-- ---------------------------------------------------------------------
CREATE TABLE factura (
    id               BIGINT   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clave_emision    TEXT     NOT NULL UNIQUE CHECK (clave_emision ~ '^[A-Za-z0-9]{16,64}$'),
    estado           TEXT     NOT NULL DEFAULT 'EN_PROCESO'
                     CHECK (estado IN ('EN_PROCESO', 'FACTURADO', 'RECHAZADO', 'INCIERTO')),
    despacho_id      BIGINT   UNIQUE REFERENCES despacho (id),
    venta_manual_id  BIGINT   UNIQUE REFERENCES venta_manual (id),
    cliente_id       BIGINT   NOT NULL REFERENCES cliente (id),
    usuario_id       SMALLINT NOT NULL REFERENCES usuario (id),
    numeracion_id    SMALLINT NOT NULL,
    prefijo          TEXT     NOT NULL,
    consecutivo      BIGINT   NOT NULL CHECK (consecutivo >= 1),
    numero           TEXT     GENERATED ALWAYS AS (prefijo || consecutivo::text) STORED,
    forma_pago       TEXT     NOT NULL CHECK (forma_pago IN ('CONTADO', 'CREDITO')),
    -- TARJETA: su código DIAN está SIN VERIFICAR; el generador del XML la rechaza (FacturaNoSoportada).
    medio_pago       TEXT     NOT NULL CHECK (medio_pago IN ('EFECTIVO', 'TARJETA')),
    total            NUMERIC(14,2) NOT NULL CHECK (total > 0),
    fecha_emision    TIMESTAMP NOT NULL,                       -- hora local; el XML lleva -05:00
    cufe             TEXT     UNIQUE CHECK (cufe ~ '^[0-9a-f]{96}$'),   -- SHA-384 en hexadecimal
    xml_sin_firmar   TEXT,
    xml_firmado      TEXT,
    respuesta_dian   JSONB,
    error            TEXT,                                     -- último error (sin secretos ni datos personales)
    creada_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizada_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT factura_origen_check CHECK (num_nonnulls(despacho_id, venta_manual_id) = 1),
    CONSTRAINT factura_numeracion_fk FOREIGN KEY (numeracion_id, prefijo) REFERENCES numeracion (id, prefijo),
    CONSTRAINT factura_numero_unico UNIQUE (prefijo, consecutivo),
    CONSTRAINT factura_facturado_check CHECK (estado <> 'FACTURADO' OR cufe IS NOT NULL),
    CONSTRAINT factura_rechazado_check CHECK (estado <> 'RECHAZADO' OR error IS NOT NULL)
);
CREATE INDEX ix_factura_cliente ON factura (cliente_id);
CREATE INDEX ix_factura_fecha   ON factura (fecha_emision);

-- ---------------------------------------------------------------------
-- Cola de envíos a la DIAN (la consume el worker con SELECT ... FOR UPDATE SKIP LOCKED)
-- ---------------------------------------------------------------------
CREATE TABLE outbox (
    id               BIGINT   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    factura_id       BIGINT   NOT NULL REFERENCES factura (id),
    tipo             TEXT     NOT NULL CHECK (tipo IN ('ENVIAR_DIAN', 'CONSULTAR_DIAN')),
    estado           TEXT     NOT NULL DEFAULT 'PENDIENTE'
                     CHECK (estado IN ('PENDIENTE', 'EN_CURSO', 'HECHO', 'FALLIDO')),
    intentos         SMALLINT NOT NULL DEFAULT 0 CHECK (intentos >= 0),
    proximo_intento  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultimo_error     TEXT,
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
    terminado_en     TIMESTAMPTZ,
    CONSTRAINT outbox_terminado_check CHECK ((estado IN ('HECHO', 'FALLIDO')) = (terminado_en IS NOT NULL))
);
CREATE UNIQUE INDEX ux_outbox_un_trabajo_vivo ON outbox (factura_id, tipo) WHERE estado IN ('PENDIENTE', 'EN_CURSO');
CREATE INDEX ix_outbox_cola ON outbox (proximo_intento) WHERE estado = 'PENDIENTE';

-- ---------------------------------------------------------------------
-- Auditoría: quién hizo qué y cuándo. Solo se agrega. `detalle` no lleva datos personales completos.
-- ---------------------------------------------------------------------
CREATE TABLE auditoria (
    id           BIGINT   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ocurrido_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    usuario_id   SMALLINT REFERENCES usuario (id),             -- NULL = el sistema o el registro público
    accion       TEXT     NOT NULL CHECK (accion ~ '^[A-Z][A-Z_]{2,59}$'),
    entidad      TEXT     NOT NULL CHECK (entidad ~ '^[a-z_]{3,40}$'),
    entidad_id   TEXT,
    detalle      JSONB    NOT NULL DEFAULT '{}',
    origen_ip    INET
);
CREATE INDEX ix_auditoria_entidad ON auditoria (entidad, entidad_id);

CREATE FUNCTION auditoria_inmutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'la auditoría solo admite agregar filas (% no permitido)', TG_OP;
END
$$;
CREATE TRIGGER auditoria_sin_cambios BEFORE UPDATE OR DELETE ON auditoria
    FOR EACH ROW EXECUTE FUNCTION auditoria_inmutable();
CREATE TRIGGER auditoria_sin_truncar BEFORE TRUNCATE ON auditoria
    FOR EACH STATEMENT EXECUTE FUNCTION auditoria_inmutable();
"""

DOWNGRADE = r"""
DROP TABLE auditoria;
DROP FUNCTION auditoria_inmutable();
DROP TABLE outbox;
DROP TABLE factura;
DROP TABLE venta_manual;
DROP TABLE usuario;
DROP TABLE numeracion;
DROP TABLE estacion;

ALTER TABLE cliente DROP CONSTRAINT cliente_dv_nit_check;
ALTER TABLE cliente DROP CONSTRAINT cliente_numero_check;
ALTER TABLE cliente DROP CONSTRAINT cliente_nombres_check;
ALTER TABLE cliente DROP CONSTRAINT cliente_autorizacion_check;
ALTER TABLE cliente ADD CONSTRAINT cliente_check CHECK (NOT autoriza_tratamiento OR fecha_autorizacion IS NOT NULL);
ALTER TABLE cliente ALTER COLUMN autoriza_tratamiento SET DEFAULT FALSE;
ALTER TABLE cliente DROP COLUMN apellidos;
ALTER TABLE cliente RENAME COLUMN nombres TO razon_social;

DROP FUNCTION dv_nit(TEXT);
"""


def _sql(texto: str) -> None:
    # SQL crudo por psycopg, sin parámetros: admite varias sentencias, '%' y cuerpos $$...$$.
    op.get_bind().connection.driver_connection.execute(texto)  # type: ignore[union-attr]


def upgrade() -> None:
    _sql(UPGRADE)


def downgrade() -> None:
    _sql(DOWNGRADE)
