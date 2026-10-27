"""
Microservicio de Pedidos
========================

Responsabilidad única: GESTIONAR LOS PEDIDOS de la tienda (registro, consulta
y ciclo de vida de su estado).

Es el único servicio con dependencias: para registrar un pedido se comunica
por HTTP/REST con los otros dos servicios:

    Pedidos ──GET /clientes/{id}──────────────────▶ Clientes   (¿existe el cliente?)
    Pedidos ──GET /productos/{id}─────────────────▶ Productos  (¿existe? ¿precio? ¿stock?)
    Pedidos ──POST /productos/{id}/descontar-stock▶ Productos  (reserva el inventario)

Mecanismos de tolerancia a fallos implementados:
    1. Tiempo de espera (timeout): ninguna llamada queda bloqueada indefinidamente.
    2. Reintentos con espera creciente: los fallos transitorios de red se reintentan.
    3. Respuesta controlada 503: si una dependencia está caída se informa qué
       servicio falló, en lugar de devolver un error genérico 500.
    4. Compensación (patrón Saga simplificado): si el descuento de stock falla a
       mitad de un pedido con varios productos, se devuelven las unidades ya
       descontadas para no dejar el inventario inconsistente.
    5. Degradación parcial: las consultas de pedidos usan solo datos propios,
       por lo que siguen funcionando aunque Productos o Clientes estén caídos.

Operaciones expuestas:
    GET  /health                 Estado del servicio (monitoreo)
    POST /pedidos                Registrar un pedido
    GET  /pedidos                Consultar pedidos (filtros por cliente y estado)
    GET  /pedidos/{id}           Consultar un pedido
    GET  /pedidos/{id}/estado    Consultar solo el estado de un pedido
    PUT  /pedidos/{id}/estado    Cambiar el estado de un pedido

Ejecución (desde la raíz del proyecto):
    uvicorn servicio_pedidos.main:app --port 8003

Documentación interactiva (Swagger): http://localhost:8003/docs
"""

import asyncio
import logging
import os
from datetime import datetime
from enum import Enum
from itertools import count
from typing import AsyncIterator, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PEDIDOS] %(message)s")
logger = logging.getLogger("pedidos")

NOMBRE_SERVICIO = "pedidos"


# ---------------------------------------------------------------------------
# Configuración (variables de entorno)
# ---------------------------------------------------------------------------
# Las direcciones de los otros servicios NO se escriben fijas en el código:
# se leen del entorno para poder desplegar el servicio en local, en Docker o
# en la nube sin modificarlo (principio de configuración externa).

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://127.0.0.1:8001")
CLIENTES_URL = os.getenv("CLIENTES_URL", "http://127.0.0.1:8002")
TIEMPO_ESPERA_SEG = float(os.getenv("TIEMPO_ESPERA_SEG", "2.0"))
MAX_REINTENTOS = int(os.getenv("MAX_REINTENTOS", "3"))
ESPERA_ENTRE_REINTENTOS_SEG = float(os.getenv("ESPERA_ENTRE_REINTENTOS_SEG", "0.5"))


# ---------------------------------------------------------------------------
# Modelos de datos (contratos de la API)
# ---------------------------------------------------------------------------

class EstadoPedido(str, Enum):
    """Estados posibles en el ciclo de vida de un pedido."""

    CREADO = "CREADO"
    PAGADO = "PAGADO"
    ENVIADO = "ENVIADO"
    ENTREGADO = "ENTREGADO"
    CANCELADO = "CANCELADO"


# Máquina de estados: desde cada estado solo se puede pasar a los indicados.
# Así se evita, por ejemplo, entregar un pedido que nunca fue pagado.
TRANSICIONES_VALIDAS: dict[EstadoPedido, set[EstadoPedido]] = {
    EstadoPedido.CREADO: {EstadoPedido.PAGADO, EstadoPedido.CANCELADO},
    EstadoPedido.PAGADO: {EstadoPedido.ENVIADO, EstadoPedido.CANCELADO},
    EstadoPedido.ENVIADO: {EstadoPedido.ENTREGADO},
    EstadoPedido.ENTREGADO: set(),
    EstadoPedido.CANCELADO: set(),
}


class ItemPedidoCrear(BaseModel):
    """Producto y cantidad solicitados dentro de un pedido."""

    producto_id: int = Field(..., gt=0, examples=[1])
    cantidad: int = Field(..., gt=0, le=100, examples=[2])


class PedidoCrear(BaseModel):
    """Datos que envía el cliente para registrar un pedido."""

    cliente_id: int = Field(..., gt=0, examples=[1])
    items: list[ItemPedidoCrear] = Field(..., min_length=1)


class ItemPedido(BaseModel):
    """
    Línea del pedido almacenada.

    Guarda una COPIA del nombre y el precio del producto en el momento de la
    compra. Así el pedido no cambia si mañana se modifica el precio en el
    catálogo, y se puede consultar sin depender del servicio de Productos.
    """

    producto_id: int
    nombre_producto: str
    precio_unitario: float
    cantidad: int
    subtotal: float


class Pedido(BaseModel):
    """Pedido tal como se almacena y se devuelve."""

    id: int
    cliente_id: int
    nombre_cliente: str
    items: list[ItemPedido]
    total: float
    estado: EstadoPedido
    fecha_creacion: datetime
    fecha_actualizacion: datetime


class CambioEstado(BaseModel):
    """Nuevo estado solicitado para un pedido."""

    estado: EstadoPedido = Field(..., examples=["PAGADO"])


# ---------------------------------------------------------------------------
# Almacenamiento en memoria
# ---------------------------------------------------------------------------

pedidos_db: list[Pedido] = []
_secuencia_id = count(1)


def reiniciar_datos() -> None:
    """Vacía la lista de pedidos (útil para pruebas)."""
    global _secuencia_id
    pedidos_db.clear()
    _secuencia_id = count(1)


def buscar_pedido(pedido_id: int) -> Pedido:
    """Devuelve el pedido con el id indicado o lanza HTTP 404 si no existe."""
    for pedido in pedidos_db:
        if pedido.id == pedido_id:
            return pedido
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Pedido {pedido_id} no encontrado")


# ---------------------------------------------------------------------------
# Comunicación con otros microservicios
# ---------------------------------------------------------------------------

class ServicioNoDisponible(Exception):
    """Se lanza cuando una dependencia no responde tras agotar los reintentos."""

    def __init__(self, servicio: str, detalle: str):
        super().__init__(f"{servicio}: {detalle}")
        self.servicio = servicio
        self.detalle = detalle


async def obtener_cliente_http() -> AsyncIterator[httpx.AsyncClient]:
    """
    Dependencia de FastAPI que entrega el cliente HTTP usado para hablar con
    los otros servicios. Al inyectarlo como dependencia, las pruebas pueden
    reemplazarlo por uno simulado sin levantar servidores reales.
    """
    async with httpx.AsyncClient() as cliente:
        yield cliente


async def llamar_servicio(
    http: httpx.AsyncClient, servicio: str, metodo: str, url: str, json: Optional[dict] = None
) -> httpx.Response:
    """
    Realiza una petición HTTP a otro microservicio aplicando timeout y reintentos.

    - Errores de red (servicio apagado, timeout) y respuestas 5xx se consideran
      transitorios y se reintentan hasta MAX_REINTENTOS veces, esperando un
      poco más en cada intento (espera lineal creciente).
    - Respuestas 4xx (404, 409...) son respuestas válidas del negocio: se
      devuelven de inmediato para que quien llama decida qué hacer.
    - Si se agotan los reintentos se lanza ServicioNoDisponible, que la
      aplicación transforma en una respuesta HTTP 503.
    """
    ultimo_error = "sin respuesta"
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            logger.info("-> %s %s %s (intento %d/%d)", servicio.upper(), metodo, url, intento, MAX_REINTENTOS)
            respuesta = await http.request(metodo, url, json=json, timeout=TIEMPO_ESPERA_SEG)
            if respuesta.status_code < 500:
                return respuesta
            ultimo_error = f"respondió HTTP {respuesta.status_code}"
        except httpx.TimeoutException:
            ultimo_error = f"no respondió en {TIEMPO_ESPERA_SEG} s (timeout)"
        except httpx.RequestError as exc:
            ultimo_error = f"error de conexión ({type(exc).__name__})"

        logger.warning("   Fallo al llamar a %s: %s", servicio.upper(), ultimo_error)
        if intento < MAX_REINTENTOS:
            await asyncio.sleep(ESPERA_ENTRE_REINTENTOS_SEG * intento)

    raise ServicioNoDisponible(servicio, ultimo_error)


async def consultar_cliente(http: httpx.AsyncClient, cliente_id: int) -> dict:
    """Pide al servicio de Clientes los datos de un cliente; 404 si no existe."""
    respuesta = await llamar_servicio(http, "clientes", "GET", f"{CLIENTES_URL}/clientes/{cliente_id}")
    if respuesta.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"El cliente {cliente_id} no existe")
    return respuesta.json()


async def consultar_producto(http: httpx.AsyncClient, producto_id: int) -> dict:
    """Pide al servicio de Productos los datos de un producto; 404 si no existe."""
    respuesta = await llamar_servicio(http, "productos", "GET", f"{PRODUCTOS_URL}/productos/{producto_id}")
    if respuesta.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"El producto {producto_id} no existe")
    return respuesta.json()


async def mover_stock(http: httpx.AsyncClient, producto_id: int, cantidad: int, operacion: str) -> httpx.Response:
    """Solicita a Productos descontar o reponer unidades (`operacion`: 'descontar' | 'reponer')."""
    url = f"{PRODUCTOS_URL}/productos/{producto_id}/{operacion}-stock"
    return await llamar_servicio(http, "productos", "POST", url, json={"cantidad": cantidad})


async def compensar_stock(http: httpx.AsyncClient, descontados: list[tuple[int, int]]) -> None:
    """
    Devuelve al inventario las unidades ya descontadas de un pedido que falló.

    Es el paso de compensación del patrón Saga: como no existe una transacción
    de base de datos que abarque varios servicios, cada paso exitoso se
    deshace explícitamente con la operación inversa.
    """
    for producto_id, cantidad in descontados:
        try:
            await mover_stock(http, producto_id, cantidad, "reponer")
            logger.info("   Compensación: repuestas %d unidades del producto %d", cantidad, producto_id)
        except ServicioNoDisponible:
            # En un sistema real esto se enviaría a una cola para reintentarlo luego.
            logger.error("   Compensación PENDIENTE: producto %d, %d unidades", producto_id, cantidad)


def agrupar_items(items: list[ItemPedidoCrear]) -> dict[int, int]:
    """Suma las cantidades si el mismo producto aparece varias veces en el pedido."""
    agrupados: dict[int, int] = {}
    for item in items:
        agrupados[item.producto_id] = agrupados.get(item.producto_id, 0) + item.cantidad
    return agrupados


# ---------------------------------------------------------------------------
# Aplicación FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Microservicio de Pedidos",
    description="Registra pedidos y gestiona su estado. Se comunica con los servicios de "
                "Clientes y Productos mediante HTTP/REST, con timeout, reintentos y compensación.",
    version="1.0.0",
    openapi_tags=[
        {"name": "Pedidos", "description": "Registro, consulta y estado de pedidos"},
        {"name": "Monitoreo", "description": "Verificación del estado del servicio"},
    ],
)


@app.exception_handler(ServicioNoDisponible)
async def manejar_servicio_no_disponible(_: Request, exc: ServicioNoDisponible) -> JSONResponse:
    """Traduce la caída de una dependencia en un 503 claro para el cliente."""
    logger.error("Dependencia NO disponible: %s (%s)", exc.servicio, exc.detalle)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": f"El servicio de {exc.servicio} no está disponible en este momento",
            "servicio_afectado": exc.servicio,
            "causa": exc.detalle,
            "sugerencia": "Intente nuevamente en unos minutos. Las consultas de pedidos existentes siguen disponibles.",
        },
    )


@app.get("/health", tags=["Monitoreo"], summary="Estado del servicio")
def health():
    """Permite al API Gateway (o a un orquestador) saber si el servicio está vivo."""
    return {"servicio": NOMBRE_SERVICIO, "estado": "activo", "total_pedidos": len(pedidos_db)}


@app.post("/pedidos", response_model=Pedido, status_code=status.HTTP_201_CREATED, tags=["Pedidos"],
          summary="Registrar un pedido")
async def crear_pedido(datos: PedidoCrear, http: httpx.AsyncClient = Depends(obtener_cliente_http)):
    """
    Registra un pedido en cuatro pasos:

    1. Valida el cliente con el servicio de **Clientes**.
    2. Consulta cada producto en **Productos** (existencia, precio y stock).
    3. Descuenta el stock en **Productos**; si algo falla, compensa lo descontado.
    4. Guarda el pedido con estado CREADO.

    Respuestas de error: 404 (cliente o producto inexistente), 409 (stock
    insuficiente), 503 (Clientes o Productos no disponibles).
    """
    logger.info("Nuevo pedido recibido para el cliente %d", datos.cliente_id)

    # Paso 1: validar cliente.
    cliente = await consultar_cliente(http, datos.cliente_id)

    # Paso 2: validar productos y armar las líneas del pedido.
    cantidades = agrupar_items(datos.items)
    lineas: list[ItemPedido] = []
    for producto_id, cantidad in cantidades.items():
        producto = await consultar_producto(http, producto_id)
        if producto["stock"] < cantidad:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Stock insuficiente para '{producto['nombre']}': disponible {producto['stock']}, solicitado {cantidad}",
            )
        lineas.append(ItemPedido(
            producto_id=producto_id,
            nombre_producto=producto["nombre"],
            precio_unitario=producto["precio"],
            cantidad=cantidad,
            subtotal=producto["precio"] * cantidad,
        ))

    # Paso 3: reservar el inventario. Productos vuelve a validar el stock en
    # este punto, por si otro pedido lo consumió entre el paso 2 y el 3.
    descontados: list[tuple[int, int]] = []
    try:
        for linea in lineas:
            respuesta = await mover_stock(http, linea.producto_id, linea.cantidad, "descontar")
            if respuesta.status_code != status.HTTP_200_OK:
                raise HTTPException(respuesta.status_code, respuesta.json().get("detail"))
            descontados.append((linea.producto_id, linea.cantidad))
    except (HTTPException, ServicioNoDisponible):
        await compensar_stock(http, descontados)
        raise

    # Paso 4: registrar el pedido.
    ahora = datetime.now()
    pedido = Pedido(
        id=next(_secuencia_id),
        cliente_id=datos.cliente_id,
        nombre_cliente=cliente["nombre"],
        items=lineas,
        total=sum(linea.subtotal for linea in lineas),
        estado=EstadoPedido.CREADO,
        fecha_creacion=ahora,
        fecha_actualizacion=ahora,
    )
    pedidos_db.append(pedido)
    logger.info("Pedido %d registrado: total $%s", pedido.id, f"{pedido.total:,.0f}")
    return pedido


@app.get("/pedidos", response_model=list[Pedido], tags=["Pedidos"], summary="Consultar pedidos")
def listar_pedidos(
    cliente_id: Optional[int] = Query(None, description="Filtra los pedidos de un cliente"),
    estado: Optional[EstadoPedido] = Query(None, description="Filtra por estado"),
):
    """Consulta pedidos usando solo datos propios: funciona aunque otros servicios estén caídos."""
    resultado = pedidos_db
    if cliente_id is not None:
        resultado = [p for p in resultado if p.cliente_id == cliente_id]
    if estado is not None:
        resultado = [p for p in resultado if p.estado == estado]
    return resultado


@app.get("/pedidos/{pedido_id}", response_model=Pedido, tags=["Pedidos"], summary="Consultar un pedido")
def obtener_pedido(pedido_id: int):
    """Devuelve el detalle completo de un pedido."""
    return buscar_pedido(pedido_id)


@app.get("/pedidos/{pedido_id}/estado", tags=["Pedidos"], summary="Consultar el estado de un pedido")
def obtener_estado(pedido_id: int):
    """Devuelve únicamente el estado actual del pedido y su última actualización."""
    pedido = buscar_pedido(pedido_id)
    return {"pedido_id": pedido.id, "estado": pedido.estado, "fecha_actualizacion": pedido.fecha_actualizacion}


@app.put("/pedidos/{pedido_id}/estado", response_model=Pedido, tags=["Pedidos"],
         summary="Cambiar el estado de un pedido")
async def cambiar_estado(pedido_id: int, cambio: CambioEstado,
                         http: httpx.AsyncClient = Depends(obtener_cliente_http)):
    """
    Avanza el pedido en su ciclo de vida: CREADO → PAGADO → ENVIADO → ENTREGADO.

    Un pedido CREADO o PAGADO puede CANCELARSE; en ese caso se devuelven sus
    unidades al inventario de Productos. Responde 409 si la transición no es
    válida (por ejemplo, de CREADO a ENTREGADO).
    """
    pedido = buscar_pedido(pedido_id)
    if cambio.estado not in TRANSICIONES_VALIDAS[pedido.estado]:
        permitidos = ", ".join(e.value for e in TRANSICIONES_VALIDAS[pedido.estado]) or "ninguno"
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No se puede pasar de {pedido.estado.value} a {cambio.estado.value}. Permitidos: {permitidos}",
        )

    if cambio.estado == EstadoPedido.CANCELADO:
        # Antes de devolver unidades se comprueba que Productos responda. Si está
        # caído, se lanza ServicioNoDisponible (503) y el pedido conserva su
        # estado, de modo que la cancelación puede reintentarse más tarde.
        await llamar_servicio(http, "productos", "GET", f"{PRODUCTOS_URL}/health")
        for linea in pedido.items:
            await mover_stock(http, linea.producto_id, linea.cantidad, "reponer")

    pedido.estado = cambio.estado
    pedido.fecha_actualizacion = datetime.now()
    logger.info("Pedido %d cambió a estado %s", pedido.id, pedido.estado.value)
    return pedido
