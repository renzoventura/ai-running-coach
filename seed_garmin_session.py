"""
One-shot Garmin session seeder with full diagnostic dump.

Saves EVERYTHING to garmin_debug_dump.json — all response bodies, headers,
cookies, and HTML — so we can analyze without needing another login attempt.

Also saves a partial session to DynamoDB if jwt_web is available, even without
csrf_token. The app will attempt to fetch csrf_token at restore time.

Run ONCE and wait for the output before retrying anything.

Usage:
    python seed_garmin_session.py
"""
import json
import os
import re
import sys
import time

import requests

DUMP_FILE = "garmin_debug_dump.json"


def save_dump(dump: dict) -> None:
    with open(DUMP_FILE, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"  [DUMP] Full diagnostics saved to {DUMP_FILE}")


def capture_response(r: requests.Response) -> dict:
    """Capture everything from a response."""
    try:
        body_json = r.json()
    except Exception:
        body_json = None
    return {
        "url": r.url,
        "status_code": r.status_code,
        "headers": dict(r.headers),
        "cookies": dict(r.cookies),
        "body_text": r.text[:10000],
        "body_json": body_json,
    }


def main() -> None:
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    user_id = os.environ.get("GARMIN_USER_ID")

    if not email or not password or not user_id:
        print("ERROR: GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_USER_ID must be set in .env")
        sys.exit(1)

    CONNECT = "https://connect.garmin.com"
    SSO = "https://sso.garmin.com"
    CLIENT_ID = "GarminConnect"
    SERVICE_URL = f"{CONNECT}/app/"

    dump: dict = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "email": email,
        "user_id": user_id,
        "steps": {},
    }

    # --- Step 1: Mobile SSO login ---
    print("Step 1: Mobile SSO login...")
    mobile_sess = requests.Session()
    mobile_sess.headers.update({
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13; Pixel 6 Build/TQ3A.230901.001) GarminConnect/4.74.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    r_signin = mobile_sess.get(f"{SSO}/mobile/sso/en/sign-in", params={"clientId": CLIENT_ID})
    dump["steps"]["1a_signin_get"] = capture_response(r_signin)
    dump["steps"]["1a_signin_cookies"] = dict(mobile_sess.cookies)
    save_dump(dump)

    r_login = mobile_sess.post(
        f"{SSO}/mobile/api/login",
        params={"clientId": CLIENT_ID, "locale": "en-US", "service": SERVICE_URL},
        json={"username": email, "password": password, "rememberMe": False, "captchaToken": ""},
    )
    dump["steps"]["1b_login_post"] = capture_response(r_login)
    dump["steps"]["1b_mobile_cookies_after"] = dict(mobile_sess.cookies)
    save_dump(dump)

    print(f"  Status: {r_login.status_code}")
    res = r_login.json()
    resp_type = res.get("responseStatus", {}).get("type")
    print(f"  Response type: {resp_type}")

    if r_login.status_code == 429 or (isinstance(res.get("error"), dict) and res["error"].get("status-code") == "429"):
        print("BLOCKED: Still rate limited. Check garmin_debug_dump.json for full response.")
        save_dump(dump)
        sys.exit(1)

    if resp_type != "SUCCESSFUL":
        print(f"FAILED: Unexpected response — {res}")
        save_dump(dump)
        sys.exit(1)

    ticket = res["serviceTicketId"]
    print(f"  Ticket: {ticket[:20]}...")
    dump["steps"]["1c_ticket"] = ticket

    # --- Step 2: Establish web session ---
    print("Step 2: Establishing web session via ticket redirect...")
    web_sess = requests.Session()
    web_sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    r2 = web_sess.get(SERVICE_URL, params={"ticket": ticket}, allow_redirects=True)
    dump["steps"]["2_ticket_redirect"] = capture_response(r2)
    dump["steps"]["2_redirect_history"] = [
        {"url": h.url, "status": h.status_code, "headers": dict(h.headers)}
        for h in r2.history
    ]
    dump["steps"]["2_web_cookies"] = dict(web_sess.cookies)
    save_dump(dump)

    print(f"  Status: {r2.status_code}, Final URL: {r2.url[:80]}")
    all_cookies = web_sess.cookies.get_dict()
    print(f"  Cookies set: {list(all_cookies.keys())}")

    jwt_from_cookie = all_cookies.get("JWT_WEB")
    if jwt_from_cookie:
        print(f"  JWT_WEB from cookie: {jwt_from_cookie[:30]}...")
    else:
        print("  WARNING: No JWT_WEB cookie set")

    # Scan HTML for any tokens
    html2 = r2.text
    dump["steps"]["2_html"] = html2[:20000]
    csrf_from_html = None
    for pattern in [
        r'"csrfToken"\s*:\s*"([^"]+)"',
        r'"csrf_token"\s*:\s*"([^"]+)"',
        r'csrf-token["\s]+content="([^"]+)"',
        r'name="csrf[_-]token"\s+content="([^"]+)"',
        r'"CSRF_TOKEN"\s*:\s*"([^"]+)"',
        r'window\.__CSRF[_A-Z]*\s*=\s*["\']([^"\']+)',
    ]:
        m = re.search(pattern, html2, re.IGNORECASE)
        if m:
            csrf_from_html = m.group(1)
            print(f"  Found CSRF in step-2 HTML: {csrf_from_html[:30]}...")
            break
    dump["steps"]["2_csrf_from_html"] = csrf_from_html
    save_dump(dump)

    # --- Step 3: di-oauth/refresh ---
    print("Step 3: Calling di-oauth/refresh...")
    r3 = web_sess.post(
        f"{CONNECT}/services/auth/token/di-oauth/refresh",
        headers={"Accept": "application/json", "NK": "NT", "Referer": f"{CONNECT}/modern/"},
    )
    dump["steps"]["3_di_oauth_refresh"] = capture_response(r3)
    dump["steps"]["3_web_cookies_after"] = dict(web_sess.cookies)
    save_dump(dump)

    print(f"  Status: {r3.status_code}")
    print(f"  Response body: {r3.text[:500]}")
    print(f"  Response headers: {json.dumps(dict(r3.headers), indent=2)}")
    print(f"  Cookies after: {list(web_sess.cookies.get_dict().keys())}")

    jwt_data = r3.json() if r3.text.strip() and r3.text.strip() != "{}" else {}
    jwt_web = jwt_data.get("encryptedToken") or jwt_from_cookie
    csrf_token = jwt_data.get("csrfToken") or jwt_data.get("csrf_token") or csrf_from_html

    # --- Step 4: Fetch /modern page ---
    print("Step 4: Fetching /modern page...")
    r4 = web_sess.get(f"{CONNECT}/modern/", headers={"Accept": "text/html"})
    dump["steps"]["4_modern_page"] = capture_response(r4)
    dump["steps"]["4_modern_html"] = r4.text[:20000]
    dump["steps"]["4_modern_cookies"] = dict(web_sess.cookies)
    save_dump(dump)

    print(f"  Status: {r4.status_code}")
    html4 = r4.text
    if not csrf_token:
        for pattern in [
            r'"csrfToken"\s*:\s*"([^"]+)"',
            r'"csrf_token"\s*:\s*"([^"]+)"',
            r'csrf-token["\s]+content="([^"]+)"',
            r'"CSRF_TOKEN"\s*:\s*"([^"]+)"',
            r'window\.__CSRF[_A-Z]*\s*=\s*["\']([^"\']+)',
        ]:
            m = re.search(pattern, html4, re.IGNORECASE)
            if m:
                csrf_token = m.group(1)
                print(f"  Found CSRF in /modern HTML: {csrf_token[:30]}...")
                break
    dump["steps"]["4_csrf_from_html"] = csrf_token
    save_dump(dump)

    # --- Step 5: Fetch /app/ page ---
    print("Step 5: Fetching /app/ page for additional tokens...")
    r5 = web_sess.get(f"{CONNECT}/app/", headers={"Accept": "text/html"})
    dump["steps"]["5_app_page"] = capture_response(r5)
    dump["steps"]["5_app_html"] = r5.text[:20000]
    dump["steps"]["5_app_cookies"] = dict(web_sess.cookies)
    save_dump(dump)

    if not csrf_token:
        for pattern in [
            r'"csrfToken"\s*:\s*"([^"]+)"',
            r'"csrf_token"\s*:\s*"([^"]+)"',
            r'"CSRF_TOKEN"\s*:\s*"([^"]+)"',
            r'window\.__CSRF[_A-Z]*\s*=\s*["\']([^"\']+)',
        ]:
            m = re.search(pattern, r5.text, re.IGNORECASE)
            if m:
                csrf_token = m.group(1)
                print(f"  Found CSRF in /app/ HTML: {csrf_token[:30]}...")
                break

    # --- Step 6: Try user profile API endpoint ---
    print("Step 6: Calling user profile API (may reveal csrf in response)...")
    api_headers = {
        "Accept": "application/json",
        "NK": "NT",
        "Referer": f"{CONNECT}/modern/",
    }
    if csrf_token:
        api_headers["connect-csrf-token"] = csrf_token
    r6 = web_sess.get(f"{CONNECT}/proxy/userprofile-service/socialProfile", headers=api_headers)
    dump["steps"]["6_profile_api"] = capture_response(r6)
    dump["steps"]["6_cookies_after"] = dict(web_sess.cookies)
    save_dump(dump)

    print(f"  Profile API status: {r6.status_code}")
    print(f"  Profile API body: {r6.text[:300]}")

    # Final state
    dump["final"] = {
        "jwt_web": jwt_web,
        "csrf_token": csrf_token,
        "all_cookies": web_sess.cookies.get_dict(),
        "has_jwt": bool(jwt_web),
        "has_csrf": bool(csrf_token),
    }
    save_dump(dump)

    print(f"\njwt_web: {'YES' if jwt_web else 'NO'}")
    print(f"csrf_token: {'YES' if csrf_token else 'NO'}")

    if not jwt_web:
        print("FAILED: No jwt_web available. Cannot save session.")
        sys.exit(1)

    if not csrf_token:
        print("WARNING: Saving partial session (no csrf_token). App will fetch it at restore time.")

    # --- Step 7: Save to DynamoDB ---
    print("\nStep 7: Saving session to DynamoDB...")
    session_data = json.dumps({
        "jwt_web": jwt_web,
        "csrf_token": csrf_token,
        "cookies": web_sess.cookies.get_dict(),
    })

    sys.path.insert(0, ".")
    from services.dynamodb import save_garmin_session
    ok = save_garmin_session(user_id, session_data)
    if ok:
        print(f"Done! Session saved for user {user_id}")
        print(f"Full debug data in {DUMP_FILE} — review it if the session doesn't work.")
    else:
        print("ERROR: Failed to save to DynamoDB.")
        sys.exit(1)


if __name__ == "__main__":
    main()
