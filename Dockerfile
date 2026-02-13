FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install FastAPI and uvicorn for dashboard
RUN pip install --no-cache-dir fastapi uvicorn jinja2

# Copy app code
COPY src ./src
COPY dashboard.py .
COPY templates ./templates

# Create static directory (required by FastAPI)
RUN mkdir -p static

# Expose port
EXPOSE 8000

# Run dashboard
CMD ["python", "dashboard.py"]
