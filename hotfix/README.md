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

Zmiana w kontenerze jest tymczasowa. Przy następnym wdrożeniu użyj obrazu
zaktualizowanego z gałęzi `main` (`b8d403c` lub nowszej).
