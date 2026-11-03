# Despliegue en el servidor de internet y piloto controlado

> Alcance: el sistema en **producción**, en un servidor de internet (VPS) con dominio propio. El banco de pruebas de
> la red local sigue en `docs/despliegue.md`. El paso de habilitación a producción con la DIAN (EDSC, clave técnica,
> primera factura real) está en `docs/salida-a-produccion.md`; esta guía dice **dónde** corre cada cosa y cómo se
> opera el piloto. Ningún valor secreto va en este documento.

## 1. Qué se levanta

`make prod` usa `infra/docker-compose.prod.yml` (proyecto `estacion-prod`, separado del banco de pruebas):

| Servicio | Qué hace | Expuesto |
|---|---|---|
| `db` | PostgreSQL 16, volumen `pgdata` | no (ni siquiera en el propio servidor) |
| `migrar` | `alembic upgrade head` y termina | no |
| `api` | FastAPI | no: solo la ve Caddy |
| `worker-dian` | la cola de envíos a la DIAN y la entrega por correo. Se reinicia solo si se cae | no |
| `web` | Caddy: HTTPS con certificado de Let's Encrypt, la PWA y `/api` | 80 y 443 |

No hay Mailpit, simulador ni worker simulado. Hay **un solo** worker: dos de la misma cola se quitarían los trabajos.

Probado en un equipo de desarrollo con `ESTACION_TLS=internal` (2026-11-03): arranque desde cero, migraciones,
importación del emisor, usuario, ingreso por HTTPS, HSTS, redirección de HTTP a HTTPS, worker en habilitación,
respaldo y restauración (también de una copia de la base de desarrollo). **Sin probar todavía:** el certificado real
de Let's Encrypt (solo se puede en el servidor, con el dominio apuntando a él) y el envío real a la DIAN desde el servidor.

## 2. El servidor

- **Máquina:** Ubuntu 24.04 LTS, 2 vCPU, 4 GB de RAM y 40 GB de disco. Con una estación sobra.
- **Dominio:** comprar uno (p. ej. `.com.co`) y crear un registro `A` del subdominio (p. ej. `eds.<dominio>`)
  apuntando a la IP del servidor. Lo verán los clientes en el QR de registro y en los correos.
- **Usuario y SSH:** un usuario sin privilegios para el despliegue (p. ej. `estacion`), en el grupo `docker`.
  SSH solo con llave (`PasswordAuthentication no`, `PermitRootLogin no`).
- **Firewall:** solo 22, 80 y 443:
  ```bash
  sudo ufw default deny incoming && sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
  ```
  Ojo: Docker publica sus puertos por fuera de `ufw`. Por eso el compose de producción solo publica 80 y 443, y la
  base no tiene `ports`.
- **Actualizaciones de seguridad:** `sudo apt install unattended-upgrades` (vienen activas en Ubuntu).
- **Docker:** Docker Engine con el plugin `compose`, desde el repositorio oficial de Docker.
- **Hora:** `timedatectl` debe mostrar la sincronización NTP activa. La DIAN rechaza fechas corridas.

## 3. Carpetas y secretos en el servidor

```
/srv/estacion/
  repo/          el repositorio (git clone); el .env va aquí, en la raíz, con permisos 0600
  secretos/      el certificado .pfx de la empresa
  respaldos/     los respaldos de la base (0700)
```

**`.env`**: partir de `.env.example` y llenar al menos:

- `POSTGRES_PASSWORD`, `JWT_SECRETO`: valores nuevos, generados en el servidor. Nunca los del portátil.
- `ESTACION_DOMINIO=eds.<dominio>` y `ESTACION_TLS=<correo para avisos de Let's Encrypt>`.
- `ESTACION_SECRETOS=/srv/estacion/secretos`.
- Las variables `DIAN_*`. Con el certificado en la carpeta de secretos, `DIAN_CERTIFICADO_RUTA=/secretos/<archivo>.pfx`:
  es la ruta **dentro** del contenedor.
- `ESTACION_SMTP_*`: la cuenta real del correo. `ESTACION_CORREO_SOLO_A` va vacío.
- Captcha del registro público (`ESTACION_CAPTCHA_*`). En internet es obligatorio en la práctica: sin él, el
  formulario de registro queda abierto a bots.
- `RESPALDO_REMOTO`: ver la sección 6.

**Certificado**: el contenedor corre con el usuario 10001. `make prod-clave-tecnica` corre con el usuario del
despliegue. Los dos deben poder leerlo, y nadie más:
```bash
sudo chown 10001:$(id -g) /srv/estacion/secretos/<archivo>.pfx && sudo chmod 0440 /srv/estacion/secretos/<archivo>.pfx
```

**Rotar antes de subir**: la contraseña de aplicación de Gmail y las claves que viajaron en los zips de octubre.
También se rotan el certificado de prueba y cualquier valor que haya pasado por chat o correo.

## 4. Primer arranque

Desde `/srv/estacion/repo`:

1. `make prod`: construye las imágenes, aplica las migraciones y levanta todo.
   El worker se reiniciará en bucle hasta el paso 3. Es a propósito: sin emisor no emite.
2. Comprobar que el certificado HTTPS salió:
   - `make prod-logs` debe mostrar `certificate obtained successfully`.
   - `https://eds.<dominio>/api/salud` debe responder `"estado":"ok"`.
3. **Datos del emisor.** La base nueva no tiene la estación, así que se copian los que la DIAN ya aceptó:
   - En el portátil: `make emisor-exportar ARCHIVO=datos/emisor.csv`.
   - Pasar el archivo al servidor: `scp datos/emisor.csv estacion@servidor:~/`.
   - En el servidor: `make prod-emisor-importar ARCHIVO=$HOME/emisor.csv` y después `rm ~/emisor.csv`.
   - Si las versiones no coinciden, el script se niega: actualizar primero el repo del portátil.
4. **Usuarios:** `make prod-usuario U=admin1 N="..." R=ADMIN`, luego uno por bombero con `R=BOMBERO`.
   Como esto está en internet, el ADMIN debe tener un PIN de 8 a 10 dígitos que no se use en otro lado.
5. `make prod-habilitacion-probar`: prueba el certificado, la firma y la conexión con la DIAN **desde el servidor**.
   No envía documentos.
6. `make prod-correo-prueba DESTINO=<correo propio>`.
7. Seguir `docs/salida-a-produccion.md` §3 con los comandos de producción:

   | En la guía de salida | En el servidor |
   |---|---|
   | `make guardar-clave-tecnica` | `make prod-clave-tecnica` |
   | `make configurar-produccion` | `make prod-configurar` |
   | `make verificar-produccion` | `make prod-verificar` |
   | `make worker-dian` | el servicio `worker-dian`, que ya corre: `make prod` lo reinicia con el `.env` nuevo |

   Después de cambiar el `.env`, correr `make prod` para que la API y el worker lo lean.
8. **Tablet:** abrir `https://eds.<dominio>/pista` en Chrome e instalar la aplicación. Ya no hace falta instalar
   ninguna CA. El QR de registro debe apuntar al dominio.

## 5. Actualizar a una versión nueva

```bash
cd /srv/estacion/repo && make prod-respaldo && git pull && make prod
```

Primero el respaldo: si una migración falla a medias, hay de dónde volver. Para revisar después de actualizar,
`make prod-ps` (todo `Up` o `healthy`) y `make prod-estado`.

## 6. Respaldos

`infra/respaldo.sh` (`make prod-respaldo`) hace lo siguiente:
- Saca un `pg_dump` y comprueba que se pueda leer.
- Lo guarda en `RESPALDO_DIR` (por defecto `/srv/estacion/respaldos`) con permisos 0600.
- Borra los de más de `RESPALDO_DIAS` (30).
- Si `RESPALDO_REMOTO` tiene valor, lo copia **fuera del servidor** con `rclone`.

1. **Copia fuera del servidor (obligatoria).** Un respaldo que solo vive en el servidor se pierde con él. Pasos:
   - `sudo apt install rclone`.
   - `rclone config`: crear un remoto de almacenamiento (Backblaze B2, Cloudflare R2, Google Drive…) y encima un
     remoto **`crypt`**, porque el respaldo lleva datos personales (Ley 1581).
   - Poner `RESPALDO_REMOTO=<remoto-cifrado>:estacion` en el `.env`.
   - Guardar la clave del `crypt` **fuera del servidor**, en el gestor de claves de la empresa. Sin ella el
     respaldo no sirve.
2. **Cada noche**, con `crontab -e` del usuario del despliegue:
   ```cron
   MAILTO=<correo de quien responde>
   30 2 * * * cd /srv/estacion/repo && infra/respaldo.sh >> /srv/estacion/respaldos/respaldo.log 2>&1 || echo "falló el respaldo de la EDS"
   ```
   Para que `MAILTO` llegue hace falta un servidor de correo saliente en la máquina. Si no lo hay, usar el
   monitoreo de la sección 7 (un chequeo tipo "heartbeat": `&& curl -fsS <url-del-chequeo>` al final de la línea).
3. **Cada mes, y antes del piloto:** `make prod-restaurar-prueba`.
   - Restaura el último respaldo en una base temporal, muestra cuántas facturas, clientes y usuarios trae, y la borra.
   - No toca la base real.
   - Probar también, al menos una vez, a bajar un respaldo **del remoto** y restaurarlo
     (`rclone copy <remoto>:estacion/<archivo> /tmp/` y luego `make prod-restaurar-prueba ARCHIVO=/tmp/<archivo>`).

**Restaurar de verdad** (solo si se perdió la base; consultar antes):
```bash
docker compose --project-directory . -f infra/docker-compose.prod.yml stop api worker-dian web
docker compose --project-directory . -f infra/docker-compose.prod.yml exec -T db \
    pg_restore -U estacion -d estacion --clean --if-exists --no-owner < /srv/estacion/respaldos/<archivo>.dump
make prod
```

Las facturas se deben conservar por años. No borrar respaldos del remoto sin consultarlo con el contador.

## 7. Monitoreo

- **Disponibilidad:** un chequeo externo gratuito (UptimeRobot, Better Stack o Healthchecks.io) contra
  `https://eds.<dominio>/api/salud` cada 5 minutos, que avise por correo o WhatsApp.
  Usar el mismo servicio como heartbeat del respaldo (sección 6).
- **Facturación:** `make prod-estado` muestra las facturas de hoy por estado y lo pendiente o FALLIDO en la cola.
  En el piloto se mira cada día (sección 8). Una alerta automática cuando hay rechazos queda pendiente.
- **Registros:** `make prod-logs`. Docker conserva hasta 50 MB por servicio (5 archivos de 10 MB).

## 8. Piloto controlado

Objetivo: emitir con el sistema en condiciones reales, con poco volumen y con revisión diaria, antes de usarlo
para todo. Las facturas del piloto son **reales** (EDSC, producción). Lo "controlado" es a quién se le factura y
cuánto se vigila, no que sean de prueba.

### 8.1 Antes de empezar (todo en ✔)

- [ ] Servidor, dominio y HTTPS funcionando (secciones 2 a 4). `make prod-verificar` todo en ✔.
- [ ] Respaldo nocturno con copia **cifrada fuera del servidor**, y una restauración probada desde el remoto (sección 6).
- [ ] Chequeo de disponibilidad avisando a una persona concreta (sección 7).
- [ ] Claves rotadas (sección 3). PIN largo para los ADMIN.
- [ ] En la isla: tablet con la PWA instalada, wifi de la pista estable y recibo impreso con tildes, logo y QR
      legible (`docs/salida-a-produccion.md` §2.4).
- [ ] Primera factura real de verificación aceptada (`docs/salida-a-produccion.md` §3, paso 9).
- [ ] El contador sabe que empieza el piloto. El sistema actual sigue funcionando para todo lo que no entra en el
      piloto. Cada venta se factura en **un solo** sistema; confirmar con el contador cómo se reporta el periodo
      con dos sistemas.
- [ ] **Plan si se cae el internet o el servidor** (escrito y conocido por los bomberos): anotar en papel la venta
      (fecha, hora, surtidor, galones, valor, cliente) y facturarla al volver el servicio con **venta manual**.
      Quién decide pasar al sistema anterior y cuándo. El sistema todavía no tiene un modo de contingencia propio.
- [ ] Bomberos capacitados en la pista (ingresar, facturar, venta manual, imprimir) y un ADMIN que sepa anular con
      nota crédito (`docs/salida-a-produccion.md` §4).

### 8.2 Alcance

- **Semana 1:** solo **clientes empresa (NIT)**, de contado y a crédito. Es el flujo principal y el que la DIAN ya
  aceptó en habilitación. Un turno o una isla, con un bombero capacitado.
- **Semana 2:** todos los turnos, y clientes con cédula que pidan factura.
- **Después:** todo lo demás, si se cumplen los criterios de la sección 8.4.

### 8.3 Rutina diaria (un ADMIN, unos 10 minutos)

1. `make prod-estado`:
   - Ninguna factura RECHAZADO ni INCIERTO sin explicar.
   - Nada FALLIDO en la cola.
   - Nada PENDIENTE desde hace más de una hora.
2. Cruzar las facturas del día con el cierre de turno: toda venta del piloto tiene su factura y ninguna está repetida.
3. Abrir el correo de una factura del día: el ZIP y el PDF llegan y abren bien.
4. Revisar `respaldo.log` y el heartbeat: el respaldo de anoche salió y se copió al remoto.
5. Anotar cualquier problema en una bitácora del piloto (fecha, factura, qué pasó, qué se hizo).

### 8.4 Cuándo ampliar y cuándo parar

- **Ampliar** cuando se cumplan las cinco condiciones:
  - cinco días hábiles seguidos sin rechazos de la DIAN;
  - los correos llegan;
  - los recibos salen bien;
  - el cruce con el turno cuadra;
  - hubo al menos una restauración probada.
- **Parar y volver al sistema anterior** ante cualquiera de estas situaciones:
  - rechazos repetidos que no se entienden;
  - una factura que no aparece o está duplicada;
  - pérdida de datos;
  - el servidor caído más de un turno.

  Al parar, no se borra nada: se anota, se investiga y se corrige antes de retomar.
