#!/bin/sh
# Generates a self-signed TLS certificate if none is present.
# To use your own certificate, mount it at /etc/nginx/ssl/server.crt + server.key
# before starting the container — this script will skip generation.

CERT_DIR=/etc/nginx/ssl
CERT="$CERT_DIR/server.crt"
KEY="$CERT_DIR/server.key"

if [ -f "$CERT" ] && [ -f "$KEY" ]; then
    echo "[nginx] SSL certificate already exists — skipping generation."
    exit 0
fi

echo "[nginx] Generating self-signed SSL certificate (valid 1 year)..."
mkdir -p "$CERT_DIR"

if ! openssl req -x509 -nodes -newkey rsa:4096 -days 365 \
    -keyout "$KEY" \
    -out "$CERT" \
    -subj "/CN=dockermind/O=DockerMind/C=PL" \
    -addext "subjectAltName=DNS:dockermind,DNS:localhost,IP:127.0.0.1"; then
    echo "[nginx] ERROR: SSL certificate generation failed." >&2
    exit 1
fi

chmod 600 "$KEY"
echo "[nginx] Certificate generated: $CERT"
