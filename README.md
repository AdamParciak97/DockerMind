# 🐳 DockerMind

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![WebSocket](https://img.shields.io/badge/WebSocket-real--time-FF6B35)
![Offline AI](https://img.shields.io/badge/AI-Offline%20%7C%20llama3-8B4FFF)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.2.0-blue)

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
| **Sekrety** | Szyfrowane AES-256 (Fernet) klucz-wartość w lokalnej bazie |
| **Grupy serwerów** | Sidebar z collapsible grupami i kolorami |
| **Grupy użytkowników** | Przypisanie widoczności grup serwerów per-użytkownik |
| **Historia zdarzeń** | Crash, restart, stop — timeline per kontener |
| **Eksport PDF** | Raport AI do pliku PDF |
| **docker-compose edit** | Edycja i zapis pliku compose z UI |
| **Akcje kontenerów** | Start / stop / restart z dashboardu |
| **LDAP / Active Directory** | Konfiguracja i logowanie przez GUI, zarządzanie rolami w DockerMind |
| **HTTPS + TLS 1.3** | nginx z self-signed cert (auto-generowanym), HSTS |
| **Audit log** | Pełna historia logowań, akcji, zmian konfiguracji |
| **Aktywne sesje** | Lista + unieważnianie sesji per-użytkownik |
| **Rotacja tokenu agenta** | Generowanie nowego AGENT_SECRET_TOKEN z GUI bez restartu centrali |
| **Backup bazy danych** | Pobieranie spójnego snapshotu SQLite jednym kliknięciem |

---

## Bezpieczeństwo / Security

DockerMind v1.2 został zaprojektowany z podejściem *security-first*:

| Mechanizm | Opis |
|-----------|------|
| **httpOnly cookie** | Token JWT w cookie — niedostępny dla JavaScript, ochrona przed XSS |
| **CSRF protection** | Double-submit cookie — nagłówek `X-CSRF-Token` weryfikowany po każdej mutacji |
| **JWT revocation** | Tabela `RevokedToken` — wylogowanie natychmiastowo unieważnia token |
| **Rate limiting** | IP: 10 prób/5min, per-username: 15 prób/15min (Python) + nginx zone |
| **Fernet AES-256** | Szyfrowanie sekretów i tokenu agenta w bazie danych |
| **Siła hasła** | Min. 8 znaków, wielka litera, cyfra — wymuszane przy tworzeniu i zmianie |
| **Timeout nieaktywności** | 15 min bez aktywności → auto-wylogowanie (13 min → ostrzeżenie) |
| **Security headers** | CSP, HSTS, X-Frame-Options, Referrer-Policy, Permissions-Policy |
| **Brak cache API** | `Cache-Control: no-store` dla wszystkich odpowiedzi `/api/*` |
| **Wyłączone docs** | `/docs`, `/redoc`, `/openapi.json` — niewidoczne w produkcji |
| **Audit log** | Każde logowanie, wylogowanie, akcja kontenera, zmiana compose — zapisana |

---

## Architektura / Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         SIEĆ LOKALNA / LAN                               │
│                                                                          │
│  ┌──────────────────────┐          ┌─────────────────────────────────┐   │
│  │   SERVER AI          │          │   SERVER CENTRAL / WEB          │   │
│  │  ai.mgmt.pl          │◄─────────│                                 │   │
│  │  nginx (SSL)         │  HTTPS   │  ┌──────────────────────────┐  │   │
│  │  /llama3/v1 ●        │          │  │  dockermind-nginx         │  │   │
│  └──────────────────────┘          │  │  :80  → redirect HTTPS   │  │   │
│                                    │  │  :443 → proxy + TLS      │  │   │
│  Browser ──► https://CENTRAL_IP    │  │  rate-limit login        │  │   │
│              Dashboard (SPA)       │  └──────────┬───────────────┘  │   │
│                                    │             │                   │   │
│                                    │  ┌──────────▼───────────────┐  │   │
│                                    │  │  dockermind-web:8080     │  │   │
│                                    │  │  FastAPI + SQLite        │  │   │
│                                    │  │  WebSocket hub           │  │   │
│                                    │  └──────────────────────────┘  │   │
│                                    └─────────────────────────────────┘   │
│                                               ▲  ▲                       │
│                                  WS (plain)   │  │ WS (plain)            │
│                                               │  │                       │
│  ┌──────────────────────┐   ┌─────────────────┴──┴────────────────────┐  │
│  │   AGENT 1            │   │   AGENT 2 (kolejny serwer)              │  │
│  │   dockermind-agent   │   │   dockermind-agent                      │  │
│  │   ├── Docker SDK     │   │   ├── Docker SDK                        │  │
│  │   ├── Docker CLI     │   │   ├── Docker CLI                        │  │
│  │   └── docker.sock    │   │   └── docker.sock                       │  │
│  └──────────────────────┘   └─────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

> Agenty łączą się po HTTP (port 80 → `/ws/agent`). Przeglądarka łączy się po HTTPS (443). Połączenia WebSocket dashboardu i terminala przechodzą przez nginx po WSS.

---

## Struktura projektu / Project structure

```
dockermind/
├── agent/
│   ├── main.py              # WebSocket client + reconnect + PTY exec sessions
│   ├── collector.py         # Docker SDK data collection
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── .env.example
├── central/
│   ├── main.py              # FastAPI + CSRF middleware + WS endpoints
│   ├── config.py            # Settings from .env
│   ├── models.py            # SQLModel DB models + auto-migration
│   ├── auth.py              # JWT + cookie auth + CSRF + agent token
│   ├── rate_limit.py        # IP sliding-window + per-username lockout
│   ├── ldap_auth.py         # LDAP / Active Directory authentication
│   ├── exchange.py          # Microsoft Graph (Exchange email)
│   ├── websocket_manager.py # Agent hub + dashboard broadcasting + terminal routing
│   ├── routers/
│   │   ├── auth.py          # Login (httpOnly cookie) / logout (JWT revoke) / me
│   │   ├── servers.py       # Servers, containers, logs, compose, actions
│   │   ├── analysis.py      # AI analysis + PDF export + email
│   │   ├── alerts.py        # Alert rules + events
│   │   ├── metrics.py       # Time-series metric snapshots
│   │   ├── secrets.py       # Encrypted key-value secrets (Fernet AES-256)
│   │   └── settings.py      # Users, groups, LDAP, sessions, agent token, backup
│   ├── Dockerfile
│   └── static/
│       └── index.html       # Single-file SPA (Alpine.js + Tailwind + Chart.js + xterm.js)
├── nginx/
│   ├── Dockerfile
│   ├── nginx.conf           # TLS, rate limiting, log masking, WS proxy
│   └── generate-cert.sh     # Auto self-signed cert on first run
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Szybki start / Quick start

### Wymagania
- Docker Engine 20.10+
- Docker Compose v2
- Porty 80 i 443 wolne na serwerze centralnym
- Port 80 wolny na serwerach z agentami

### Centrala (SERVER WEB)

```bash
# 1. Sklonuj repozytorium
git clone https://github.com/AdamParciak97/DockerMind.git
cd DockerMind

# 2. Skonfiguruj środowisko
cp .env.example .env
nano .env
# Ustaw obowiązkowo:
#   CT_PASSWORD=<silne-haslo>
#   CT_SECRET_KEY=$(openssl rand -hex 32)
#   AGENT_SECRET_TOKEN=$(openssl rand -hex 32)

# 3. Zbuduj i uruchom
docker compose up -d --build

# Dashboard: https://<IP_SERWERA>
# Przeglądarka pokaże ostrzeżenie SSL (self-signed) — kliknij "Zaawansowane → Kontynuuj"
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
# Oczekiwany output: "Registered as 'nazwa-serwera' (192.168.1.200)"
```

---

## Konfiguracja .env / Environment variables

### Centrala — `.env`

| Zmienna | Domyślna | Opis |
|---------|----------|------|
| `CT_USERNAME` | `admin` | Login administratora środowiskowego |
| `CT_PASSWORD` | *(brak)* | **Wymagane.** Hasło admina (min. 8 znaków, wielka litera, cyfra) |
| `CT_SECRET_KEY` | *(brak)* | **Wymagane.** Klucz JWT + szyfrowanie (min. 32 znaki) |
| `AGENT_SECRET_TOKEN` | *(brak)* | **Wymagane.** Token agentów (można rotować z GUI) |
| `AI_BASE_URL` | `https://ai.mgmt.pl/llama3/v1` | URL modelu AI (OpenAI-compatible) |
| `AI_MODEL` | `llama3` | Nazwa modelu |
| `DB_PATH` | `/app/data/dockermind.db` | Ścieżka do bazy SQLite |
| `JWT_EXPIRE_MINUTES` | `480` | Czas ważności sesji (8h) |
| `SMTP_HOST` | *(brak)* | Serwer SMTP do wysyłki raportów |
| `SMTP_PORT` | `587` | Port SMTP |
| `SMTP_USER` / `SMTP_PASSWORD` | *(brak)* | Dane SMTP |

> **Generowanie kluczy:** `openssl rand -hex 32`

### Agent — `agent/.env`

| Zmienna | Przykład | Opis |
|---------|----------|------|
| `CENTRAL_HOST` | `192.168.1.100` | IP lub hostname centrali |
| `AGENT_TOKEN` | *(token)* | Identyczny z `AGENT_SECRET_TOKEN` na centrali |
| `AGENT_NAME` | `serwer-prod-01` | Wyświetlana nazwa w dashboardzie |
| `AGENT_IP` | *(auto)* | Nadpisanie auto-wykrytego IP hosta |

> Hostname i IP są wykrywane automatycznie — hostname z `/etc/hostname` hosta, IP przez `/proc/1/net`.

---

## REST API

### Autoryzacja
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `POST` | `/api/auth/login` | Logowanie — ustawia httpOnly cookie `dm_token` |
| `POST` | `/api/auth/logout` | Wylogowanie — unieważnia JWT, kasuje cookie |
| `GET`  | `/api/auth/me` | Dane zalogowanego użytkownika |

### Serwery i kontenery
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `GET`  | `/api/servers` | Lista serwerów |
| `GET`  | `/api/servers/{id}` | Szczegóły serwera + kontenery |
| `GET`  | `/api/servers/{id}/containers/{name}/logs` | Logi kontenera |
| `GET`  | `/api/servers/{id}/containers/{name}/compose` | docker-compose.yml |
| `PUT`  | `/api/servers/{id}/containers/{name}/compose` | Zapisz compose (admin) |
| `POST` | `/api/servers/{id}/containers/{name}/action` | start/stop/restart (admin) |
| `GET`  | `/api/health` | Status centrali + liczba agentów |

### Metryki
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `GET`  | `/api/metrics/{agent_id}/{container}` | Snapshoty CPU/RAM/Net/Disk |

### Analiza AI
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `POST` | `/api/analyze` | Uruchom analizę AI (streaming przez WS) |
| `GET`  | `/api/analyses` | Lista analiz |
| `GET`  | `/api/analyses/{id}` | Szczegóły analizy |
| `DELETE` | `/api/analyses/{id}` | Usuń |
| `GET`  | `/api/analyses/{id}/pdf` | Eksport do PDF |
| `POST` | `/api/analyses/{id}/email` | Wyślij mailem |

### Alerty
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `GET`  | `/api/alerts` | Lista reguł alertów |
| `POST` | `/api/alerts` | Utwórz regułę |
| `DELETE` | `/api/alerts/{id}` | Usuń regułę |
| `PUT`  | `/api/alerts/{id}/toggle` | Włącz/wyłącz |
| `GET`  | `/api/alert-events` | Lista zdarzeń |
| `POST` | `/api/alert-events/{id}/ack` | Potwierdź zdarzenie |

### Sekrety
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `GET`  | `/api/secrets` | Lista sekretów (wartości ukryte) |
| `POST` | `/api/secrets` | Utwórz sekret |
| `GET`  | `/api/secrets/{id}/reveal` | Pokaż wartość (admin) |
| `DELETE` | `/api/secrets/{id}` | Usuń |

### Ustawienia (admin)
| Metoda | Endpoint | Opis |
|--------|----------|------|
| `PUT`  | `/api/settings/password` | Zmień własne hasło |
| `GET/POST` | `/api/users` | Lista / tworzenie użytkowników |
| `DELETE` | `/api/users/{username}` | Usuń użytkownika |
| `GET/POST/DELETE` | `/api/server-groups` | Grupy serwerów |
| `PUT`  | `/api/server-groups/{id}/members` | Członkowie grupy serwerów |
| `GET/POST/DELETE` | `/api/user-groups` | Grupy użytkowników |
| `PUT`  | `/api/user-groups/{id}/members` | Członkowie grupy |
| `PUT`  | `/api/user-groups/{id}/server-groups` | Widoczność grup serwerów |
| `GET/PUT` | `/api/settings/ldap` | Konfiguracja LDAP |
| `POST` | `/api/settings/ldap/test` | Test połączenia LDAP |
| `GET`  | `/api/audit-logs` | Historia zdarzeń bezpieczeństwa |
| `GET`  | `/api/settings/sessions` | Lista aktywnych sesji |
| `DELETE` | `/api/settings/sessions/{jti}` | Unieważnij sesję |
| `GET`  | `/api/settings/agent-token` | Info o tokenie agenta |
| `POST` | `/api/settings/agent-token/rotate` | Generuj nowy token |
| `GET`  | `/api/settings/backup` | Pobierz backup bazy danych |

### WebSocket
| Endpoint | Opis |
|----------|------|
| `ws://HOST/ws/agent` | Połączenie agenta (nagłówek `X-Agent-Token`) |
| `wss://HOST/ws/dashboard` | Live updates dashboardu (cookie `dm_token`) |
| `wss://HOST/ws/terminal?agent_id=ID&container=NAME` | Terminal PTY (cookie `dm_token`) |

---

## Technologie / Tech stack

| Komponent | Technologia |
|-----------|-------------|
| Backend   | Python 3.12, FastAPI 0.111, Uvicorn |
| AI client | openai + httpx |
| Database  | SQLite via SQLModel + SQLAlchemy |
| Auth      | JWT (pyjwt) + bcrypt (passlib) + httpOnly cookie |
| Crypto    | Fernet AES-256 (cryptography) |
| WebSocket | FastAPI WebSocket + websockets |
| Terminal  | xterm.js 5.3 + PTY (pty module) |
| Docker    | docker-py SDK + Docker CLI |
| PDF       | fpdf2 |
| LDAP      | ldap3 |
| Frontend  | Alpine.js 3, Tailwind CSS, Chart.js 4, highlight.js 11, xterm.js 5 |
| Proxy     | nginx:alpine — TLS 1.2/1.3, HSTS, rate limiting |

---

## Rozwiązywanie problemów / Troubleshooting

### Agent nie pojawia się w dashboardzie

```bash
# Logi agenta
docker logs dockermind-agent -f

# Sprawdź osiągalność centrali (port 80 dla agentów)
curl http://$CENTRAL_HOST/api/health

# Tokeny muszą być identyczne:
# agent/.env:        AGENT_TOKEN=abc123...
# central/.env:  AGENT_SECRET_TOKEN=abc123...
# Lub token ustawiony przez GUI (Settings → System → Rotacja tokenu)
```

### Przeglądarka pokazuje "Twoje połączenie nie jest prywatne"

To normalny komunikat dla self-signed certyfikatu. Kliknij „Zaawansowane" → „Kontynuuj do [adres]". W środowisku produkcyjnym podmień certyfikat w `dockermind_ssl` volume na certyfikat od CA (np. Let's Encrypt).

### Błąd CSRF 403

Odśwież stronę (`F5`) — przeglądarka pobierze nowy `csrf_token` cookie. Może się zdarzyć po wygaśnięciu cookie lub otwieraniu wielu kart.

### Terminal pokazuje "Proces zakończony" od razu

Agent musi mieć dostęp do Docker socket. Sprawdź `docker-compose.yml` agenta:
```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
  - /:/host
  - /proc/1/net:/host-proc-net:ro
```

### Upgrade z v1.1 do v1.2

Baza danych migruje się automatycznie przy starcie (nowe tabele: `ActiveSession`, `AgentToken`, `RevokedToken`, `AuditLog`). Nie trzeba ręcznie zmieniać schematu.

```bash
docker load < dockermind-web-1.2.tar.gz
docker load < dockermind-nginx-1.2.tar.gz
docker compose up -d
```

### WebSocket rozłącza się

Sprawdź `nginx.conf` — timeouty WebSocket:
```nginx
proxy_read_timeout 86400s;
proxy_send_timeout 86400s;
```

### Baza danych / dysk zapełniony

Użyj funkcji backup (Settings → System → Pobierz backup), następnie:
```bash
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

## Changelog

### v1.2.0
- **HTTPS** — nginx z auto-generowanym self-signed certyfikatem, TLS 1.2/1.3, HSTS
- **httpOnly cookie auth** — token JWT przeniesiony z localStorage do cookie niedostępnego dla JS
- **CSRF protection** — double-submit cookie (`X-CSRF-Token` header weryfikowany po każdej mutacji)
- **JWT revocation** — wylogowanie natychmiast unieważnia token (tabela `RevokedToken`)
- **Aktywne sesje** — lista + unieważnianie per-sesja (IP, user-agent, czas)
- **Rotacja tokenu agenta** — generowanie nowego `AGENT_SECRET_TOKEN` z GUI bez restartu
- **Backup bazy danych** — spójny snapshot SQLite do pobrania jednym kliknięciem
- **LDAP / Active Directory** — pełna konfiguracja przez GUI, zarządzanie rolami LDAP użytkowników w DockerMind
- **Grupy użytkowników** → **Grupy serwerów** — kontrola widoczności: użytkownik widzi tylko przypisane grupy serwerów
- **Audit log** — historia logowań, akcji kontenerów, zmian konfiguracji
- **Rate limiting** — IP sliding-window + per-username lockout (Python) + nginx `limit_req_zone`
- **Siła hasła** — min. 8 znaków, wielka litera, cyfra (przy tworzeniu i zmianie)
- **Timeout nieaktywności** — 15 min bez aktywności → auto-wylogowanie (13 min → baner ostrzegawczy)
- **Fernet AES-256** — szyfrowanie sekretów i tokenu agenta (zastąpienie XOR-cipher z v1.0)
- **Security headers** — CSP, X-Frame-Options, Referrer-Policy, Permissions-Policy, Cache-Control: no-store
- **Wyłączone /docs** — `/docs`, `/redoc`, `/openapi.json` niedostępne w produkcji
- **Maskowanie logów nginx** — query string (`?token=`) nie trafia do logów access.log

### v1.1.0
- Terminal w przeglądarce (xterm.js + PTY)
- Alerty złożone (min. czas trwania)
- Metryki historyczne + wykresy
- Sekrety (szyfrowane)
- Akcje kontenerów (start/stop/restart)
- Grupy serwerów w sidebarze
- Edycja docker-compose z UI

### v1.0.0
- Dashboard real-time (WebSocket)
- Analiza AI (streaming llama3)
- Multi-agent architecture
- JWT auth + role admin/user

---

*DockerMind — AI diagnostics for your Docker infrastructure, fully offline.*
