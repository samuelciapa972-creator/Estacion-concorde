# Despliegue del banco de pruebas (contenedores + tablet)

> Alcance: **banco de pruebas interno**. Nada de esto se instala en la estación ni toca equipos o sistemas de
> IMED OIL (ver `CLAUDE.md`). La emisión usa el proveedor **simulado**: no sale nada hacia la DIAN.

## 1. Qué se levanta

`make stack` (o `make stack-simulador` para tener además el surtidor simulado) usa `infra/docker-compose.yml`:

| Servicio | Qué hace | Expuesto |
|---|---|---|
| `db` | PostgreSQL 16, volumen `pgdata` | solo `127.0.0.1:5432` (pruebas y `make api`) |
| `migrar` | `alembic upgrade head` y termina; `api` y `worker` esperan a que acabe | no |
| `api` | FastAPI | no: solo la ve Caddy |
| `worker` | cola de envíos con el proveedor **simulado** | no |
| `web` | Caddy: HTTPS, la PWA y `/api` → `api` en el mismo origen | puertos 80 y 443 |
| `simulador` | (perfil `simulador`) un despacho cada 20 s | no |

## 2. Primer arranque

1. Copiar `.env.example` a `.env` en la raíz y llenar como mínimo:
   - `JWT_SECRETO`: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Sin él la API **no arranca**, a propósito.
   - `ESTACION_HOST`: los nombres o IPs por los que se entra, separados por coma, p. ej. `localhost, 192.168.1.50`.
     Caddy emite un certificado para cada uno; si la tablet entra por una IP que no está aquí, el navegador lo rechaza.
   - `POSTGRES_PASSWORD`: fuera de un portátil de desarrollo, una clave propia (el valor por defecto es `estacion`).
   - Captcha del registro público: vacío = sin captcha (la API lo advierte al arrancar). Ver `.env.example`.
2. `make stack` (o `make stack-simulador`).
3. Crear el primer administrador y los bomberos (el PIN se pide por consola; se guarda con hash):
   ```bash
   make stack-usuario U=admin1 N="Admin Uno" R=ADMIN
   make stack-usuario U=bombero1 N="Bombero Uno" R=BOMBERO
   ```
4. `make stack-logs` para ver que `migrar` terminó bien y que `api`, `worker` y `web` están sanos.

`make stack-down` detiene todo y conserva los datos. `make db-reset` **borra** la base de desarrollo (solo el
volumen de PostgreSQL; la CA de Caddy se conserva).

## 3. HTTPS local y la tablet

La PWA (instalarla en la pantalla de inicio, el service worker, el aviso de "sin conexión") solo funciona con
HTTPS. En la red local no hay dominio público, así que los certificados los firma la **CA interna de Caddy**.
Su certificado raíz se instala **una vez** en cada tablet:

1. `make ca`: copia el certificado raíz a `infra/caddy-root.crt`. No es secreto, pero cambia en cada instalación
   y está en `.gitignore`: no va al repositorio.
2. Pasarlo a la tablet (USB o correo interno).
3. En Android: *Ajustes → Seguridad → Cifrado y credenciales → Instalar un certificado → Certificado de CA*
   (el nombre exacto del menú cambia según la marca y la versión). **SIN VERIFICAR** en la tablet real de la
   estación: la versión de Android/Chrome todavía no se conoce.
4. Abrir `https://<ESTACION_HOST>/pista` en Chrome, ingresar con el PIN y usar *Instalar aplicación* (o *Agregar
   a la pantalla principal*).

Mientras el volumen `caddy_data` exista, la CA no cambia. Si se borra, hay que repetir los pasos 1 a 3 en cada tablet.

**Registro público por QR:** el celular del cliente **no** tiene instalada la CA de Caddy, así que verá una
advertencia de certificado si el QR apunta a esta instalación local. Para el piloto, el registro público necesita un
dominio con certificado público o un servicio aparte. Está pendiente de decidir.

## 4. Actualizar a una versión nueva

```bash
git pull
make stack          # reconstruye las imágenes; `migrar` aplica las migraciones nuevas antes de que suba la API
```

Caddy sirve `index.html`, el service worker y el manifiesto con `Cache-Control: no-cache`, así que la tablet toma
la versión nueva al recargar. Los archivos de `/assets/` llevan hash en el nombre y se guardan en caché indefinidamente.

## 5. Respaldos

Pendiente de definir junto con `docs/runbook.md` (Fase 8). Mientras tanto, para sacar una copia manual:

```bash
docker compose --project-directory . -f infra/docker-compose.yml exec -T db \
    pg_dump -U estacion -Fc estacion > respaldo_$(date +%F).dump
```

El respaldo contiene **datos personales** (Ley 1581): se guarda fuera del repositorio y con acceso restringido.

## 6. Lo que este despliegue NO hace todavía

- No firma ni envía nada a la DIAN (Fase 6). El `worker` usa el proveedor simulado.
- No lee los surtidores reales: los despachos entran por el simulador, por venta manual o por la carga del archivo
  de Speed Solutions.
- No tiene monitoreo ni alertas, más allá de los `healthcheck` del compose.
