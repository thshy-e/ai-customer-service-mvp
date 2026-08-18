import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from redis.asyncio import Redis

from .bot import CustomerServiceBot
from .business import BusinessHours
from .catalog import Catalog
from .chatwoot import ChatwootClient
from .dedupe import RedisEventStore
from .llm import OpenAICompatibleGateway
from .models import DirectChatRequest, DirectChatResponse, WebhookEvent
from .security import verify_chatwoot_signature
from .settings import settings


logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


def build_bot() -> CustomerServiceBot:
    catalog = Catalog.from_directory(settings.config_dir)
    business = BusinessHours.from_file(settings.config_dir / "business-hours.yaml")
    chatwoot = ChatwootClient(
        settings.chatwoot_api_url,
        settings.chatwoot_account_id,
        settings.chatwoot_api_token,
        settings.public_asset_base_url,
    )
    model = None
    if settings.llm_configured:
        model = OpenAICompatibleGateway(
            settings.llm_api_key,
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_timeout_seconds,
        )
    return CustomerServiceBot(
        catalog,
        business,
        chatwoot,
        model,
        settings.config_dir / "sales-policy.yaml",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis
    app.state.event_store = RedisEventStore(redis)
    app.state.bot = build_bot()
    yield
    await redis.aclose()


app = FastAPI(title="AI Customer Service MVP", version="0.1.0", lifespan=lifespan)


async def process_event(bot: CustomerServiceBot, event: WebhookEvent) -> None:
    try:
        await bot.handle(event)
    except Exception:
        logger.exception("Failed to process Chatwoot event", extra={"event_id": event.id})
        if event.conversation_id:
            try:
                await bot.chatwoot.handoff(
                    event.conversation_id,
                    "自动客服暂时无法回答，我已将会话转给人工客服。",
                )
            except Exception:
                logger.exception("Fallback handoff also failed")


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    redis_ok = False
    try:
        redis_ok = bool(await request.app.state.redis.ping())
    except Exception:
        logger.warning("Redis health check failed")
    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": redis_ok,
        "llm_configured": settings.llm_configured,
        "model": settings.llm_model if settings.llm_configured else None,
    }


@app.post("/chat", response_model=DirectChatResponse)
async def direct_chat(payload: DirectChatRequest, request: Request) -> DirectChatResponse:
    try:
        history = [turn.model_dump() for turn in payload.history]
        return await request.app.state.bot.direct_reply(payload.message, history)
    except Exception as exc:
        logger.exception("Direct chat failed")
        raise HTTPException(status_code=503, detail="AI service is temporarily unavailable") from exc


@app.post("/webhooks/chatwoot", status_code=202)
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    raw_body = await request.body()
    if not verify_chatwoot_signature(
        raw_body,
        settings.chatwoot_webhook_secret,
        request.headers.get("x-chatwoot-signature"),
        request.headers.get("x-chatwoot-timestamp"),
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        event = WebhookEvent.model_validate(json.loads(raw_body))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid webhook payload") from exc

    event_key = f"{event.event}:{event.id or event.conversation_id}"
    if not await request.app.state.event_store.claim(event_key):
        return {"accepted": False, "duplicate": True}

    background_tasks.add_task(process_event, request.app.state.bot, event)
    return {"accepted": True, "duplicate": False}
