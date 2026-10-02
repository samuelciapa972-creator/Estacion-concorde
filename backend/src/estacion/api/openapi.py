"""Esquema OpenAPI de la API, sin base de datos ni secretos: `python -m estacion.api.openapi > web/openapi.json`.

El frontend genera sus tipos de TypeScript desde ese archivo (`make tipos`). La prueba `test_openapi_al_dia`
falla si el archivo del frontend quedó viejo respecto a la API.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .ajustes import Ajustes
from .app import crear_app

# Ajustes de relleno: crear la app no abre conexiones (el pool se abre al arrancar el servidor) ni usa el secreto.
_AJUSTES_SOLO_ESQUEMA = Ajustes(database_url="postgresql://sin-conexion/solo-esquema", jwt_secreto="-" * 32)


def esquema() -> dict[str, Any]:
    return crear_app(_AJUSTES_SOLO_ESQUEMA, entorno_dian={}).openapi()


def como_texto() -> str:
    return json.dumps(esquema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    sys.stdout.write(como_texto())
