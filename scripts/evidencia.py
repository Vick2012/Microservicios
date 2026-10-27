"""
Utilidades para registrar las evidencias de ejecución.

Cada petición se imprime en consola y además se guarda en un archivo de texto
dentro de la carpeta `evidencias/`, listo para copiar al documento final.
"""

import json
from datetime import datetime
from pathlib import Path

import httpx

CARPETA_EVIDENCIAS = Path(__file__).resolve().parent.parent / "evidencias"


class Evidencia:
    """Registra peticiones HTTP y sus respuestas en consola y en un archivo."""

    def __init__(self, nombre_archivo: str, titulo: str):
        CARPETA_EVIDENCIAS.mkdir(exist_ok=True)
        self.ruta = CARPETA_EVIDENCIAS / nombre_archivo
        self.lineas: list[str] = []
        self.exitosas = 0
        self.fallidas = 0
        self.escribir("=" * 78)
        self.escribir(titulo)
        self.escribir(f"Fecha de ejecución: {datetime.now():%Y-%m-%d %H:%M:%S}")
        self.escribir("=" * 78)

    def escribir(self, texto: str = "") -> None:
        print(texto)
        self.lineas.append(texto)

    def seccion(self, titulo: str) -> None:
        self.escribir()
        self.escribir(f"--- {titulo} " + "-" * max(0, 72 - len(titulo)))

    def peticion(self, cliente: httpx.Client, metodo: str, url: str, esperado: int,
                 descripcion: str, **kwargs) -> httpx.Response:
        """Ejecuta la petición, muestra la respuesta y verifica el código esperado."""
        respuesta = cliente.request(metodo, url, **kwargs)
        correcto = respuesta.status_code == esperado
        self.exitosas += correcto
        self.fallidas += not correcto
        marca = "[OK]   " if correcto else "[FALLO]"
        self.escribir(f"{marca} {descripcion}")
        self.escribir(f"        {metodo} {url}  ->  HTTP {respuesta.status_code} (esperado {esperado})")
        if "json" in kwargs:
            self.escribir("        Cuerpo enviado: " + json.dumps(kwargs["json"], ensure_ascii=False))
        if respuesta.content:
            cuerpo = json.dumps(respuesta.json(), ensure_ascii=False, indent=2, default=str)
            if len(cuerpo) > 900:
                cuerpo = cuerpo[:900] + "\n... (respuesta recortada)"
            self.escribir("        Respuesta: " + cuerpo.replace("\n", "\n        "))
        return respuesta

    def cerrar(self) -> bool:
        """Escribe el resumen, guarda el archivo y devuelve True si todo pasó."""
        self.escribir()
        self.escribir("=" * 78)
        self.escribir(f"RESUMEN: {self.exitosas} verificaciones correctas, {self.fallidas} fallidas")
        self.escribir("=" * 78)
        self.ruta.write_text("\n".join(self.lineas), encoding="utf-8")
        print(f"\nEvidencia guardada en: {self.ruta}")
        return self.fallidas == 0
