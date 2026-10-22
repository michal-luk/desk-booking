# Security policy

The application handles logins, single sign-on and API keys, so please report security issues **privately** —
not in public issues:

- e-mail: **michal.lukawski@neodesk.pl**, or
- GitHub: *Security* tab → **Report a vulnerability**.

Please include the affected version / commit, steps to reproduce and the impact. You will get an answer as soon as
possible; once a fix is released the issue will be described publicly.

## Deployment checklist

- Run behind HTTPS (`APP_URL` starting with `https://`).
- Set `ENCRYPTION_KEY` and `SECRET_KEY` in `.env` and keep a backup of `ENCRYPTION_KEY` outside the server.
- Never publish the `data/` directory, the `.env` file or database dumps.
- Prefer SSO with *Allowed e-mail domains*; keep password login for administrators as break-glass access only.
- Revoke API keys of people who leave (*Users → user → Revoke key*) and block or delete their accounts.

---

## Bezpieczeństwo (PL)

Błędy bezpieczeństwa zgłaszaj **prywatnie**, nie w publicznym zgłoszeniu: e-mailem na michal.lukawski@neodesk.pl
albo przez „Report a vulnerability” w zakładce Security repozytorium. Przy wdrożeniu: HTTPS, `ENCRYPTION_KEY`
w `.env` z kopią poza serwerem, nigdy nie publikuj katalogu `data/`, pliku `.env` ani zrzutów bazy.
