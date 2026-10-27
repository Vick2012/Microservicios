<#
.SYNOPSIS
    Levanta los 4 componentes de la tienda virtual, cada uno en su propia ventana.

.DESCRIPTION
    Cada microservicio es un proceso independiente con su propio puerto:
        Gateway   -> http://localhost:8000
        Productos -> http://localhost:8001
        Clientes  -> http://localhost:8002
        Pedidos   -> http://localhost:8003
    Para simular la caida de un servicio basta con cerrar su ventana (o Ctrl+C).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\iniciar_servicios.ps1
#>

$raiz = Split-Path -Parent $PSScriptRoot
$python = Join-Path $raiz ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No se encontro el entorno virtual (.venv). Ejecute primero:" -ForegroundColor Red
    Write-Host "    python -m venv .venv"
    Write-Host "    .venv\Scripts\python -m pip install -r requirements.txt"
    exit 1
}

$servicios = @(
    @{ Nombre = "PRODUCTOS"; Modulo = "servicio_productos.main:app"; Puerto = 8001 },
    @{ Nombre = "CLIENTES";  Modulo = "servicio_clientes.main:app";  Puerto = 8002 },
    @{ Nombre = "PEDIDOS";   Modulo = "servicio_pedidos.main:app";   Puerto = 8003 },
    @{ Nombre = "GATEWAY";   Modulo = "gateway.main:app";            Puerto = 8000 }
)

foreach ($s in $servicios) {
    $comando = "`$host.UI.RawUI.WindowTitle = 'Servicio $($s.Nombre) :$($s.Puerto)'; " +
               "& '$python' -m uvicorn $($s.Modulo) --port $($s.Puerto)"
    Start-Process powershell -WorkingDirectory $raiz -ArgumentList "-NoExit", "-Command", $comando
    Write-Host "Iniciado $($s.Nombre) en http://localhost:$($s.Puerto)/docs" -ForegroundColor Green
}

Write-Host ""
Write-Host "Estado general: http://localhost:8000/api/estado" -ForegroundColor Cyan
