FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PYTHOPTS=""

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

# Flask app via gunicorn, bind to 8000 (Traefik will route to it)
ENV PORT=8000
EXPOSE 8000

# Increase timeout and optimize for memory usage
CMD ["gunicorn", "-b", "0.0.0.0:8000", "--timeout", "30", "--worker-class", "sync", "--workers", "1", "--max-requests", "100", "--max-requests-jitter", "10", "--preload", "app:app"]


