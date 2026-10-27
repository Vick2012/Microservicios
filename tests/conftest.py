"""
Configuración compartida de las pruebas.

Para probar la comunicación entre servicios sin levantar servidores reales se
usa una "red simulada": un transporte de httpx que entrega cada petición
directamente a la aplicación FastAPI correspondiente según el host de la URL
(http://productos, http://clientes, http://pedidos). La red permite además
"apagar" un servicio para probar la tolerancia a fallos.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import main as gateway
from servicio_clientes import main as clientes
from servicio_pedidos import main as pedidos
from servicio_productos import main as productos


class RedSimulada(httpx.AsyncBaseTransport):
    """Transporte HTTP que enruta peticiones a apps ASGI en memoria."""

    def __init__(self, apps: dict):
        self.transportes = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}
        self.caidos: set[str] = set()
        self.rutas_con_fallo: set[str] = set()
        self.peticiones: dict[str, int] = {host: 0 for host in apps}

    def apagar(self, host: str) -> None:
        """Simula que el servicio dejó de funcionar (conexión rechazada)."""
        self.caidos.add(host)

    def fallar_ruta(self, ruta: str) -> None:
        """Simula un fallo de red solo en una ruta concreta (el resto responde)."""
        self.rutas_con_fallo.add(ruta)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.peticiones[host] = self.peticiones.get(host, 0) + 1
        if host in self.caidos or host not in self.transportes or request.url.path in self.rutas_con_fallo:
            raise httpx.ConnectError(f"Conexión rechazada por {host}", request=request)
        return await self.transportes[host].handle_async_request(request)


@pytest.fixture(autouse=True)
def datos_limpios():
    """Antes de cada prueba restaura los datos semilla de todos los servicios."""
    productos.reiniciar_datos()
    clientes.reiniciar_datos()
    pedidos.reiniciar_datos()


@pytest.fixture
def red(monkeypatch) -> RedSimulada:
    """Red simulada con los tres servicios y sus URLs apuntando a ella."""
    red = RedSimulada({"productos": productos.app, "clientes": clientes.app, "pedidos": pedidos.app})

    async def cliente_simulado():
        async with httpx.AsyncClient(transport=red) as cliente:
            yield cliente

    monkeypatch.setattr(pedidos, "PRODUCTOS_URL", "http://productos")
    monkeypatch.setattr(pedidos, "CLIENTES_URL", "http://clientes")
    monkeypatch.setattr(pedidos, "ESPERA_ENTRE_REINTENTOS_SEG", 0)  # pruebas rápidas
    monkeypatch.setitem(gateway.SERVICIOS, "productos", "http://productos")
    monkeypatch.setitem(gateway.SERVICIOS, "clientes", "http://clientes")
    monkeypatch.setitem(gateway.SERVICIOS, "pedidos", "http://pedidos")

    pedidos.app.dependency_overrides[pedidos.obtener_cliente_http] = cliente_simulado
    gateway.app.dependency_overrides[gateway.obtener_cliente_http] = cliente_simulado
    yield red
    pedidos.app.dependency_overrides.clear()
    gateway.app.dependency_overrides.clear()


@pytest.fixture
def api_productos() -> TestClient:
    return TestClient(productos.app)


@pytest.fixture
def api_clientes() -> TestClient:
    return TestClient(clientes.app)


@pytest.fixture
def api_pedidos(red) -> TestClient:
    return TestClient(pedidos.app)


@pytest.fixture
def api_gateway(red) -> TestClient:
    return TestClient(gateway.app)
