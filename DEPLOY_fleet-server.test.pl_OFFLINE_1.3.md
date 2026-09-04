# DockerMind 1.3 Offline Deployment for fleet-server.test.pl

## Założenia

Ta wersja jest przygotowana pod:

- DockerMind FQDN: `fleet-server.test.pl`
- DockerMind URL: `https://fleet-server.test.pl:8443`
- Agent URL: `wss://fleet-server.test.pl:8443/ws/agent`
- AI endpoint: `https://asystent-ai.test.pl/llama3/v1`
- port `80` zostaje włączony
- DockerMind i AI używają różnych CA

## Gotowe pliki

- central env: [deploy.central.fleet-server.test.pl.env.example](/D:/cursor/kursor-projekty/docker-mind/dockermind/deploy.central.fleet-server.test.pl.env.example)
- agent env: [deploy.agent.fleet-server.test.pl.env.example](/D:/cursor/kursor-projekty/docker-mind/dockermind/deploy.agent.fleet-server.test.pl.env.example)
- ogólna instrukcja: [DEPLOY_OFFLINE_1.3.md](/D:/cursor/kursor-projekty/docker-mind/dockermind/DEPLOY_OFFLINE_1.3.md)

## 1. Certyfikaty DockerMind

Masz:

- `server.crt`
- `server.key`
- `intermediate.crt`
- `root.crt`

Na serwerze centrali w katalogu `offline-package/central/ssl/` przygotuj:

```bash
cat server.crt intermediate.crt > server.crt.bundle
mv server.crt.bundle server.crt
cp server.key server.key
```

Finalnie w `offline-package/central/ssl/` mają być:

- `server.crt`
  zawartość: certyfikat serwera + intermediate
- `server.key`

## 2. Bundle CA dla agentów

Ponieważ agent łączy się po `wss://fleet-server.test.pl:8443/ws/agent`, w katalogu:

```text
offline-package/agent/certs/
```

utwórz:

```bash
cat root.crt intermediate.crt > central-ca.pem
```

Finalny plik:

- `offline-package/agent/certs/central-ca.pem`

Ten plik jest używany przez:

```env
CENTRAL_CA_CERT=/etc/dockermind/certs/central-ca.pem
```

## 3. Certyfikat AI

Powiedziałeś, że dla AI masz:

- `full-chain.crt`

W tej konfiguracji użyj dokładnie tego pliku jako bundle zaufania dla centrali i skopiuj go do:

```text
offline-package/central/certs/ai-full-chain.crt
```

W `.env` centrali ustaw:

```env
AI_CA_CERT=/app/certs/ai-full-chain.crt
```

Uwaga:

- to jest pragmatyczna konfiguracja pod plik, który masz teraz
- jeżeli handshake TLS do AI nie przejdzie, poproś administratora AI o właściwy bundle CA tego serwera
  najlepiej `root + intermediate`

## 4. Gotowy `.env` centrali

Skopiuj:

```bash
cp deploy.central.fleet-server.test.pl.env.example offline-package/central/.env
```

Potem uzupełnij tylko:

- `AI_API_TOKEN`
- `CT_PASSWORD`
- `CT_SECRET_KEY`
- `AGENT_SECRET_TOKEN`

Docelowa zawartość:

```env
AI_BASE_URL=https://asystent-ai.test.pl/llama3/v1
AI_MODEL=llama3
AI_API_TOKEN=<TOKEN_AI>
AI_CA_CERT=/app/certs/ai-full-chain.crt

CT_USERNAME=admin
CT_PASSWORD=<SILNE_HASLO_ADMINA>
CT_SECRET_KEY=<WYGNERUJ_OPENSSL_RAND_HEX_32>
AGENT_SECRET_TOKEN=<TOKEN_DLA_AGENTOW>
```

Do wygenerowania sekretów:

```bash
openssl rand -hex 32
```

## 5. Gotowy `.env` agenta

Skopiuj:

```bash
cp deploy.agent.fleet-server.test.pl.env.example offline-package/agent/.env
```

Potem uzupełnij:

- `AGENT_TOKEN`
- `AGENT_NAME`
- opcjonalnie `AGENT_IP`

Docelowa zawartość:

```env
CENTRAL_URL=wss://fleet-server.test.pl:8443/ws/agent
AGENT_TOKEN=<TEN_SAM_TOKEN_CO_AGENT_SECRET_TOKEN>
AGENT_NAME=<NAZWA_SERWERA>
CENTRAL_CA_CERT=/etc/dockermind/certs/central-ca.pem
```

## 6. Wdrożenie centrali

Na nowym serwerze:

```bash
tar -xzf dockermind-offline-1.3.tar.gz
cd offline-package/central
docker load -i dockermind-web-1.3.tar
docker load -i dockermind-nginx-1.3.tar
docker compose up -d
```

Weryfikacja:

```bash
docker compose ps
docker logs dockermind-nginx --tail 50
docker logs dockermind-web --tail 50
```

Panel:

```text
https://fleet-server.test.pl:8443
```

## 7. Wdrożenie agenta

Na każdym serwerze z agentem:

```bash
cd offline-package/agent
docker load -i dockermind-agent-1.3.tar
docker compose up -d
docker logs dockermind-agent --tail 100 -f
```

Agent powinien łączyć się do:

```text
wss://fleet-server.test.pl:8443/ws/agent
```

## 8. Jak podmienić certyfikaty później

DockerMind:

1. podmień `offline-package/central/ssl/server.crt`
2. podmień `offline-package/central/ssl/server.key`
3. jeśli zmieni się CA, przebuduj też:

```bash
cat root.crt intermediate.crt > offline-package/agent/certs/central-ca.pem
```

4. zrestartuj:

```bash
docker compose restart dockermind-nginx
```

AI:

1. podmień `offline-package/central/certs/ai-full-chain.crt`
2. zrestartuj:

```bash
docker compose restart dockermind-web
```

## 9. Porty

W tej wersji:

- `80:80` zostaje
- `8443:443` obsługuje właściwy panel i `wss`

Czyli używasz:

- `https://fleet-server.test.pl:8443`
- `wss://fleet-server.test.pl:8443/ws/agent`
