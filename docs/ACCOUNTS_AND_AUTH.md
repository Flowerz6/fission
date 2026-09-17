# Accounts & Authentication

> How to sign in the test accounts so live runs work. This is the step people get stuck
> on, so it's written as a procedure. Do the section that matches the kind of run you want.
>
> The full env contract is [`.env.example`](../.env.example); running the tool is
> [GETTING_STARTED.md](GETTING_STARTED.md); the code is `adapters/_auth.py` (API auth),
> `adapters/_ui_mode_common/` (browser auth), and `core/account_pool.py` (roles).

## The one thing to understand first

There are **two completely separate auth worlds**, and which one you need depends only on
whether the run talks to a **browser** or an **API**:

| World | Used by | How it authenticates | State lives in |
|---|---|---|---|
| **Browser (`ui_mode`)** | live Gemini side-panel targets, UI senders | a persistent, already-signed-in Chrome profile — **no tokens** | a Chrome profile dir on disk |
| **API mode** | Docs/Gmail/Sheets API ops (planting bait, some setup) | Google OAuth token or a service-account key | a token JSON under `~/.advtest/` |

Most live demo work is **browser** auth — signing accounts into Chrome profiles. You only
touch API auth when a run plants content through the API (for example a guide whose setup
step sends the bait email as the attacker).

## Accounts and roles

The tool models up to five accounts by **role** (`core/account_pool.py`,
`core/surface.py`). You do not need all of them — a single-account smoke run needs only the
first.

| Role | What it is | Browser profile env var | Default profile dir |
|---|---|---|---|
| `recipient` | the victim — Gemini operates on this account's Docs/Gmail/Sheets | `ADVTEST_PLAYWRIGHT_PROFILE_DIR` | `~/.advtest/playwright-profile` |
| `attacker_sender` | plants poisoned content into the recipient's surfaces | `ADVTEST_PROFILE_ATTACKER_SENDER` | `~/.advtest/profile-attacker-sender` |
| `bystander` | a third party | `ADVTEST_PROFILE_BYSTANDER` | `~/.advtest/profile-bystander` |
| gmail sender | UI-only Gmail bait sender | `ADVTEST_PROFILE_GMAIL_SENDER` | `~/.advtest/profile-gmail-sender` |
| outlook sender | UI-only Outlook bait sender (Outlook has no API path) | `ADVTEST_PROFILE_OUTLOOK_SENDER` | `~/.advtest/profile-outlook-sender` |

For API ops each role also has its own OAuth token cache: `recipient` uses
`ADVTEST_OAUTH_TOKEN_CACHE` (default `~/.advtest/oauth-token.json`); `attacker_sender` uses
`ADVTEST_OAUTH_TOKEN_ATTACKER_SENDER` (default `~/.advtest/oauth-token-attacker-sender.json`);
`bystander` uses `ADVTEST_OAUTH_TOKEN_BYSTANDER`. This is how a run can plant a bait email
*as the attacker* while the side panel runs *as the recipient*.

---

## Path A — browser (`ui_mode`) sign-in  ·  the common case

Each account is signed into its own persistent Chrome profile **once**, by hand. There is a
tool for exactly this: `scripts/setup_playwright_profile.py`. It opens a real Chrome against
the profile dir and waits — **you sign in, then close the window; closing is the "done"
signal.**

```bash
# Recipient (the Gemini account) — signs into ~/.advtest/playwright-profile
poetry run python scripts/setup_playwright_profile.py --service google

# Only if your run uses them:
poetry run python scripts/setup_playwright_profile.py --service gmail    # Gmail sender profile
poetry run python scripts/setup_playwright_profile.py --service outlook  # Outlook sender profile
```

While the window is open: sign fully into the account, then **open a Doc/Sheet/Gmail and
confirm the Gemini side panel opens** for that account. That confirms the account actually
has Gemini enabled, which is the usual reason a `ui_mode` run "connects but does nothing".
Then close the window.

Notes:
- `--service google|gmail|outlook` picks the default profile dir and sign-in URL for you.
  Use `--profile-dir` / `--start-url` to override; `--role` is just a label.
- The runtime launches **headed system Chrome** (`channel="chrome"`) against these dirs and
  keeps one browser open across a run, so sign-in cookies persist and Google's
  "unusual activity" defenses aren't tripped.
- **One `ui_mode` run at a time per profile.** Two runs sharing one Chrome profile corrupt
  it. For genuine parallelism, use separate profiles via the account pool.

That's all browser auth needs. No `ANTHROPIC_API_KEY`-adjacent Google keys, no
`ADVTEST_AUTH_MODE`. (You still need `ANTHROPIC_API_KEY` — that drives the attacker/judge,
not the target.)

---

## Path B — API auth  ·  only when a run uses the Google APIs

Set `ADVTEST_AUTH_MODE` to one of two modes. It is **required** for any API op — an unset
value raises with a hint.

### Option B1 — OAuth user flow (interactive, best for a single developer)

1. In **Google Cloud Console**, create an OAuth **Desktop app** client and download its
   `client_secret*.json`.
2. Point the tool at it and choose the mode:
   ```bash
   export ADVTEST_AUTH_MODE=oauth_user
   export ADVTEST_OAUTH_CLIENT_SECRETS=$HOME/.advtest/oauth-client.json   # the file from step 1
   export ADVTEST_OAUTH_TOKEN_CACHE=$HOME/.advtest/oauth-token.json       # optional; this is the default
   ```
3. The **first API operation** pops a browser consent screen (there is no separate bootstrap
   command — it happens lazily on first use). Approve it. The token is written to the cache
   and reused silently after that; expired tokens refresh on their own.

Scopes requested are least-privilege: Docs + `drive.file`, Gmail `gmail.modify`, Sheets +
`drive.file`. If you request a surface whose scope wasn't granted before, the tool discards
the cache and re-prompts rather than failing at runtime.

> `ADVTEST_OAUTH_CLIENT_SECRETS` is easy to miss — it was absent from the older
> `.env.example` and is now documented there. Without it, the first OAuth run can't start.

### Option B2 — service account (best for automation / a Workspace domain)

```bash
export ADVTEST_AUTH_MODE=service_account
export ADVTEST_SA_KEY_PATH=/path/to/service_account.json
export ADVTEST_SA_IMPERSONATE=user@yourdomain.com   # optional: domain-wide delegation
```

The service account must have the same three scope bundles pre-authorised in the Workspace
admin console. With `ADVTEST_SA_IMPERSONATE` set it acts as that user (domain-wide
delegation); without it, it acts as itself.

---

## What each kind of run actually requires

| Run | Browser sign-in | `ADVTEST_AUTH_MODE` + Google API creds | `ANTHROPIC_API_KEY` | `GEMINI_API_KEY` |
|---|---|---|---|---|
| Dashboard only | — | — | — | — |
| API-mode attack (Gemini via API) | — | **yes** | **yes** | **yes** |
| `ui_mode` attack (Gemini side panel) | **yes** (recipient) | only if a setup step calls an API | **yes** | — |
| `make fission` full e2e | **yes** (recipient + gmail + outlook) | only for API setup steps | **yes** | — |

The recurring gotchas: the side panel **is** the model in `ui_mode`, so no `GEMINI_API_KEY`
there; and a guide's setup step that plants bait through the Gmail API still needs API auth
even though the attack itself is browser-driven.

---

## Solo-developer quick sequence

1. `cp .env.example .env`; set `ANTHROPIC_API_KEY`.
2. Browser runs: `poetry run python scripts/setup_playwright_profile.py --service google`
   (add `--service gmail` / `--service outlook` if your run sends bait). Sign in, confirm
   the Gemini side panel opens, close the window.
3. API runs: set `ADVTEST_AUTH_MODE` and the matching creds (B1 or B2). The first OAuth call
   opens a consent browser and writes the token cache.
4. Run something — [GETTING_STARTED.md](GETTING_STARTED.md) §4–6.
