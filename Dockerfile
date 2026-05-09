FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create static image dir at build time
RUN mkdir -p /app/static/test_images

# Expose port (Render sets $PORT at runtime)
EXPOSE 10000

# Start with 1 worker — critical for key rotation to work correctly
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --timeout 300
