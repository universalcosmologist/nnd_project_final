FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends tshark libpcap-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN useradd --create-home appuser && mkdir -p /app/data /app/logs && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000
CMD ["gunicorn", "-k", "eventlet", "--workers", "1", "--bind", "0.0.0.0:5000", "wsgi:app"]
