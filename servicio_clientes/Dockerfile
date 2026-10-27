# Imagen independiente del servicio: se construye y despliega por separado.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./servicio_clientes/
EXPOSE 8002
CMD ["uvicorn", "servicio_clientes.main:app", "--host", "0.0.0.0", "--port", "8002"]
