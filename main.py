"""FastAPI app entry point."""
import logging
import time

from fastapi import FastAPI, Request
from mangum import Mangum

from routers import chat, onboard, health, training_plan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("api")

app = FastAPI(
    title="AI Running Coach",
    description="AI-powered running coach backed by Garmin data and Amazon Bedrock.",
    version="0.1.0",
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    logger.info("→ %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        raise
    elapsed = (time.perf_counter() - start) * 1000
    logger.info("← %s %s %d (%.0fms)", request.method, request.url.path, response.status_code, elapsed)
    return response


app.include_router(chat.router)
app.include_router(onboard.router)
app.include_router(health.router)
app.include_router(training_plan.router)

# Mangum adapter — used as the Lambda handler in handler.py
handler = Mangum(app, lifespan="off")
