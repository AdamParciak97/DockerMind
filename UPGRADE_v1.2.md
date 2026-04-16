# Upgrade do v1.2

## Co nowego

- **HTTPS na porcie 443** — self-signed cert generowany automatycznie przy pierwszym starcie
- **HTTP port 80** — tylko dla agentów (`/ws/agent`), reszta przekierowana na HTTPS
- **Logowanie LDAP / Active Directory** — opcjonalne
- **Wysyłka email przez Exchange Online** (Microsoft Graph API) — opcjonalne
- **Rate limiting** — blokada IP po 10 nieudanych próbach logowania
- **Security headers** — X-Frame-Options, CSP, HSTS
- **Minimum hasła 8 znaków**

---

## Upgrade Central (serwer web)

```bash
# 1. Skopiuj nowe obrazy na serwer
scp dockermind-web.tar nginx.tar user@SERWER_CENTRAL:~/

# 2. Zaloguj się na serwer
ssh user@SERWER_CENTRAL

# 3. Zatrzymaj stare kontenery
cd ~/dockermind          # katalog z docker-compose.yml
docker compose down

# 4. Załaduj nowe obrazy
docker load -i ~/dockermind-web.tar
docker load -i ~/nginx.tar

# 5. Zaktualizuj docker-compose.yml
#    Pobierz nowy plik lub edytuj ręcznie — kluczowe zmiany:
#
#    nginx:
#      build: ./nginx          ← USUŃ tę linię (obraz już załadowany)
#      image: dockermind-nginx:1.2    ← ZMIEŃ z nginx:alpine
#      ports:
#        - "80:80"
#        - "443:443"           ← DODAJ
#      volumes:
#        - dockermind_ssl:/etc/nginx/ssl    ← ZMIEŃ (było: ./nginx/nginx.conf...)
#
#    volumes:
#      dockermind_ssl: {}      ← DODAJ
#
#    Najprościej — nadpisz plik nowym:
scp docker-compose.yml user@SERWER_CENTRAL:~/dockermind/

# 6. Zaktualizuj nginx.conf
mkdir -p ~/dockermind/nginx
scp nginx/nginx.conf user@SERWER_CENTRAL:~/dockermind/nginx/
#    (nginx.conf nie jest już montowany jako plik — jest w obrazie,
#     ale warto mieć kopię dla referencji)

# 7. Uruchom
docker compose up -d

# 8. Sprawdź logi
docker logs dockermind-nginx -f --tail=20
# Powinna pojawić się linia:
# [nginx] Generating self-signed SSL certificate (valid 10 years)...
# lub: [nginx] SSL certificate already exists — skipping generation.

docker logs dockermind-web -f --tail=20
# Sprawdź czy brak błędów SECURITY: ...
```

**Weryfikacja:**
```bash
# Dashboard powinien być dostępny przez HTTPS
curl -k https://localhost/api/health

# Port 80 przekierowuje na 443
curl -v http://localhost/api/health
# → 301 Moved Permanently → https://...
```

> **Dane bezpieczne** — volume `dockermind_data` z bazą SQLite nie jest usuwany przez `down`.

> **Przeglądarka** — przy pierwszym wejściu na `https://IP` pojawi się ostrzeżenie o self-signed cert. Zaakceptuj wyjątek (w Chrome: "Zaawansowane → Przejdź do...").

---

## Upgrade Agentów

Agent v1.2 nie ma zmian funkcjonalnych. **Konfiguracja agenta nie wymaga zmian** — nadal łączy się przez `ws://` na porcie 80.

```bash
# 1. Skopiuj nowy obraz na serwer z agentem
scp dockermind-agent.tar user@SERWER_AGENT:~/

# 2. Zaloguj się na serwer
ssh user@SERWER_AGENT

# 3. Podmień kontener
cd ~/dockermind-agent     # katalog z docker-compose.yml agenta
docker compose down
docker load -i ~/dockermind-agent.tar

# 4. Zaktualizuj tag w docker-compose.yml (jeśli nie masz nowego pliku)
sed -i 's/dockermind-agent:1\.[0-9]/dockermind-agent:1.2/' docker-compose.yml

# 5. Uruchom
docker compose up -d

# 6. Sprawdź połączenie z centralą
docker logs dockermind-agent -f --tail=20
# Powinna pojawić się linia:
# Registered as 'nazwa-serwera' (192.168.x.x)
```

> **.env agenta bez zmian** — `CENTRAL_URL=ws://IP_CENTRALI/ws/agent` pozostaje taki sam.

---

## Opcjonalne — własny certyfikat SSL zamiast self-signed

Jeśli masz certyfikat podpisany przez wewnętrzne CA lub Let's Encrypt:

```bash
# Na serwerze Central — skopiuj cert do volume przed uruchomieniem
docker run --rm -v dockermind_ssl:/ssl alpine sh -c \
  "mkdir -p /ssl"

# Skopiuj pliki certyfikatu
docker cp twoj-cert.crt dockermind-nginx:/etc/nginx/ssl/server.crt
docker cp twoj-klucz.key dockermind-nginx:/etc/nginx/ssl/server.key

# Lub przez volume (gdy kontener nie działa):
# znajdź ścieżkę volume
docker volume inspect dockermind_ssl
# skopiuj pliki do podanej ścieżki Mountpoint
```

---

## Opcjonalne — konfiguracja Exchange Online

Dodaj do `.env` na serwerze Central:

```env
EXCHANGE_ENABLED=true
EXCHANGE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
EXCHANGE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
EXCHANGE_CLIENT_SECRET=twoj-client-secret
EXCHANGE_SENDER=dockermind@firma.pl
```

Wymagana konfiguracja w Azure AD:
1. **Azure Portal → App registrations → New registration**
2. **Certificates & secrets → New client secret** → zapisz wartość
3. **API permissions → Add → Microsoft Graph → Application permissions → Mail.Send → Grant admin consent**
4. Skopiuj **Application (client) ID** i **Directory (tenant) ID** z Overview

Następnie restart centrali:
```bash
docker compose restart dockermind-web
```

---

## Opcjonalne — konfiguracja LDAP

Dodaj do `.env` na serwerze Central:

```env
LDAP_ENABLED=true
LDAP_SERVER=192.168.1.10
LDAP_BIND_DN=cn=svc-dockermind,ou=service,dc=firma,dc=pl
LDAP_BIND_PASSWORD=haslo-konta-serwisowego
LDAP_BASE_DN=ou=users,dc=firma,dc=pl
LDAP_USER_FILTER=(sAMAccountName={username})
LDAP_ADMIN_GROUP_DN=cn=dockermind-admins,ou=groups,dc=firma,dc=pl
```

```bash
docker compose restart dockermind-web
docker logs dockermind-web 2>&1 | grep LDAP
```

---

## Uwagi po aktualizacji

**Re-login wymagany** — stare tokeny JWT nie zawierają pola `role`. Wszyscy użytkownicy muszą się wylogować i zalogować ponownie.

**Hasła < 8 znaków** — istniejące hasła DB-userów działają do czasu zmiany. Przy następnej zmianie wymagane będzie min. 8 znaków.
