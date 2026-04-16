FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl bzip2 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn[standard]==0.30.1 \
    websockets==12.0 \
    sqlmodel==0.0.19 \
    openai==1.35.3 \
    httpx==0.27.0 \
    python-dotenv==1.0.1 \
    pyjwt==2.8.0 \
    "passlib[bcrypt]==1.7.4" \
    bcrypt==3.2.2 \
    python-multipart==0.0.9 \
    aiosqlite==0.20.0 \
    fpdf2==2.7.9 \
    ldap3==2.9.1 \
    "cryptography>=42.0.0"

WORKDIR /app

# Download frontend assets at build time — baked into image for offline use
RUN mkdir -p static/fonts && \
    curl -sL "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2" \
         -o /tmp/dejavu.tar.bz2 && \
    tar -xjf /tmp/dejavu.tar.bz2 -C /tmp && \
    cp /tmp/dejavu-fonts-ttf-2.37/ttf/DejaVuSans.ttf      static/fonts/DejaVuSans.ttf && \
    cp /tmp/dejavu-fonts-ttf-2.37/ttf/DejaVuSans-Bold.ttf static/fonts/DejaVuSans-Bold.ttf && \
    rm -rf /tmp/dejavu*

RUN mkdir -p static && \
    curl -sL "https://cdn.tailwindcss.com"                                                                  -o static/tailwind.js && \
    curl -sL "https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js"                                           -o static/alpine.min.js && \
    curl -sL "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"                          -o static/chart.min.js && \
    curl -sL "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/highlight.min.js"               -o static/highlight.min.js && \
    curl -sL "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/languages/yaml.min.js"          -o static/highlight-yaml.min.js && \
    curl -sL "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/styles/github-dark.min.css"     -o static/highlight-dark.min.css && \
    curl -sL "https://unpkg.com/xterm@5.3.0/lib/xterm.js"                                                 -o static/xterm.js && \
    curl -sL "https://unpkg.com/xterm@5.3.0/css/xterm.css"                                                -o static/xterm.css && \
    curl -sL "https://unpkg.com/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"                             -o static/xterm-fit.js

COPY . .

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
