# Single sign-on (OpenID Connect)

*Administration → Settings → Language, login and SSO*. Each provider has its own panel: enable the button, enter the client ID
and secret, optionally restrict e-mail domains and allow automatic account creation.

The **Redirect URI** to register at the provider is shown in each panel:

```
{APP_URL}/sso/microsoft/callback
{APP_URL}/sso/google/callback
{APP_URL}/sso/oidc/callback
```

`APP_URL` must be the public address users open (e.g. `https://desks.example.com/desks`).

## How accounts are matched

- The user is identified by **e-mail** from the ID token (`email`, for Microsoft also `preferred_username` / `upn`).
  Google and generic providers must confirm the address (`email_verified`).
- An existing, active account with that e-mail logs in. First and last name are filled from the token if empty.
- No account: login is refused, unless **Create accounts automatically** is enabled — then a *User* account is created.
- **Allowed e-mail domains** (`example.com, example.org`) reject everyone else even if the provider accepts them.

## Password login next to SSO

- While no SSO provider is enabled, password login always works.
- *Allow password login next to SSO* — both on the login screen.
- *…only for administrators* — regular users only via SSO; administrators keep password access as a break-glass
  option if the provider is down. With password login disabled, administrators reach the form via “log in with a password”.

## Microsoft Entra ID (Azure AD / Microsoft 365)

1. [Entra admin center](https://entra.microsoft.com) → **App registrations** → **New registration**.
2. Supported account types: *this organizational directory only* (single tenant) — recommended.
3. Redirect URI: platform **Web**, value `{APP_URL}/sso/microsoft/callback`.
4. **Certificates & secrets** → **New client secret** → copy the *Value* (not the Secret ID).
5. In the app: *Application (client) ID*, *Client secret*, *Directory (tenant) ID*.
   - Tenant ID of your directory → only your organisation.
   - `organizations` → work accounts of any organisation (combine with *Allowed e-mail domains*).
6. Permissions: the default `openid`, `email`, `profile` are enough — no admin consent needed.

## Google / Google Workspace

1. [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services** → **OAuth consent screen** — type *Internal*
   for Workspace (only your domain) or *External*.
2. **Credentials** → **Create credentials** → **OAuth client ID** → *Web application*.
3. **Authorized redirect URIs**: `{APP_URL}/sso/google/callback`.
4. In the app: Client ID, Client secret. With exactly one allowed domain, Google shows only accounts of that domain (`hd`).

## Any OpenID Connect provider

Keycloak, Okta, Auth0, ADFS (2016+), Authentik, JumpCloud, Zitadel, …

1. Create a **confidential** client with the *authorization code* flow and the redirect URI `{APP_URL}/sso/oidc/callback`.
2. In the app:
   - **Provider address** — the issuer URL; `/.well-known/openid-configuration` is appended automatically
     (e.g. Keycloak: `https://sso.example.com/realms/company`, Okta: `https://company.okta.com`).
   - Client ID and secret, scopes (default `openid email profile`), button label.
3. **Check provider connection** downloads the provider configuration to verify the address.

## Security details

- Authorization code flow with **PKCE (S256)**, `state` and `nonce`, 10-minute limit for returning from the provider.
- ID token verified with the provider's signing keys (JWKS): signature, issuer (multi-tenant Microsoft issuer with
  the user's tenant ID), audience, expiry, nonce.
- Client secrets are stored encrypted (AES-256-GCM) and never sent back to the browser.
