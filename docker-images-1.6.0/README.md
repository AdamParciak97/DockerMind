# DockerMind 1.6.0 — paczka offline

Zawiera obrazy:

- `dockermind-web:1.6.0` — centrala, obrazy Docker i centrum zadań;
- `dockermind-agent:1.6.0` — operacje na obrazach, inspekcja kontenerów i monitoring;
- `dockermind-nginx:1.6.0` — proxy HTTPS.

Na serwerze offline:

```sh
docker load -i dockermind-images-1.6.0.tar
```

Skopiuj `docker-compose.yml`, uzupełnij `.env` i uruchom:

```sh
docker compose up -d --force-recreate
```

Pliki konfiguracyjne w tym katalogu:

- `central.env.example` — zmienne centrali; skopiuj jako `.env` obok `docker-compose.yml`;
- `agent.env.example` — zmienne agenta; skopiuj jako `.env` obok `agent-compose.yml`;
- `agent-compose.yml` — Compose do uruchomienia agenta na monitorowanym hoście.

Nie dodawaj żadnych wcześniejszych mountów hotfixów. Ta paczka zawiera wszystkie
zmiany w obrazach.
