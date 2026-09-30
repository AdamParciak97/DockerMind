# Hotfix jako bind mount

Skopiuj `hotfix/servers.py` na hosta centrali, zachowując strukturę:

```text
<katalog-wdrożenia>/hotfix/servers.py
```

W `docker-compose.yml`, w usłudze `dockermind-web`, dopisz do istniejącej sekcji
`volumes` jedną linię:

```yaml
      - ./hotfix/servers.py:/app/routers/servers.py:ro
```

Nie usuwaj pozostałych wolumenów. Następnie przeładuj tylko centralę:

```sh
docker compose up -d --force-recreate dockermind-web
```

Agentów i obrazów nie trzeba przenosić. Po restarcie odśwież stronę przez `Ctrl+F5`.

Plik jest kompatybilny z obecną wersją obrazu centrali 1.5 i nie wymaga aktualizacji
`websocket_manager.py`.
