"""
Demostración de tolerancia a fallos sobre el despliegue con Docker.

Versión de demo_tolerancia_fallos.py para contenedores: en lugar de matar un
proceso, detiene el contenedor de Productos con `docker compose stop` y luego
lo vuelve a iniciar con `docker compose start`. Así se evidencia el
DESPLIEGUE INDEPENDIENTE: se apaga y enciende un servicio sin tocar los demás.

Requisito: haber levantado el sistema con `docker compose up --build -d`.
Uso:       .venv\\Scripts\\python scripts\\demo_docker_fallos.py
Salida:    evidencias/demo_docker_fallos.txt
"""

import subprocess
import sys
import time
from pathlib import Path

import httpx

from evidencia import Evidencia

RAIZ = Path(__file__).resolve().parent.parent
GATEWAY = "http://127.0.0.1:8000"


def docker_compose(ev: Evidencia, *argumentos: str) -> None:
    """Ejecuta un comando de docker compose y registra su salida en la evidencia."""
    comando = ["docker", "compose", *argumentos]
    ev.escribir("        $ " + " ".join(comando))
    resultado = subprocess.run(comando, cwd=RAIZ, capture_output=True, text=True)
    for linea in (resultado.stdout + resultado.stderr).strip().splitlines():
        ev.escribir("          " + linea.strip())


def esperar_gateway_operativo(api: httpx.Client, segundos: int = 30) -> None:
    """Espera a que el gateway reporte todos los servicios activos."""
    for _ in range(segundos * 2):
        try:
            if api.get("/api/estado").json()["sistema"] == "operativo":
                return
        except httpx.RequestError:
            pass
        time.sleep(0.5)


def main() -> bool:
    ev = Evidencia("demo_docker_fallos.txt", "TOLERANCIA A FALLOS CON DOCKER - CAÍDA DEL CONTENEDOR DE PRODUCTOS")
    pedido = {"cliente_id": 2, "items": [{"producto_id": 3, "cantidad": 1}]}

    with httpx.Client(base_url=GATEWAY, timeout=20) as api:
        ev.seccion("1. Contenedores en ejecución")
        docker_compose(ev, "ps", "--format", "table {{.Service}}\t{{.State}}\t{{.Ports}}")
        ev.peticion(api, "GET", "/api/estado", 200, "Sistema operativo")
        ev.peticion(api, "POST", "/api/pedidos", 201, "Registrar pedido con todo funcionando", json=pedido)

        ev.seccion("2. Se detiene SOLO el contenedor de Productos")
        docker_compose(ev, "stop", "productos")
        docker_compose(ev, "ps", "--all", "--format", "table {{.Service}}\t{{.State}}")

        ev.seccion("3. Comportamiento con Productos caído")
        ev.peticion(api, "GET", "/api/estado", 200, "El gateway reporta el sistema 'degradado'")
        inicio = time.perf_counter()
        ev.peticion(api, "POST", "/api/pedidos", 503, "Registrar pedido: 503 controlado tras reintentos", json=pedido)
        ev.escribir(f"        Tiempo hasta responder: {time.perf_counter() - inicio:.2f} s")
        ev.peticion(api, "GET", "/api/clientes", 200, "Clientes sigue funcionando (aislamiento)")
        ev.peticion(api, "GET", "/api/pedidos", 200, "Los pedidos existentes se siguen consultando")

        ev.seccion("4. Recuperación: se inicia de nuevo el contenedor de Productos")
        docker_compose(ev, "start", "productos")
        esperar_gateway_operativo(api)
        ev.peticion(api, "GET", "/api/estado", 200, "Sistema nuevamente operativo")
        ev.peticion(api, "POST", "/api/pedidos", 201, "Registrar pedido tras la recuperación", json=pedido)

    return ev.cerrar()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
