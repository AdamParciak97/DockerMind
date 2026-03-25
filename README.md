# 🐳 DockerMind

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![WebSocket](https://img.shields.io/badge/WebSocket-real--time-FF6B35)
![Offline AI](https://img.shields.io/badge/AI-Offline%20%7C%20llama3-8B4FFF)
![License](https://img.shields.io/badge/license-MIT-green)

**AI-powered Docker monitoring platform — fully offline, 3-server architecture.**

Monitors all Docker containers across your infrastructure. One click triggers a
streaming AI analysis (llama3) that diagnoses problems, assesses risk, and provides
copy-paste fix commands — entirely within your local network, no internet required.

---

## Architektura / Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SIEĆ LOKALNA / LAN                               │
│                                                                         │
│  ┌──────────────────────┐         ┌──────────────────────────────────┐  │
│  │   SERVER 1 — AI      │         │   SERVER 2 — CENTRAL / WEB       │  │
│  │  192.168.10.57       │◄────────│   (twój serwer)                  │  │
│  │  ai.mgmt.pl          │  HTTPS  │                                  │  │
│  │                      │  SSL    │  ┌─────────────────────────────┐ │  │
│  │  nginx (SSL)         │ verify  │  │  nginx:80                   │ │  │
│  │  ┌───────────────┐   │  =False │  │  ┌─────────────────────┐   │ │  │
│  │  │/llama3/v1 ●   │   │         │  │  │  /        → :8080   │   │ │  │
│  │  │/qwen/v1       │   │         │  │  │  /ws/     → :8080   │   │ │  │
│  │  │/vision/v1     │   │         │  │  └─────────────────────┘   │ │  │
│  │  └───────────────┘   │         │  └────────────┬────────────────┘ │  │
│  │                      │         │               │                  │  │
│  │  (istniejący,        │         │  ┌────────────▼────────────────┐ │  │
│  │   nie modyfikować)   │         │  │  dockermind-web:8080        │ │  │
│  └──────────────────────┘         │  │  FastAPI + SQLite           │ │  │
│                                   │  │  WebSocket hub              │ │  │
│                                   │  └─────────────────────────────┘ │  │
│                                   └──────────────────────────────────┘  │
│                                              ▲  ▲                        │
│                                     WebSocket│  │WebSocket               │
│                                              │  │                        │
│  ┌──────────────────────┐    ┌───────────────┴──┴──────────────────┐    │
│  │   SERVER 3 — AGENT   │    │   SERVER 4 — AGENT (kolejny)        │    │
│  │   (monitorowany)     │    │   (monitorowany)                    │    │
│  │                      │    │                                     │    │
│  │  dockermind-agent    │    │   dockermind-agent                  │    │
│  │  ├── Docker SDK      │    │   ├── Docker SDK                    │    │
│  │  ├── collector.py    │    │   ├── collector.py                  │    │
│  │  └── /var/run/       │    │   └── /var/run/docker.sock          │    │
│  │       docker.sock    │    │                                     │    │
│  └──────────────────────┘    └─────────────────────────────────────┘    │
│                                                                         │
│       Browser ──► http://SERVER_2_IP  ──► Dashboard                    │
└─────────────────────────────────────────────────────────────────────────┘

Przepływ danych / Data flow:
  Agent  ──WS──►  nginx:80/ws/  ──►  dockermind-web:8080
  Browser ──HTTP►  nginx:80/     ──►  dockermind-web:8080
  dockermind-web ──HTTPS(verify=False)──►  ai.mgmt.pl/llama3/v1
```

---

## Struktura projektu / Project structure

```
dockermind/
├── agent/
│   ├── main.py              # WebSocket client + reconnect loop
│   ├── collector.py         # Docker SDK data collection
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── docker-compose.yml
│   └── .env.example
├── central/
│   ├── main.py              # FastAPI app entry point + WS endpoints
│   ├── config.py            # Settings from .env
│   ├── models.py            # SQLModel DB models + query helpers
│   ├── auth.py              # JWT login + agent token verification
│   ├── websocket_manager.py # Agent tracking + dashboard broadcasting
│   ├── routers/
│   │   ├── auth.py          # POST /api/auth/login
│   │   ├── servers.py       # GET /api/servers, /containers, /logs, /compose
│   │   └── analysis.py      # POST /api/analyze, GET/DELETE /api/analyses
│   ├── ai/
│   │   └── analyzer.py      # OpenAI client (verify=False) + streaming
│   ├── Dockerfile
│   ├── requirements.txt
│   └── static/
│       └── index.html       # Single-file SPA (Alpine.js + Tailwind + Chart.js)
├── nginx/
│   └── nginx.conf           # Reverse proxy + WebSocket upgrade
├── docker-compose.yml       # SERVER 2: web + nginx
├── .env.example             # SERVER 2 environment template
├── build-and-export.sh      # Offline package builder
└── README.md
```

---

## Szybki start / Quick start

### SERVER 2 — Instalacja centrali (PL)

> Wymagania: Docker + Docker Compose, port 80 wolny.

```bash
# 1. Skopiuj pliki na serwer
scp -r dockermind/ user@192.168.1.100:~/

# 2. Przejdź do katalogu
cd ~/dockermind

# 3. Utwórz plik .env na podstawie szablonu
cp .env.example .env
nano .env          # ← ustaw hasło, klucze JWT i token agentów

# 4. Uruchom
docker compose up -d

# 5. Sprawdź logi
docker compose logs -f

# Dashboard dostępny pod: http://192.168.1.100
# Login: admin / (hasło z .env)
```

---

### SERVER 3+ — Instalacja agenta (PL)

> Wymagania: Docker + Docker Compose, dostęp do SERVER 2 port 80.

```bash
# 1. Skopiuj katalog agent/ na monitorowany serwer
scp -r dockermind/agent/ user@192.168.1.200:~/dockermind-agent/

# 2. Utwórz .env
cd ~/dockermind-agent
cp .env.example .env
nano .env
# Ustaw:
#   CENTRAL_HOST=192.168.1.100
#   AGENT_TOKEN=ten-sam-token-co-AGENT_SECRET_TOKEN-na-serwerze-2
#   AGENT_NAME=nazwa-serwera-produkcyjnego

# 3. Uruchom
docker compose up -d

# 4. Sprawdź połączenie
docker logs dockermind-agent -f
# Powinno pojawić się: "Registered as 'nazwa-serwera' (IP)"
```

---

### Quick start — SERVER 2 (EN)

```bash
cp .env.example .env && nano .env   # set passwords and tokens
docker compose up -d
# Dashboard: http://<SERVER2_IP>  |  Login: admin / <your password>
```

### Quick start — AGENT (EN)

```bash
cp .env.example .env && nano .env   # set CENTRAL_HOST, AGENT_TOKEN, AGENT_NAME
docker compose up -d
docker logs dockermind-agent -f     # verify connection
```

---

## Konfiguracja .env / Environment variables

### SERVER 2 — `dockermind/.env`

| Zmienna | Domyślna | Opis |
|---------|----------|------|
| `CT_USERNAME` | `admin` | Login do dashboardu |
| `CT_PASSWORD` | *(brak)* | **Wymagane.** Hasło do dashboardu |
| `CT_SECRET_KEY` | *(brak)* | **Wymagane.** Klucz JWT (min. 32 znaki, losowy) |
| `AGENT_SECRET_TOKEN` | *(brak)* | **Wymagane.** Token agentów (min. 32 znaki) |
| `AI_BASE_URL` | `https://ai.mgmt.pl/llama3/v1` | URL modelu AI |
| `AI_MODEL` | `llama3` | Nazwa modelu |
| `CT_PORT` | `8080` | Port wewnętrzny FastAPI (nie zmieniaj) |
| `DB_PATH` | `/app/data/dockermind.db` | Ścieżka do bazy SQLite |
| `JWT_EXPIRE_MINUTES` | `480` | Czas ważności tokenu JWT (minuty) |

### SERVER 3+ — `agent/.env`

| Zmienna | Przykład | Opis |
|---------|----------|------|
| `CENTRAL_HOST` | `192.168.1.100` | IP lub hostname serwera centralnego |
| `AGENT_TOKEN` | *(token)* | **Musi być identyczny** z `AGENT_SECRET_TOKEN` na serwerze 2 |
| `AGENT_NAME` | `serwer-prod-01` | Wyświetlana nazwa serwera w dashboardzie |

> **Generowanie bezpiecznych tokenów:**
> ```bash
> openssl rand -hex 32
> ```

---

## Jak dodać nowy monitorowany serwer / How to add a new server

1. Skopiuj katalog `agent/` na nowy serwer.
2. Utwórz `.env` z:
   - `CENTRAL_HOST` = IP serwera centralnego
   - `AGENT_TOKEN` = **ten sam token** co `AGENT_SECRET_TOKEN` w `.env` serwera 2
   - `AGENT_NAME` = unikalną nazwą (np. `baza-danych-01`)
3. `docker compose up -d`
4. Serwer pojawi się w sidebarze dashboardu w ciągu 30 sekund.

---

## Wdrożenie offline / Offline deployment

### Krok 1 — Budowanie paczki (na maszynie z internetem)

```bash
cd dockermind/
chmod +x build-and-export.sh
./build-and-export.sh
# Wyjście: dockermind-offline.tar.gz (~600 MB)
```

Skrypt wykonuje:
- `docker compose build` — buduje `dockermind-web:1.0` (pobiera frontend assets)
- `docker build` — buduje `dockermind-agent:1.0`
- `docker save` — eksportuje obrazy do plików `.tar`
- Tworzy `offline-package/` z gotowymi skryptami instalacyjnymi
- Pakuje wszystko do `dockermind-offline.tar.gz`

### Krok 2 — Transfer na docelowe serwery

```bash
# Na pendrive / przez scp:
scp dockermind-offline.tar.gz user@192.168.1.100:~/
```

### Krok 3 — Instalacja SERVER 2 (centrala)

```bash
tar -xzf dockermind-offline.tar.gz
cd offline-package/server2/
bash install.sh
# Skrypt: docker load → edycja .env → docker compose up -d
```

### Krok 4 — Instalacja agenta (każdy monitorowany serwer)

```bash
tar -xzf dockermind-offline.tar.gz
cd offline-package/agent/
bash install.sh
# Skrypt: docker load → edycja .env → docker compose up -d
```

---

## REST API

| Metoda | Endpoint | Opis |
|--------|----------|------|
| `POST` | `/api/auth/login` | Logowanie, zwraca JWT |
| `GET`  | `/api/health` | Status (publiczny) |
| `GET`  | `/api/servers` | Lista serwerów z licznikami kontenerów |
| `GET`  | `/api/servers/{id}` | Szczegóły serwera + kontenery |
| `GET`  | `/api/servers/{id}/containers` | Lista kontenerów (bez logów) |
| `GET`  | `/api/servers/{id}/containers/{name}/logs?lines=200` | Pobierz logi |
| `GET`  | `/api/servers/{id}/containers/{name}/compose` | Pobierz docker-compose.yml |
| `GET`  | `/api/servers/{id}/containers/{name}/history?days=7` | Dane do wykresów |
| `POST` | `/api/analyze` | Uruchom analizę AI (streaming przez WS) |
| `GET`  | `/api/analyses` | Lista zapisanych analiz |
| `GET`  | `/api/analyses/{id}` | Szczegóły analizy |
| `DELETE` | `/api/analyses/{id}` | Usuń analizę |

### WebSocket endpoints

| Endpoint | Opis |
|----------|------|
| `ws://HOST/ws/agent` | Połączenie agenta (header: `X-Agent-Token`) |
| `ws://HOST/ws/dashboard?token=JWT` | Połączenie dashboardu |

---

## Rozwiązywanie problemów / Troubleshooting

### Błąd SSL: `certificate verify failed` (SERVER 2 → AI)

**Objaw:** `httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]`

**Rozwiązanie:** Sprawdź, czy `extra_hosts` jest ustawione w `docker-compose.yml`:
```yaml
extra_hosts:
  - "ai.mgmt.pl:192.168.10.57"
```
Klient AI używa `verify=False` — upewnij się, że `AI_BASE_URL` zaczyna się od `https://`.

---

### Agent nie pojawia się w dashboardzie

**Sprawdź kolejno:**

```bash
# 1. Czy agent jest uruchomiony?
docker ps | grep dockermind-agent

# 2. Logi agenta
docker logs dockermind-agent -f

# 3. Czy serwer centralny jest osiągalny?
curl http://$CENTRAL_HOST/api/health

# 4. Czy tokeny są identyczne?
# agent/.env:       AGENT_TOKEN=abc123...
# server2/.env: AGENT_SECRET_TOKEN=abc123...  ← muszą być TAKIE SAME
```

---

### WebSocket rozłącza się co kilka minut

**Objaw:** Dashboard pokazuje "Łączenie..." co jakiś czas.

**Przyczyna:** Timeout nginx lub firewall dla idle połączeń.

**Rozwiązanie:** Sprawdź `nginx.conf` — timeouty powinny wynosić:
```nginx
proxy_read_timeout 86400s;
proxy_send_timeout 86400s;
```
Dla firewalli: upewnij się, że przepuszczają długo żyjące TCP connections (lub WebSocket keepalive działa — agent wysyła ping co 20s).

---

### Dashboard: "Token nieważny lub wygasł"

```bash
# Wyloguj się i zaloguj ponownie
# Lub wydłuż czas ważności w .env:
JWT_EXPIRE_MINUTES=1440   # 24h
docker compose restart dockermind-web
```

---

### Baza danych / dysk zapełniony

```bash
# Sprawdź rozmiar wolumenu
docker system df -v | grep dockermind_data

# Usuń stare analizy przez API
curl -X DELETE http://HOST/api/analyses/1 -H "Authorization: Bearer TOKEN"

# Lub wyczyść całą bazę (UWAGA: nieodwracalne!)
docker compose down
docker volume rm dockermind_dockermind_data
docker compose up -d
```

---

### Kontener nie ma logów

**Możliwe przyczyny:**
- Kontener używa sterownika logów `none` lub `syslog`
- Kontener jest zbyt nowy i nie wyprodukował jeszcze logów
- Brak uprawnień do `docker.sock` — sprawdź, czy wolumin jest zamontowany:
  ```yaml
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
  ```

---

### docker-compose.yml nie jest wykrywany

Agent przeszukuje:
1. `/etc/dockermind/<nazwa-kontenera>/`
2. Label kontenera: `com.docker.compose.project.working_dir`
3. Rekurencyjnie: `/opt/**`, `/home/**`, `/srv/**`, `/root/**`

**Aby wymusić wykrywanie** — utwórz plik w `/etc/dockermind/`:
```bash
mkdir -p /etc/dockermind/nazwa-kontenera/
cp /ścieżka/do/docker-compose.yml /etc/dockermind/nazwa-kontenera/
```

---

## Bezpieczeństwo / Security notes

- Dashboard dostępny tylko przez HTTP — rozważ dodanie HTTPS na nginx w produkcji
- `AGENT_SECRET_TOKEN` i `CT_SECRET_KEY` powinny mieć min. 32 losowe znaki
- Kontenery uruchomione z `cap_drop: ALL`, `no-new-privileges`, `read_only`
- Docker socket zamontowany tylko do odczytu (`:ro`) w agencie
- Agent nie ma dostępu do internetu — komunikuje się tylko z SERVER 2

---

## Technologie / Tech stack

| Komponent | Technologia |
|-----------|-------------|
| Backend   | Python 3.12, FastAPI, Uvicorn |
| AI client | openai + httpx (SSL verify=False) |
| Database  | SQLite via SQLModel |
| Auth      | JWT (pyjwt) + bcrypt (passlib) |
| WebSocket | FastAPI WebSocket + websockets |
| Docker    | docker-py SDK |
| Frontend  | Alpine.js 3, Tailwind CSS, Chart.js 4, highlight.js 11 |
| Proxy     | nginx:alpine |
| Packaging | Docker + docker-compose |

---

*DockerMind — AI diagnostics for your Docker infrastructure, fully offline.*
