FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data (SQLite + uploads) gemmes i en volume så det overlever genstart
ENV DATA_DIR=/data \
    PORT=8080
VOLUME ["/data"]
EXPOSE 8080

# Let produktionsserver (waitress). app:app kører db.init_db() + scheduler ved import.
CMD ["waitress-serve", "--listen=0.0.0.0:8080", "app:app"]
