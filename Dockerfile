FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set up environment for persistent storage
ENV DATA_DIR="/data"
ENV PORT=8080

# Create entrypoint script
RUN echo '#!/bin/sh\n\
# Ensure persistent directories exist\n\
mkdir -p /data/test_images\n\
\n\
# Symlink static/test_images to the persistent volume so Flask serves them correctly\n\
rm -rf /app/static/test_images\n\
ln -s /data/test_images /app/static/test_images\n\
\n\
# Start the app\n\
exec gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 300\n\
' > /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
