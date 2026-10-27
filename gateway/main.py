"""
API Gateway de la Tienda Virtual
================================

Representa la capa "APLICACIÓN CLIENTE" del diagrama de arquitectura: es el
PUNTO DE ENTRADA ÚNICO para el usuario (frontend web, app móvil, Postman...).

    USUARIO ──▶ GATEWAY (:8000) ──┬──▶ Productos (:8001)
                                  ├──▶ Clientes  (:8002)
                                  └──▶ Pedidos   (:8003)

Responsabilidades:
    - Enrutar cada petición al microservicio correspondiente según la URL:
          /api/productos/...  ──▶  {PRODUCTOS_URL}/productos/...
          /api/clientes/...   ──▶  {CLIENTES_URL}/clientes/...
          /api/pedidos/...    ──▶  {PEDIDOS_URL}/pedidos/...
    - Ocultar al usuario la ubicación (host y puerto) de cada servicio.
    - Ofrecer un tablero de salud (/api/estado) que muestra qué servicios
      están activos y cuáles caídos.
    - Aislar fallos: si un servicio está caído, solo fallan sus rutas (503);
      las de los demás servicios siguen respondiendo con normalidad.

El gateway NO contiene lógica de negocio: solo reenvía peticiones.

Ejecución (desde la raíz del proyecto):
    uvicorn gateway.main:app --port 8000

Documentación: http://localhost:8000/docs
"""

import logging
import os
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GATEWAY] %(message)s")
logger = logging.getLogger("gateway")

# Tabla de enrutamiento: nombre del recurso -> URL base del microservicio dueño.
SERVICIOS: dict[str, str] = {
    "productos": os.getenv("PRODUCTOS_URL", "http://127.0.0.1:8001"),
    "clientes": os.getenv("CLIENTES_URL", "http://127.0.0.1:8002"),
    "pedidos": os.getenv("PEDIDOS_URL", "http://127.0.0.1:8003"),
}

# El gateway espera un poco más que Pedidos, porque Pedidos a su vez hace
# reintentos contra Productos y Clientes antes de responder.
TIEMPO_ESPERA_SEG = float(os.getenv("TIEMPO_ESPERA_SEG", "10.0"))
TIEMPO_ESPERA_HEALTH_SEG = 1.0


async def obtener_cliente_http() -> AsyncIterator[httpx.AsyncClient]:
    """Dependencia que entrega el cliente HTTP (reemplazable en las pruebas)."""
    async with httpx.AsyncClient() as cliente:
        yield cliente


app = FastAPI(
    title="API Gateway - Tienda Virtual",
    description="Punto de entrada único. Enruta las peticiones `/api/{servicio}/...` hacia los "
                "microservicios de Productos, Clientes y Pedidos. La documentación detallada de "
                "cada API está en el `/docs` de cada servicio (puertos 8001, 8002 y 8003).",
    version="1.0.0",
)


@app.get("/", tags=["Gateway"], summary="Información del gateway")
def raiz():
    """Muestra las rutas disponibles y a qué servicio se dirige cada una."""
    return {
        "aplicacion": "Tienda Virtual - Arquitectura de Microservicios",
        "rutas": {f"/api/{nombre}": url for nombre, url in SERVICIOS.items()},
        "estado_servicios": "/api/estado",
    }


@app.get("/api/estado", tags=["Gateway"], summary="Estado de todos los microservicios")
async def estado_servicios(http: httpx.AsyncClient = Depends(obtener_cliente_http)):
    """
    Consulta el /health de cada microservicio y resume el estado del sistema:
    - "operativo": todos los servicios responden.
    - "degradado": al menos uno está caído, pero el resto sigue funcionando.
    """
    detalle = {}
    for nombre, url in SERVICIOS.items():
        try:
            respuesta = await http.get(f"{url}/health", timeout=TIEMPO_ESPERA_HEALTH_SEG)
            detalle[nombre] = "activo" if respuesta.status_code == 200 else f"error HTTP {respuesta.status_code}"
        except httpx.RequestError:
            detalle[nombre] = "caído"
    general = "operativo" if all(e == "activo" for e in detalle.values()) else "degradado"
    return {"sistema": general, "servicios": detalle}


# Se declaran dos rutas para aceptar tanto /api/productos como /api/productos/1/...
@app.api_route("/api/{servicio}", methods=["GET", "POST", "PUT", "DELETE"], tags=["Enrutamiento"],
               summary="Reenviar petición a un microservicio (recurso raíz)")
@app.api_route("/api/{servicio}/{ruta:path}", methods=["GET", "POST", "PUT", "DELETE"], tags=["Enrutamiento"],
               summary="Reenviar petición a un microservicio")
async def reenviar(servicio: str, request: Request, ruta: str = "",
                   http: httpx.AsyncClient = Depends(obtener_cliente_http)):
    """
    Reenvía la petición (método, cuerpo y parámetros) al microservicio dueño
    del recurso y devuelve su respuesta sin modificarla.
    """
    if servicio not in SERVICIOS:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Servicio '{servicio}' desconocido. Disponibles: {', '.join(SERVICIOS)}")

    destino = f"{SERVICIOS[servicio]}/{servicio}" + (f"/{ruta}" if ruta else "")
    logger.info("%s %s -> %s", request.method, request.url.path, destino)

    try:
        respuesta = await http.request(
            request.method,
            destino,
            params=request.query_params,
            content=await request.body(),
            headers={"content-type": request.headers.get("content-type", "application/json")},
            timeout=TIEMPO_ESPERA_SEG,
        )
    except httpx.RequestError as exc:
        # Aislamiento de fallos: solo falla la ruta del servicio caído.
        logger.error("Servicio %s no disponible: %s", servicio, type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": f"El servicio de {servicio} no está disponible", "servicio_afectado": servicio},
        )

    return Response(
        content=respuesta.content,
        status_code=respuesta.status_code,
        media_type=respuesta.headers.get("content-type"),
    )
