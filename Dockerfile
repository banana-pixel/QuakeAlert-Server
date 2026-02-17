FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all python scripts (bridge.py and server.py)
COPY *.py ./

# Default command (will be overridden in docker-compose)
CMD ["python", "bridge.py"]
