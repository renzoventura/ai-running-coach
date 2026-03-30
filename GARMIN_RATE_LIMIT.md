# Garmin Rate Limit & Session Tracker

## Current Status
- **Status:** ✅ RESOLVED — session working as of 2026-03-30
- **First locked:** ~2026-03-23 (Monday)
- **Resolved:** 2026-03-30 (Monday night) — JWT_WEB session seeded, browser headers fix applied
- **Next action:** None — auto re-login on JWT expiry is handled in code

---

## How the Session Works (as of 2026-03-30)

Garmin uses two layers of protection that we had to solve separately:

### Layer 1: SSO Login (Cloudflare bot protection)
`sso.garmin.com/mobile/api/login` is protected by Cloudflare. It blocks Python `requests` with standard headers.

**Solution:** The react branch of `python-garminconnect` (v0.2.41) mimics an Android app User-Agent (`Dalvik/2.1.0 ... GarminConnect/4.74.1`), which bypasses this check. The SSO login returns a service ticket on success.

### Layer 2: API calls (TLS fingerprinting + browser header checks)
`connect.garmin.com/gc-api/` routes to `connectapi.garmin.com`. Even with a valid session, API calls return 403 without the right browser identity headers.

**Solution:** After restoring the session, we patch the library's internal `requests.Session` with:
```python
"sec-ch-ua": '"Google Chrome";v="131", "Not_A Brand";v="24"'
"sec-ch-ua-platform": '"Windows"'
"sec-fetch-mode": "cors"
"sec-fetch-site": "same-origin"
"sec-fetch-dest": "empty"
"X-Requested-With": "XMLHttpRequest"
```

### Layer 3 (potential future issue): TLS fingerprinting
The project [garmin-connect-mcp](https://github.com/etweisberg/garmin-connect-mcp) uses a full Chromium browser (Playwright) to bypass Cloudflare's **TLS fingerprinting** — Garmin can detect non-browser TLS handshakes at the TCP layer, before any HTTP headers are sent. Our `requests`-based approach spoofs HTTP headers but NOT the TLS fingerprint. This works currently but could break if Garmin tightens enforcement.

**If we start getting unexplained 403s again despite correct headers, TLS fingerprinting is the next thing to investigate.** The fix would be to route requests through a real browser (Playwright/Pyppeteer) or a TLS-spoofing library like `curl_cffi`.

### Session lifecycle
- JWT_WEB expires every **~2 hours**
- On expiry, `_try_restore()` detects it (decodes JWT `exp` claim) and returns `False`
- `connect()` falls through to `_full_login()` — does a fresh SSO login silently
- New session saved to DynamoDB and in-memory cache
- No manual intervention needed

---

## History of Attempts

| Date | Action | Result |
|------|--------|--------|
| Mon 2026-03-23 | Multiple login attempts via app | 429 rate limited |
| Thu 2026-03-26 | Switched to `garminconnect` react branch (v0.2.41) | Still 429 |
| Thu 2026-03-26 | Changed WiFi to phone hotspot | Still 429 — rate limit is account-based, not IP |
| Thu 2026-03-26 | Tried seeding session via JWT_WEB browser cookie | 500 from Garmin refresh endpoint |
| Thu 2026-03-26 | Multiple retry attempts throughout the day | Likely reset the timer each time |
| Sat 2026-03-28 | Library attempt — login SUCCESSFUL but `_establish_session` failed | Rate limit cleared but `di-oauth/refresh` returned `{}` |
| Sat 2026-03-28 | Debug script with wrong service URL | Re-triggered 429 immediately |
| Sun 2026-03-29 | Manual script: SSO login OK, JWT_WEB cookie set, `di-oauth/refresh` returned `{}` | csrf_token missing |
| Sun 2026-03-29 | Library attempt immediately after manual script | Re-triggered 429 |
| Mon 2026-03-30 | `seed_garmin_session.py` — SSO login OK, CSRF found in HTML meta tag | Session saved to DynamoDB ✅ |
| Mon 2026-03-30 | Discovered API calls returning 403 despite valid session | Missing `sec-ch-ua` browser headers |
| Mon 2026-03-30 | Patched `requests.Session` with browser identity headers in `_try_restore()` | 36 activities returned ✅ |

---

## What Was Fixed in Code

| File | Change |
|------|--------|
| `requirements.txt` | Pin `garminconnect` to react branch (v0.2.41) — bypasses Cloudflare on SSO login |
| `services/garmin.py` | Use `self.client` instead of `self.garth` (API change in react branch) |
| `services/garmin.py` | `_try_restore()` checks JWT expiry before restore — forces re-login when expired |
| `services/garmin.py` | Patch `client.client.cs.headers` with browser identity headers after every restore or fresh login |
| `seed_garmin_session.py` | One-shot session seeder — logs full diagnostics to `garmin_debug_dump.json`, saves session even without csrf_token |

---

## If It Breaks Again

### 429 Rate Limit
- **Do not retry.** Each failed attempt resets the timer.
- Wait a full 24–48 hours with zero attempts.
- Run `seed_garmin_session.py` once:
  ```bash
  export $(cat .env | grep -v '^#' | xargs) && .venv/bin/python seed_garmin_session.py
  ```
- Check `garmin_debug_dump.json` for the full response at each step.

### 403 on API calls
- Check that `_BROWSER_HEADERS` are being applied in `_try_restore()` and `_full_login()`.
- If headers are fine, Garmin may have tightened TLS fingerprinting — see Layer 3 above.
- Reference: https://github.com/etweisberg/garmin-connect-mcp uses Playwright as a workaround.

### Empty data (no 403, but activities/sleep return nothing)
- JWT may be expired — check `exp` in the JWT_WEB from DynamoDB session.
- Delete `GARMIN_SESSION` from DynamoDB to force a fresh login on next request.

---

## Key Technical Notes

- `di-oauth/refresh` returns `{}` (201) for our session — the csrf_token does NOT come from there
- The real csrf_token is in the HTML meta tag: `<meta name="csrf-token" content="..."/>`
- This HTML csrf_token works for `/gc-api/` API calls when combined with the browser identity headers
- Rate limiting is **account-level**, not IP-based — changing networks has no effect
- The session cookie (`Fe26.2*...`) is a Hapi.js iron-encrypted cookie — we cannot decrypt it
