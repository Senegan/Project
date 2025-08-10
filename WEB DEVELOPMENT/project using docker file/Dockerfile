# Use official Python image
FROM python:3.10-slim

# Install system dependencies for Selenium, Chrome & Edge
RUN apt-get update && \
    apt-get install -y \
    gcc \
    g++ \
    libgomp1 \
    curl \
    gnupg \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# ------------------------
# Install Microsoft Edge
# ------------------------
RUN curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > microsoft.gpg && \
    install -o root -g root -m 644 microsoft.gpg /usr/share/keyrings/ && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/edge stable main" > /etc/apt/sources.list.d/microsoft-edge.list && \
    apt-get update && apt-get install -y microsoft-edge-stable && \
    rm microsoft.gpg



# ------------------------
# Copy drivers
# ------------------------
COPY webdriver/msedgedriver /app/webdriver/msedgedriver
RUN chmod +x /app/webdriver/msedgedriver

# Create Edge cache folder (important for headless mode)
RUN mkdir -p /tmp/edge_cache && chmod -R 777 /tmp /app/webdriver

# ------------------------
# Set working directory
# ------------------------
WORKDIR /app

# Copy requirements first (for build cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose Flask port
EXPOSE 5000

# Start the application
CMD ["python", "main.py"]
