# DockerMind 1.3 — wdrożenie dostępu do hosta

Ten katalog zawiera obrazy i pliki potrzebne do wdrożenia zmian na serwerze
centralnym oraz na każdym serwerze z agentem.

## 1. Serwer centralny

Przenieś katalog `central/` na serwer z DockerMind. W katalogu, w którym działa
obecny plik `docker-compose.yml`, wykonaj:

```bash
docker load -i dockermind-web-1.3-host-access.tar
docker compose up -d --no-deps dockermind-web
```

Zachowaj istniejący plik `.env` centrali — nie jest wymagane dodanie żadnej nowej
zmiennej.

## 2. Każdy serwer agenta

Przenieś zawartość katalogu `agent/` do katalogu agenta na danym serwerze,
zastępując jego `docker-compose.yml`. Uzupełnij istniejący `.env` zgodnie z
`agent.env.example`, przede wszystkim:

```env
HOST_ACCESS_ENABLED=true
```

Następnie wykonaj:

```bash
docker load -i dockermind-agent-1.3-host-access.tar
docker compose up -d --force-recreate
```

W panelu zaloguj się jako administrator, wybierz serwer i użyj **Terminal hosta**.
W trybie wyboru możesz zaznaczyć wiele serwerów; akcje Start/Stop/Restart obejmą
wszystkie ich kontenery oraz kontenery zaznaczone pojedynczo.

> Terminal hosta zapewnia administratorowi powłokę hosta. Włączaj go wyłącznie na
> serwerach, którym ufasz.
