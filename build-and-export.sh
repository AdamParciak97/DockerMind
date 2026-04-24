#!/bin/bash
set -e
echo "=== Building DockerMind offline package ==="

# Build images (explicit — docker-compose.yml nie ma build: żeby działać offline)
docker build -t dockermind-web:1.2   ./central
docker build -t dockermind-nginx:1.2 ./nginx
docker build -t dockermind-agent:1.2 ./agent

# Pull base image for python
docker pull python:3.12-slim

# Export images to tar
echo "Exporting images..."
docker save dockermind-web:1.2   -o dockermind-web.tar
docker save dockermind-agent:1.2 -o dockermind-agent.tar
docker save dockermind-nginx:1.2 -o nginx.tar
chmod 600 dockermind-web.tar dockermind-agent.tar nginx.tar

# Create package structure
if [ -e offline-package ] && [ ! -d offline-package ]; then
    echo "ERROR: offline-package exists but is not a directory. Aborting." >&2
    exit 1
fi
rm -rf offline-package
mkdir -p offline-package/central/nginx
mkdir -p offline-package/agent

# Central files
cp dockermind-web.tar  offline-package/central/
cp nginx.tar           offline-package/central/
cp docker-compose.yml  offline-package/central/
cp .env.example        offline-package/central/
cp nginx/nginx.conf    offline-package/central/nginx/

# Agent files
cp dockermind-agent.tar     offline-package/agent/
cp agent/docker-compose.yml offline-package/agent/
cp agent/.env.example       offline-package/agent/

# install.sh for Central
cat > offline-package/central/install.sh << 'EOF'
#!/bin/bash
set -e
echo "=== Instalacja DockerMind Central (v1.2) ==="

docker load -i dockermind-web.tar
docker load -i nginx.tar

if [ ! -f .env ]; then
    install -m 600 .env.example .env
    # Pre-fill random secrets so they are not displayed in the terminal
    sed -i "s|^CT_SECRET_KEY=.*|CT_SECRET_KEY=$(openssl rand -hex 32)|" .env
    sed -i "s|^AGENT_SECRET_TOKEN=.*|AGENT_SECRET_TOKEN=$(openssl rand -hex 32)|" .env
    echo ""
    echo "WAŻNE: Uzupełnij plik .env przed uruchomieniem:"
    echo "  CT_PASSWORD=silne-haslo   ← zmień obowiązkowo"
    echo "  CT_SECRET_KEY i AGENT_SECRET_TOKEN zostały wygenerowane automatycznie."
    echo ""
    read -p "Naciśnij ENTER po edycji .env..."
fi

docker compose up -d
echo ""
IP=$(hostname -I | awk '{print $1}')
echo "Gotowe!"
echo "  Dashboard: https://$IP  (certyfikat self-signed — zaakceptuj w przeglądarce)"
echo "  Login:     admin / (CT_PASSWORD z .env)"
echo ""
echo "Agenty łączą się przez: ws://$IP/ws/agent (HTTP, port 80)"
EOF

# install.sh for Agent
cat > offline-package/agent/install.sh << 'EOF'
#!/bin/bash
set -e
echo "=== Instalacja DockerMind Agent (v1.2) ==="

docker load -i dockermind-agent.tar

if [ ! -f .env ]; then
    install -m 600 .env.example .env
    echo ""
    echo "Uzupełnij .env:"
    echo "  CENTRAL_HOST=IP_serwera_central"
    echo "  AGENT_TOKEN=ten_sam_token_co_AGENT_SECRET_TOKEN_na_centrali"
    echo "  AGENT_NAME=nazwa-tego-serwera"
    echo ""
    read -p "Naciśnij ENTER po edycji .env..."
fi

docker compose up -d
echo ""
echo "Agent uruchomiony. Sprawdź logi:"
echo "  docker logs dockermind-agent -f"
EOF

chmod +x offline-package/central/install.sh
chmod +x offline-package/agent/install.sh

# Final archive
tar -czf dockermind-offline.tar.gz offline-package/
echo ""
echo "=== GOTOWE: dockermind-offline.tar.gz ==="
du -sh dockermind-offline.tar.gz
echo ""
echo "Central: skopiuj offline-package/central/ → bash install.sh"
echo "Agenty:  skopiuj offline-package/agent/   → bash install.sh"
