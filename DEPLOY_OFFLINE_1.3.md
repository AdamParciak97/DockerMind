# DockerMind 1.3 Offline Deployment

## Cel

Ten dokument opisuje wdrożenie DockerMind 1.3 na nowym serwerze offline z:

- panelem po `https://FQDN:8443`
- agentami po `wss://FQDN:8443/ws/agent`
- własnymi certyfikatami TLS
- własnym adresem AI i własnym FQDN

## Zbudowane obrazy

- `dockermind-web:1.3`
- `dockermind-nginx:1.3`
- `dockermind-agent:1.3`

## Artefakt offline

- `dockermind-offline-1.3.tar.gz`

## Budowa paczki

Na stacji z Dockerem uruchom:

```bash
./build-and-export.sh
```

Powstaną:

- `dockermind-web-1.3.tar`
- `dockermind-nginx-1.3.tar`
- `dockermind-agent-1.3.tar`
- `dockermind-offline-1.3.tar.gz`

## Wdrożenie centrali na nowym serwerze

Skopiuj paczkę i uruchom:

```bash
tar -xzf dockermind-offline-1.3.tar.gz
cd offline-package/central
chmod +x install.sh
./install.sh
```

Skrypt przygotuje:

- `.env`
- `ssl/`
- `certs/`

## Konfiguracja centrali

W `offline-package/central/.env` ustaw:

```env
AI_BASE_URL=https://ai.example.local/llama3/v1
AI_MODEL=llama3
AI_API_TOKEN=<TOKEN>
# jeśli AI używa prywatnego CA:
AI_CA_CERT=/app/certs/ai-ca.pem

CT_USERNAME=admin
CT_PASSWORD=<SILNE_HASLO>
CT_SECRET_KEY=<LOSOWY_KLUCZ>
AGENT_SECRET_TOKEN=<TOKEN_DLA_AGENTOW>
```

## Własne certyfikaty TLS dla DockerMind

Katalog `offline-package/central/ssl/` powinien zawierać:

- `server.crt`
- `server.key`

`server.crt` powinien być full chain:

```text
certyfikat serwera
intermediate CA
```

Przykład:

```bash
cat dockermind-server.crt intermediate-ca.crt > ssl/server.crt
cp dockermind-server.key ssl/server.key
chmod 600 ssl/server.key
```

Uwagi:

- CN lub SAN certyfikatu musi zawierać docelowy FQDN, np. `dockermind.example.local`
- root CA zwykle dystrybuujesz osobno do klientów i agentów

## Prywatne CA dla AI

Jeżeli endpoint AI ma certyfikat podpisany przez prywatny CA, skopiuj bundle do:

```text
offline-package/central/certs/ai-ca.pem
```

Najczęściej ten plik zawiera:

- root CA
- intermediate CA

## Start centrali

Po uzupełnieniu `.env`, `ssl/server.crt`, `ssl/server.key` i ewentualnie `certs/ai-ca.pem`:

```bash
docker compose up -d
docker compose ps
docker logs dockermind-nginx --tail 50
docker logs dockermind-web --tail 50
```

Panel będzie działał pod:

```text
https://dockermind.example.local:8443
```

## Wdrożenie agenta

Na serwerze agenta:

```bash
cd offline-package/agent
chmod +x install.sh
./install.sh
```

W `offline-package/agent/.env` ustaw:

```env
CENTRAL_URL=wss://dockermind.example.local:8443/ws/agent
AGENT_TOKEN=<TEN_SAM_TOKEN_CO_AGENT_SECRET_TOKEN>
AGENT_NAME=serwer-prod-01
# jeśli central używa prywatnego CA:
CENTRAL_CA_CERT=/etc/dockermind/certs/central-ca.pem
```

Jeżeli central używa prywatnego CA, skopiuj bundle do:

```text
offline-package/agent/certs/central-ca.pem
```

Ten plik zwykle zawiera:

- root CA
- intermediate CA

Potem uruchom:

```bash
docker compose up -d
docker logs dockermind-agent --tail 100 -f
```

## Jak zmienić FQDN DockerMind

Zmień:

1. rekord DNS lub `/etc/hosts`
2. `ssl/server.crt` i `ssl/server.key`
3. `CENTRAL_URL` w każdym agencie

Docelowo:

```text
https://NOWY_FQDN:8443
wss://NOWY_FQDN:8443/ws/agent
```

Po zmianie:

```bash
docker compose restart
```

na centrali i na agentach.

## Jak zmienić adres AI

Na centrali edytuj `.env`:

```env
AI_BASE_URL=https://NOWY_AI_FQDN/llama3/v1
AI_MODEL=llama3
AI_API_TOKEN=<NOWY_TOKEN>
```

Jeżeli nowy AI endpoint ma inny prywatny CA:

1. podmień `certs/ai-ca.pem`
2. zostaw `AI_CA_CERT=/app/certs/ai-ca.pem`
3. zrestartuj:

```bash
docker compose restart dockermind-web
```

## Jak podmienić certyfikaty DockerMind

Na centrali:

1. podmień `ssl/server.crt`
2. podmień `ssl/server.key`
3. jeśli zmienił się issuer, podmień też `agent/certs/central-ca.pem` na agentach
4. zrestartuj nginx:

```bash
docker compose restart dockermind-nginx
```

Na agentach, jeśli zmienił się CA:

```bash
docker compose restart dockermind-agent
```

## Port 8443

Aktualne mapowanie portów:

- `80:80` tylko dla opcjonalnego redirectu HTTP -> HTTPS
- `8443:443` jako właściwy dostęp do aplikacji

Jeżeli nie chcesz publikować `80`, usuń z `docker-compose.yml` linię:

```yaml
- "80:80"
```

Wtedy używaj wyłącznie:

```text
https://FQDN:8443
wss://FQDN:8443/ws/agent
```

## Weryfikacja końcowa

Na centrali:

```bash
docker compose ps
docker logs dockermind-nginx --tail 50
docker logs dockermind-web --tail 50
```

Na agencie:

```bash
docker compose ps
docker logs dockermind-agent --tail 100
```

Oczekiwany wynik:

- panel logowania działa po `https://FQDN:8443`
- agent łączy się po `wss`
- analiza AI działa przez nowy `AI_BASE_URL`
