# Tienda Virtual — Arquitectura de Microservicios

Taller práctico de **Sistemas Distribuidos / Arquitectura de Software**.
Implementación en **Python + FastAPI** de una tienda virtual dividida en
microservicios independientes que se comunican mediante **API REST (HTTP)**.

## Arquitectura

```
                         USUARIO
                            |
                            v
              API GATEWAY  (aplicación cliente)  :8000
              /api/productos  /api/clientes  /api/pedidos
                 |               |               |
                 v               v               v
          +-----------+   +-----------+   +-----------+
          | PRODUCTOS |   | CLIENTES  |   |  PEDIDOS  |
          |   :8001   |   |   :8002   |   |   :8003   |
          +-----------+   +-----------+   +-----------+
                ^               ^            |      |
                |               +--- GET ----+      |
                |                 /clientes/{id}    |
                +-------- GET /productos/{id} ------+
                         POST /productos/{id}/descontar-stock
                 |               |               |
             Catálogo        Usuarios         Órdenes
            (en memoria)   (en memoria)    (en memoria)
```

| Microservicio | Puerto | Responsabilidad | Depende de |
|---|---|---|---|
| **Gateway** | 8000 | Punto de entrada único, enrutamiento y tablero de salud | Los tres servicios |
| **Productos** | 8001 | Catálogo e inventario (stock) | Ninguno |
| **Clientes** | 8002 | Registro y consulta de clientes | Ninguno |
| **Pedidos** | 8003 | Registro de pedidos y ciclo de vida de su estado | Clientes, Productos |

Cada servicio es **dueño exclusivo de sus datos**: ninguno lee la lista de otro;
todo intercambio de información pasa por su API REST.

## Endpoints

### Productos — http://127.0.0.1:8001/docs
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/productos?categoria=&solo_disponibles=` | Consultar catálogo |
| GET | `/productos/{id}` | Consultar un producto |
| POST | `/productos` | Registrar producto |
| PUT | `/productos/{id}` | Actualizar producto |
| DELETE | `/productos/{id}` | Eliminar producto |
| POST | `/productos/{id}/descontar-stock` | Descontar unidades (usado por Pedidos) |
| POST | `/productos/{id}/reponer-stock` | Reponer unidades (compensación) |

### Clientes — http://127.0.0.1:8002/docs
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/clientes?ciudad=` | Consultar clientes |
| GET | `/clientes/{id}` | Consultar un cliente |
| POST | `/clientes` | Registrar cliente (correo y documento únicos) |
| PUT | `/clientes/{id}` | Actualizar cliente |
| DELETE | `/clientes/{id}` | Eliminar cliente |

### Pedidos — http://127.0.0.1:8003/docs
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/pedidos` | Registrar pedido (valida cliente, productos y stock) |
| GET | `/pedidos?cliente_id=&estado=` | Consultar pedidos |
| GET | `/pedidos/{id}` | Consultar un pedido |
| GET | `/pedidos/{id}/estado` | Consultar su estado |
| PUT | `/pedidos/{id}/estado` | Cambiar estado: CREADO → PAGADO → ENVIADO → ENTREGADO (o CANCELADO) |

### Gateway — http://127.0.0.1:8000
| Ruta | Descripción |
|---|---|
| `/api/estado` | Estado de los tres servicios (`operativo` / `degradado`) |
| `/api/{servicio}/...` | Reenvía la petición al servicio correspondiente |

Todos los servicios exponen `GET /health` para monitoreo.

## Tolerancia a fallos implementada

1. **Timeout** de 2 s en cada llamada entre servicios.
2. **Reintentos** (3) con espera creciente ante errores de red o respuestas 5xx.
3. **Respuesta controlada 503** indicando qué servicio falló, en lugar de un 500.
4. **Compensación (Saga simplificada):** si el descuento de stock falla a mitad
   de un pedido, se devuelven las unidades ya descontadas.
5. **Degradación parcial:** los pedidos guardan una copia de nombre y precio,
   por lo que se consultan aunque Productos esté caído.
6. **Aislamiento en el gateway:** si un servicio cae, solo sus rutas fallan.

## Cómo ejecutarlo (Windows / PowerShell)

Desde la carpeta del proyecto:

```powershell
# 1. Crear el entorno virtual e instalar dependencias (solo la primera vez)
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 2. Pruebas automáticas (36 pruebas)
.venv\Scripts\python -m pytest -v

# 3. Levantar los 4 servicios (abre una ventana por servicio)
powershell -ExecutionPolicy Bypass -File scripts\iniciar_servicios.ps1

# 4. Con los servicios arriba: prueba de extremo a extremo
.venv\Scripts\python scripts\prueba_e2e.py

# 5. Cerrar las ventanas de los servicios y ejecutar la demo de caída de Productos
.venv\Scripts\python scripts\demo_tolerancia_fallos.py
```

Las evidencias quedan en `evidencias/prueba_e2e.txt` y
`evidencias/demo_tolerancia_fallos.txt`.

> En Windows, conectarse a un puerto sin servicio tarda unos 2 s en fallar;
> por eso, con Productos caído, el pedido responde 503 tras unos 8 s
> (3 intentos con timeout + esperas entre reintentos).

### Con Docker (opcional)

```powershell
docker compose up --build -d
.venv\Scripts\python scripts\prueba_e2e.py          # prueba E2E contra los contenedores
.venv\Scripts\python scripts\demo_docker_fallos.py  # detiene y reinicia el contenedor de Productos
docker compose down
```

> Antes de usar Docker cierre las ventanas de `iniciar_servicios.ps1`: ambos
> modos usan los mismos puertos (8000-8003).

## Informe del taller

El documento final está en `docs/Taller_Microservicios_Grupo7.docx`, y los
diagramas e imágenes que usa están en `docs/diagramas` y `docs/img`.

## Estructura del proyecto

```
TallerMicroservicios/
├── servicio_productos/   main.py, Dockerfile, requirements.txt
├── servicio_clientes/    main.py, Dockerfile, requirements.txt
├── servicio_pedidos/     main.py, Dockerfile, requirements.txt
├── gateway/              main.py, Dockerfile, requirements.txt
├── tests/                Pruebas pytest (unitarias, integración y fallos)
├── scripts/              Arranque, prueba E2E y demo de tolerancia a fallos
├── evidencias/           Salidas de ejecución para el informe
├── docker-compose.yml
└── requirements.txt
```

> Los datos se guardan en listas en memoria por simplicidad del taller. En un
> sistema real cada microservicio tendría su propia base de datos.
