"""
Pruebas del microservicio de Pedidos, incluida la comunicación con Productos
y Clientes y los escenarios de fallo (tolerancia a fallos).
"""

from servicio_pedidos import main as pedidos
from servicio_productos import main as productos

PEDIDO = {"cliente_id": 1, "items": [{"producto_id": 1, "cantidad": 2}, {"producto_id": 2, "cantidad": 1}]}


def stock(producto_id: int) -> int:
    return productos.buscar_producto(producto_id).stock


def test_registrar_pedido_valida_y_descuenta_stock(api_pedidos):
    r = api_pedidos.post("/pedidos", json=PEDIDO)
    assert r.status_code == 201
    pedido = r.json()
    assert pedido["estado"] == "CREADO"
    assert pedido["nombre_cliente"] == "Laura Gómez"
    assert pedido["total"] == 2899000 * 2 + 459000
    assert stock(1) == 8 and stock(2) == 24


def test_items_repetidos_se_agrupan(api_pedidos):
    datos = {"cliente_id": 1, "items": [{"producto_id": 2, "cantidad": 1}, {"producto_id": 2, "cantidad": 2}]}
    pedido = api_pedidos.post("/pedidos", json=datos).json()
    assert len(pedido["items"]) == 1 and pedido["items"][0]["cantidad"] == 3


def test_cliente_inexistente(api_pedidos):
    r = api_pedidos.post("/pedidos", json={**PEDIDO, "cliente_id": 99})
    assert r.status_code == 404
    assert stock(1) == 10  # no se tocó el inventario


def test_producto_inexistente(api_pedidos):
    r = api_pedidos.post("/pedidos", json={"cliente_id": 1, "items": [{"producto_id": 99, "cantidad": 1}]})
    assert r.status_code == 404


def test_stock_insuficiente(api_pedidos):
    r = api_pedidos.post("/pedidos", json={"cliente_id": 1, "items": [{"producto_id": 4, "cantidad": 1}]})
    assert r.status_code == 409


def test_consultar_pedidos_y_estado(api_pedidos):
    api_pedidos.post("/pedidos", json=PEDIDO)
    assert len(api_pedidos.get("/pedidos", params={"cliente_id": 1}).json()) == 1
    assert api_pedidos.get("/pedidos/1/estado").json()["estado"] == "CREADO"
    assert api_pedidos.get("/pedidos/99").status_code == 404


def test_ciclo_de_vida_del_pedido(api_pedidos):
    api_pedidos.post("/pedidos", json=PEDIDO)
    for estado in ("PAGADO", "ENVIADO", "ENTREGADO"):
        r = api_pedidos.put("/pedidos/1/estado", json={"estado": estado})
        assert r.status_code == 200 and r.json()["estado"] == estado


def test_transicion_invalida(api_pedidos):
    api_pedidos.post("/pedidos", json=PEDIDO)
    r = api_pedidos.put("/pedidos/1/estado", json={"estado": "ENTREGADO"})
    assert r.status_code == 409


def test_cancelar_pedido_repone_stock(api_pedidos):
    api_pedidos.post("/pedidos", json=PEDIDO)
    assert stock(1) == 8
    r = api_pedidos.put("/pedidos/1/estado", json={"estado": "CANCELADO"})
    assert r.status_code == 200
    assert stock(1) == 10 and stock(2) == 25


# --------------------------- Tolerancia a fallos ---------------------------

def test_productos_caido_responde_503_con_reintentos(api_pedidos, red):
    red.apagar("productos")
    r = api_pedidos.post("/pedidos", json=PEDIDO)
    assert r.status_code == 503
    assert r.json()["servicio_afectado"] == "productos"
    assert red.peticiones["productos"] == pedidos.MAX_REINTENTOS  # se reintentó
    assert pedidos.pedidos_db == []  # no se registró un pedido incompleto


def test_clientes_caido_responde_503(api_pedidos, red):
    red.apagar("clientes")
    r = api_pedidos.post("/pedidos", json=PEDIDO)
    assert r.status_code == 503 and r.json()["servicio_afectado"] == "clientes"


def test_consultas_siguen_funcionando_sin_productos(api_pedidos, red):
    """Degradación parcial: los pedidos ya registrados se consultan sin Productos."""
    api_pedidos.post("/pedidos", json=PEDIDO)
    red.apagar("productos")
    r = api_pedidos.get("/pedidos/1")
    assert r.status_code == 200 and r.json()["items"][0]["nombre_producto"] == "Laptop Lenovo IdeaPad 5"


def test_compensacion_si_falla_segundo_producto(api_pedidos, red):
    """Si el 2.º descuento falla, se devuelve el stock del 1.º (patrón Saga)."""
    red.fallar_ruta("/productos/2/descontar-stock")
    r = api_pedidos.post("/pedidos", json=PEDIDO)
    assert r.status_code == 503
    assert stock(1) == 10  # las 2 unidades del producto 1 fueron devueltas
    assert pedidos.pedidos_db == []


def test_cancelar_con_productos_caido_conserva_estado(api_pedidos, red):
    api_pedidos.post("/pedidos", json=PEDIDO)
    red.apagar("productos")
    r = api_pedidos.put("/pedidos/1/estado", json={"estado": "CANCELADO"})
    assert r.status_code == 503
    assert api_pedidos.get("/pedidos/1/estado").json()["estado"] == "CREADO"
