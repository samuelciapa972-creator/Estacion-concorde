# Imagen de la API, el worker y el simulador (mismo código, distinto comando). Ver infra/docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY alembic.ini ./
COPY migrations ./migrations

# Sin privilegios de root dentro del contenedor.
RUN useradd --system --uid 10001 --no-create-home estacion
USER estacion

EXPOSE 8000
CMD ["python", "-m", "estacion.api", "--host", "0.0.0.0", "--puerto", "8000"]
