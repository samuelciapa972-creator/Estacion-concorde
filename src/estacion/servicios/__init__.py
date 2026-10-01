"""Servicios de dominio sobre PostgreSQL: numeración, emisión de facturas y cola de envíos.

Todas las funciones reciben una conexión de psycopg en modo autocommit y abren su propia transacción
(`with conn.transaction()`), para que nada quede a medias si algo falla.
"""
