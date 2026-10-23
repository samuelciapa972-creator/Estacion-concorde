"""Envío de correo: SMTP real contra un servidor mínimo local, y la clasificación temporal / rechazo."""

from __future__ import annotations

import smtplib
import socketserver
import threading

import pytest

from estacion.entrega.correo import (
    ConListaBlanca,
    CorreoRechazado,
    CorreoTemporal,
    EnviadorEnMemoria,
    EnviadorSmtp,
    enviador_desde_entorno,
    mensaje_factura,
)

M = mensaje_factura(
    remitente="estacion@example.com", destinatario="ana@example.com",
    asunto="900123456;EDS PRUEBA SAS;SETP1;01;EDS PRUEBA", nombre_zip="z.zip", zip=b"PK\x03\x04", prueba=True,
)  # fmt: skip


class _Smtp(socketserver.StreamRequestHandler):
    """Lo mínimo del protocolo SMTP para recibir un correo; guarda lo que llega después de DATA."""

    def handle(self):
        self.wfile.write(b"220 prueba\r\n")
        datos = False
        cuerpo = []
        for linea in self.rfile:
            if datos:
                if linea == b".\r\n":
                    datos = False
                    self.server.recibidos.append(b"".join(cuerpo))  # type: ignore[attr-defined]
                    self.wfile.write(b"250 ok\r\n")
                else:
                    cuerpo.append(linea)
                continue
            orden = linea[:4].upper()
            if orden == b"DATA":
                datos = True
                self.wfile.write(b"354 siga\r\n")
            elif orden == b"QUIT":
                self.wfile.write(b"221 adios\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


@pytest.fixture
def servidor_smtp():
    servidor = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Smtp)
    servidor.recibidos = []  # type: ignore[attr-defined]
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    yield "127.0.0.1", servidor.server_address[1], servidor.recibidos  # type: ignore[attr-defined]
    servidor.shutdown()
    servidor.server_close()


def test_mensaje_de_prueba_lo_dice_en_el_cuerpo():
    assert "banco de pruebas" in M.texto.lower() and "banco de pruebas" in M.html.lower()
    assert "SETP1" in M.texto  # el número sale del asunto
    real = mensaje_factura(remitente="a@example.com", destinatario="b@example.com", asunto="1;A;FE9;01;A",
                           nombre_zip="z.zip", zip=b"", prueba=False)  # fmt: skip
    assert "banco de pruebas" not in real.texto.lower()


def test_smtp_entrega_el_zip_adjunto(servidor_smtp):
    host, puerto, recibidos = servidor_smtp
    EnviadorSmtp(host, puerto).enviar(M)
    (crudo,) = recibidos
    assert b"Subject: 900123456;EDS PRUEBA SAS;SETP1;01;EDS PRUEBA" in crudo
    assert b'filename="z.zip"' in crudo and b"application/zip" in crudo
    assert b"To: ana@example.com" in crudo


def test_smtp_caido_es_temporal():
    with pytest.raises(CorreoTemporal):
        EnviadorSmtp("127.0.0.1", 1, timeout=1).enviar(M)


class _SmtpFalso:
    error: Exception

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_message(self, m):
        raise self.error


@pytest.mark.parametrize(
    ("error", "esperado"),
    [
        (smtplib.SMTPRecipientsRefused({"ana@example.com": (550, b"no existe")}), CorreoRechazado),
        (smtplib.SMTPDataError(552, b"muy grande"), CorreoRechazado),
        (smtplib.SMTPDataError(451, b"intente luego"), CorreoTemporal),
        (smtplib.SMTPServerDisconnected("se cayó"), CorreoTemporal),
    ],
)
def test_clasificacion_de_errores_sin_datos_personales(monkeypatch, error, esperado):
    _SmtpFalso.error = error
    monkeypatch.setattr(smtplib, "SMTP", _SmtpFalso)
    with pytest.raises(esperado) as e:
        EnviadorSmtp("x", 25).enviar(M)
    assert "ana@example.com" not in str(e.value)  # Ley 1581: los errores no llevan el correo


def test_desde_entorno():
    e = EnviadorSmtp.desde_entorno({"ESTACION_SMTP_HOST": "mailpit", "ESTACION_SMTP_PUERTO": "2525"})
    assert (e.host, e.puerto) == ("mailpit", 2525)
    assert (EnviadorSmtp.desde_entorno({}).host, EnviadorSmtp.desde_entorno({}).puerto) == ("localhost", 1025)
    with pytest.raises(ValueError, match="ESTACION_SMTP_PUERTO"):
        EnviadorSmtp.desde_entorno({"ESTACION_SMTP_PUERTO": "abc"})


def test_en_memoria():
    e = EnviadorEnMemoria()
    e.enviar(M)
    assert e.enviados == [M]
    e.falla_siguiente = "temporal"
    with pytest.raises(CorreoTemporal):
        e.enviar(M)
    e.falla_siguiente = "rechazo"
    with pytest.raises(CorreoRechazado):
        e.enviar(M)
    e.enviar(M)  # la falla es de una sola vez
    assert e.enviados == [M, M]


class _SmtpGrabador:
    """SMTP falso que anota las órdenes: starttls, login y el mensaje enviado."""

    instancias: list[_SmtpGrabador] = []
    error_login: Exception | None = None

    def __init__(self, host, puerto, timeout=None, **k):
        self.host, self.puerto, self.ordenes, self.enviado = host, puerto, [], None
        _SmtpGrabador.instancias.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, **k):
        self.ordenes.append("starttls")

    def login(self, usuario, clave):
        self.ordenes.append(("login", usuario))
        if _SmtpGrabador.error_login is not None:
            raise _SmtpGrabador.error_login

    def send_message(self, m):
        self.ordenes.append("enviar")
        self.enviado = m


@pytest.fixture
def grabador(monkeypatch):
    _SmtpGrabador.instancias = []
    _SmtpGrabador.error_login = None
    monkeypatch.setattr(smtplib, "SMTP", _SmtpGrabador)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _SmtpGrabador)
    return _SmtpGrabador


GMAIL = dict(host="smtp.gmail.com", puerto=587, seguridad="starttls", usuario="yo@gmail.com",
             clave="abcd efgh ijkl mnop", remitente="yo@gmail.com")  # fmt: skip


def test_starttls_y_login_antes_de_enviar(grabador):
    EnviadorSmtp(**GMAIL).enviar(M)
    (s,) = grabador.instancias
    assert s.ordenes == ["starttls", ("login", "yo@gmail.com"), "enviar"]


def test_ssl_usa_smtp_ssl_sin_starttls(grabador, monkeypatch):
    usados = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: usados.append("ssl") or _SmtpGrabador(*a, **k))
    EnviadorSmtp(**{**GMAIL, "puerto": 465, "seguridad": "ssl"}).enviar(M)
    assert usados == ["ssl"]
    assert grabador.instancias[0].ordenes == [("login", "yo@gmail.com"), "enviar"]


def test_remitente_configurado_y_responder_al_emisor(grabador):
    EnviadorSmtp(**GMAIL).enviar(M)
    enviado = grabador.instancias[0].enviado
    assert enviado["From"] == "yo@gmail.com"
    assert enviado["Reply-To"] == "estacion@example.com"  # el correo del emisor que va en el XML


def test_sin_remitente_configurado_usa_el_del_emisor_sin_reply_to(grabador):
    EnviadorSmtp("localhost", 1025).enviar(M)
    enviado = grabador.instancias[0].enviado
    assert enviado["From"] == "estacion@example.com" and enviado["Reply-To"] is None
    assert grabador.instancias[0].ordenes == ["enviar"]  # Mailpit: sin TLS ni login


def test_credenciales_invalidas_es_temporal_y_no_filtra_la_clave(grabador):
    grabador.error_login = smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
    with pytest.raises(CorreoTemporal, match="credenciales SMTP") as e:
        EnviadorSmtp(**GMAIL).enviar(M)
    assert "abcd" not in str(e.value) and "yo@gmail.com" not in str(e.value)


def test_gmail_cierra_la_conexion_tras_el_535_y_sigue_siendo_credenciales(grabador):
    # smtplib prueba otro método tras el 535 y Gmail ya cerró: llega SMTPServerDisconnected desde login().
    grabador.error_login = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    with pytest.raises(CorreoTemporal, match="credenciales SMTP"):
        EnviadorSmtp(**GMAIL).enviar(M)


def test_la_clave_no_aparece_en_repr():
    assert "abcd" not in repr(EnviadorSmtp(**GMAIL))


def test_desde_entorno_gmail():
    e = EnviadorSmtp.desde_entorno({
        "ESTACION_SMTP_HOST": "smtp.gmail.com", "ESTACION_SMTP_PUERTO": "587", "ESTACION_SMTP_SEGURIDAD": "starttls",
        "ESTACION_SMTP_USUARIO": "yo@gmail.com", "ESTACION_SMTP_CLAVE": "abcd efgh ijkl mnop",
    })  # fmt: skip
    assert (e.seguridad, e.usuario, e.clave) == ("starttls", "yo@gmail.com", "abcd efgh ijkl mnop")
    assert e.remitente == "yo@gmail.com"  # sin ESTACION_SMTP_REMITENTE, el usuario


def test_desde_entorno_rechaza_configuraciones_inseguras_o_incompletas():
    with pytest.raises(ValueError, match="sin cifrar"):
        EnviadorSmtp.desde_entorno({"ESTACION_SMTP_USUARIO": "yo@gmail.com", "ESTACION_SMTP_CLAVE": "x"})
    with pytest.raises(ValueError, match="ESTACION_SMTP_CLAVE"):
        EnviadorSmtp.desde_entorno({"ESTACION_SMTP_SEGURIDAD": "starttls", "ESTACION_SMTP_USUARIO": "yo@gmail.com"})
    with pytest.raises(ValueError, match="ESTACION_SMTP_SEGURIDAD"):
        EnviadorSmtp.desde_entorno({"ESTACION_SMTP_SEGURIDAD": "tls"})


def test_lista_blanca():
    interno = EnviadorEnMemoria()
    e = ConListaBlanca(interno, frozenset({"ana@example.com"}))
    e.enviar(M)  # ana está en la lista
    otro = mensaje_factura(remitente="estacion@example.com", destinatario="Pedro@Example.com", asunto="1;A;SETP2;01;A",
                           nombre_zip="z.zip", zip=b"", prueba=True)  # fmt: skip
    with pytest.raises(CorreoRechazado, match="lista blanca") as err:
        e.enviar(otro)
    assert "Pedro" not in str(err.value).lower() and "pedro" not in str(err.value)
    assert interno.enviados == [M]


def test_enviador_desde_entorno_envuelve_con_lista_blanca():
    e = enviador_desde_entorno({"ESTACION_CORREO_SOLO_A": " Ana@Example.com , yo@gmail.com "})
    assert isinstance(e, ConListaBlanca) and e.permitidos == frozenset({"ana@example.com", "yo@gmail.com"})
    assert isinstance(enviador_desde_entorno({}), EnviadorSmtp)


def test_probar_correo(grabador, monkeypatch, capsys):
    from estacion.entrega import probar_correo

    for k, v in {"ESTACION_SMTP_HOST": "smtp.gmail.com", "ESTACION_SMTP_PUERTO": "587", "ESTACION_SMTP_SEGURIDAD":
                 "starttls", "ESTACION_SMTP_USUARIO": "yo@gmail.com", "ESTACION_SMTP_CLAVE": "x"}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ESTACION_CORREO_SOLO_A", raising=False)
    assert probar_correo.main(["ana@example.com"]) == 0
    assert grabador.instancias[0].enviado["To"] == "ana@example.com"
    grabador.error_login = smtplib.SMTPAuthenticationError(535, b"no")
    assert probar_correo.main(["ana@example.com"]) == 1
    assert "credenciales" in capsys.readouterr().err


def test_correo_de_nota_dice_nota_credito():
    nota = mensaje_factura(
        remitente="estacion@example.com", destinatario="ana@example.com",
        asunto="900123456;EDS PRUEBA SAS;NCP1;91;EDS PRUEBA", nombre_zip="z.zip", zip=b"PK\x03\x04", prueba=True,
        tipo_documento="nc",
    )  # fmt: skip
    assert "nota crédito electrónica NCP1 de EDS PRUEBA SAS" in nota.texto
    assert "BANCO DE PRUEBAS: esta nota crédito no tiene validez fiscal" in nota.texto
    assert "factura" not in nota.texto and "nota crédito" in nota.html
    assert "factura electrónica SETP1" in M.texto  # por defecto sigue siendo factura
