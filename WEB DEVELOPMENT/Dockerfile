# Use official Python image
FROM python:3.10-slim

# Install system dependencies for Selenium
RUN apt-get update && \
    apt-get install -y \
    gcc \
    g++ \
    libgomp1 \
    curl \
    gnupg \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Microsoft Edge
RUN curl -sSL https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    echo "deb [arch=amd64] https://packages.microsoft.com/repos/edge stable main" > /etc/apt/sources.list.d/microsoft-edge.list && \
    apt-get update && \
    apt-get install -y microsoft-edge-stable

# Copy local msedgedriver (from your project directory)
COPY webdriver/msedgedriver /usr/local/bin/msedgedriver
RUN chmod +x /usr/local/bin/msedgedriver

# Add these to your existing Dockerfile
RUN mkdir -p /tmp/edge_cache && \
    chmod -R 777 /tmp && \
    chmod -R 777 /usr/local/bin/msedgedriver
    
# Set working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 5000

# Run the application
CMD ["python", "original_code.py"]