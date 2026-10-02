"""Administración por API contra PostgreSQL real: búsqueda de ventas, usuarios, clientes, vehículos e
importación CSV. Datos sintéticos."""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import Api, clave, persona

pytestmark = pytest.mark.bd

RANGO = {"desde": "2026-09-30T00:00:00", "hasta": "2026-10-01T00:00:00"}


def facturar(api: Api, origen_tipo: str, origen_id: int) -> dict:
    datos = {"clave": clave(), "origen_tipo": origen_tipo, "origen_id": origen_id, "cliente_id": api.e.cliente,
             "medio_pago": "EFECTIVO"}  # fmt: skip
    r = api.post("/facturas", datos, como=api.bombero)
    assert r.status_code == 201, r.text
    return r.json()


def ventas(api: Api, **filtros) -> dict:
    r = api.get("/ventas", como=api.admin, params={**RANGO, **filtros})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------ ventas
def test_buscar_ventas_con_filtros_y_totales(api):
    d = api.e.datos
    despacho = d.despacho()  # 30-sep 10:02, lado A, DIESEL, 1,861 gal, $29.757
    d.conn.execute("UPDATE despacho SET ref_vehiculo = 'GZY848', placa_tipo = 'PLACA' WHERE id = %s", (despacho,))
    bombero_id = d.uno("SELECT id FROM usuario WHERE usuario = %s", (api.bombero,))
    manual = d.venta_manual(bombero_id)  # 30-sep 11:00, lado B, $50.000
    factura = facturar(api, "despacho", despacho)

    todo = ventas(api)
    assert [f["origen_tipo"] for f in todo["filas"]] == ["venta_manual", "despacho"]  # más reciente primero
    assert todo["total"] == {"ventas": 2, "galones": "4.981", "valor": "79757"}
    assert {(m["lado"], m["ventas"]) for m in todo["por_manguera"]} == {("A", 1), ("B", 1)}

    fila = ventas(api, placa="gzy-8")["filas"]
    assert len(fila) == 1 and fila[0]["origen_id"] == despacho
    assert fila[0]["factura_numero"] == factura["numero"] and fila[0]["vendedor"] == "Bombero Prueba"
    assert fila[0]["cliente_nombre"] == "ANA MARÍA PÉREZ GÓMEZ" and fila[0]["volumen"] == "1.861"
    assert [f["origen_id"] for f in ventas(api, factura=factura["numero"].lower())["filas"]] == [despacho]
    assert [f["origen_id"] for f in ventas(api, lado="b")["filas"]] == [manual]
    assert ventas(api, vendedor_id=bombero_id)["total"]["ventas"] == 2  # factura del despacho + venta digitada
    assert ventas(api, estado_facturacion="PENDIENTE")["total"]["ventas"] == 1
    cliente_doc = d.uno("SELECT numero_documento FROM cliente WHERE id = %s", (api.e.cliente,))
    assert ventas(api, cliente=cliente_doc)["total"]["ventas"] == 1
    externo = d.uno("SELECT id_externo FROM despacho WHERE id = %s", (despacho,))
    assert ventas(api, id_externo=externo)["total"]["ventas"] == 1

    # La página no cambia los totales
    pagina = ventas(api, limite=1)
    assert len(pagina["filas"]) == 1 and pagina["total"]["ventas"] == 2
    # Fuera del rango no hay nada
    assert api.get("/ventas", como=api.admin, params={"desde": "2026-10-01T00:00", "hasta": "2026-10-02T00:00"}).json()[
        "total"
    ] == {"ventas": 0, "galones": "0.000", "valor": "0"}


def test_venta_manual_enlazada_cuenta_una_sola_vez(api):
    d = api.e.datos
    despacho = d.despacho()
    bombero_id = d.uno("SELECT id FROM usuario WHERE usuario = %s", (api.bombero,))
    manual = d.venta_manual(bombero_id, lado="A", volumen="1.861", valor=29757, ppu=15990)
    factura = facturar(api, "venta_manual", manual)
    d.conn.execute("UPDATE venta_manual SET despacho_id = %s WHERE id = %s", (despacho, manual))

    r = ventas(api)
    assert r["total"]["ventas"] == 1
    (fila,) = r["filas"]
    assert (fila["origen_tipo"], fila["origen_id"], fila["factura_numero"]) == ("despacho", despacho, factura["numero"])


def test_ventas_validaciones_y_permisos(api):
    assert api.get("/ventas", como=api.bombero, params=RANGO).status_code == 403
    assert api.get("/ventas", params=RANGO).status_code == 401
    al_reves = {"desde": RANGO["hasta"], "hasta": RANGO["desde"]}
    assert api.get("/ventas", como=api.admin, params=al_reves).status_code == 422
    largo = {"desde": "2025-01-01T00:00", "hasta": "2026-10-01T00:00"}
    assert api.get("/ventas", como=api.admin, params=largo).status_code == 422
    assert api.get("/ventas", como=api.admin, params={**RANGO, "estado_facturacion": "X"}).status_code == 422


def test_usuarios_sin_hash_del_pin(api):
    r = api.get("/usuarios", como=api.admin)
    assert r.status_code == 200
    assert {u["usuario"] for u in r.json()} >= {api.bombero, api.admin}
    assert all(set(u) == {"id", "usuario", "nombre", "rol", "activo"} for u in r.json())
    assert api.get("/usuarios", como=api.bombero).status_code == 403


# ------------------------------------------------------------------------------------------ clientes
def test_listar_y_buscar_clientes(api):
    api.post("/clientes", persona(), como=api.bombero)
    r = api.get("/clientes", como=api.admin, params={"texto": "luis car"}).json()
    assert r["total"] == 1 and r["filas"][0]["nombre_mostrar"] == "LUIS CARLOS ROJAS"
    assert r["filas"][0]["email_enmascarado"] == "lu********@example.com"
    assert api.get("/clientes", como=api.admin, params={"texto": "10496"}).json()["total"] == 1
    assert api.get("/clientes", como=api.admin, params={"limite": 1}).json()["total"] == 2  # + el del escenario
    assert api.get("/clientes", como=api.bombero).status_code == 403


def test_baja_logica_de_cliente(api):
    cid = api.post("/clientes", persona(), como=api.bombero).json()["id"]
    baja = api.post(f"/clientes/{cid}/desactivar", {}, como=api.admin)
    assert baja.status_code == 200 and baja.json()["activo"] is False
    # En la pista ya no aparece y no se le puede facturar
    assert api.get("/clientes/buscar?tipo=CC&numero=1049612345", como=api.bombero).status_code == 404
    despacho = api.e.datos.despacho()
    datos = {"clave": clave(), "origen_tipo": "despacho", "origen_id": despacho, "cliente_id": cid,
             "medio_pago": "EFECTIVO"}  # fmt: skip
    assert api.post("/facturas", datos, como=api.bombero).status_code == 422
    assert api.get("/clientes", como=api.admin, params={"estado": "inactivos"}).json()["total"] == 1

    assert api.post(f"/clientes/{cid}/activar", {}, como=api.admin).json()["activo"] is True
    assert api.post("/clientes/999999/desactivar", {}, como=api.admin).status_code == 404
    acciones = api.e.datos.bd.consultar(
        "SELECT accion, detalle::text FROM auditoria WHERE entidad = 'cliente' AND entidad_id = %s ORDER BY id",
        (str(cid),),
    )
    assert [a for a, _ in acciones] == ["CLIENTE_REGISTRADO", "CLIENTE_DESACTIVADO", "CLIENTE_ACTIVADO"]


# ------------------------------------------------------------------------------------------ vehículos
def test_vehiculos(api):
    cid = api.e.cliente
    nuevo = api.post("/vehiculos", {"placa": " gzy-848 ", "cliente_id": cid, "descripcion": "Bus 12"}, como=api.admin)
    assert nuevo.status_code == 201, nuevo.text
    v = nuevo.json()
    assert (v["identificador"], v["tipo"], v["cliente_nombre"]) == ("GZY848", "PLACA", "ANA MARÍA PÉREZ GÓMEZ")
    equipo = api.post("/vehiculos", {"placa": "Bobcat", "cliente_id": cid}, como=api.admin).json()
    assert equipo["tipo"] == "EQUIPO_TEXTO"

    assert api.post("/vehiculos", {"placa": "GZY848", "cliente_id": cid}, como=api.admin).status_code == 409
    assert api.post("/vehiculos", {"placa": "1", "cliente_id": cid}, como=api.admin).status_code == 422
    assert api.post("/vehiculos", {"placa": "ABC123", "cliente_id": 999999}, como=api.admin).status_code == 422

    assert api.get("/vehiculos", como=api.admin, params={"texto": "gzy"}).json()["total"] == 1
    assert api.get("/vehiculos", como=api.admin, params={"cliente_id": cid}).json()["total"] == 2
    assert api.post(f"/vehiculos/{v['id']}/desactivar", {}, como=api.admin).json()["activo"] is False
    assert api.get("/vehiculos", como=api.admin, params={"texto": "gzy"}).json()["total"] == 0
    assert api.get("/vehiculos", como=api.bombero).status_code == 403


# ------------------------------------------------------------------------------------------ importación CSV
CABECERA = "tipo_documento;numero_documento;nombres;apellidos;email;autoriza_tratamiento;fecha_autorizacion\n"


def importar(api: Api, ruta: str, texto: str | bytes, en_seco: bool = True):
    cuerpo = texto.encode() if isinstance(texto, str) else texto
    return api.cliente.post(
        f"{ruta}?en_seco={'true' if en_seco else 'false'}",
        content=cuerpo,
        headers={**api.entrar(api.admin), "Content-Type": "text/csv"},
    )


def test_importar_clientes(api):
    texto = CABECERA + (
        "CC;71234567;Pedro;Prueba Uno;pedro@example.com;SI;2025-03-01\n"  # 2: nueva
        "NIT;900373115;Transportes de Prueba SAS;;flota@example.com;sí;15/02/2024\n"  # 3: nueva (DV se calcula)
        "CC;81234567;Sin;Permiso;sin@example.com;NO;2025-03-01\n"  # 4: sin autorización
        "CC;91234567;Fecha;Futura;f@example.com;SI;2099-01-01\n"  # 5: fecha futura
        "CC;123;Corta;Cédula;c@example.com;SI;2025-03-01\n"  # 6: documento inválido
        "CC;71234567;Pedro;Repetido;p2@example.com;SI;2025-03-01\n"  # 7: repetido en el archivo
    )
    seco = importar(api, "/clientes/importar", texto)
    assert seco.status_code == 200, seco.text
    r = seco.json()
    assert (r["en_seco"], r["filas"], r["nuevas"], r["existentes"]) == (True, 6, 2, 0)
    assert [x["fila"] for x in r["rechazadas"]] == [4, 5, 6, 7]
    assert "Ley 1581" in r["rechazadas"][0]["problemas"][0]
    assert api.e.datos.uno("SELECT count(*) FROM cliente WHERE numero_documento = '71234567'") == 0  # en seco

    real = importar(api, "/clientes/importar", texto, en_seco=False).json()
    assert (real["en_seco"], real["nuevas"]) == (False, 2)
    nit = api.e.datos.bd.consultar(
        "SELECT digito_verificacion, nombres, fecha_autorizacion::date::text FROM cliente WHERE numero_documento = %s",
        ("900373115",),
    )
    assert nit == [("3", "TRANSPORTES DE PRUEBA SAS", "2024-02-15")]
    # Repetir: nada se sobrescribe
    otra = importar(api, "/clientes/importar", texto, en_seco=False).json()
    assert (otra["nuevas"], otra["existentes"]) == (0, 2)
    detalle = api.e.datos.uno("SELECT detalle::text FROM auditoria WHERE accion = 'CLIENTES_IMPORTADOS' LIMIT 1")
    assert "71234567" not in detalle and "Pedro" not in detalle.upper()


def test_importar_clientes_excel_en_espanol(api):
    """Separador ';' y codificación Windows-1252, como guarda Excel en español."""
    texto = (CABECERA + "CC;71234568;José;Núñez;jose@example.com;Sí;01/03/2025\n").encode("cp1252")
    r = importar(api, "/clientes/importar", texto, en_seco=False).json()
    assert r["nuevas"] == 1
    assert api.e.datos.uno("SELECT apellidos FROM cliente WHERE numero_documento = '71234568'") == "NÚÑEZ"


def test_importar_archivo_invalido(api):
    assert importar(api, "/clientes/importar", "documento,nombre\n1,2\n").status_code == 422
    assert importar(api, "/clientes/importar", "").status_code == 422
    assert importar(api, "/clientes/importar", CABECERA + "x" * (2 * 1024 * 1024)).status_code == 413
    sin_admin = api.cliente.post(
        "/clientes/importar", content=CABECERA.encode(), headers={**api.entrar(api.bombero), "Content-Type": "text/csv"}
    )
    assert sin_admin.status_code == 403


def test_importar_vehiculos(api):
    doc = api.e.datos.uno("SELECT numero_documento FROM cliente WHERE id = %s", (api.e.cliente,))
    otro = api.post("/clientes", persona(), como=api.bombero).json()["id"]
    api.post("/vehiculos", {"placa": "TTT111", "cliente_id": otro}, como=api.admin)
    texto = (
        "placa,tipo_documento,numero_documento,descripcion\n"
        f"gzy-848,CC,{doc},Bus 12\n"  # 2: nueva
        f"Motoniveladora,CC,{doc},\n"  # 3: nueva (equipo)
        "ABC123,CC,5555555,\n"  # 4: dueño no registrado
        f"0,CC,{doc},\n"  # 5: relleno, no es vehículo
        f"TTT111,CC,{doc},\n"  # 6: de otro cliente
        f"GZY848,CC,{doc},\n"  # 7: repetida en el archivo
    )
    r = importar(api, "/vehiculos/importar", texto, en_seco=False).json()
    assert (r["nuevas"], r["existentes"]) == (2, 0)
    assert [x["fila"] for x in r["rechazadas"]] == [4, 5, 6, 7]
    tipos = api.e.datos.bd.consultar(
        "SELECT identificador, tipo, descripcion FROM vehiculo WHERE cliente_id = %s ORDER BY 1", (api.e.cliente,)
    )
    assert tipos == [("GZY848", "PLACA", "Bus 12"), ("MOTONIVELADORA", "EQUIPO_TEXTO", None)]
    assert importar(api, "/vehiculos/importar", texto, en_seco=False).json()["existentes"] == 2


def test_totales_de_ventas_cuadran_con_decimales(api):
    """Los totales se suman como Decimal en la base, no como flotante."""
    d = api.e.datos
    for _ in range(3):
        d.despacho()
    r = ventas(api)
    assert Decimal(r["total"]["galones"]) == Decimal("1.861") * 3 and r["total"]["valor"] == "89271"
