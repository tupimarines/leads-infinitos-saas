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

# Increase timeout to allow long-running scrapes in MVP1
CMD ["gunicorn", "-b", "0.0.0.0:8000", "--timeout", "600", "app:app"]


