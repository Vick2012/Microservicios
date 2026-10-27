"""
Prueba de extremo a extremo (E2E) de la tienda virtual.

Recorre el flujo completo de un usuario a través del API Gateway, con los
servicios corriendo de verdad (cada uno en su puerto):

    1. Verifica el estado de todos los microservicios.
    2. Consulta y registra productos.         (servicio de Productos)
    3. Registra y consulta clientes.          (servicio de Clientes)
    4. Registra un pedido y sigue su estado.  (Pedidos -> Clientes y Productos)
    5. Prueba los casos de error de negocio (404, 409, 422).

Requisito: tener los servicios levantados (scripts/iniciar_servicios.ps1).
Uso:       .venv\\Scripts\\python scripts\\prueba_e2e.py
Salida:    evidencias/prueba_e2e.txt
"""

import sys

import httpx

from evidencia import Evidencia

GATEWAY = "http://127.0.0.1:8000"


def main() -> bool:
    ev = Evidencia("prueba_e2e.txt", "PRUEBA DE EXTREMO A EXTREMO - TIENDA VIRTUAL CON MICROSERVICIOS")
    with httpx.Client(base_url=GATEWAY, timeout=15) as api:
        try:
            api.get("/")
        except httpx.ConnectError:
            print("No hay conexión con el gateway. Ejecute primero scripts\\iniciar_servicios.ps1")
            return False

        ev.seccion("1. Estado de los microservicios")
        ev.peticion(api, "GET", "/api/estado", 200, "Todos los servicios responden")

        ev.seccion("2. Servicio de PRODUCTOS")
        ev.peticion(api, "GET", "/api/productos", 200, "Consultar el catálogo completo")
        nuevo_producto = {"nombre": "Audífonos Sony WH-1000XM5", "descripcion": "Cancelación de ruido",
                          "categoria": "Audio", "precio": 1599000, "stock": 6}
        producto = ev.peticion(api, "POST", "/api/productos", 201, "Registrar un producto",
                               json=nuevo_producto).json()
        ev.peticion(api, "GET", f"/api/productos/{producto['id']}", 200, "Consultar el producto registrado")
        ev.peticion(api, "GET", "/api/productos?categoria=Accesorios", 200, "Filtrar por categoría")

        ev.seccion("3. Servicio de CLIENTES")
        nuevo_cliente = {"nombre": "Camila Torres", "documento": "52123456", "email": "camila.torres@correo.com",
                         "telefono": "3105551234", "ciudad": "Cali"}
        cliente = ev.peticion(api, "POST", "/api/clientes", 201, "Registrar un cliente", json=nuevo_cliente).json()
        ev.peticion(api, "GET", f"/api/clientes/{cliente['id']}", 200, "Consultar el cliente registrado")
        ev.peticion(api, "GET", "/api/clientes", 200, "Consultar todos los clientes")

        ev.seccion("4. Servicio de PEDIDOS (comunicación entre servicios)")
        datos_pedido = {"cliente_id": cliente["id"],
                        "items": [{"producto_id": producto["id"], "cantidad": 2}, {"producto_id": 2, "cantidad": 1}]}
        pedido = ev.peticion(api, "POST", "/api/pedidos", 201,
                             "Registrar pedido (valida cliente, productos y descuenta stock)",
                             json=datos_pedido).json()
        ev.peticion(api, "GET", f"/api/productos/{producto['id']}", 200,
                    "El stock del producto bajó de 6 a 4")
        ev.peticion(api, "GET", f"/api/pedidos/{pedido['id']}/estado", 200, "Consultar el estado del pedido")
        for estado in ("PAGADO", "ENVIADO", "ENTREGADO"):
            ev.peticion(api, "PUT", f"/api/pedidos/{pedido['id']}/estado", 200,
                        f"Cambiar el estado a {estado}", json={"estado": estado})
        ev.peticion(api, "GET", f"/api/pedidos?cliente_id={cliente['id']}", 200, "Consultar pedidos del cliente")

        ev.seccion("5. Casos de error controlados")
        ev.peticion(api, "POST", "/api/pedidos", 404, "Pedido con cliente inexistente",
                    json={"cliente_id": 999, "items": [{"producto_id": 1, "cantidad": 1}]})
        ev.peticion(api, "POST", "/api/pedidos", 409, "Pedido de producto sin stock (teclado, stock 0)",
                    json={"cliente_id": 1, "items": [{"producto_id": 4, "cantidad": 1}]})
        ev.peticion(api, "POST", "/api/clientes", 409, "Cliente con correo duplicado", json=nuevo_cliente)
        ev.peticion(api, "POST", "/api/productos", 422, "Producto con precio negativo",
                    json={**nuevo_producto, "precio": -5})
        ev.peticion(api, "PUT", f"/api/pedidos/{pedido['id']}/estado", 409,
                    "Transición inválida (cancelar un pedido ya entregado)", json={"estado": "CANCELADO"})

    return ev.cerrar()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
