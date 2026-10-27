"""
Microservicio de Productos
==========================

Responsabilidad única: administrar el CATÁLOGO de la tienda virtual.

Este servicio es el dueño exclusivo de los datos de productos (nombre, precio,
categoría y stock). Ningún otro servicio modifica estos datos directamente:
si el servicio de Pedidos necesita descontar inventario, lo solicita a través
de la API REST de este servicio.

Operaciones expuestas:
    GET    /health                           Estado del servicio (monitoreo)
    GET    /productos                        Consultar el catálogo (con filtros)
    GET    /productos/{id}                   Consultar un producto
    POST   /productos                        Registrar un producto
    PUT    /productos/{id}                   Actualizar un producto
    DELETE /productos/{id}                   Eliminar un producto
    POST   /productos/{id}/descontar-stock   Descontar unidades (lo usa Pedidos)
    POST   /productos/{id}/reponer-stock     Devolver unidades (compensación)

Ejecución (desde la raíz del proyecto):
    uvicorn servicio_productos.main:app --port 8001

Documentación interactiva (Swagger): http://localhost:8001/docs

Nota: para simplificar el taller los datos se guardan en una lista en memoria,
por lo que se pierden al reiniciar el servicio. En un sistema real cada
microservicio tendría su propia base de datos.
"""

import logging
from itertools import count
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PRODUCTOS] %(message)s")
logger = logging.getLogger("productos")

NOMBRE_SERVICIO = "productos"


# ---------------------------------------------------------------------------
# Modelos de datos (contratos de la API)
# ---------------------------------------------------------------------------
# Pydantic valida automáticamente los datos de entrada: si un cliente envía
# un precio negativo o un nombre vacío, FastAPI responde 422 sin que el
# código del endpoint llegue a ejecutarse.

class ProductoBase(BaseModel):
    """Campos comunes de un producto del catálogo."""

    nombre: str = Field(..., min_length=2, max_length=100, examples=["Laptop Lenovo IdeaPad 5"])
    descripcion: str = Field("", max_length=300, examples=["Ryzen 7, 16 GB RAM, SSD 512 GB"])
    categoria: str = Field(..., min_length=2, max_length=50, examples=["Portátiles"])
    precio: float = Field(..., gt=0, description="Precio unitario en pesos colombianos", examples=[2899000])
    stock: int = Field(..., ge=0, description="Unidades disponibles en inventario", examples=[15])


class ProductoCrear(ProductoBase):
    """Datos que envía el cliente para registrar o actualizar un producto."""


class Producto(ProductoBase):
    """Producto tal como se almacena y se devuelve (incluye su identificador)."""

    id: int


class MovimientoStock(BaseModel):
    """Cantidad de unidades a descontar o reponer en el inventario."""

    cantidad: int = Field(..., gt=0, examples=[2])


# ---------------------------------------------------------------------------
# Almacenamiento en memoria
# ---------------------------------------------------------------------------

productos_db: list[Producto] = []
_secuencia_id = count(1)

# Catálogo inicial para que el servicio tenga datos de prueba al arrancar.
DATOS_SEMILLA = [
    {"nombre": "Laptop Lenovo IdeaPad 5", "descripcion": "Ryzen 7, 16 GB RAM, SSD 512 GB",
     "categoria": "Portátiles", "precio": 2899000, "stock": 10},
    {"nombre": "Mouse Logitech MX Master 3S", "descripcion": "Inalámbrico, 8000 DPI",
     "categoria": "Accesorios", "precio": 459000, "stock": 25},
    {"nombre": "Monitor Samsung 27\" 4K", "descripcion": "Panel IPS, 60 Hz, HDR10",
     "categoria": "Monitores", "precio": 1349000, "stock": 8},
    {"nombre": "Teclado mecánico Redragon Kumara", "descripcion": "Switches rojos, retroiluminado",
     "categoria": "Accesorios", "precio": 189000, "stock": 0},
]


def reiniciar_datos() -> None:
    """Restaura el catálogo a los datos semilla (útil para pruebas)."""
    global _secuencia_id
    productos_db.clear()
    _secuencia_id = count(1)
    for datos in DATOS_SEMILLA:
        productos_db.append(Producto(id=next(_secuencia_id), **datos))


def buscar_producto(producto_id: int) -> Producto:
    """Devuelve el producto con el id indicado o lanza HTTP 404 si no existe."""
    for producto in productos_db:
        if producto.id == producto_id:
            return producto
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Producto {producto_id} no encontrado")


reiniciar_datos()


# ---------------------------------------------------------------------------
# Aplicación FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Microservicio de Productos",
    description="Administra el catálogo de la tienda virtual: consulta, registro, "
                "actualización de productos y control de inventario.",
    version="1.0.0",
    openapi_tags=[
        {"name": "Productos", "description": "Operaciones sobre el catálogo"},
        {"name": "Inventario", "description": "Movimientos de stock solicitados por otros servicios"},
        {"name": "Monitoreo", "description": "Verificación del estado del servicio"},
    ],
)


@app.get("/health", tags=["Monitoreo"], summary="Estado del servicio")
def health():
    """Permite al API Gateway (o a un orquestador) saber si el servicio está vivo."""
    return {"servicio": NOMBRE_SERVICIO, "estado": "activo", "total_productos": len(productos_db)}


@app.get("/productos", response_model=list[Producto], tags=["Productos"], summary="Consultar el catálogo")
def listar_productos(
    categoria: Optional[str] = Query(None, description="Filtra por categoría (sin distinguir mayúsculas)"),
    solo_disponibles: bool = Query(False, description="Si es true, excluye productos sin stock"),
):
    """Devuelve el catálogo completo o filtrado por categoría y disponibilidad."""
    resultado = productos_db
    if categoria:
        resultado = [p for p in resultado if p.categoria.lower() == categoria.lower()]
    if solo_disponibles:
        resultado = [p for p in resultado if p.stock > 0]
    return resultado


@app.get("/productos/{producto_id}", response_model=Producto, tags=["Productos"],
         summary="Consultar un producto")
def obtener_producto(producto_id: int):
    """Devuelve un producto por su id. Lo usa el servicio de Pedidos para validar."""
    return buscar_producto(producto_id)


@app.post("/productos", response_model=Producto, status_code=status.HTTP_201_CREATED,
          tags=["Productos"], summary="Registrar un producto")
def crear_producto(datos: ProductoCrear):
    """Registra un nuevo producto en el catálogo y le asigna un id."""
    producto = Producto(id=next(_secuencia_id), **datos.model_dump())
    productos_db.append(producto)
    logger.info("Producto registrado: %s (id=%d)", producto.nombre, producto.id)
    return producto


@app.put("/productos/{producto_id}", response_model=Producto, tags=["Productos"],
         summary="Actualizar un producto")
def actualizar_producto(producto_id: int, datos: ProductoCrear):
    """Reemplaza todos los datos de un producto existente (semántica PUT)."""
    actual = buscar_producto(producto_id)
    actualizado = Producto(id=actual.id, **datos.model_dump())
    productos_db[productos_db.index(actual)] = actualizado
    logger.info("Producto actualizado: id=%d", producto_id)
    return actualizado


@app.delete("/productos/{producto_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Productos"],
            summary="Eliminar un producto")
def eliminar_producto(producto_id: int):
    """Elimina un producto del catálogo. Responde 204 sin contenido."""
    productos_db.remove(buscar_producto(producto_id))
    logger.info("Producto eliminado: id=%d", producto_id)


@app.post("/productos/{producto_id}/descontar-stock", response_model=Producto, tags=["Inventario"],
          summary="Descontar unidades del inventario")
def descontar_stock(producto_id: int, movimiento: MovimientoStock):
    """
    Descuenta unidades cuando se registra un pedido.

    La validación del stock se hace aquí, en el dueño del dato, para que dos
    pedidos simultáneos no puedan dejar el inventario en negativo.
    Responde 409 (conflicto) si no hay unidades suficientes.
    """
    producto = buscar_producto(producto_id)
    if producto.stock < movimiento.cantidad:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Stock insuficiente para '{producto.nombre}': "
                   f"disponible {producto.stock}, solicitado {movimiento.cantidad}",
        )
    producto.stock -= movimiento.cantidad
    logger.info("Stock descontado: id=%d -%d (queda %d)", producto_id, movimiento.cantidad, producto.stock)
    return producto


@app.post("/productos/{producto_id}/reponer-stock", response_model=Producto, tags=["Inventario"],
          summary="Reponer unidades al inventario")
def reponer_stock(producto_id: int, movimiento: MovimientoStock):
    """
    Devuelve unidades al inventario.

    Se usa como operación de COMPENSACIÓN: si un pedido falla a mitad de camino
    o se cancela, Pedidos devuelve las unidades que había descontado.
    """
    producto = buscar_producto(producto_id)
    producto.stock += movimiento.cantidad
    logger.info("Stock repuesto: id=%d +%d (queda %d)", producto_id, movimiento.cantidad, producto.stock)
    return producto
