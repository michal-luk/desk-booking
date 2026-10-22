# Translations

The user interface, e-mails and API / MCP messages are translated. Every language is one JSON file in
[`locales/`](../locales/):

```json
{
  "_language": "Polski",
  "Book": "Zarezerwuj",
  "Booked: {place}": "Zarezerwowano: {place}"
}
```

- Keys are the English texts used in the code (`_('Book')` in Python and templates, `t('Book')` in JavaScript).
- `{place}`, `{n}`… are placeholders — keep them in the translation.
- An empty value means "not translated yet" — the English text is shown.
- `_language` is the name shown in the language switch.
- `en.json` lists every text of the application and is the template for new languages.

## Adding a language

1. Copy `locales/en.json` to `locales/<code>.json` (e.g. `de.json`) and change `_language` to `Deutsch`.
2. Translate the values.
3. Restart the application — the new language appears in the switch on the login screen, in the top bar,
   in *My account* and in *Settings → Default language*.

## Checking translations

```bash
python tools/i18n_check.py            # lists missing and unused texts of every language
python tools/i18n_check.py --update   # rewrites en.json and adds new texts (empty) to the other languages
```

Run `--update` after changing texts in the code, then fill in the empty values. GitHub Actions runs the check,
so a pull request with a missing Polish translation fails.
