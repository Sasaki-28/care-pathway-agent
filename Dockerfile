FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Cloud Run sends traffic to $PORT (8080 by default).
CMD exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8080}
