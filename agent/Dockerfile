FROM python:3.12-slim

# Install Docker CLI static binary (baked in for offline use)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz" \
       -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && chmod +x /usr/local/bin/docker \
    && rm -rf /tmp/docker*

RUN pip install --no-cache-dir \
    websockets==12.0 \
    docker==7.1.0 \
    python-dotenv==1.0.1 \
    httpx==0.27.0 \
    aiohttp==3.9.5

WORKDIR /app
COPY . .

CMD ["python", "main.py"]
