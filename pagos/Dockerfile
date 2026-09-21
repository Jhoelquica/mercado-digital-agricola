FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# No corre como root: si alguien lograra ejecutar código dentro del contenedor, no tendría permisos de administrador.
RUN useradd --system --uid 10001 --no-create-home app
USER 10001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8007"]