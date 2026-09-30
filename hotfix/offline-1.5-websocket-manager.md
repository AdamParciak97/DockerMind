# Hotfix dla paczki `docker-images-1.5`

Ten plik pochodzi bezpośrednio z obrazu `dockermind-web:1.5` i jest zgodny z jego
pozostałymi modułami. Nie używaj wcześniejszego hotfixu `servers.py` z tym obrazem.

Skopiuj `websocket_manager.py` do katalogu wdrożenia offline, np.:

```text
<katalog-compose>/hotfix/websocket_manager.py
```

W usłudze `dockermind-web` dopisz do istniejących wolumenów:

```yaml
      - ./hotfix/websocket_manager.py:/app/websocket_manager.py:ro
```

Usuń wcześniejszy mount `servers.py`, jeśli go dodawałeś. Następnie:

```sh
docker compose up -d --force-recreate dockermind-web
```

Hotfix normalizuje capability `host_commands` i `command_protocol` podczas
rejestracji agenta. Nie zmienia obrazu ani kontenera agenta i nie wymaga jego
przenoszenia. Po restarcie centrali odśwież dashboard przez `Ctrl+F5`.
