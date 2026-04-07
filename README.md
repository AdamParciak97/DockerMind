# 🐳 DockerMind

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![WebSocket](https://img.shields.io/badge/WebSocket-real--time-FF6B35)
![Offline AI](https://img.shields.io/badge/AI-Offline%20%7C%20llama3-8B4FFF)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.1.0-blue)

**AI-powered Docker monitoring platform — fully offline, multi-server architecture.**

<img width="1919" height="1038" alt="image" src="https://github.com/user-attachments/assets/3d39f2ef-e94b-4e9d-b6ea-402bc7ba957d" />


Monitors all Docker containers across your infrastructure. One click triggers a streaming AI analysis (llama3) that diagnoses problems, assesses risk, and provides copy-paste fix commands — entirely within your local network, no internet required.

---

## Funkcje / Features

| Funkcja | Opis |
|---------|------|
| **Dashboard w czasie rzeczywistym** | CPU, RAM, sieć, dysk — odświeżane co 30s przez WebSocket |
| **Terminal w przeglądarce** | `docker exec` przez xterm.js — w pełni offline |
| **Analiza AI** | Streaming llama3/qwen — diagnoza, ocena ryzyka, komendy naprawcze |
| **Alerty** | Reguły per-kontener, alerty złożone (min. czas trwania N minut) |
| **Metryki historyczne** | CPU/RAM/Net/Disk co 30s, wykresy do 24h |
| **Porównanie kontenerów** | Multi-seria CPU/RAM dla wszystkich kontenerów serwera |
| **Sekrety** | Szyfrowane klucz-wartość w lokalnej bazie |
| **Wielokrotni użytkownicy** | Konta DB + bcrypt, role admin/user |
| **Grupy serwerów** | Sidebar z collapsible grupami i kolorami |
| **Historia zdarzeń** | Crash, restart, stop — timeline per kontener |
| **Eksport PDF** | Raport AI do pliku PDF |
| **docker-compose edit** | Edycja i zapis pliku compose z UI |
| **Akcje kontenerów** | Start / stop / restart z dashboardu |

---

## Architektura / Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SIEĆ LOKALNA / LAN                               │
│                                                                         │
│  ┌──────────────────────┐         ┌──────────────────────────────────┐  │
│  │   SERVER AI          │         │   SERVER CENTRAL / WEB           │  │
│  │  ai.mgmt.pl          │◄────────│                                  │  │
│  │  nginx (SSL)         │  HTTPS  │  ┌─────────────────────────────┐ │  │
│  │  /llama3/v1 ●        │         │  │  nginx:80                   │ │  │
│  └──────────────────────┘         │  │  /  →  dockermind-web:8080  │ │  │
│                                   │  │  /ws/ →  dockermind-web     │ │  │
│                                   │  └────────────┬────────────────┘ │  │
│                                   │               │                  │  │
│                                   │  ┌────────────▼────────────────┐ │  │
│                                   │  │  dockermind-web:8080        │ │  │
│                                   │  │  FastAPI + SQLite           │ │  │
│                                   │  │  WebSocket hub              │ │  │
│                                   │  └─────────────────────────────┘ │  │
│                                   └──────────────────────────────────┘  │
│                                              ▲  ▲                        │
│                                     WebSocket│  │WebSocket               │
│                                              │  │                        │
│  ┌──────────────────────┐    ┌───────────────┴──┴──────────────────┐    │
│  │   AGENT 1            │    │   AGENT 2 (kolejny serwer)          │    │
│  │   dockermind-agent   │    │   dockermind-agent                  │    │
│  │   ├── Docker SDK     │    │   ├── Docker SDK                    │    │
│  │   ├── Docker CLI     │    │   ├── Docker CLI                    │    │
│  │   └── docker.sock    │    │   └── docker.sock                   │    │
│  └──────────────────────┘    └─────────────────────────────────────┘    │
│                                                                         │
│       Browser ──► http://CENTRAL_IP  ──► Dashboard                     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Struktura projektu / Project structure

```
dockermind/
├── agent/
│   ├── main.py              # WebSocket client + reconnect + PTY exec sessions
│   ├── collector.py         # Docker SDK data collection
│   ├── Dockerfile           # Includes Docker CLI static binary
│   ├── docker-compose.yml
│   └── .env.example
├── central/
│   ├── main.py              # FastAPI + WS endpoints (/ws/agent, /ws/dashboard, /ws/terminal)
│   ├── config.py            # Settings from .env
│   ├── models.py            # SQLModel DB models + auto-migration
│   ├── auth.py              # JWT + agent token verification
│   ├── websocket_manager.py # Agent hub + dashboard broadcasting + terminal routing
│   ├── routers/
│   │   ├── auth.py          # POST /api/auth/login, GET /api/auth/me
│   │   ├── servers.py       # Servers, containers, logs, compose, actions
│   │   ├── analysis.py      # AI analysis + PDF export + email
│   │   ├── alerts.py        # Alert rules + events
│   │   ├── metrics.py       # Time-series metric snapshots
│   │   ├── secrets.py       # Encrypted key-value secrets
│   │   └── settings.py      # Users, server groups, user groups
│   ├── Dockerfile
│   └── static/
│       └── index.html       # Single-file SPA (Alpine.js + Tailwind + Chart.js + xterm.js)
├── nginx/
│   └── nginx.conf
├── docker-compose.yml       # Central: web + nginx
├── .env.example
└── README.md
```

---

## Szybki start / Quick start

### Centrala (SERVER WEB)

> Wymagania: Docker + Docker Compose, port 80 wolny.

```bash
# 1. Sklonuj repozytorium
git clone https://github.com/AdamParciak97/DockerMind.git
cd DockerMind

# 2. Skonfiguruj środowisko
cp .env.example .env
nano .env          # ustaw CT_PASSWORD, CT_SECRET_KEY, AGENT_SECRET_TOKEN

# 3. Zbuduj i uruchom
docker compose up -d --build

# Dashboard: http://<IP_SERWERA>
# Login: admin / <hasło z .env>
```

### Agent (na każdym monitorowanym serwerze)

```bash
# 1. Skopiuj katalog agent/ na serwer
scp -r DockerMind/agent/ user@192.168.1.200:~/dockermind-agent/

# 2. Skonfiguruj
cd ~/dockermind-agent
cp .env.example .env
nano .env
# CENTRAL_HOST=192.168.1.100    ← IP centrali
# AGENT_TOKEN=...               ← identyczny z AGENT_SECRET_TOKEN na centrali
# AGENT_NAME=nazwa-serwera

# 3. Uruchom
docker compose up -d

# 4. Weryfikacja
docker logs dockermind-agent -f
# Powinno pojawić się: "Registered as 'nazwa-serwera' (192.168.1.200)"
```

---

## Konfiguracja .env / Environment variables

### Centrala — `.env`

| Zmienna | Domyślna | Opis |
|---------|----------|------|
| `CT_USERNAME` | `admin` | Login administratora |
| `CT_PASSWORD` | *(brak)* | **Wymagane.** Hasło admina |
| `CT_SECRET_KEY` | *(brak)* | **Wymagane.** Klucz JWT (min. 32 znaki) |
| `AGENT_SECRET_TOKEN` | *(brak)* | **Wymagane.** Token agentów (min. 32 znaki) |
| `AI_BASE_URL` | `https://ai.mgmt.pl/llama3/v1` | URL modelu AI (OpenAI-compatible) |
| `AI_MODEL` | `llama3` | Nazwa modelu |
| `DB_PATH` | `/app/data/dockermind.db` | Ścieżka do bazy SQLite |
| `JWT_EXPIRE_MINUTES` | `480` | Czas ważności tokenu JWT |
| `SMTP_HOST` | *(brak)* | Serwer SMTP do wysyłki raportów |
| `SMTP_PORT` | `587` | Port SMTP |
| `SMTP_USER` / `SMTP_PASSWORD` | *(brak)* | Dane uwierzytelniające SMTP |

### Agent — `agent/.env`

| Zmienna | Przykład | Opis |
|---------|----------|------|
| `CENTRAL_HOST` | `192.168.1.100` | IP centrali |
| `AGENT_TOKEN` | *(token)* | Identyczny z `AGENT_SECRET_TOKEN` na centrali |
| `AGENT_NAME` | `serwer-prod-01` | Wyświetlana nazwa |
| `AGENT_IP` | *(auto)* | Nadpisanie auto-wykrytego IP hosta |

> Hostname i IP są wykrywane automatycznie: hostname z `/etc/hostname` hosta, IP przez `/proc/1/net`.

> **Generowanie tokenów:** `openssl rand -hex 32`

---

## REST API

### Autoryzacja
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `POST` | `/api/auth/login` | Logowanie, zwraca JWT |
| `GET`  | `/api/auth/me` | Dane zalogowanego użytkownika |

### Serwery i kontenery
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `GET`  | `/api/servers` | Lista serwerów |
| `GET`  | `/api/servers/{id}` | Szczegóły serwera + kontenery |
| `GET`  | `/api/servers/{id}/containers/{name}/logs` | Logi kontenera |
| `GET`  | `/api/servers/{id}/containers/{name}/compose` | docker-compose.yml |
| `POST` | `/api/servers/{id}/containers/{name}/compose` | Zapisz docker-compose.yml |
| `POST` | `/api/servers/{id}/containers/{name}/action` | start/stop/restart |
| `GET`  | `/api/servers/{id}/containers/{name}/history` | Historia zdarzeń |

### Metryki
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `GET`  | `/api/metrics/{agent_id}/{container}` | Snapshoty CPU/RAM/Net/Disk |

### Analiza AI
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `POST` | `/api/analyze` | Uruchom analizę AI |
| `GET`  | `/api/analyses` | Lista analiz |
| `GET`  | `/api/analyses/{id}/pdf` | Eksport do PDF |
| `POST` | `/api/analyses/{id}/email` | Wyślij mailem |

### Alerty
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `GET`  | `/api/alerts` | Lista reguł |
| `POST` | `/api/alerts` | Utwórz regułę |
| `DELETE` | `/api/alerts/{id}` | Usuń regułę |
| `PUT`  | `/api/alerts/{id}/toggle` | Włącz/wyłącz |
| `GET`  | `/api/alert-events` | Lista zdarzeń alertów |
| `POST` | `/api/alert-events/{id}/ack` | Potwierdź zdarzenie |

### Sekrety
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `GET`  | `/api/secrets` | Lista sekretów (wartości ukryte) |
| `POST` | `/api/secrets` | Utwórz sekret |
| `GET`  | `/api/secrets/{id}/reveal` | Pokaż wartość |
| `DELETE` | `/api/secrets/{id}` | Usuń |

### Ustawienia
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `PUT`  | `/api/settings/password` | Zmień hasło |
| `GET/POST/DELETE` | `/api/users` | Zarządzanie użytkownikami (admin) |
| `GET/POST/DELETE` | `/api/server-groups` | Grupy serwerów |
| `PUT`  | `/api/server-groups/{id}/members` | Członkowie grupy |
| `GET/POST/DELETE` | `/api/user-groups` | Grupy użytkowników |

### WebSocket
| Endpoint | Opis |
|----------|------|
| `ws://HOST/ws/agent?agent_token=TOKEN` | Połączenie agenta |
| `ws://HOST/ws/dashboard?token=JWT` | Live updates dashboardu |
| `ws://HOST/ws/terminal?token=JWT&agent_id=ID&container=NAME` | Terminal PTY |

---

## Technologie / Tech stack

| Komponent | Technologia |
|-----------|-------------|
| Backend   | Python 3.12, FastAPI, Uvicorn |
| AI client | openai + httpx |
| Database  | SQLite via SQLModel |
| Auth      | JWT (pyjwt) + bcrypt (passlib) |
| WebSocket | FastAPI WebSocket + websockets |
| Terminal  | xterm.js 5.3 + PTY (pty module) |
| Docker    | docker-py SDK + Docker CLI |
| PDF       | fpdf2 |
| Frontend  | Alpine.js 3, Tailwind CSS, Chart.js 4, highlight.js 11, xterm.js 5 |
| Proxy     | nginx:alpine |

---

## Rozwiązywanie problemów / Troubleshooting

### Agent nie pojawia się w dashboardzie

```bash
# Logi agenta
docker logs dockermind-agent -f

# Sprawdź osiągalność centrali
curl http://$CENTRAL_HOST/api/health

# Tokeny muszą być identyczne:
# agent/.env:        AGENT_TOKEN=abc123...
# central/.env:  AGENT_SECRET_TOKEN=abc123...
```

### Terminal pokazuje "Proces zakończony" od razu

Agent musi mieć dostęp do Docker socket i zamontowany `/proc/1/net`. Sprawdź `docker-compose.yml` agenta:
```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
  - /:/host
  - /proc/1/net:/host-proc-net:ro
```

### Błąd 500 przy wejściu w alerty (po upgrade z v1.0)

Migracja bazy uruchamia się automatycznie przy starcie centrali — dodaje brakującą kolumnę `min_duration`. Jeśli błąd nadal występuje, zrestartuj kontener:
```bash
docker compose restart dockermind-web
```

### WebSocket rozłącza się

Sprawdź `nginx.conf` — timeouty powinny wynosić:
```nginx
proxy_read_timeout 86400s;
proxy_send_timeout 86400s;
```

### Baza danych / dysk zapełniony

```bash
# Sprawdź rozmiar
docker system df -v | grep dockermind_data

# Wyczyść (UWAGA: nieodwracalne!)
docker compose down
docker volume rm dockermind_dockermind_data
docker compose up -d
```

---

## 📸 Screenshots
<img width="1916" height="780" alt="image" src="https://github.com/user-attachments/assets/d59bcafe-f41b-43ad-bfcd-a4843190051c" />

<img width="1623" height="992" alt="image" src="https://github.com/user-attachments/assets/d321628e-a2f1-40c3-9f52-60237b6c0905" />

<img width="1609" height="637" alt="image" src="https://github.com/user-attachments/assets/49f7172b-24a1-4225-9803-18a873ecb1c1" />

---

*DockerMind — AI diagnostics for your Docker infrastructure, fully offline.*
