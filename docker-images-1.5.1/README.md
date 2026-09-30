# DockerMind 1.5.1 — paczka offline

Ta paczka zawiera poprawione obrazy centrali, agenta i nginx:

- `dockermind-web:1.5.1`
- `dockermind-agent:1.5.1`
- `dockermind-nginx:1.5.1`

Na serwerze offline:

```sh
docker load -i dockermind-images-1.5.1.tar
```

Skopiuj `docker-compose.yml` do katalogu wdrożenia, uzupełnij `.env` i uruchom:

```sh
docker compose up -d --force-recreate
```

Usuń wszystkie wcześniejsze mounty hotfixów (`servers.py` i
`websocket_manager.py`). Ta wersja zawiera poprawiony protokół capabilities
agenta i wspólne komendy bez dodatkowych plików bind mount.
