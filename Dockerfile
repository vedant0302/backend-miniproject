FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy app files
COPY server.py .

# Playwright browsers are pre-installed in the base image
# Just install the chromium binaries
RUN playwright install chromium

EXPOSE 8080
CMD ["gunicorn", "server:app", "--bind", "0.0.0.0:8080"]
