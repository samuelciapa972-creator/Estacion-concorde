"""Envío del correo con la factura (un único ZIP adjunto, asunto reglamentado por el anexo).

Dos implementaciones de `EnviadorCorreo`:
    · `EnviadorSmtp`: SMTP con la librería estándar. Por defecto apunta a Mailpit (localhost:1025, sin TLS ni usuario):
      los correos se ven en http://localhost:8025 y no salen a internet. Con un servidor real (p. ej. Gmail:
      smtp.gmail.com:587, STARTTLS y una contraseña de aplicación) se configura por variables de entorno; ver
      `.env.example`. Con usuario, el cifrado es obligatorio: la clave nunca viaja en claro.
    · `EnviadorEnMemoria`: para las pruebas.

`ConListaBlanca` envuelve a cualquiera de los dos y solo deja salir correos a las direcciones permitidas
(ESTACION_CORREO_SOLO_A): mientras se prueba con un buzón real, los clientes de prueba con correos inventados no
reciben nada (y sus rebotes no ponen en riesgo la cuenta que envía).

Los errores se clasifican para la cola: `CorreoTemporal` (servidor caído, 4xx: se reintenta) y `CorreoRechazado`
(5xx, destinatario inválido: no se reintenta; se corrige y se reenvía). Sus mensajes NUNCA llevan direcciones de
correo (Ley 1581).
"""

from __future__ import annotations

import os
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from html import escape
from typing import Literal, Protocol

Seguridad = Literal["ninguna", "starttls", "ssl"]
_SEGURIDADES: tuple[Seguridad, ...] = ("ninguna", "starttls", "ssl")


class CorreoTemporal(Exception):
    """El correo no salió, pero puede salir más tarde (conexión, 4xx)."""


class CorreoRechazado(Exception):
    """El servidor rechazó el correo de forma definitiva (5xx, destinatario inválido)."""


@dataclass(frozen=True, slots=True)
class Mensaje:
    remitente: str
    destinatario: str
    asunto: str
    texto: str
    html: str
    adjunto_nombre: str
    adjunto: bytes = field(repr=False)


class EnviadorCorreo(Protocol):
    def enviar(self, m: Mensaje) -> None: ...


def mensaje_factura(
    *,
    remitente: str,
    destinatario: str,
    asunto: str,
    nombre_zip: str,
    zip: bytes,
    prueba: bool,
    tipo_documento: Literal["fv", "nc"] = "fv",
) -> Mensaje:
    """El correo de entrega. El cuerpo no está reglamentado; el asunto sí (ver `facturacion/paquete.asunto`)."""
    campos = asunto.split(";")
    numero = campos[2] if len(campos) > 2 else ""
    emisor = campos[1] if len(campos) > 1 else ""
    documento = "nota crédito" if tipo_documento == "nc" else "factura"
    aviso = (
        f"BANCO DE PRUEBAS: esta {documento} no tiene validez fiscal y no fue enviada a la DIAN.\n\n" if prueba else ""
    )
    texto = (
        f"{aviso}Adjuntamos su {documento} electrónica {numero} de {emisor}.\n"
        f"El archivo {nombre_zip} contiene el documento electrónico (XML) y su representación gráfica (PDF).\n"
    )
    html = "".join(f"<p>{escape(linea)}</p>" for linea in texto.splitlines() if linea)
    return Mensaje(remitente, destinatario, asunto, texto, html, nombre_zip, zip)


@dataclass(frozen=True, slots=True)
class EnviadorSmtp:
    """`remitente` vacío: el From es el correo del emisor (Mailpit). Con remitente (la cuenta autenticada), el From
    es esa cuenta y el correo del emisor va en Reply-To: los servidores reales no dejan enviar a nombre de otro."""

    host: str
    puerto: int
    timeout: float = 10.0
    seguridad: Seguridad = "ninguna"
    usuario: str = ""
    clave: str = field(default="", repr=False)
    remitente: str = ""

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] | None = None) -> EnviadorSmtp:
        """Los mensajes de error nombran variables, nunca sus valores."""
        entorno = os.environ if entorno is None else entorno
        leer = lambda nombre, defecto="": entorno.get(nombre, defecto).strip() or defecto  # noqa: E731
        puerto = leer("ESTACION_SMTP_PUERTO", "1025")
        if not puerto.isdigit():
            raise ValueError(f"ESTACION_SMTP_PUERTO debe ser un número de puerto, no {puerto!r}")
        valor = leer("ESTACION_SMTP_SEGURIDAD", "ninguna").lower()
        seguridad = next((s for s in _SEGURIDADES if s == valor), None)
        if seguridad is None:
            raise ValueError(f"ESTACION_SMTP_SEGURIDAD debe ser una de {', '.join(_SEGURIDADES)}")
        usuario, clave = leer("ESTACION_SMTP_USUARIO"), entorno.get("ESTACION_SMTP_CLAVE", "").strip()
        if usuario and seguridad == "ninguna":
            raise ValueError(
                "con ESTACION_SMTP_USUARIO la clave viajaría sin cifrar: use ESTACION_SMTP_SEGURIDAD=starttls o ssl"
            )
        if usuario and not clave:
            raise ValueError("falta ESTACION_SMTP_CLAVE para ESTACION_SMTP_USUARIO")
        return cls(
            leer("ESTACION_SMTP_HOST", "localhost"), int(puerto), seguridad=seguridad,
            usuario=usuario, clave=clave, remitente=leer("ESTACION_SMTP_REMITENTE", usuario),
        )  # fmt: skip

    def enviar(self, m: Mensaje) -> None:
        msg = EmailMessage()
        if self.remitente:
            msg["From"] = self.remitente
            if m.remitente and m.remitente.lower() != self.remitente.lower():
                msg["Reply-To"] = m.remitente
        else:
            msg["From"] = m.remitente
        msg["To"] = m.destinatario
        msg["Subject"] = m.asunto
        msg.set_content(m.texto)
        msg.add_alternative(m.html, subtype="html")
        msg.add_attachment(m.adjunto, maintype="application", subtype="zip", filename=m.adjunto_nombre)
        try:
            conexion: smtplib.SMTP
            if self.seguridad == "ssl":
                contexto = ssl.create_default_context()
                conexion = smtplib.SMTP_SSL(self.host, self.puerto, timeout=self.timeout, context=contexto)
            else:
                conexion = smtplib.SMTP(self.host, self.puerto, timeout=self.timeout)
            with conexion as s:
                if self.seguridad == "starttls":
                    s.starttls(context=ssl.create_default_context())
                if self.usuario:
                    try:
                        s.login(self.usuario, self.clave)
                    except smtplib.SMTPServerDisconnected:
                        # Gmail responde 535 y cierra; smtplib intenta otro método y solo ve la desconexión.
                        raise smtplib.SMTPAuthenticationError(535, b"sesion cerrada al autenticar") from None
                s.send_message(msg)
        except smtplib.SMTPAuthenticationError as e:
            # Configuración, no el destinatario: se reintenta para que, al corregir el .env, la cola se vacíe sola.
            raise CorreoTemporal(f"credenciales SMTP inválidas (código {e.smtp_code}); revise el .env") from None
        except smtplib.SMTPNotSupportedError:
            raise CorreoTemporal("el servidor de correo no admite STARTTLS o el login; revise el .env") from None
        except smtplib.SMTPRecipientsRefused:
            raise CorreoRechazado("el servidor rechazó la dirección del destinatario") from None
        except smtplib.SMTPResponseException as e:  # incluye SMTPDataError y SMTPSenderRefused
            if 400 <= e.smtp_code < 500:
                raise CorreoTemporal(f"el servidor de correo pidió reintentar (código {e.smtp_code})") from None
            raise CorreoRechazado(f"el servidor rechazó el correo (código {e.smtp_code})") from None
        except (OSError, smtplib.SMTPException) as e:  # conexión rechazada, tiempo de espera, desconexión, TLS
            raise CorreoTemporal(f"servidor de correo no disponible ({type(e).__name__})") from None


@dataclass(frozen=True, slots=True)
class ConListaBlanca:
    """Solo deja salir correos a `permitidos` (en minúsculas). Los demás son rechazo: el trabajo queda FALLIDO con
    el motivo y se puede reenviar después de agregar la dirección."""

    interno: EnviadorCorreo
    permitidos: frozenset[str]

    def enviar(self, m: Mensaje) -> None:
        if m.destinatario.strip().lower() not in self.permitidos:
            raise CorreoRechazado("destinatario fuera de la lista blanca de pruebas (ESTACION_CORREO_SOLO_A)")
        self.interno.enviar(m)


def enviador_desde_entorno(entorno: Mapping[str, str] | None = None) -> EnviadorCorreo:
    """El enviador SMTP del entorno, envuelto en la lista blanca si ESTACION_CORREO_SOLO_A tiene direcciones."""
    entorno = os.environ if entorno is None else entorno
    smtp = EnviadorSmtp.desde_entorno(entorno)
    permitidos = frozenset(d.strip().lower() for d in entorno.get("ESTACION_CORREO_SOLO_A", "").split(",") if d.strip())
    return ConListaBlanca(smtp, permitidos) if permitidos else smtp


@dataclass
class EnviadorEnMemoria:
    """Para las pruebas: guarda lo enviado. `falla_siguiente` hace fallar SOLO el próximo envío."""

    enviados: list[Mensaje] = field(default_factory=list)
    falla_siguiente: Literal["temporal", "rechazo"] | None = None

    def enviar(self, m: Mensaje) -> None:
        falla, self.falla_siguiente = self.falla_siguiente, None
        if falla == "temporal":
            raise CorreoTemporal("servidor de correo no disponible (simulado)")
        if falla == "rechazo":
            raise CorreoRechazado("el servidor rechazó el correo (simulado)")
        self.enviados.append(m)
