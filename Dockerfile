FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all python scripts (bridge.py and server.py)
COPY *.py .

# Default command (will be overridden in docker-compose)
CMD ["python", "bridge.py"]
