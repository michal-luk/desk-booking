# Contributing

Thanks for your interest! Bug reports, ideas and pull requests are welcome.

1. **Issues** — describe what happened, what you expected and how to reproduce it. Security issues: see [SECURITY.md](SECURITY.md).
2. **Pull requests** — fork → branch → pull request with a short description of the change.
3. **Run the smoke test** before sending:
   ```bash
   python tests/smoke_test.py
   python tests/smoke_test.py --db postgresql://user:pass@localhost:5432/empty_db   # optional
   python tests/smoke_test.py --db mariadb://user:pass@localhost:3306/empty_db      # optional
   python tools/i18n_check.py                                                      # translations
   ```
   GitHub Actions runs it automatically on SQLite, PostgreSQL and MariaDB.
4. **Code style** — follow the surrounding code: English identifiers and comments; user-facing texts through
   `_()` / `t()` with translations in `locales/` (see [docs/TRANSLATIONS.md](docs/TRANSLATIONS.md)); SQL with `?`
   placeholders through `db.query` / `db.execute` / `db.insert` (works on every supported database); booking logic
   in `booking.py` so the web UI, REST API and MCP server stay consistent.

By submitting a contribution you agree that it is licensed under the [MIT License](LICENSE).

---

## Jak pomóc (PL)

Zgłoszenia i pull requesty są mile widziane. Przed wysłaniem zmian uruchom `python tests/smoke_test.py`
i `python tools/i18n_check.py`. Kod i komentarze po angielsku, teksty interfejsu przez `_()` / `t()` z tłumaczeniami
w `locales/`; logika rezerwacji w `booking.py`. Wysyłając zmiany, zgadzasz się na licencję MIT.
