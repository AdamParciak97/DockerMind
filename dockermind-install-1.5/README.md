# DockerMind 1.5 — pakiet instalacyjny

Ten katalog zawiera gotowy pakiet do uruchomienia DockerMind na nowym serwerze:

- `dockermind-images-1.5.tar` — obrazy web, agent i nginx,
- `docker-compose.yml`,
- `.env.example`,
- katalogi `certs`, `ssl` i `hotfix` wymagane przez Compose.

## Wymagania

- Docker Engine 24+ oraz Docker Compose v2,
- Linux z dostępem do `/var/run/docker.sock`, jeśli ma działać lokalny agent,
- port `80` lub wybrany port HTTP oraz `8443` dla HTTPS.

## Instalacja

1. Skopiuj cały katalog na serwer, np. do `/opt/dockermind`.
2. Przejdź do katalogu:

   ```bash
   cd /opt/dockermind
   ```

3. Załaduj obrazy:

   ```bash
   docker load -i dockermind-images-1.5.tar
   ```

4. Utwórz konfigurację i ustaw własne sekrety:

   ```bash
   cp .env.example .env
   nano .env
   ```

   Uzupełnij przede wszystkim `AI_API_TOKEN`, `CT_SECRET_KEY` i `AGENT_SECRET_TOKEN`.
   Sekrety powinny być długimi, losowymi wartościami.

5. Uruchom centralę i nginx:

   ```bash
   docker compose up -d
   ```

6. Jeśli ten sam serwer ma być monitorowany lokalnym agentem, uruchom także profil agenta:

   ```bash
   docker compose --profile local-agent up -d dockermind-agent
   ```

7. Sprawdź stan:

   ```bash
   docker compose ps
   ```

Aplikacja będzie dostępna pod `https://ADRES_SERWERA:8443`. Przy lokalnym uruchomieniu użyj `https://localhost:8443`.

## Logowanie

Domyślne dane z `.env.example`:

- login: `admin`
- hasło: `Inventory-Local-2026!`

Po pierwszym uruchomieniu zmień hasło w Ustawieniach.

## Aktualizacja pakietu

Załaduj nowe archiwum obrazów, a następnie odtwórz kontenery:

```bash
docker load -i dockermind-images-NEW.tar
docker compose up -d --force-recreate
```

Dane aplikacji są przechowywane w wolumenie `dockermind_data`, więc odtworzenie kontenerów nie usuwa bazy danych.

## Zatrzymanie i diagnostyka

```bash
docker compose down
docker compose logs --tail=100 dockermind-web
docker compose logs --tail=100 nginx
```
