FROM python:3.11-slim

WORKDIR /app

# Minimal system deps. asyncpg has pre-built wheels for Python 3.11 slim,
# so no compiler toolchain is needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy app source.
COPY . .

# Railway injects PORT; we read it in config.effective_port.
EXPOSE 8000

CMD ["python", "-m", "main"]
