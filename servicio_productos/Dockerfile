# Imagen independiente del servicio: se construye y despliega por separado.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./servicio_productos/
EXPOSE 8001
CMD ["uvicorn", "servicio_productos.main:app", "--host", "0.0.0.0", "--port", "8001"]
