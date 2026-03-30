# AI Running Coach — API Documentation

## Overview

The AI Running Coach backend is a FastAPI application that provides personalised running coaching powered by fitness data and Amazon Bedrock (Claude Haiku 4.5). The API is deployed as an AWS Lambda function via Mangum and can also be run locally with uvicorn.

The backend supports **two data sources**:
- **Garmin Connect** — full data: activities, sleep, resting HR, training load
- **Strava** — activity data only (sleep and resting HR not available via Strava)

**Base URL (local):** `http://localhost:8000`

**Content-Type:** All requests and responses use `application/json`.

---

## Authentication

The API does not currently enforce authentication middleware. The frontend is expected to pass the **Clerk `userId`** as the `user_id` field in every request body or query parameter. This value is used as the partition key for all DynamoDB records (stored as `USER#<userId>`).

> **Planned:** A future middleware layer will validate the Clerk session token from the `Authorization: Bearer <token>` header and verify that the `user_id` in the request matches the authenticated Clerk user before processing.

---

## Onboarding Flows

### Garmin Flow

1. **`POST /connect-garmin`** — link Garmin account, sets `onboardingStatus` to `"garmin_connected"`, `dataSource` to `"garmin"`
2. **`GET /user/status`** — frontend checks status on load to decide which screen to show
3. **`POST /chat/stream`** — onboarding agent asks 4 questions one at a time, saving each answer
4. After the 4th answer, the agent calls `complete_onboarding()` internally — sets `onboardingStatus` to `"complete"` and triggers background training plan generation
5. Frontend detects `"complete"` (via `GET /user/status` after stream `[DONE]`) and transitions to coaching view
6. **`GET /training-plan`** — fetch the generated plan and display it

### Strava Flow

1. Frontend redirects user to Strava's OAuth authorization URL (see below), passing `user_id` as the `state` param
2. User authorises on Strava
3. **`GET /auth/strava/callback`** — Strava redirects here with `code` and `state`. Backend exchanges code for tokens, saves credentials, creates profile with `dataSource="strava"`, pre-caches last 28 days of activities to DynamoDB, then redirects browser to `FRONTEND_URL/chat`
4. **`GET /user/status`** — returns `"garmin_connected"` (same status, different source), `data_source: "strava"`
5. **`POST /chat/stream`** — onboarding agent collects name, goal, race date, days per week
6. After onboarding completes, training plan is generated in the background using Strava activity data to calibrate starting mileage
7. Coaching agent uses Strava tools (no sleep or HR data available)

**Strava OAuth authorization URL** (frontend initiates this redirect):
```
https://www.strava.com/oauth/authorize
  ?client_id=<STRAVA_CLIENT_ID>
  &redirect_uri=<API_BASE_URL>/auth/strava/callback
  &response_type=code
  &approval_prompt=auto
  &scope=activity:read_all
  &state=<clerk_user_id>
```

### Onboarding questions (both flows, in order)
1. Name
2. Goal — First 5K / First 10K / First half marathon / First marathon / Just run consistently
3. Target race date — skipped if goal is "Just run consistently"
4. Days per week — 3, 4, or 5

---

## Endpoints

### `GET /user/status`

Returns the user's onboarding status and data source. Call this on app load to decide which screen to show, and after each onboarding stream completes to detect when onboarding finishes.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `onboarding_status` | `string` | `"not_found"` · `"garmin_connected"` · `"complete"` |
| `data_source` | `string` | `"garmin"` or `"strava"` (default `"garmin"`) |

**Status meanings:**
- `"not_found"` → no profile exists, show Connect screen (Garmin or Strava)
- `"garmin_connected"` → data source linked but onboarding not finished, go to chat
- `"complete"` → onboarding done, go to coaching chat

#### Example Response

```json
{
  "onboarding_status": "complete",
  "data_source": "strava"
}
```

---

### `POST /connect-garmin`

Links a Garmin Connect account to the user and initialises their profile. Validates credentials by attempting a real Garmin login before saving — returns `401` immediately if wrong. On success, password is encrypted with AWS KMS before being stored.

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |
| `garmin_email` | `string` | Yes | Garmin Connect email |
| `garmin_password` | `string` | Yes | Garmin Connect password (encrypted at rest via KMS) |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `success` | `boolean` | `true` if connected successfully |
| `message` | `string` | Human-readable confirmation |

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `401` | `"Invalid Garmin credentials..."` | Wrong email or password |
| `429` | `"Garmin is temporarily rate limiting connections..."` | Too many login attempts — wait and retry |
| `500` | `"Server configuration error."` | `KMS_KEY_ID` env var not set |
| `500` | `"Failed to secure credentials. Please try again."` | KMS encryption failed |
| `500` | `"Failed to save credentials. Please try again."` | DynamoDB write failed |
| `503` | `"Unable to connect to Garmin. Please try again."` | Transient Garmin error |

#### Example Request

```json
POST /connect-garmin
{
  "user_id": "user_2abc123def456",
  "garmin_email": "runner@example.com",
  "garmin_password": "my-garmin-password"
}
```

#### Example Response

```json
{
  "success": true,
  "message": "Garmin connected. Starting onboarding."
}
```

---

### `GET /auth/strava/callback`

Strava OAuth callback endpoint — Strava redirects here after the user authorises. Exchanges the authorization code for tokens, saves credentials to DynamoDB, creates a profile with `dataSource="strava"`, and redirects the browser to `FRONTEND_URL/chat`.

**The frontend does not call this directly.** Strava calls it after the user approves access.

#### Query Parameters

| Parameter | Type | Description |
|---|---|---|
| `code` | `string` | Authorization code from Strava |
| `state` | `string` | Clerk `user_id` passed as the OAuth state param |
| `error` | `string` | Set by Strava if the user denied access |

#### Response

- **Success** → `302` redirect to `FRONTEND_URL/chat`
- **Denied** → `302` redirect to `FRONTEND_URL?strava_error=access_denied`
- **502** → Strava token exchange failed (Strava API error)

---

### `POST /auth/strava/refresh`

Refreshes a Strava access token if it has expired. Called automatically by the chat stream — frontends do not need to call this directly.

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `refreshed` | `boolean` | `true` if token was refreshed, `false` if still valid |
| `message` | `string` | Human-readable status |

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `404` | `"No Strava credentials found for this user."` | User has not connected Strava |
| `502` | `"Failed to refresh Strava token."` | Strava API error |

---

### `POST /activities/sync`

Fetches activities for a specific date range from the user's data source (Strava), caches them to DynamoDB, and returns the results. Call this when the user navigates to a new calendar month.

> **Strava only.** Garmin activities are cached automatically during chat sessions.

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |
| `since` | `string` | Yes | Start date inclusive (`YYYY-MM-DD`) |
| `until` | `string` | Yes | End date inclusive (`YYYY-MM-DD`) |

#### Response Body — `200 OK`

Same shape as `GET /activities` — `{ activities: ActivitySummary[] }`.

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `400` | `"On-demand sync is only supported for Strava users."` | User has Garmin as data source |
| `404` | `"User not found."` | No profile for this `user_id` |
| `404` | `"Strava credentials not found."` | User has not connected Strava |
| `502` | `"Failed to refresh Strava token."` | Strava token refresh failed |

#### Example Request

```json
POST /activities/sync
{
  "user_id": "user_2abc123def456",
  "since": "2026-02-01",
  "until": "2026-02-28"
}
```

#### Frontend Usage

```js
// Called when user navigates to a new month
async function syncMonth(userId, year, month) {
  const since = `${year}-${String(month).padStart(2, '0')}-01`;
  const lastDay = new Date(year, month, 0).getDate();
  const until = `${year}-${String(month).padStart(2, '0')}-${lastDay}`;

  const res = await fetch("http://localhost:8000/activities/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, since, until }),
  });
  const { activities } = await res.json();
  return activities;
}
```

---

### `GET /activities`

Returns cached activity records for a user. Reads from DynamoDB only — no live API call.

**When activities are cached:**
- **Strava:** Pre-cached immediately on `GET /auth/strava/callback` (28 days). Refreshed on every chat session and on `POST /activities/sync`.
- **Garmin:** Cached on every chat session when the agent calls `get_recent_activities`.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |
| `since` | `string` | No | ISO date (`YYYY-MM-DD`). Only return activities on or after this date. |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `activities` | `array` | List of activity summaries sorted by date ascending |
| `activities[].date` | `string` | ISO date (`YYYY-MM-DD`) |
| `activities[].type` | `string` | `"running"`, `"cycling"`, etc. |
| `activities[].distance_km` | `number` | Distance in kilometres |
| `activities[].duration_min` | `integer\|null` | Elapsed time in minutes |
| `activities[].avg_pace` | `string\|null` | Average pace per km (`"M:SS"`) |

#### Example Response

```json
{
  "activities": [
    {
      "date": "2026-03-18",
      "type": "running",
      "distance_km": 10.2,
      "duration_min": 52,
      "avg_pace": "5:06"
    }
  ]
}
```

> **Calendar checkmarks:** Cross-reference `activities[].date` against plan days. If a date has a `"running"` activity and a matching plan day, show the checkmark.

---

### `POST /chat/stream`

Sends a message to the AI coach and streams the response token by token using Server-Sent Events (SSE).

**Routing logic:**
- `onboardingStatus = "garmin_connected"` → **onboarding agent** (same for Garmin and Strava)
- `onboardingStatus = "complete"` + `dataSource = "garmin"` → **Garmin coaching agent**
- `onboardingStatus = "complete"` + `dataSource = "strava"` → **Strava coaching agent**

**Garmin coaching agent** calls:
- `get_recent_activities` — last 28 days of runs (fresh 14 days + DynamoDB cache)
- `get_sleep_data` — last 7 nights
- `get_training_load` — training status and recovery metrics
- `get_heart_rate` — resting HR and 7-day trends

**Strava coaching agent** calls:
- `get_recent_activities` — last 28 days from Strava API
- `get_sleep_data` — returns a note that sleep is unavailable via Strava
- `get_training_load` — Strava athlete stats (recent and YTD totals)
- `get_heart_rate` — returns a note that resting HR is unavailable via Strava

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |
| `message` | `string` | Yes | The user's message |
| `timezone` | `string` | No | IANA timezone string (default `"Australia/Melbourne"`). Use `Intl.DateTimeFormat().resolvedOptions().timeZone`. |

#### Response — `text/event-stream`

```
data: Good session yesterday.\n\n
data:  Take it easy today.\n\n
data: [DONE]\n\n
```

`[DONE]` signals completion. `[ERROR]` signals failure mid-stream.

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `404` | `"User not found..."` | No profile for this `user_id` |

#### Frontend Usage

```js
useEffect(() => {
  const controller = new AbortController();

  async function startStream() {
    const res = await fetch("http://localhost:8000/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id,
        message,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
      signal: controller.signal,
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value);
      for (const line of text.split("\n")) {
        if (line.startsWith("data: ")) {
          const chunk = line.slice(6);
          if (chunk === "[DONE]" || chunk === "[ERROR]") return;
          appendToMessage(chunk);
        }
      }
    }
  }

  startStream();
  return () => controller.abort(); // prevents double-stream in React StrictMode
}, []);
```

#### After Onboarding Completes

When the onboarding agent finishes, it calls `complete_onboarding()` internally. After receiving `[DONE]`:

1. Call `GET /user/status` — if `"complete"`, onboarding finished
2. Transition to coaching chat view
3. Call `GET /training-plan` to load and display the plan (poll every 5s until `weeks` has data)

---

### `GET /chat/history`

Returns saved chat history for a user, newest first.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |
| `limit` | `integer` | No | Max messages to return (default `50`) |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `messages` | `array` | Newest first |
| `messages[].role` | `string` | `"user"` or `"assistant"` |
| `messages[].message` | `string` | Message content |
| `messages[].timestamp` | `string` | ISO datetime (UTC) |

---

### `POST /training-plan/generate`

Generates a complete multi-week training block. Normally triggered automatically when onboarding completes. Use this to regenerate on demand.

**Plan length by goal:**

| Goal | Weeks |
|---|---|
| First 5K | 8 |
| First 10K | 10 |
| First half marathon | 12 |
| First marathon | 18 |
| Just run consistently | 8 |

If the user has a target race date, the plan runs from next Monday to race day, capped at the goal default. Always starts on a Monday.

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `weeks` | `array` | All `PlanWeek` objects sorted by `week_start` ascending |
| `weeks[].week_start` | `string` | ISO date of the Monday this week starts |
| `weeks[].days` | `array` | 7 `PlanDay` objects |

**`PlanDay` object:**

| Field | Type | Description |
|---|---|---|
| `date` | `string` | `YYYY-MM-DD` |
| `week_start` | `string` | `YYYY-MM-DD` (Monday) |
| `type` | `string` | `intervals`, `tempo`, `threshold`, `fartlek`, `easy`, `long`, or `rest` |
| `distance` | `number` | Kilometres (`0` for rest) |
| `description` | `string` | Specific workout description |

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `404` | `"User profile not found..."` | No profile |
| `404` | `"User credentials not found..."` | No credentials (Garmin only) |
| `503` | `"Unable to retrieve Garmin credentials..."` | KMS failure (Garmin only) |
| `503` | `"Unable to connect to Garmin..."` | Garmin auth failed (Garmin only) |
| `500` | `"Failed to generate training plan..."` | Agent or JSON parse error |

> **Note:** `POST /training-plan/generate` is Garmin-only. For Strava users, the plan is generated automatically in the background after onboarding completes via `POST /chat/stream`.

---

### `GET /training-plan`

Returns all saved training plan days for the user, grouped by week.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |

#### Response Body — `200 OK`

Same shape as `POST /training-plan/generate` — `{ weeks: PlanWeek[] }`.

---

### `DELETE /conversation`

Deletes all chat history for a user. Profile and credentials are preserved.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |

#### Response Body — `200 OK`

```json
{ "success": true, "message": "Conversation cleared." }
```

---

### `DELETE /user`

Deletes all DynamoDB data for a user — profile, Garmin credentials, Strava credentials, Garmin session cache, chat history, training plan, and cached activities. The user will need to complete onboarding again.

> **Note:** Does not delete the Clerk account. Handle that on the frontend with `deleteUser()`.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId |

#### Response Body — `200 OK`

```json
{ "success": true, "message": "User data deleted. Please complete onboarding again." }
```

> **Frontend:** After this, call `GET /user/status` — it returns `"not_found"`. Clear all local state and redirect to the connect screen.

---

### `GET /health`

Health check.

#### Response — `200 OK`

```json
{ "status": "ok" }
```

---

## DynamoDB Schema

Single-table design. Partition key: `PK = USER#<userId>`. Sort key: `SK`.

| SK | Contents |
|---|---|
| `PROFILE` | `onboardingStatus`, `dataSource`, `name`, `goal`, `targetRaceDate`, `daysPerWeek`, `createdAt` |
| `CREDENTIALS` | `garminEmail`, `garminPasswordEncrypted` (KMS), `kmsKeyId` |
| `GARMIN_SESSION` | `sessionData` — JSON string `{jwt_web, csrf_token, cookies}`. JWT expires every ~2 hours; backend auto re-logins on expiry using stored credentials. |
| `STRAVA_CREDENTIALS` | `athleteId`, `accessToken`, `refreshToken`, `expiresAt` |
| `CHAT#<timestamp>` | `role`, `message`, `conversationId` |
| `PLAN#<date>` | `weekStart`, `type`, `distance`, `description` |
| `ACTIVITY#<date>#<id>` | `date`, `type`, `distance_km`, `avg_pace_per_km`, `avg_hr`, etc. |
| `SYNC#<YYYY-MM>` | Presence marker — written once per month when Strava activities are first fetched. Prevents redundant Strava API calls on subsequent calendar views. |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MODEL_ID` | No | Bedrock model ID (default `au.anthropic.claude-haiku-4-5-20251001-v1:0`) |
| `AWS_REGION` | No | AWS region (default `ap-southeast-2`) |
| `DYNAMODB_TABLE` | No | DynamoDB table name (default `ai-running-coach`) |
| `KMS_KEY_ID` | **Yes** (Garmin) | KMS key for encrypting Garmin passwords |
| `STRAVA_CLIENT_ID` | **Yes** (Strava) | Strava API app client ID |
| `STRAVA_CLIENT_SECRET` | **Yes** (Strava) | Strava API app client secret |
| `FRONTEND_URL` | **Yes** (Strava) | Base URL for Strava OAuth redirect (e.g. `https://yourapp.com`) |
| `GARMIN_EMAIL` | Dev only | Garmin account email — used by `seed_garmin_session.py` to seed an initial session |
| `GARMIN_PASSWORD` | Dev only | Garmin account password — used by `seed_garmin_session.py` |
| `GARMIN_USER_ID` | Dev only | Clerk userId to associate the seeded session with — used by `seed_garmin_session.py` |

---

## Error Response Format

```json
{
  "detail": "Human-readable error message."
}
```

---

## Local Development

```bash
set -a && source .env && set +a
uvicorn main:app --reload
```

Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)
