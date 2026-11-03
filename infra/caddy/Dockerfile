# Caddy con el frontend compilado. Se construye desde la RAÍZ del repositorio (ver infra/docker-compose.yml).
FROM node:22-alpine AS web
WORKDIR /web
COPY Frontend/package.json Frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY Frontend/ ./
RUN npm run build

FROM caddy:2-alpine
# Banco de pruebas: infra/caddy/Caddyfile (CA interna). Producción: infra/caddy/Caddyfile.prod (docker-compose.prod.yml).
ARG CADDYFILE=infra/caddy/Caddyfile
COPY ${CADDYFILE} /etc/caddy/Caddyfile
COPY --from=web /web/dist /srv
