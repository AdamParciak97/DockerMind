# Testy dashboardu, komend i reguł

Uruchamiaj z katalogu głównego repozytorium. Backend wymaga zależności
z `central/requirements.txt`. Docelowy Python obrazów Docker to 3.12.

PowerShell:

```powershell
$env:PYTHONPATH='central'
$env:DB_PATH='.tmp-fleet/tests.db'
python -m unittest discover -s central/tests -p 'test_*.py'
node central/tests/test_fleet_logic.cjs
```

Testy Pythona sprawdzają m.in. role i widoczność serwerów, ograniczenia komend,
timeout, przechwytywanie stdout/stderr, usuwanie oczekujących żądań, reguły,
grupowanie i natychmiastowe rozwiązywanie alertów. Test rzeczywistych procesów
zastępuje wyłącznie launcher `nsenter` lokalnym Pythonem; nie wchodzi w przestrzenie
nazw hosta. Test logiki JS sprawdza limit równoległości, niezmienność rozpoczętej
partii, brak automatycznych ponowień i zatrzymanie kolejki po wylogowaniu.

Testy przeglądarkowe wymagają Node.js z globalnym `WebSocket`, pakietu Playwright
i Edge. `PLAYWRIGHT_MODULE` może wskazać zainstalowany `playwright` lub
`playwright-core`, a `PLAYWRIGHT_CHANNEL` inny dostępny kanał Chromium.
Umieść `alpine.min.js` (3.14.1) i `tailwind.js` używane przez obraz centrali
w `.tmp-fleet/assets` albo ustaw `FLEET_TEST_ASSETS` na katalog tych plików.

```powershell
node central/tests/test_fleet_browser.cjs
```

Ten test używa odpowiedzi testowych API i zapisuje zrzuty desktop/mobile do
`.tmp-fleet`. Obejmuje filtry, wybór serwerów, równoległe komendy i niezależne
błędy, bezpieczne wyświetlanie wyjścia, formularz reguł oraz błędną odpowiedź HTML.

Test integracyjny używa rzeczywistej centrali, logowania, CSRF, bazy SQLite i
WebSocketów. Agenty są symulowane, więc nie wykonuje poleceń na hostach.
Uruchom w osobnym terminalu:

```powershell
python central/tests/serve_fleet_preview.py
```

Następnie:

```powershell
node central/tests/test_fleet_integration.cjs
```

Serwer testowy ma osobną tymczasową bazę i działa wyłącznie na localhost:18082.
Zatrzymaj go przez Ctrl+C. Nie kieruj testu integracyjnego do wdrożenia produkcyjnego:
test usuwa istniejące reguły z **testowej** bazy. Przed wdrożeniem na Linuxie
sprawdź również `hostname; pwd; docker ps` na jednym agencie z aktualnym Compose
i `HOST_ACCESS_ENABLED=true`, aby zweryfikować wejście w przestrzenie nazw hosta.
