# AI Running Coach — API Documentation

## Overview

The AI Running Coach backend is a FastAPI application that provides personalised running coaching powered by Garmin Connect data and Amazon Bedrock (Claude Haiku 4.5). The API is deployed as an AWS Lambda function via Mangum and can also be run locally with uvicorn.

**Base URL (local):** `http://localhost:8000`

**Content-Type:** All requests and responses use `application/json`.

---

## Authentication

The API does not currently enforce authentication middleware. The frontend is expected to pass the **Clerk `userId`** as the `user_id` field in every request body or query parameter. This value is used as the partition key for all DynamoDB records (stored as `USER#<userId>`).

> **Planned:** A future middleware layer will validate the Clerk session token from the `Authorization: Bearer <token>` header and verify that the `user_id` in the request matches the authenticated Clerk user before processing.

---

## Onboarding Flow

The full user journey after Clerk signup:

1. **`POST /connect-garmin`** — link Garmin account, sets `onboardingStatus` to `"garmin_connected"`
2. **`GET /user/status`** — frontend checks status on load to decide which screen to show
3. **`POST /chat/stream`** — onboarding agent asks 4 questions one at a time, saving each answer to DynamoDB
4. After the 4th answer, the agent calls `complete_onboarding()` internally — sets `onboardingStatus` to `"complete"` and triggers background training plan generation
5. Frontend detects `"complete"` status (via `GET /user/status` after stream `[DONE]`) and transitions to coaching view
6. **`GET /training-plan`** — fetch the generated plan and display it

Once `onboardingStatus` is `"complete"`, all subsequent `POST /chat/stream` messages are handled by the **coaching agent**, which has full access to the user's Garmin data and training history.

**Onboarding questions (in order):**
1. Name
2. Goal — First 5K / First 10K / First half marathon / First marathon / Just run consistently
3. Target race date — skipped if goal is "Just run consistently"
4. Days per week — 3, 4, or 5

---

## Endpoints

### `GET /user/status`

Returns the user's onboarding status. Call this on app load to decide which screen to show, and after each onboarding stream completes to detect when onboarding finishes.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `onboarding_status` | `string` | `"not_found"` · `"garmin_connected"` · `"complete"` |

**Status meanings:**
- `"not_found"` → no profile exists, show Connect Garmin screen
- `"garmin_connected"` → Garmin linked but onboarding not finished, go to chat (onboarding agent will guide them)
- `"complete"` → onboarding done, go to coaching chat

#### Example Request

```
GET /user/status?user_id=user_2abc123def456
```

#### Example Response

```json
{
  "onboarding_status": "complete"
}
```

---

### `POST /connect-garmin`

Links a Garmin Connect account to the user and initialises their profile. The Garmin password is encrypted with AWS KMS before being stored in DynamoDB. After this, send the user to `POST /chat/stream` where the onboarding agent will guide them through setup.

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |
| `garmin_email` | `string` | Yes | Garmin Connect account email address |
| `garmin_password` | `string` | Yes | Garmin Connect account password (encrypted at rest via KMS) |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `success` | `boolean` | `true` if Garmin was connected successfully |
| `message` | `string` | Human-readable confirmation |

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `500` | `"Server configuration error."` | `KMS_KEY_ID` environment variable not set |
| `500` | `"Failed to secure credentials. Please try again."` | KMS encryption failed |
| `500` | `"Failed to save credentials. Please try again."` | DynamoDB write failed for credentials |
| `500` | `"Failed to create profile. Please try again."` | DynamoDB write failed for profile |

#### Example Request

```json
POST /connect-garmin
Content-Type: application/json

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

### `GET /activities`

Returns cached Garmin activity records for a user. Reads from DynamoDB only — no Garmin API call. Activities are cached automatically each time the coaching agent calls `get_recent_activities` during a chat session.

Use this on calendar load to show checkmarks on days where a run was recorded. The cache covers the last 28 days and is refreshed on every coaching chat.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |
| `since` | `string` | No | ISO date (`YYYY-MM-DD`). Only return activities on or after this date. |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `activities` | `array` | List of activity summaries sorted by date ascending |
| `activities[].date` | `string` | ISO date the activity was recorded (`YYYY-MM-DD`) |
| `activities[].type` | `string` | Activity type e.g. `"running"`, `"cycling"` |
| `activities[].distance_km` | `number` | Distance in kilometres |
| `activities[].duration_min` | `integer\|null` | Elapsed time in minutes |
| `activities[].avg_pace` | `string\|null` | Average pace per km (`"M:SS"`) |

#### Example Request

```
GET /activities?user_id=user_2abc123def456&since=2026-03-01
```

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
    },
    {
      "date": "2026-03-20",
      "type": "running",
      "distance_km": 6.0,
      "duration_min": 32,
      "avg_pace": "5:20"
    }
  ]
}
```

> **Calendar checkmarks:** Cross-reference `activities[].date` against your plan days. If a date has an activity of type `"running"` and a matching plan day, show the checkmark.

> **Cache note:** The cache populates after the user's first coaching chat. If they've just completed onboarding, call `GET /activities` after the training plan is loaded — data may be empty until the first chat session.

---

### `POST /chat/stream`

Sends a message to the AI coach and streams the response token by token using Server-Sent Events (SSE). Routes to the **onboarding agent** if `onboardingStatus` is `"garmin_connected"`, or the **coaching agent** if `"complete"`. The conversation is saved to DynamoDB after the stream completes.

On each request the coaching agent:

1. Fetches Garmin credentials from DynamoDB and decrypts via KMS
2. Authenticates with Garmin Connect
3. Retrieves the last 20 messages of chat history for context
4. Runs the Strands agent (Claude Haiku 4.5) which can call:
   - `get_recent_activities` — last 28 days of runs
   - `get_sleep_data` — last 7 nights of sleep
   - `get_training_load` — training load and recovery metrics
   - `get_heart_rate` — resting HR and 7-day trends
5. Streams the response, then saves both messages to DynamoDB

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |
| `message` | `string` | Yes | The user's message |
| `timezone` | `string` | No | IANA timezone string (default `"Australia/Melbourne"`). Use `Intl.DateTimeFormat().resolvedOptions().timeZone` on the frontend. |

#### Response — `text/event-stream`

Each chunk is sent as an SSE `data` event:

```
data: Good session\n\n
data:  yesterday\n\n
data: . Take it easy today.\n\n
data: [DONE]\n\n
```

`[DONE]` signals the stream is complete. `[ERROR]` signals a failure mid-stream.

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `404` | `"User not found. Please connect your Garmin account first."` | No profile found for this `user_id` |
| `404` | `"User credentials not found. Please complete onboarding first."` | No Garmin credentials found (coaching agent only) |
| `503` | `"Unable to retrieve Garmin credentials. Please try again."` | KMS decryption failed |
| `503` | `"Unable to connect to Garmin. Please check your credentials and try again."` | Garmin Connect authentication failed |

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
          appendToMessage(chunk); // render incrementally
        }
      }
    }
  }

  startStream();
  return () => controller.abort(); // cancel on re-render (prevents double-stream in React StrictMode)
}, []);
```

> **Important:** Always use `AbortController` to cancel the fetch on cleanup. Without it, React StrictMode will fire two concurrent streams and interleave their chunks, corrupting the output.

#### After Onboarding Completes

When the onboarding agent finishes collecting all 4 answers, it calls `complete_onboarding()` internally. After receiving `[DONE]`:

1. Call `GET /user/status` — if it returns `"complete"`, onboarding just finished
2. Transition to coaching chat view
3. Call `GET /training-plan` to load and display the generated plan

---

### `GET /chat/history`

Returns the saved chat history for a user, newest messages first. Use this on page load to restore previous conversation state.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |
| `limit` | `integer` | No | Max messages to return (default `50`) |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `messages` | `array` | List of message objects, newest first |
| `messages[].role` | `string` | `"user"` or `"assistant"` |
| `messages[].message` | `string` | Message content |
| `messages[].timestamp` | `string` | ISO datetime string (UTC) |

#### Example Request

```
GET /chat/history?user_id=user_2abc123def456&limit=50
```

#### Example Response

```json
{
  "messages": [
    {
      "role": "assistant",
      "message": "Good session yesterday. Take it easy today.",
      "timestamp": "2026-03-21T10:34:22.123456+00:00"
    },
    {
      "role": "user",
      "message": "How did my training go this week?",
      "timestamp": "2026-03-21T10:34:18.000000+00:00"
    }
  ]
}
```

---

### `POST /training-plan/generate`

Generates a complete multi-week training block for the user. Normally triggered automatically when onboarding completes — use this endpoint to regenerate a plan on demand.

**Plan length by goal:**
| Goal | Weeks |
|---|---|
| First 5K | 8 |
| First 10K | 10 |
| First half marathon | 12 |
| First marathon | 18 |
| Just run consistently | 8 |

If the user has a target race date, the plan runs from next Monday to race day, capped at the goal's default. Plan always starts on a Monday.

On each request the backend:

1. Fetches the user's profile (goal, target race date, training days) from DynamoDB
2. Fetches Garmin credentials, decrypts via KMS, and authenticates with Garmin Connect
3. Runs the Strands agent (Claude Haiku 4.5) which checks recent Garmin data, then generates the full block structured as: base phase → build phase → peak phase → taper
4. Saves one DynamoDB item per day (`SK: PLAN#YYYY-MM-DD`)
5. Returns all weeks

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |

#### Response Body — `200 OK`

Same shape as `GET /training-plan` — all weeks grouped and sorted chronologically.

| Field | Type | Description |
|---|---|---|
| `weeks` | `array` | All generated `PlanWeek` objects sorted by `week_start` ascending |
| `weeks[].week_start` | `string` | ISO date of the Monday this week starts on |
| `weeks[].days` | `array` | Array of 7 `PlanDay` objects |

**`PlanDay` object:**

| Field | Type | Description |
|---|---|---|
| `date` | `string` | ISO date of this day (`YYYY-MM-DD`) |
| `week_start` | `string` | ISO date of the Monday this day belongs to |
| `type` | `string` | Workout type: `intervals`, `tempo`, `threshold`, `fartlek`, `easy`, `long`, or `rest` |
| `distance` | `number` | Distance in kilometres (`0` for rest days) |
| `description` | `string` | Specific workout description |

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `404` | `"User profile not found. Please complete onboarding first."` | No profile found for this `user_id` |
| `404` | `"User credentials not found. Please complete onboarding first."` | No Garmin credentials found for this `user_id` |
| `503` | `"Unable to retrieve Garmin credentials. Please try again."` | KMS decryption failed |
| `503` | `"Unable to connect to Garmin. Please check your credentials and try again."` | Garmin Connect authentication failed |
| `500` | `"Failed to generate training plan. Please try again."` | Agent error or invalid JSON returned |

#### Example Request

```json
POST /training-plan/generate
Content-Type: application/json

{
  "user_id": "user_2abc123def456"
}
```

#### Example Response

```json
{
  "weeks": [
    {
      "week_start": "2026-03-30",
      "days": [
        {
          "date": "2026-03-30",
          "week_start": "2026-03-30",
          "type": "easy",
          "distance": 6.0,
          "description": "Easy aerobic run at conversational pace. Keep HR in zone 2."
        },
        {
          "date": "2026-03-31",
          "week_start": "2026-03-30",
          "type": "rest",
          "distance": 0,
          "description": "Rest day — recovery, stretching, hydration."
        }
      ]
    },
    {
      "week_start": "2026-04-06",
      "days": [
        {
          "date": "2026-04-06",
          "week_start": "2026-04-06",
          "type": "easy",
          "distance": 7.0,
          "description": "Easy run building on last week's base."
        }
      ]
    }
  ]
}
```

---

### `GET /training-plan`

Returns all saved training plan days for the user, grouped by week. Weeks are sorted chronologically.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `weeks` | `array` | List of `PlanWeek` objects sorted by `week_start` ascending |
| `weeks[].week_start` | `string` | ISO date of the Monday this week starts on |
| `weeks[].days` | `array` | Array of `PlanDay` objects for that week (see schema above) |

#### Example Request

```
GET /training-plan?user_id=user_2abc123def456
```

#### Example Response

```json
{
  "weeks": [
    {
      "week_start": "2026-03-30",
      "days": [
        {
          "date": "2026-03-30",
          "week_start": "2026-03-30",
          "type": "easy",
          "distance": 8.0,
          "description": "Easy aerobic run at conversational pace. Keep HR in zone 2."
        }
      ]
    }
  ]
}
```

---

### `DELETE /conversation`

Deletes all chat history for a user. Profile and Garmin credentials are preserved — the user does not need to re-onboard.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `success` | `boolean` | `true` if conversation was cleared |
| `message` | `string` | Human-readable confirmation |

#### Example Request

```
DELETE /conversation?user_id=user_2abc123def456
```

#### Example Response

```json
{
  "success": true,
  "message": "Conversation cleared."
}
```

---

### `DELETE /user`

Deletes all data for a user from DynamoDB — profile, Garmin credentials, chat history, and training plan. The user will need to complete onboarding again after this.

> **Note:** This does not delete the Clerk account. Handle Clerk account deletion on the frontend using Clerk's `deleteUser()` method.

#### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `success` | `boolean` | `true` if user data was deleted |
| `message` | `string` | Human-readable confirmation |

#### Example Request

```
DELETE /user?user_id=user_2abc123def456
```

#### Example Response

```json
{
  "success": true,
  "message": "User data deleted. Please complete onboarding again."
}
```

> **Frontend:** After this completes, call `GET /user/status` — it will return `"not_found"`. Clear all local state (chat history, plan data, onboarding status) and redirect to the Connect Garmin screen.

---

### `GET /health`

Health check endpoint.

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `status` | `string` | Always `"ok"` when the service is running |

#### Example Request

```
GET /health
```

#### Example Response

```json
{
  "status": "ok"
}
```

---

## Error Response Format

All error responses follow FastAPI's default format:

```json
{
  "detail": "Human-readable error message."
}
```

---

## Local Development

Start the server:

```bash
set -a && source .env && set +a
uvicorn main:app --reload
```

Interactive API docs (Swagger UI): [http://localhost:8000/docs](http://localhost:8000/docs)

Alternative docs (ReDoc): [http://localhost:8000/redoc](http://localhost:8000/redoc)
