# Ensayo general en la estación (banco de pruebas, habilitación)

> Qué es: probar el sistema en la isla, con la tablet, la impresora y la red reales, **antes** de contratar el
> servidor. Corre en el portátil con `make stack` y `DIAN_AMBIENTE=2`: las facturas son de habilitación (SETP), sin
> validez fiscal, y no gastan números EDSC. Lo que solo se puede probar en el servidor de internet está en
> `docs/despliegue-produccion.md` (sección 8.1).
>
> Cómo se usa: imprimir esta hoja, marcar cada casilla y anotar lo que salga distinto en la columna **Anotación**.
> Si algo falla, **no se arregla en el momento**: se anota (con foto o captura si se puede) y se sigue con lo demás.

## A. En la casa, antes de viajar

- [ ] `git pull` en `main` y `make stack` (reconstruye con el código nuevo). `make stack-logs`: `api` y `web` sanos.
- [ ] `.env` con `DIAN_AMBIENTE=2` y `DIAN_TEST_SET_ID` **vacío** (con valor, la DIAN rechaza todo: el set ya pasó).
- [ ] `ESTACION_CORREO_SOLO_A` vacío o con los correos de prueba que se van a usar en la estación.
- [ ] `make habilitacion-probar` en verde (certificado, firma y red).
- [ ] `infra/caddy-root.crt` a mano (`make ca`), por si hay que instalarlo en otra tablet.
- [ ] Un respaldo de la base antes de salir: `docker compose --project-directory . -f infra/docker-compose.yml exec -T db pg_dump -U estacion -Fc estacion > $HOME/respaldo_antes_ensayo.dump`
- [ ] Cuentas de prueba listas: un cliente **empresa (NIT)** y uno con **cédula**, con correos a los que se tenga acceso.
- [ ] Llevar: portátil y cargador, tablet o teléfono, la impresora térmica con su cable (y adaptador OTG si es USB),
      papel térmico, esta hoja impresa y un esfero.

## B. Al llegar a la estación: red

1. Conectar el portátil y la tablet a **la misma red** (el wifi de la pista).
2. Ver la IP del portátil en esa red: `ip -4 addr` (algo como `192.168.x.y`).
3. En el `.env`: `ESTACION_HOST="localhost, <esa IP>"` (**con comillas**: tiene un espacio) y
   `ESTACION_IP=<esa IP>`. Luego `make stack`.
4. Si el portátil tiene firewall activo, permitir los puertos 80 y 443 en esa red.
5. En la tablet: `https://<esa IP>/pista`. La CA de Caddy es la misma de la casa (mientras no se borre el volumen
   `caddy_data`), así que no hay que reinstalarla. Si el navegador advierte el certificado, revisar el paso 3.
6. En otra terminal: `make worker-dian`. En el registro debe salir "proveedor DIAN, ambiente de HABILITACIÓN".
7. `make habilitacion-probar` **desde la red de la estación**: confirma que el internet de allá llega a la DIAN.

| | Anotación |
|---|---|
| IP del portátil en la estación | |
| Red wifi usada (nombre) y señal en la isla (buena / regular / mala) | |
| ¿La tablet abrió `/pista` sin advertencia? | |

## C. Pruebas de facturación (lo normal)

| # | Prueba | Resultado esperado | ✔ / ✘ | Anotación |
|---|---|---|---|---|
| C1 | Ingresar con el PIN de un bombero | Entra a la pista | | |
| C2 | Factura a **empresa (NIT)**, de contado, efectivo | FACTURADO en menos de 1 minuto; llega el correo con ZIP y PDF | | |
| C3 | Factura a empresa (NIT) **a crédito** | FACTURADO; el PDF y el recibo dicen crédito y la fecha de vencimiento | | |
| C4 | Factura a **cédula** con **tarjeta** | FACTURADO; medio de pago tarjeta | | |
| C5 | **Venta manual** (surtidor Wayne) y facturarla | FACTURADO | | |
| C6 | **Imprimir recibo** de C2 en la térmica | Sale completo: logo, tildes (á, é, ñ), razón social, NIT y QR | | |
| C7 | Escanear el QR del recibo con un celular | Abre la consulta de la DIAN (habilitación) | | |
| C8 | Un ADMIN **anula** C5 con nota crédito | La nota queda aceptada; la factura queda anulada | | |
| C9 | Abrir el ZIP del correo de la nota | El XML y el PDF corresponden a la nota | | |

## D. Pruebas de fallas (lo que más importa)

Después de cada una, revisar en **Facturación** que no haya facturas repetidas ni perdidas.

| # | Prueba | Resultado esperado | ✔ / ✘ | Anotación |
|---|---|---|---|---|
| D1 | **Doble toque rápido** en Facturar | Sale una sola factura | | |
| D2 | **Quitar el wifi de la tablet** justo al pulsar Facturar; volver a conectarla y repetir | Queda una sola factura. Anotar qué vio el bombero y si lo entendió | | |
| D3 | Con el wifi quitado, intentar facturar | Mensaje claro de que no hay conexión; nada queda a medias | | |
| D4 | **Desconectar el internet del portátil** (no el wifi local) y facturar; reconectar a los 5 minutos | La factura queda en espera (EN_PROCESO) y se completa sola al volver el internet | | |
| D5 | **Cerrar `make worker-dian` (Ctrl+C) justo después de facturar**, esperar 1 minuto y volver a abrirlo | La factura se completa al reabrir; una sola factura | | |
| D6 | **Reiniciar el portátil** con facturas hechas; después `make stack` y `make worker-dian` | Todo vuelve; las facturas siguen ahí | | |
| D7 | Factura a un cliente con un **correo mal escrito** (p. ej. `sin-arroba.com` o un dominio inventado) | La factura queda FACTURADO; la entrega muestra el error; se corrige el correo y **Reenviar** funciona | | |
| D8 | **Impresora apagada** al pulsar Imprimir recibo; encenderla y repetir | Avisa del error; al repetir imprime | | |
| D9 | Pasar la tablet a **datos móviles** (si ya tiene plan) | Con el banco de pruebas **no** funciona (el portátil está en la red local). Solo se anota que el plan de datos tiene señal en la isla; la prueba real va en el servidor | | |

## E. Turno simulado (30 a 60 minutos)

Un bombero factura **sin ayuda**, como en un turno real: ventas a empresas, a cédula, venta manual, recibos.
Quien acompaña solo observa y anota; no explica nada salvo que el bombero quede bloqueado.

| Qué observar | Anotación |
|---|---|
| ¿Dónde dudó o preguntó? | |
| ¿Algún botón o texto que no entendió? | |
| ¿Cuánto se demora una factura de principio a fin (con recibo)? | |
| ¿Algo se ve cortado o muy pequeño en la tablet? | |
| Al final: ¿las facturas del sistema cuadran con lo que anotó el bombero? | |

## F. Al terminar

- [ ] `make stack-logs` y la terminal del worker: copiar o fotografiar cualquier ERROR.
- [ ] En Facturación: ninguna factura en RECHAZADO, INCIERTO o EN_PROCESO sin explicación.
- [ ] Fotos de los recibos impresos (uno bueno y cualquiera que salga raro).
- [ ] Traer esta hoja con las anotaciones: con ella se decide qué corregir antes del servidor.

Las facturas del ensayo son de habilitación: no hay que anularlas ni reportarlas, y no aparecen en los reportes de
producción (cada documento guarda su ambiente).
