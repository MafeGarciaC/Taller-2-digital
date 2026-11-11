FROM python:3.12-slim
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
# Documentos de ejemplo para el comando de datos de prueba (no son credenciales)
COPY documentos_ejemplo /srv/documentos_ejemplo
# Sin credenciales en la imagen: todas llegan en tiempo de ejecución como secretos de Docker
USER nobody
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
