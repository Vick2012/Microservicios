"""
Microservicio de Clientes
=========================

Responsabilidad única: administrar la información de los CLIENTES de la tienda.

Es el dueño exclusivo de los datos personales (nombre, correo, documento,
teléfono y ciudad). El servicio de Pedidos lo consulta para verificar que un
cliente existe antes de registrarle un pedido, pero nunca accede a sus datos
de forma directa.

Operaciones expuestas:
    GET    /health            Estado del servicio (monitoreo)
    GET    /clientes          Consultar clientes (con filtro por ciudad)
    GET    /clientes/{id}     Consultar un cliente
    POST   /clientes          Registrar un cliente
    PUT    /clientes/{id}     Actualizar un cliente
    DELETE /clientes/{id}     Eliminar un cliente

Ejecución (desde la raíz del proyecto):
    uvicorn servicio_clientes.main:app --port 8002

Documentación interactiva (Swagger): http://localhost:8002/docs
"""

import logging
from itertools import count
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLIENTES] %(message)s")
logger = logging.getLogger("clientes")

NOMBRE_SERVICIO = "clientes"

# Expresión regular sencilla para validar correos sin dependencias externas.
PATRON_EMAIL = r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$"


# ---------------------------------------------------------------------------
# Modelos de datos (contratos de la API)
# ---------------------------------------------------------------------------

class ClienteBase(BaseModel):
    """Campos comunes de un cliente."""

    nombre: str = Field(..., min_length=3, max_length=100, examples=["Laura Gómez"])
    documento: str = Field(..., min_length=5, max_length=20, pattern=r"^\d+$",
                           description="Número de documento (solo dígitos)", examples=["1020304050"])
    email: str = Field(..., pattern=PATRON_EMAIL, examples=["laura.gomez@correo.com"])
    telefono: str = Field("", max_length=20, examples=["3001234567"])
    ciudad: str = Field(..., min_length=2, max_length=60, examples=["Bogotá"])


class ClienteCrear(ClienteBase):
    """Datos que se envían para registrar o actualizar un cliente."""


class Cliente(ClienteBase):
    """Cliente tal como se almacena y se devuelve (incluye su identificador)."""

    id: int


# ---------------------------------------------------------------------------
# Almacenamiento en memoria
# ---------------------------------------------------------------------------

clientes_db: list[Cliente] = []
_secuencia_id = count(1)

DATOS_SEMILLA = [
    {"nombre": "Laura Gómez", "documento": "1020304050", "email": "laura.gomez@correo.com",
     "telefono": "3001234567", "ciudad": "Bogotá"},
    {"nombre": "Andrés Rojas", "documento": "1098765432", "email": "andres.rojas@correo.com",
     "telefono": "3159876543", "ciudad": "Medellín"},
]


def reiniciar_datos() -> None:
    """Restaura la lista de clientes a los datos semilla (útil para pruebas)."""
    global _secuencia_id
    clientes_db.clear()
    _secuencia_id = count(1)
    for datos in DATOS_SEMILLA:
        clientes_db.append(Cliente(id=next(_secuencia_id), **datos))


def buscar_cliente(cliente_id: int) -> Cliente:
    """Devuelve el cliente con el id indicado o lanza HTTP 404 si no existe."""
    for cliente in clientes_db:
        if cliente.id == cliente_id:
            return cliente
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Cliente {cliente_id} no encontrado")


def validar_unicidad(datos: ClienteCrear, excluir_id: Optional[int] = None) -> None:
    """
    Garantiza que el correo y el documento no estén registrados por otro cliente.

    Lanza HTTP 409 (conflicto) si encuentra un duplicado. `excluir_id` permite
    que un cliente se actualice a sí mismo sin chocar con su propio registro.
    """
    for cliente in clientes_db:
        if cliente.id == excluir_id:
            continue
        if cliente.email.lower() == datos.email.lower():
            raise HTTPException(status.HTTP_409_CONFLICT, f"El correo {datos.email} ya está registrado")
        if cliente.documento == datos.documento:
            raise HTTPException(status.HTTP_409_CONFLICT, f"El documento {datos.documento} ya está registrado")


reiniciar_datos()


# ---------------------------------------------------------------------------
# Aplicación FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Microservicio de Clientes",
    description="Administra el registro y la consulta de los clientes de la tienda virtual.",
    version="1.0.0",
    openapi_tags=[
        {"name": "Clientes", "description": "Registro y consulta de clientes"},
        {"name": "Monitoreo", "description": "Verificación del estado del servicio"},
    ],
)


@app.get("/health", tags=["Monitoreo"], summary="Estado del servicio")
def health():
    """Permite al API Gateway (o a un orquestador) saber si el servicio está vivo."""
    return {"servicio": NOMBRE_SERVICIO, "estado": "activo", "total_clientes": len(clientes_db)}


@app.get("/clientes", response_model=list[Cliente], tags=["Clientes"], summary="Consultar clientes")
def listar_clientes(ciudad: Optional[str] = Query(None, description="Filtra por ciudad")):
    """Devuelve todos los clientes o solo los de una ciudad."""
    if ciudad:
        return [c for c in clientes_db if c.ciudad.lower() == ciudad.lower()]
    return clientes_db


@app.get("/clientes/{cliente_id}", response_model=Cliente, tags=["Clientes"], summary="Consultar un cliente")
def obtener_cliente(cliente_id: int):
    """Devuelve un cliente por su id. Lo usa el servicio de Pedidos para validar."""
    return buscar_cliente(cliente_id)


@app.post("/clientes", response_model=Cliente, status_code=status.HTTP_201_CREATED,
          tags=["Clientes"], summary="Registrar un cliente")
def crear_cliente(datos: ClienteCrear):
    """Registra un nuevo cliente. Responde 409 si el correo o documento ya existen."""
    validar_unicidad(datos)
    cliente = Cliente(id=next(_secuencia_id), **datos.model_dump())
    clientes_db.append(cliente)
    logger.info("Cliente registrado: %s (id=%d)", cliente.nombre, cliente.id)
    return cliente


@app.put("/clientes/{cliente_id}", response_model=Cliente, tags=["Clientes"], summary="Actualizar un cliente")
def actualizar_cliente(cliente_id: int, datos: ClienteCrear):
    """Reemplaza todos los datos de un cliente existente."""
    actual = buscar_cliente(cliente_id)
    validar_unicidad(datos, excluir_id=cliente_id)
    actualizado = Cliente(id=actual.id, **datos.model_dump())
    clientes_db[clientes_db.index(actual)] = actualizado
    logger.info("Cliente actualizado: id=%d", cliente_id)
    return actualizado


@app.delete("/clientes/{cliente_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Clientes"],
            summary="Eliminar un cliente")
def eliminar_cliente(cliente_id: int):
    """Elimina un cliente. Responde 204 sin contenido."""
    clientes_db.remove(buscar_cliente(cliente_id))
    logger.info("Cliente eliminado: id=%d", cliente_id)
