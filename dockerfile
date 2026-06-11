# Use slim Python 3.11 base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependency needed by pdfplumber
RUN apt-get update && apt-get install -y \
    libpoppler-cpp-dev \
    poppler-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY server.py .
COPY config.py .
COPY pdf_processor/ ./pdf_processor/
COPY static/ ./static/

# Create uploads directory
RUN mkdir -p uploads

# Create non-root user for security
RUN adduser --disabled-password --no-create-home appuser
USER appuser

# Cloud Run injects PORT env var — default to 8080
ENV PORT=8080

# Start the app
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1