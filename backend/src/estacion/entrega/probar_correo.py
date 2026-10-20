"""Prueba de la configuración de correo, sin factura: `python -m estacion.entrega.probar_correo destino@correo.com`.

Usa las mismas variables ESTACION_SMTP_* que el worker (y respeta ESTACION_CORREO_SOLO_A). Sirve para comprobar el
servidor, el cifrado y la contraseña de aplicación antes de entregar facturas.
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile

from .correo import CorreoRechazado, CorreoTemporal, Mensaje, enviador_desde_entorno


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m estacion.entrega.probar_correo", description=__doc__.splitlines()[0]
    )
    parser.add_argument("destino", help="dirección que recibe el correo de prueba")
    args = parser.parse_args(argv)
    try:
        enviador = enviador_desde_entorno()
    except ValueError as e:
        print(f"configuración inválida: {e}", file=sys.stderr)
        return 2
    texto = "Correo de prueba del sistema de facturación de la estación. Si lo recibe, la configuración funciona.\n"
    zip_prueba = io.BytesIO()
    with zipfile.ZipFile(zip_prueba, "w") as z:  # el adjunto es un ZIP, como el de las facturas
        z.writestr("prueba.txt", texto)
    mensaje = Mensaje(
        remitente="prueba@estacion.local", destinatario=args.destino, asunto="Prueba de correo - facturación estación",
        texto=texto, html=f"<p>{texto.strip()}</p>", adjunto_nombre="prueba.zip", adjunto=zip_prueba.getvalue(),
    )  # fmt: skip
    try:
        enviador.enviar(mensaje)
    except (CorreoTemporal, CorreoRechazado) as e:
        print(f"no se envió: {e}", file=sys.stderr)
        return 1
    print("enviado; revise la bandeja de entrada (y la de spam)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
