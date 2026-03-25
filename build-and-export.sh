#!/bin/bash
set -e
echo "=== Building DockerMind offline package ==="

# Build images
docker compose build
docker build -t dockermind-agent:1.0 ./agent

# Pull base images
docker pull nginx:alpine
docker pull python:3.12-slim

# Export images to tar
echo "Exporting images..."
docker save dockermind-web:1.0 -o dockermind-web.tar
docker save dockermind-agent:1.0 -o dockermind-agent.tar
docker save nginx:alpine -o nginx.tar

# Create package structure
mkdir -p offline-package/server2/nginx
mkdir -p offline-package/agent

# Server 2 files
cp dockermind-web.tar offline-package/server2/
cp nginx.tar          offline-package/server2/
cp docker-compose.yml offline-package/server2/
cp .env.example       offline-package/server2/
cp nginx/nginx.conf   offline-package/server2/nginx/

# Agent files
cp dockermind-agent.tar      offline-package/agent/
cp agent/docker-compose.yml  offline-package/agent/
cp agent/.env.example        offline-package/agent/

# install.sh for SERVER 2
cat > offline-package/server2/install.sh << 'EOF'
#!/bin/bash
set -e
echo "=== Installing DockerMind Central ==="
docker load -i dockermind-web.tar
docker load -i nginx.tar
cp .env.example .env
echo ""
echo "WAŻNE: Edytuj plik .env przed uruchomieniem!"
echo "  nano .env"
echo ""
read -p "Naciśnij ENTER po edycji .env..."
docker compose up -d
echo ""
echo "Gotowe! Dashboard: http://$(hostname -I | awk '{print $1}')"
echo "Login: admin / (hasło z .env)"
EOF

# install.sh for AGENT servers
cat > offline-package/agent/install.sh << 'EOF'
#!/bin/bash
set -e
echo "=== Installing DockerMind Agent ==="
docker load -i dockermind-agent.tar
cp .env.example .env
echo ""
echo "Edytuj .env:"
echo "  CENTRAL_HOST=IP_SERWERA_2"
echo "  AGENT_TOKEN=ten-sam-token-co-na-serwerze-2"
echo "  AGENT_NAME=nazwa-tego-serwera"
echo ""
read -p "Naciśnij ENTER po edycji .env..."
docker compose up -d
echo ""
echo "Agent uruchomiony!"
echo "Sprawdź: docker logs dockermind-agent -f"
EOF

chmod +x offline-package/server2/install.sh
chmod +x offline-package/agent/install.sh

# Final archive
tar -czf dockermind-offline.tar.gz offline-package/
echo ""
echo "=== GOTOWE: dockermind-offline.tar.gz ==="
du -sh dockermind-offline.tar.gz
echo ""
echo "SERVER 2: skopiuj offline-package/server2/ → bash install.sh"
echo "AGENTY:   skopiuj offline-package/agent/   → bash install.sh"
