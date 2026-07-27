FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src
COPY alembic/ ./alembic
COPY alembic.ini .

ENV PYTHONPATH=/app/src
EXPOSE 8377

CMD ["python", "-m", "bayan.api"]
