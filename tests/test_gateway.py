"""Pruebas del API Gateway: enrutamiento y aislamiento de fallos."""


def test_enruta_a_productos(api_gateway):
    r = api_gateway.get("/api/productos", params={"categoria": "Monitores"})
    assert r.status_code == 200 and len(r.json()) == 1


def test_enruta_post_a_clientes(api_gateway):
    nuevo = {"nombre": "Camila Torres", "documento": "52123456", "email": "camila@correo.com", "ciudad": "Cali"}
    r = api_gateway.post("/api/clientes", json=nuevo)
    assert r.status_code == 201


def test_enruta_subrutas_y_errores(api_gateway):
    assert api_gateway.get("/api/productos/1").json()["id"] == 1
    assert api_gateway.get("/api/productos/999").status_code == 404


def test_servicio_desconocido(api_gateway):
    assert api_gateway.get("/api/pagos").status_code == 404


def test_estado_operativo(api_gateway):
    assert api_gateway.get("/api/estado").json()["sistema"] == "operativo"


def test_aislamiento_de_fallos(api_gateway, red):
    """Con Productos caído, sus rutas dan 503 pero Clientes sigue funcionando."""
    red.apagar("productos")
    assert api_gateway.get("/api/productos").status_code == 503
    assert api_gateway.get("/api/clientes").status_code == 200
    estado = api_gateway.get("/api/estado").json()
    assert estado["sistema"] == "degradado"
    assert estado["servicios"]["productos"] == "caído"
