# Backend Architecture

```mermaid
flowchart TD
    Client(["Frontend / API Consumer"])

    subgraph Lambda["AWS Lambda (ap-southeast-2)"]
        subgraph FastAPI["FastAPI + Mangum"]
            Onboard["POST /onboard"]
            Chat["POST /chat"]
            ChatStream["POST /chat/stream"]
            ChatHistory["GET /chat/history"]
            PlanGenerate["POST /training-plan/generate"]
            PlanGet["GET /training-plan"]
            Health["GET /health"]
        end

        subgraph Agent["Strands Agent"]
            LLM["Amazon Bedrock\nClaude Haiku 4.5\nau.anthropic.claude-haiku-4-5"]
            Tools["Tools\n• get_recent_activities\n• get_sleep_data\n• get_training_load\n• get_heart_rate"]
        end

        subgraph Services["Services"]
            GarminSvc["services/garmin.py\nGarminClient"]
            DynamoSvc["services/dynamodb.py"]
            KMSSvc["services/kms.py"]
        end
    end

    subgraph AWS["AWS Services"]
        DynamoDB[("DynamoDB\nai-running-coach\n\nUSER#id · PROFILE\nUSER#id · CREDENTIALS\nUSER#id · CHAT#timestamp\nUSER#id · PLAN#date")]
        KMS["AWS KMS\nKey: 50e766ca..."]
    end

    Garmin["☁️ Garmin Connect API"]

    %% Client → endpoints
    Client -->|"POST user_id + garmin creds + profile"| Onboard
    Client -->|"POST user_id + message + timezone"| Chat
    Client -->|"POST user_id + message + timezone"| ChatStream
    Client -->|"GET user_id"| ChatHistory
    Client -->|"POST user_id"| PlanGenerate
    Client -->|"GET user_id"| PlanGet

    %% Onboard flow
    Onboard --> KMSSvc
    KMSSvc -->|"encrypt"| KMS
    KMS -->|"ciphertext"| KMSSvc
    Onboard --> DynamoSvc
    DynamoSvc -->|"save PROFILE + CREDENTIALS"| DynamoDB

    %% Chat flow
    Chat --> DynamoSvc
    ChatStream --> DynamoSvc
    DynamoSvc -->|"fetch CREDENTIALS"| DynamoDB
    Chat --> KMSSvc
    ChatStream --> KMSSvc
    KMSSvc -->|"decrypt"| KMS
    KMS -->|"plaintext password"| KMSSvc
    Chat --> GarminSvc
    ChatStream --> GarminSvc
    GarminSvc -->|"login + fetch data"| Garmin
    Garmin -->|"activities, sleep, HR, load"| Tools
    Tools --> LLM
    LLM -->|"response"| Chat
    LLM -->|"SSE stream"| ChatStream
    Chat -->|"save CHAT# items"| DynamoSvc
    ChatStream -->|"save CHAT# items after stream"| DynamoSvc

    %% History
    ChatHistory --> DynamoSvc
    DynamoSvc -->|"fetch CHAT# items"| DynamoDB

    %% Training plan
    PlanGenerate --> DynamoSvc
    PlanGenerate --> GarminSvc
    GarminSvc -->|"login + fetch data"| Garmin
    Garmin -->|"fitness context"| Tools
    Tools --> LLM
    LLM -->|"7-day JSON plan"| PlanGenerate
    PlanGenerate -->|"save PLAN# items"| DynamoSvc
    PlanGet --> DynamoSvc
    DynamoSvc -->|"fetch PLAN# items"| DynamoDB

    %% Responses back to client
    Chat -->|"JSON response"| Client
    ChatStream -->|"SSE stream"| Client
    ChatHistory -->|"message array"| Client
    PlanGenerate -->|"week JSON"| Client
    PlanGet -->|"weeks array"| Client
```

## DynamoDB Single-Table Design

| PK | SK | Description |
|---|---|---|
| `USER#<userId>` | `PROFILE` | Goal race, target time, training days |
| `USER#<userId>` | `CREDENTIALS` | Garmin email + KMS-encrypted password |
| `USER#<userId>` | `CHAT#<timestamp>` | Individual chat messages |
| `USER#<userId>` | `PLAN#<YYYY-MM-DD>` | Individual training plan days |
