# Rezerwacja biurek

> English: [README.md](README.md) · dokumentacja (EN): [instalacja](docs/INSTALL.md), [SSO](docs/SSO.md), [REST API](docs/API.md), [MCP](docs/MCP.md), [tłumaczenia](docs/TRANSLATIONS.md)

Aplikacja WWW do rezerwacji **biurek, salek i budek** w biurze, instalowana na własnym serwerze. Rysujesz plany pięter,
łączysz ludzi w zespoły, każdy rezerwuje z laptopa albo telefonu — a **asystent AI może rezerwować za nich** przez
wbudowany serwer MCP.

Interfejs **po polsku i po angielsku** (wybór na ekranie logowania i w ustawieniach konta); kolejny język to jeden plik JSON.

![Plan piętra po polsku](docs/screenshots/plan-room-pl.png)

## Możliwości

- Interaktywny plan piętra (powiększanie, przesuwanie, dotyk) i widok listy; działa na telefonie.
- Biurka na cały dzień lub na godziny, salki i budki na godziny, zakresy dat z wybranymi dniami tygodnia.
- Bez kolizji: jedno miejsce nie zostanie zarezerwowane dwa razy, jedna osoba nie zajmie dwóch biurek naraz.
- Biurka stałe (przypisane), biurka i strefy zespołów, biurka dla wszystkich.
- *Moje rezerwacje* z anulowaniem hurtowym, *Mój zespół* — kto z zespołu jest w biurze w który dzień.
- Kierownik zespołu rezerwuje i anuluje za członków zespołu, administrator za każdego.
- Powiadomienia e-mail w języku odbiorcy (każdy może je wyłączyć).
- Edytor planu (SVG): biurka, bloki biurek, salki, budki, ściany, strefy, napisy, tło piętra, cofnij/ponów.
- Konta: e-mail, imię, nazwisko, rola (*Administrator*, *Edytor*, *Użytkownik*), język; zaproszenia i linki do
  zmiany hasła, hasła tymczasowe, odblokowanie po błędnych logowaniach.
- Zespoły: członków dodaje się po e-mailu, imieniu i nazwisku (konto zakłada się samo) albo hurtem z listy
  (`e-mail; Imię; Nazwisko` z Excela lub `Imię Nazwisko <e-mail>` z Outlooka); link do hasła prosto z listy członków.
- Wszystkie rezerwacje z filtrami, eksport CSV, dziennik zdarzeń.
- **Ustawienia w aplikacji**: baza danych, język, logowanie i SSO, poczta SMTP, API i MCP, kolory.

## Uruchomienie

### Docker

```bash
docker compose up -d                 # http://localhost:2230/desks
docker compose logs app              # jednorazowy kod instalacyjny pierwszego administratora
```

Dane (SQLite, klucze) leżą w wolumenie `data`. W komplecie jest PostgreSQL (usługa `db`) — przełączysz się na niego
w *Ustawienia → Baza danych* (serwer `db`, port 5432, baza i użytkownik `desks`).

### Python 3.10+

```bash
pip install -r requirements.txt
copy .env.example .env               # Linux: cp; uzupełnij APP_URL
python run.py                        # http://localhost:2230/desks
```

**Pierwsze uruchomienie:** gdy w bazie nie ma administratora, aplikacja kieruje na `/setup`, a w logu serwera wypisuje
jednorazowy kod. Tam zakłada się konto administratora; resztę ustawia się w aplikacji.

## Ustawienia administratora (Administracja → Ustawienia)

| Zakładka | Co |
|---|---|
| Baza danych | SQLite / PostgreSQL / MariaDB / MySQL, połączenie, TLS, test połączenia; przełączenie z **przeniesieniem wszystkich danych** albo na bazę, która już je ma. Wybór zapisuje się w `data/database.json` (hasło zaszyfrowane). |
| Język, logowanie i SSO | domyślny język; logowanie hasłem obok SSO / tylko dla administratorów; Microsoft, Google, inny OIDC. Adres powrotu: `{APP_URL}/sso/<microsoft\|google\|oidc>/callback`. |
| Poczta | SMTP (Microsoft 365, Gmail, własny serwer) — potwierdzenia, zaproszenia, reset hasła; wysyłka testowa. |
| API i MCP | włączenie REST API i serwera MCP, limit zapytań na klucz, unieważnienie wszystkich kluczy. |
| Wygląd | kolor nagłówka, główny i akcent — gotowe zestawy i podgląd na żywo. |

## Bezpieczeństwo

- Hasła: scrypt; 5 błędnych prób = blokada konta na 15 min, limit prób z jednego IP.
- Sekrety w bazie (SSO, hasło SMTP, klucze API) i hasło do bazy w `data/database.json`: AES-256-GCM, klucz
  `ENCRYPTION_KEY` z `.env` (bez niego — `data/encryption_key`). **Zrób kopię klucza poza serwerem.**
- Klucze API i tokeny jednorazowe wyszukiwane po skrócie SHA-256.
- SSO: authorization code + PKCE + state + nonce, podpis tokenu ID sprawdzany kluczami JWKS dostawcy.
- Sesja: ciasteczko HttpOnly / SameSite / Secure przy https, token CSRF, wylogowanie po bezczynności, nagłówki bezpieczeństwa.

## REST API i MCP

Każdy użytkownik — także administrator — generuje własny klucz w **Moje konto** (pokazanie, wygenerowanie nowego
i usunięcie istniejącego klucza wymaga hasła). Klucz działa z uprawnieniami właściciela.

- REST: `{APP_URL}/api/v1`, nagłówek `X-API-Key: <klucz>` — opis w [docs/API.md](docs/API.md).
- MCP: `{APP_URL}/mcp` z nagłówkiem `Authorization: Bearer <klucz>` — opis w [docs/MCP.md](docs/MCP.md).

```bash
claude mcp add --transport http desk-booking https://serwer/desks/mcp --header "Authorization: Bearer rzb_..."
```

## Testy

```bash
python tests/smoke_test.py                                             # SQLite w katalogu tymczasowym
python tests/smoke_test.py --db postgresql://user:haslo@host:5432/pusta_baza   # albo mariadb:// / mysql://
python tools/i18n_check.py                                             # czy wszystko przetłumaczone
```

## Autor i licencja

Autor: Michał Łukawski — michal.lukawski@neodesk.pl

[MIT](LICENSE) — możesz używać, zmieniać i rozpowszechniać, także komercyjnie, zachowując informację o licencji.
Zgłaszanie błędów bezpieczeństwa: [SECURITY.md](SECURITY.md), współpraca: [CONTRIBUTING.md](CONTRIBUTING.md).
