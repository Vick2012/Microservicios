"""Pruebas unitarias del microservicio de Clientes."""

NUEVO = {"nombre": "Camila Torres", "documento": "52123456", "email": "camila.torres@correo.com",
         "telefono": "3105551234", "ciudad": "Cali"}


def test_health(api_clientes):
    assert api_clientes.get("/health").json()["estado"] == "activo"


def test_consultar_clientes(api_clientes):
    assert len(api_clientes.get("/clientes").json()) == 2
    assert len(api_clientes.get("/clientes", params={"ciudad": "bogotá"}).json()) == 1


def test_registrar_y_consultar_cliente(api_clientes):
    r = api_clientes.post("/clientes", json=NUEVO)
    assert r.status_code == 201
    assert api_clientes.get(f"/clientes/{r.json()['id']}").json()["email"] == NUEVO["email"]


def test_cliente_inexistente(api_clientes):
    assert api_clientes.get("/clientes/999").status_code == 404


def test_email_duplicado(api_clientes):
    r = api_clientes.post("/clientes", json={**NUEVO, "email": "LAURA.GOMEZ@correo.com"})
    assert r.status_code == 409


def test_email_invalido(api_clientes):
    assert api_clientes.post("/clientes", json={**NUEVO, "email": "no-es-correo"}).status_code == 422


def test_actualizar_y_eliminar_cliente(api_clientes):
    r = api_clientes.put("/clientes/1", json={**NUEVO, "email": "laura.gomez@correo.com", "documento": "1020304050"})
    assert r.status_code == 200 and r.json()["ciudad"] == "Cali"
    assert api_clientes.delete("/clientes/1").status_code == 204
    assert api_clientes.get("/clientes/1").status_code == 404
