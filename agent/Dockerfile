FROM python:3.12-slim

RUN pip install --no-cache-dir \
    websockets==12.0 \
    docker==7.1.0 \
    python-dotenv==1.0.1 \
    httpx==0.27.0 \
    aiohttp==3.9.5

WORKDIR /app
COPY . .

CMD ["python", "main.py"]
