#!/bin/bash
set -e
echo "=== Building DockerMind offline package 1.3 ==="

docker build -t dockermind-web:1.3 ./central
docker build -t dockermind-nginx:1.3 ./nginx
docker build -t dockermind-agent:1.3 ./agent

echo "Exporting images..."
docker save dockermind-web:1.3 -o dockermind-web-1.3.tar
docker save dockermind-nginx:1.3 -o dockermind-nginx-1.3.tar
docker save dockermind-agent:1.3 -o dockermind-agent-1.3.tar
chmod 600 dockermind-web-1.3.tar dockermind-nginx-1.3.tar dockermind-agent-1.3.tar

if [ -e offline-package ] && [ ! -d offline-package ]; then
    echo "ERROR: offline-package exists but is not a directory. Aborting." >&2
    exit 1
fi

rm -rf offline-package
mkdir -p offline-package/central/nginx
mkdir -p offline-package/central/ssl
mkdir -p offline-package/central/certs
mkdir -p offline-package/agent/certs
mkdir -p offline-package/examples

cp dockermind-web-1.3.tar offline-package/central/
cp dockermind-nginx-1.3.tar offline-package/central/
cp docker-compose.yml offline-package/central/
cp .env.example offline-package/central/
cp nginx/nginx.conf offline-package/central/nginx/

cp dockermind-agent-1.3.tar offline-package/agent/
cp agent/docker-compose.yml offline-package/agent/
cp agent/.env.example offline-package/agent/

cp DEPLOY_OFFLINE_1.3.md offline-package/
cp DEPLOY_fleet-server.test.pl_OFFLINE_1.3.md offline-package/
cp deploy.central.fleet-server.test.pl.env.example offline-package/examples/
cp deploy.agent.fleet-server.test.pl.env.example offline-package/examples/

cat > offline-package/central/install.sh << 'EOF'
#!/bin/bash
set -e
echo "=== Instalacja DockerMind Central (v1.3) ==="

docker load -i dockermind-web-1.3.tar
docker load -i dockermind-nginx-1.3.tar

mkdir -p ssl certs

if [ ! -f .env ]; then
    install -m 600 .env.example .env
    sed -i "s|^CT_SECRET_KEY=.*|CT_SECRET_KEY=$(openssl rand -hex 32)|" .env
    sed -i "s|^AGENT_SECRET_TOKEN=.*|AGENT_SECRET_TOKEN=$(openssl rand -hex 32)|" .env
    echo ""
    echo "WAŻNE: uzupełnij plik .env przed uruchomieniem:"
    echo "  CT_PASSWORD=<silne_haslo>"
    echo "  AI_BASE_URL=https://twoj-ai.example.local/llama3/v1"
    echo "  AI_API_TOKEN=<token>"
    echo ""
    read -p "Naciśnij ENTER po edycji .env..."
fi

docker compose up -d
echo ""
echo "DockerMind Central uruchomiony."
echo "Panel:  https://<FQDN_LUB_IP>:8443"
echo "Agenci: wss://<FQDN_LUB_IP>:8443/ws/agent"
EOF

cat > offline-package/agent/install.sh << 'EOF'
#!/bin/bash
set -e
echo "=== Instalacja DockerMind Agent (v1.3) ==="

docker load -i dockermind-agent-1.3.tar

mkdir -p certs

if [ ! -f .env ]; then
    install -m 600 .env.example .env
    echo ""
    echo "Uzupełnij .env:"
    echo "  CENTRAL_URL=wss://FQDN_CENTRALI:8443/ws/agent"
    echo "  AGENT_TOKEN=<ten_sam_token_co_AGENT_SECRET_TOKEN_na_centrali>"
    echo "  AGENT_NAME=<nazwa_serwera>"
    echo ""
    read -p "Naciśnij ENTER po edycji .env..."
fi

docker compose up -d
echo ""
echo "Agent uruchomiony."
echo "Logi: docker logs dockermind-agent -f"
EOF

chmod +x offline-package/central/install.sh
chmod +x offline-package/agent/install.sh

tar -czf dockermind-offline-1.3.tar.gz offline-package/
echo ""
echo "=== GOTOWE: dockermind-offline-1.3.tar.gz ==="
du -sh dockermind-offline-1.3.tar.gz
