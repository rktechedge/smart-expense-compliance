FROM python:3.11-slim

WORKDIR /app

# Install dependencies first
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source code
COPY main.py .

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
