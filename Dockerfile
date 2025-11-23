# Use Alpine Linux as base (much smaller than slim)
FROM python:3.11-alpine

# Set working directory
WORKDIR /app

# Environment variables to keep Python clean and fast
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install dependencies
RUN pip install --no-cache-dir docker flask gunicorn

# Copy application files
COPY app.py .
COPY templates ./templates

# Expose the web port
EXPOSE 5000

# --- HEALTHCHECK ---
# Fix: Use 127.0.0.1 instead of localhost
# -q: Quiet (no output)
# --spider: Check existence only
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:5000 || exit 1

# Start the application using Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "app:app"]