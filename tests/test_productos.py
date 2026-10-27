"""Pruebas unitarias del microservicio de Productos."""

NUEVO = {"nombre": "Audífonos Sony WH-1000XM5", "categoria": "Audio", "precio": 1599000, "stock": 5}


def test_health(api_productos):
    r = api_productos.get("/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "activo"


def test_consultar_catalogo(api_productos):
    r = api_productos.get("/productos")
    assert r.status_code == 200
    assert len(r.json()) == 4


def test_filtrar_por_categoria_y_disponibilidad(api_productos):
    accesorios = api_productos.get("/productos", params={"categoria": "accesorios"}).json()
    assert len(accesorios) == 2
    disponibles = api_productos.get("/productos", params={"solo_disponibles": True}).json()
    assert all(p["stock"] > 0 for p in disponibles)


def test_consultar_producto_inexistente(api_productos):
    assert api_productos.get("/productos/999").status_code == 404


def test_registrar_producto(api_productos):
    r = api_productos.post("/productos", json=NUEVO)
    assert r.status_code == 201
    assert r.json()["id"] == 5
    assert api_productos.get("/productos/5").json()["nombre"] == NUEVO["nombre"]


def test_registrar_producto_invalido(api_productos):
    r = api_productos.post("/productos", json={**NUEVO, "precio": -10})
    assert r.status_code == 422


def test_actualizar_y_eliminar_producto(api_productos):
    r = api_productos.put("/productos/1", json={**NUEVO, "precio": 999000})
    assert r.status_code == 200 and r.json()["precio"] == 999000
    assert api_productos.delete("/productos/1").status_code == 204
    assert api_productos.get("/productos/1").status_code == 404


def test_descontar_y_reponer_stock(api_productos):
    r = api_productos.post("/productos/1/descontar-stock", json={"cantidad": 3})
    assert r.json()["stock"] == 7
    r = api_productos.post("/productos/1/reponer-stock", json={"cantidad": 3})
    assert r.json()["stock"] == 10


def test_descontar_stock_insuficiente(api_productos):
    r = api_productos.post("/productos/3/descontar-stock", json={"cantidad": 50})
    assert r.status_code == 409
    assert api_productos.get("/productos/3").json()["stock"] == 8  # no cambió
