# AI Running Coach - Backend

## Stack
- Python AWS Lambda function
- FastAPI for routing and auto-generated API docs
- Mangum as the Lambda adapter for FastAPI
- Strands agent framework for orchestration
- Amazon Bedrock Claude Haiku 3 for LLM calls
- DynamoDB for storing user profiles, Garmin credentials (encrypted), chat history
- garmin-connect library for Garmin data
- AWS region: ap-southeast-2 (Sydney)

## Architecture
- FastAPI app wrapped with Mangum for Lambda compatibility
- Run locally with uvicorn for development
- API docs auto-generated at /docs and /redoc
- Single Lambda function handles all requests routed by FastAPI
- Strands agent is initialised per request, not persisted
- DynamoDB table name stored in DYNAMODB_TABLE env var
- All Garmin credentials encrypted at rest using AWS KMS before storing in DynamoDB

## Endpoints
- POST /chat — receives message, returns agent response
- POST /onboard — saves user profile and Garmin credentials
- GET /health — health check

## Agent Tools
- get_recent_activities — last 4 weeks of activities
- get_sleep_data — recent sleep data
- get_training_load — current load and recovery metrics
- get_heart_rate — resting HR and recent HR trends

## Local Development
- Run with: uvicorn main:app --reload
- API docs available at: http://localhost:8000/docs

## Code Style
- Type hints on all functions
- Pydantic models for all request and response schemas
- Descriptive function and variable names
- All sensitive operations wrapped in try/except with meaningful error messages
- Never log Garmin credentials or any PII
