# System Architecture

```mermaid
flowchart TD
    User(["👤 User"])

    subgraph Frontend["Frontend (Next.js 14 App Router)"]
        subgraph Pages["Pages"]
            SignIn["/sign-in\n/sign-up"]
            Onboarding["/onboarding\nOnboardingForm"]
            Chat["/chat\nChatContainer\nMessageList\nMessageBubble\nStreamingMarkdown"]
            Calendar["/calendar\nWeeklyCalendar"]
        end

        subgraph Middleware["src/middleware.ts"]
            AuthGate["Unauthenticated → /sign-in\nNo cookie → /onboarding\nOnboarded → pass through"]
        end

        subgraph APIRoutes["Next.js API Routes (src/app/api/)"]
            ApiOnboard["POST /api/onboard\ninjects userId\nsets onboarded cookie\nupdates Clerk metadata"]
            ApiChat["POST /api/chat\ninjects userId + timezone\nproxies SSE stream"]
            ApiHistory["GET /api/chat/history\ninjects userId"]
            ApiPlan["GET /api/training-plan\nPOST /api/training-plan/generate\ninjects userId"]
        end

        Clerk["Clerk\nauth() server-side\nClerkProvider + UserButton"]
        ApiLib["src/lib/api.ts\nTyped fetch helpers"]
    end

    subgraph Backend["Backend (AWS Lambda — ap-southeast-2)"]
        FastAPI["FastAPI + Mangum\n\nPOST /onboard\nPOST /chat/stream\nGET  /chat/history\nGET  /training-plan\nPOST /training-plan/generate\nGET  /health"]

        subgraph Agent["Strands Agent"]
            LLM["Amazon Bedrock\nClaude Haiku 4.5\nau.anthropic.claude-haiku-4-5"]
            Tools["Agent Tools\n• get_recent_activities\n• get_sleep_data\n• get_training_load\n• get_heart_rate"]
        end
    end

    subgraph AWS["AWS Services"]
        DynamoDB[("DynamoDB\nai-running-coach\n\nUSER#id | PROFILE\nUSER#id | CREDENTIALS\nUSER#id | CHAT#timestamp\nUSER#id | PLAN#date")]
        KMS["AWS KMS\nEncrypt / Decrypt\nGarmin password"]
    end

    Garmin["☁️ Garmin Connect API"]

    %% Auth flow
    User -->|"signs in"| Clerk
    User -->|"every request"| Middleware
    Middleware --> AuthGate
    Clerk -->|"userId (server-side only)"| APIRoutes

    %% Client → API Routes
    Chat -->|"POST /api/chat"| ApiChat
    Onboarding -->|"POST /api/onboard"| ApiOnboard
    Chat -->|"GET /api/chat/history"| ApiHistory
    Calendar -->|"GET/POST /api/training-plan"| ApiPlan
    ApiLib -.->|"used by all pages"| Pages

    %% API Routes → Backend
    ApiOnboard -->|"POST /onboard"| FastAPI
    ApiChat -->|"POST /chat/stream\nSSE proxy"| FastAPI
    ApiHistory -->|"GET /chat/history"| FastAPI
    ApiPlan -->|"GET/POST /training-plan"| FastAPI

    %% Backend internals
    FastAPI -->|"encrypt password"| KMS
    FastAPI -->|"decrypt password"| KMS
    FastAPI -->|"read/write"| DynamoDB
    FastAPI -->|"login"| Garmin
    Garmin -->|"fitness data"| Tools
    Tools -->|"data context"| LLM
    LLM -->|"SSE stream"| FastAPI
    FastAPI -->|"SSE stream"| ApiChat
    ApiChat -->|"proxied SSE stream"| Chat
```

## Request Flows

### Auth & Routing
1. Every request hits `src/middleware.ts`
2. Unauthenticated → redirected to `/sign-in`
3. Authenticated but no `onboarded` cookie → redirected to `/onboarding`
4. Fully onboarded → passes through to `/chat` or `/calendar`
5. `userId` is **never exposed to the client** — always injected server-side via `auth()` in API route handlers

### Onboarding
1. User fills in Garmin credentials + race goal on `/onboarding`
2. Client posts to `POST /api/onboard` (Next.js route handler)
3. Route handler injects `userId` via `auth()`, proxies to backend `POST /onboard`
4. Backend encrypts Garmin password with **KMS**, saves profile + credentials to **DynamoDB**
5. Route handler sets `onboarded=true` httpOnly cookie + updates Clerk `unsafeMetadata`

### Chat (Streaming)
1. User sends message in `ChatContainer`
2. Client posts to `POST /api/chat` with message + timezone
3. Next.js route handler injects `userId`, proxies to backend `POST /chat/stream`
4. Backend decrypts Garmin credentials via **KMS**, authenticates with **Garmin Connect**
5. **Strands Agent** fetches activities, sleep, HR, training load via Garmin tools
6. **Claude Haiku 4.5** generates response — streamed back via **SSE**
7. Next.js proxies the SSE stream directly to the client (`new Response(body)`)
8. `ChatContainer` reads the stream with `ReadableStreamDefaultReader`, appending tokens to the last assistant message in state
9. `StreamingMarkdown` renders plain text while streaming, switches to `react-markdown + remark-gfm` when done
10. Full conversation saved to DynamoDB after stream completes

### Training Plan
1. `WeeklyCalendar` fetches existing plan on mount via `GET /api/training-plan`
2. User hits "Generate" → `POST /api/training-plan/generate`
3. Backend fetches profile + Garmin data, agent generates **7-day JSON plan**
4. One DynamoDB item saved per day (`PLAN#YYYY-MM-DD`)
5. Frontend receives the generated week and renders it in the 7-column calendar grid
