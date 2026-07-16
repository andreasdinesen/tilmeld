FROM python:3.12-slim

WORKDIR /app

# tzdata så tidszonen (TZ) virker — ellers kører containeren i UTC og frister
# ville være forskudt i forhold til dansk tid.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data (SQLite + uploads) gemmes i en volume så det overlever genstart
ENV DATA_DIR=/data \
    PORT=8080 \
    TZ=Europe/Copenhagen
VOLUME ["/data"]
EXPOSE 8080

# Let produktionsserver (waitress). app:app kører db.init_db() + scheduler ved import.
CMD ["waitress-serve", "--listen=0.0.0.0:8080", "app:app"]
