# Garmin Rate Limit Tracker

## Current Status
- **Status:** LOCKED (re-triggered again)
- **First locked:** ~2026-03-23 (Monday)
- **Last retry:** 2026-03-29 (Sunday ~19:35) — 2 attempts, re-triggered rate limit
- **Unlock ETA:** Monday 2026-03-30 morning (wait overnight)

## ⚠️ Important: Retrying Makes It Worse
Every failed login attempt (429) likely resets Garmin's lockout timer.
**Do not retry until a full 24–48 hours of zero attempts have passed.**

## What We've Tried
| Date | Action | Result |
|------|--------|--------|
| Mon 2026-03-23 | Multiple login attempts via app | 429 rate limited |
| Thu 2026-03-26 | Switched to `garminconnect` react branch (v0.2.41) | Still 429 |
| Thu 2026-03-26 | Changed WiFi to phone hotspot | Still 429 (rate limit is account-based, not IP) |
| Thu 2026-03-26 | Tried seeding session via JWT_WEB browser cookie | 500 from Garmin refresh endpoint |
| Thu 2026-03-26 | Multiple retry attempts throughout the day | Likely reset the timer each time |
| Sat 2026-03-28 | Library attempt — login SUCCESSFUL but `_establish_session` failed (missing JWT/CSRF in response) | Rate limit cleared but new bug found |
| Sat 2026-03-28 | Debug script attempt with wrong service URL | Re-triggered 429 immediately |
| Sun 2026-03-29 | Manual script: SSO login OK (200), JWT_WEB cookie set, but di-oauth/refresh returned `{}` | Rate limit cleared but csrf_token missing |
| Sun 2026-03-29 | Library attempt immediately after manual script | Re-triggered 429 |

## What's Been Fixed
- Upgraded to `garminconnect` react branch — bypasses Cloudflare bot protection on login
- Updated `services/garmin.py` to use `self.client` instead of `self.garth` (API change in new library)
- Session caching in DynamoDB — once a successful login happens, it won't need to re-login for weeks

## Next Steps
1. **Wait until Sunday 2026-03-29 morning** — full overnight rest, no attempts
2. Run `seed_garmin_session.py` — ONE attempt only, with your Clerk user ID in `.env` as `GARMIN_USER_ID`:
   ```
   export $(cat .env | xargs) && .venv/bin/python seed_garmin_session.py
   ```
3. The script will show exactly what `di-oauth/refresh` returns so we can fix the JWT issue
4. If it works, session is saved to DynamoDB — then follow the onboarding plan below
5. If still blocked, consider creating a second Garmin account for dev/testing

## Onboarding Plan (after session is seeded)
**Order matters — do not run onboarding before seeding the session.**

1. Run `seed_garmin_session.py` with `GARMIN_USER_ID` set to your Clerk user ID
2. Clear your existing DynamoDB user data (profile, strava creds, activities, sync markers, chat, plan)
3. Go through onboarding from the frontend — select Garmin, enter credentials
4. `POST /connect-garmin` calls `garmin_client.connect()` → finds cached session in DynamoDB → **no fresh SSO login** → safe from 429

**Why this works:** `connect()` checks DynamoDB session cache before attempting a real Garmin login.
If the session is already seeded, onboarding never touches Garmin's SSO endpoint.

## Root Cause
Garmin uses Cloudflare on `sso.garmin.com/mobile/api/login` which blocks automated login attempts.
The react branch of `python-garminconnect` mimics the Android app User-Agent to bypass this,
but repeated 429s during development have locked the account.
