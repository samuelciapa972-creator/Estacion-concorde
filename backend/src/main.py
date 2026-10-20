"""Compatibilidad: `uvicorn src.main:app` sirve la API REAL (la de `python -m estacion.api`).

Antes este archivo era una app vacía de prueba (solo `/` y `/api/v1/health`): quien la arrancaba veía la API
"funcionando" pero sin rutas, y el frontend recibía 404 en todo. El comando recomendado sigue siendo
`python -m estacion.api --recargar` (con DATABASE_URL y JWT_SECRETO en el entorno; sin ellos falla al arrancar
con un mensaje claro, a propósito).
"""

from estacion.api.app import crear_app

app = crear_app()
