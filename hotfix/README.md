# Hotfix: komunikat „Agent wymaga aktualizacji”

Hotfix dotyczy wyłącznie kontenera centrali (`dockermind-web`). Agentów nie trzeba
przenosić ani przebudowywać. Centrala normalizuje teraz capability `command_protocol`
i `host_commands`, niezależnie od tego, czy agent przekazał je jako liczbę/boolean,
czy tekst.

Uruchom w katalogu repozytorium DockerMind na hoście centrali:

```powershell
powershell -ExecutionPolicy Bypass -File .\hotfix\apply-agent-capabilities-hotfix.ps1
```

Skrypt kopiuje `servers.py`, `websocket_manager.py` i `fleet.js` do kontenera
`dockermind-web`, a następnie restartuje centralę. Jeśli kontener ma inną nazwę:

```powershell
$env:DOCKER_MIND_CONTAINER = 'inna-nazwa-centrali'
powershell -ExecutionPolicy Bypass -File .\hotfix\apply-agent-capabilities-hotfix.ps1
```

## Obraz dla serwera offline

Na komputerze z Dockerem i lokalnym obrazem `dockermind-web:1.5` zbuduj obraz:

```powershell
docker build -f hotfix/Dockerfile -t dockermind-web:1.5-hotfix1 .
docker save -o dockermind-web-1.5-hotfix1.tar dockermind-web:1.5-hotfix1
```

Przenieś plik `.tar` na serwer offline i zaimportuj:

```sh
docker load -i dockermind-web-1.5-hotfix1.tar
```

W katalogu wdrożenia centrali połóż plik `hotfix/docker-compose.override.yml`
(albo skopiuj jego zawartość do własnego override) i uruchom:

```sh
docker compose -f docker-compose.yml -f hotfix/docker-compose.override.yml up -d --no-deps --force-recreate dockermind-web
```

Hotfix zmienia tylko centralę. Agentów nie trzeba podmieniać.
