# AI Running Coach — API Documentation

## Overview

The AI Running Coach backend is a FastAPI application that provides personalised running coaching powered by Garmin Connect data and Amazon Bedrock (Claude Haiku 4.5). The API is deployed as an AWS Lambda function via Mangum and can also be run locally with uvicorn.

**Base URL (local):** `http://localhost:8000`

**Content-Type:** All requests and responses use `application/json`.

---

## Authentication

The API does not currently enforce authentication middleware. The frontend is expected to pass the **Clerk `userId`** as the `user_id` field in every request body. This value is used as the partition key for all DynamoDB records (stored as `USER#<userId>`).

> **Planned:** A future middleware layer will validate the Clerk session token from the `Authorization: Bearer <token>` header and verify that the `user_id` in the request body matches the authenticated Clerk user before processing.

---

## Endpoints

### `POST /onboard`

Saves a new user's profile and Garmin Connect credentials. The Garmin password is encrypted with AWS KMS before being stored in DynamoDB. Call this once when a user signs up or updates their Garmin credentials.

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |
| `goal_race` | `string` | Yes | The user's target race (e.g. `"Sydney Marathon 2026"`) |
| `target_time` | `string` | Yes | Target finish time in `HH:MM:SS` format (e.g. `"3:45:00"`) |
| `training_days` | `integer` | Yes | Number of days per week the user trains (e.g. `4`) |
| `garmin_email` | `string` | Yes | Garmin Connect account email address |
| `garmin_password` | `string` | Yes | Garmin Connect account password (encrypted at rest via KMS) |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `success` | `boolean` | `true` if onboarding completed successfully |
| `message` | `string` | Human-readable confirmation message |

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `500` | `"Server configuration error."` | `KMS_KEY_ID` environment variable not set |
| `500` | `"Failed to secure credentials. Please try again."` | KMS encryption failed |
| `500` | `"Failed to save credentials. Please try again."` | DynamoDB write failed for credentials |
| `500` | `"Failed to save profile. Please try again."` | DynamoDB write failed for profile |

#### Example Request

```json
POST /onboard
Content-Type: application/json

{
  "user_id": "user_2abc123def456",
  "goal_race": "Sydney Marathon 2026",
  "target_time": "3:45:00",
  "training_days": 4,
  "garmin_email": "runner@example.com",
  "garmin_password": "my-garmin-password"
}
```

#### Example Response

```json
{
  "success": true,
  "message": "Onboarding complete."
}
```

---

### `POST /chat`

Sends a message to the AI running coach and returns a personalised response. On each request the backend:

1. Fetches the user's Garmin credentials from DynamoDB and decrypts them via KMS
2. Authenticates with Garmin Connect
3. Retrieves the last 20 messages of chat history for conversation context
4. Runs the Strands agent (Claude Haiku 4.5) which can call any of the following tools:
   - `get_recent_activities` — last 28 days of runs
   - `get_sleep_data` — last 7 nights of sleep
   - `get_training_load` — training load and recovery metrics
   - `get_heart_rate` — resting HR and 7-day trends
5. Saves the user message and agent response to DynamoDB
6. Returns the agent's response

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |
| `message` | `string` | Yes | The user's message or question for the coaching agent |
| `timezone` | `string` | No | IANA timezone string (default `"Australia/Melbourne"`). Use `Intl.DateTimeFormat().resolvedOptions().timeZone` on the frontend. |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `response` | `string` | The AI coach's response in markdown format |

#### Error Responses

| Status | Detail | Cause |
|---|---|---|
| `404` | `"User credentials not found. Please complete onboarding first."` | No Garmin credentials found for this `user_id` |
| `503` | `"Unable to retrieve Garmin credentials. Please try again."` | KMS decryption failed |
| `503` | `"Unable to connect to Garmin. Please check your credentials and try again."` | Garmin Connect authentication failed (wrong credentials or Garmin service down) |
| `500` | `"Agent error. Please try again."` | Bedrock model error or agent execution failure |

#### Example Request

```json
POST /chat
Content-Type: application/json

{
  "user_id": "user_2abc123def456",
  "message": "How did my training go this week?"
}
```

#### Example Response

```json
{
  "response": "## Weekly Training Overview\n\n**Running Performance:**\n- You've maintained consistent training with a good mix of easy runs, tempo work, and long runs\n- Your average pace has been well-controlled across different intensity zones\n\n**Training Load & Recovery:**\n- **Training Status:** PRODUCTIVE — your body is adapting well\n- **Resting Heart Rate:** 40 bpm — excellent cardiovascular fitness\n- **HRV:** BALANCED — nervous system is recovering well\n\n**Recommendations:**\n1. Maintain current training structure — it's working\n2. Add one easy long run to build aerobic base\n3. Don't increase weekly mileage by more than 10%\n\nYou're doing great! Keep it up 💪"
}
```

---

### `POST /chat/stream`

Same as `POST /chat` but streams the response token by token using Server-Sent Events (SSE). The conversation is saved to DynamoDB after the stream completes.

#### Request Body

Same as `POST /chat` (`user_id`, `message`, `timezone`).

#### Response — `text/event-stream`

Each chunk is sent as an SSE `data` event:

```
data: Good session\n\n
data:  yesterday\n\n
data: . Take it easy today.\n\n
data: [DONE]\n\n
```

`[DONE]` signals the stream is complete. `[ERROR]` signals a failure mid-stream.

#### Frontend Usage

```js
const res = await fetch("http://localhost:8000/chat/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ user_id, message, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }),
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
      if (chunk === "[DONE]" || chunk === "[ERROR]") break;
      appendToMessage(chunk); // render incrementally
    }
  }
}
```

---

### `GET /chat/history`

Returns the saved chat history for a user, newest messages first. Use this on page load to restore previous conversation state in the frontend.

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

Generates a personalised 7-day training plan starting from the next Monday. On each request the backend:

1. Fetches the user's profile (goal race, target time, training days) from DynamoDB
2. Fetches Garmin credentials, decrypts via KMS, and authenticates with Garmin Connect
3. Runs the Strands agent (Claude Haiku 4.5) which uses Garmin tools to assess current fitness before generating the plan
4. Saves one DynamoDB item per day (`SK: PLAN#YYYY-MM-DD`)
5. Returns the generated week

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | `string` | Yes | Clerk userId of the authenticated user |

#### Response Body — `200 OK`

| Field | Type | Description |
|---|---|---|
| `week` | `object` | The generated plan week |
| `week.week_start` | `string` | ISO date of the Monday this week starts on |
| `week.days` | `array` | Array of 7 `PlanDay` objects |

**`PlanDay` object:**

| Field | Type | Description |
|---|---|---|
| `date` | `string` | ISO date of this day (`YYYY-MM-DD`) |
| `week_start` | `string` | ISO date of the Monday this day belongs to |
| `type` | `string` | Workout type: `intervals`, `tempo`, `threshold`, `fartlek`, `easy`, `long`, or `rest` |
| `distance` | `number` | Distance in kilometres (`0` for rest days) |
| `description` | `string` | Detailed workout description |

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
  "week": {
    "week_start": "2026-03-30",
    "days": [
      {
        "date": "2026-03-30",
        "week_start": "2026-03-30",
        "type": "easy",
        "distance": 8.0,
        "description": "Easy aerobic run at conversational pace. Keep HR in zone 2."
      },
      {
        "date": "2026-03-31",
        "week_start": "2026-03-30",
        "type": "intervals",
        "distance": 10.0,
        "description": "10 min WU, 6 × 1km @ 4:00/km with 90s rest, 10 min CD"
      },
      {
        "date": "2026-04-01",
        "week_start": "2026-03-30",
        "type": "rest",
        "distance": 0,
        "description": "Rest day — focus on recovery, stretching, and hydration."
      }
    ]
  }
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

### `GET /health`

Health check endpoint. Use this to verify the service is running.

#### Request Body

None.

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

All error responses follow FastAPI's default error format:

```json
{
  "detail": "Human-readable error message."
}
```

---

## Local Development

Start the server:

```bash
source .env
uvicorn main:app --reload
```

Interactive API docs (Swagger UI): [http://localhost:8000/docs](http://localhost:8000/docs)

Alternative docs (ReDoc): [http://localhost:8000/redoc](http://localhost:8000/redoc)
