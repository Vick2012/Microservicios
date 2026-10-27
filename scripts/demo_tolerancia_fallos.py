"""
Demostración de tolerancia a fallos: ¿qué pasa si el servicio de Productos cae?

El script levanta por sí mismo los cuatro procesos (gateway y tres servicios),
y luego:

    1. Verifica que el sistema esté "operativo".
    2. Registra un pedido con todo funcionando.
    3. DETIENE el proceso de Productos (simula la caída).
    4. Comprueba el comportamiento con Productos caído:
         - el gateway reporta el sistema como "degradado";
         - registrar un pedido devuelve 503 (tras reintentos) y no un error 500;
         - Clientes sigue funcionando con normalidad;
         - los pedidos existentes se siguen consultando.
    5. REINICIA Productos y verifica que el sistema se recupera.

Requisito: que los puertos 8000-8003 estén libres (cierre las ventanas de
iniciar_servicios.ps1 antes de ejecutarlo).
Uso:       .venv\\Scripts\\python scripts\\demo_tolerancia_fallos.py
Salida:    evidencias/demo_tolerancia_fallos.txt
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from evidencia import Evidencia

RAIZ = Path(__file__).resolve().parent.parent
GATEWAY = "http://127.0.0.1:8000"
SERVICIOS = {
    "productos": ("servicio_productos.main:app", 8001),
    "clientes": ("servicio_clientes.main:app", 8002),
    "pedidos": ("servicio_pedidos.main:app", 8003),
    "gateway": ("gateway.main:app", 8000),
}


def puerto_ocupado(puerto: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", puerto)) == 0


def iniciar(nombre: str) -> subprocess.Popen:
    """Lanza un servicio como proceso independiente y espera a que responda."""
    modulo, puerto = SERVICIOS[nombre]
    proceso = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", modulo, "--port", str(puerto), "--log-level", "warning"],
        cwd=RAIZ,
    )
    for _ in range(50):
        if puerto_ocupado(puerto):
            return proceso
        time.sleep(0.2)
    raise RuntimeError(f"El servicio {nombre} no arrancó en el puerto {puerto}")


def main() -> bool:
    ocupados = [str(p) for _, p in SERVICIOS.values() if puerto_ocupado(p)]
    if ocupados:
        print(f"Los puertos {', '.join(ocupados)} están en uso. Cierre los servicios antes de ejecutar la demo.")
        return False

    procesos = {nombre: iniciar(nombre) for nombre in SERVICIOS}
    ev = Evidencia("demo_tolerancia_fallos.txt", "DEMOSTRACIÓN DE TOLERANCIA A FALLOS - CAÍDA DEL SERVICIO DE PRODUCTOS")
    pedido = {"cliente_id": 1, "items": [{"producto_id": 1, "cantidad": 1}]}

    try:
        with httpx.Client(base_url=GATEWAY, timeout=15) as api:
            ev.seccion("1. Situación normal: todos los servicios activos")
            ev.peticion(api, "GET", "/api/estado", 200, "Sistema operativo")
            ev.peticion(api, "POST", "/api/pedidos", 201, "Registrar pedido con todo funcionando", json=pedido)

            ev.seccion("2. Se detiene el proceso del servicio de PRODUCTOS")
            procesos["productos"].terminate()
            procesos["productos"].wait()
            ev.escribir("        Proceso de Productos (puerto 8001) detenido.")

            ev.seccion("3. Comportamiento con Productos caído")
            ev.peticion(api, "GET", "/api/estado", 200, "El gateway detecta el sistema como 'degradado'")
            inicio = time.perf_counter()
            ev.peticion(api, "POST", "/api/pedidos", 503,
                        "Registrar pedido: respuesta controlada 503 tras reintentos", json=pedido)
            ev.escribir(f"        Tiempo hasta responder (3 intentos con espera creciente): "
                        f"{time.perf_counter() - inicio:.2f} s")
            ev.peticion(api, "GET", "/api/productos", 503, "Consultar catálogo: 503 desde el gateway")
            ev.peticion(api, "GET", "/api/clientes", 200, "Clientes SIGUE funcionando (aislamiento de fallos)")
            ev.peticion(api, "GET", "/api/pedidos/1", 200, "Los pedidos existentes se siguen consultando")

            ev.seccion("4. Recuperación: se reinicia el servicio de PRODUCTOS")
            procesos["productos"] = iniciar("productos")
            ev.peticion(api, "GET", "/api/estado", 200, "Sistema nuevamente operativo")
            ev.peticion(api, "POST", "/api/pedidos", 201, "Registrar pedido tras la recuperación", json=pedido)
            ev.escribir()
            ev.escribir("Nota: al reiniciarse, Productos vuelve a sus datos semilla porque usa listas en")
            ev.escribir("memoria. En producción cada servicio tendría su propia base de datos persistente.")
    finally:
        for proceso in procesos.values():
            proceso.terminate()

    return ev.cerrar()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
