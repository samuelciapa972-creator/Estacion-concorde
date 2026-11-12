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
- [ ] Llevar: portátil y cargador, tablet o teléfono, un **cable USB-A a USB-B** (de impresora), un **adaptador
      OTG** para la tablet (ver B2), papel térmico, esta hoja impresa y un esfero.

## B. Al llegar a la estación: red

1. Conectar el portátil y la tablet a **la misma red** (el wifi de la pista).
2. Ver la IP del portátil en esa red: `ip -4 addr` (algo como `192.168.x.y`).
3. En el `.env`: `ESTACION_HOST="localhost, <esa IP>"` (**con comillas**: tiene un espacio) y
   `ESTACION_IP=<esa IP>`. Luego `make stack`.
4. Abrir los puertos 80 y 443 del firewall **solo para la red de la estación** (con Docker Desktop, ufw sí los
   bloquea: la tablet ve "took too long to respond"). Con la red `192.168.x.0/24` de allá:
   `sudo ufw allow from 192.168.x.0/24 to any port 80,443 proto tcp`
5. En la tablet: `https://<esa IP>/pista`. La CA de Caddy es la misma de la casa (mientras no se borre el volumen
   `caddy_data`), así que no hay que reinstalarla. Si el navegador advierte el certificado, revisar el paso 3.
6. En otra terminal: `make worker-dian`. En el registro debe salir "proveedor DIAN, ambiente de HABILITACIÓN".
7. `make habilitacion-probar` **desde la red de la estación**: confirma que el internet de allá llega a la DIAN.

| | Anotación |
|---|---|
| IP del portátil en la estación | |
| Red wifi usada (nombre) y señal en la isla (buena / regular / mala) | |
| ¿La tablet abrió `/pista` sin advertencia? | |

## B2. Impresora del recibo (RawBT)

> Lo que se sabe (visita del 2026-11-06). La térmica de la isla es una **POS-5890U-L**: 58 mm, ESC/POS,
> conexión **USB y Bluetooth, sin red**. Por eso RawBT con "IP 192.168.1.1, puerto 9100" falló con *failed to
> connect … after 5000ms*: esa impresora no tiene IP. Además, **no es de la tablet**: el botón Imprimir de Nexus
> le pide a la Raspberry (`POST /imprimir_venta`) que imprima, y es la Raspberry la que tiene la impresora. Por
> la foto de abajo solo tiene conectado el cable de corriente, así que la Raspberry la usa **por Bluetooth**; por
> eso no aparece en la búsqueda de la tablet. El **USB queda libre**. No se toca el Bluetooth de la Raspberry ni
> se usa `/imprimir_venta` (Nexus dejaría de imprimir, y eso está fuera del permiso de solo lectura).
>
> Dos caminos para operar, con permiso de la empresa porque la impresora es de IMED:
> - **Compartirla:** la Raspberry imprime por Bluetooth y la tablet por **USB con adaptador OTG** (RawBT en modo
>   USB). No hay que comprar nada. Riesgo bajo: dos impresiones en el mismo segundo se mezclan.
> - **Una segunda térmica de 58 mm** solo para nuestro sistema. Si es el mismo modelo, POS-5890U-L, queda probada.
>
> En los dos casos el código no cambia: el recibo ya sale en ESC/POS de 58 mm.

**Primero, inalámbrica: ¿la impresora está libre por Bluetooth? (solo escucha, no toca nada)**

Estas impresoras aceptan **una sola conexión Bluetooth a la vez**. Si la Raspberry la tiene conectada todo el
tiempo, no aparece en la búsqueda. Si solo se conecta cuando imprime, sí aparece y se puede usar sin cables.

1. En el portátil: `bluetoothctl --timeout 30 scan on`. Buscar un nombre como `Printer001`, `POS58` o
   `BlueTooth Printer`, o una dirección sin nombre que aparezca y desaparezca cuando se apaga la impresora.
2. **Si aparece:** `bluetoothctl pair XX:XX:XX:XX:XX:XX` (PIN `0000` o `1234`) y enviar el recibo:
   ```bash
   python3 -c "import socket,sys;s=socket.socket(socket.AF_BLUETOOTH,socket.SOCK_STREAM,socket.BTPROTO_RFCOMM);s.connect((sys.argv[1],1));s.sendall(open(sys.argv[2],'rb').read());s.close()" XX:XX:XX:XX:XX:XX datos/impresora/recibo_SETP990000018.bin
   ```
   Luego imprimir desde Nexus para confirmar que la Raspberry sigue imprimiendo. Si todo sale bien, la tablet
   también puede usarla por Bluetooth con RawBT.
3. **Si no aparece:** la Raspberry la tiene tomada; seguir con la prueba por USB. **No** apagar la impresora para
   quitársela a la Raspberry: puede que no se reconecte sola y Nexus quede sin imprimir. Solo con autorización
   escrita de la empresa y alguien de IMED presente.

**Si no, por cable: prueba directa desde el portátil (C6 sin RawBT, sin interrumpir a Nexus)**

En casa: **reiniciar el portátil** si se actualizó el kernel. Sin el módulo `usblp` no aparece `/dev/usb/lp0`.
El recibo de prueba (SETP990000018) ya está en `datos/impresora/recibo_SETP990000018.bin`; para otra factura se
descarga con el botón Recibo.

1. Con permiso del encargado: un cable **USB-A a USB-B** del portátil al puerto USB de la impresora. El
   Bluetooth de la Raspberry no se toca.
2. `ls /dev/usb/` debe mostrar `lp0`. Luego `cat datos/impresora/recibo_SETP990000018.bin > /dev/usb/lp0`.
3. Revisar C6 en el papel (logo, tildes, ñ, razón social, NIT y QR) y C7 (escanear el QR).
4. Retirar el cable e **imprimir algo desde Nexus** para confirmar que sigue igual.

**Confirmar cómo está conectada (sin desconectar nada)**

- [ ] Foto de toda la parte de atrás y de abajo: ¿solo el cable de corriente? ¿El USB está libre?
- [ ] Hoja de autoprueba: apagarla y encenderla **con FEED presionado**. Foto de la hoja: ¿trae nombre, dirección
      o PIN de Bluetooth?
- [ ] En la tablet, Ajustes → Bluetooth → **Dispositivos vinculados**: foto de la lista.
- [ ] Al encenderla, ¿se prende una luz azul (Bluetooth)?
- [ ] Imprimir algo desde Nexus y confirmar que sigue imprimiendo después de las pruebas.

**Con la tablet por USB (impresora compartida)**

1. Adaptador **OTG** de la tablet (USB-C o micro-USB, según la tablet) a USB-A hembra, y el cable USB-A a USB-B
   hasta la impresora.
2. Android pregunta si RawBT puede usar el dispositivo USB: aceptar y marcar "usar siempre".
3. En RawBT, Add printer: conexión **USB**, controlador **ESC/POS general**, papel de **58 mm**. Impresión de
   prueba; luego C6, C7 y D8.
4. Imprimir desde Nexus y desde nuestro sistema, alternando, para ver que las dos sigan funcionando.

**Con una impresora propia (Bluetooth)**

1. Encenderla y buscarla en la tablet (Ajustes → Bluetooth) en los primeros 30 segundos. Si no sale, encender la
   **ubicación** de la tablet: algunas versiones de Android no buscan sin ella. Puede salir sin nombre, solo con la
   dirección (`XX:XX:…`). Si pide PIN: `0000` o `1234`.
2. En RawBT, Add printer: conexión **Bluetooth** (no red), la impresora emparejada, controlador **ESC/POS
   general**, papel de **58 mm**. Pulsar la impresión de prueba de RawBT.
3. Seguir con C6, C7 y D8.
4. Plan B, si el Bluetooth falla: cable USB con adaptador **OTG** a la tablet y en RawBT la conexión **USB**.

| | Anotación |
|---|---|
| ¿Cómo está conectada la impresora de Nexus? (USB a la Raspberry / Bluetooth / otro) | |
| ¿Apareció la impresora en el escaneo Bluetooth del portátil? (nombre y dirección) | |
| ¿Imprimió el recibo desde el portátil (Bluetooth / USB)? ¿Cómo salieron el logo, las tildes y el QR? | |
| ¿La tablet acepta OTG? ¿RawBT imprimió por USB? | |
| Nombre o dirección Bluetooth de nuestra impresora | |
| ¿Imprimió la prueba de RawBT? | |
| ¿Nexus sigue imprimiendo bien? | |

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
