import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from redis.asyncio import Redis

from .bot import CustomerServiceBot
from .business import BusinessHours
from .catalog import Catalog
from .chatwoot import ChatwootClient
from .dedupe import RedisEventStore
from .llm import OpenAICompatibleGateway
from .models import DirectChatRequest, DirectChatResponse, WebhookEvent
from .security import verify_chatwoot_signature
from .sales_auth import authenticate, issue_token, require_sales_principal
from .sales_db import create_sales_database
from .sales_schemas import (
    InquiryResponse,
    InterviewAnswerRequest,
    InterviewResponse,
    KnowledgeJobResponse,
    KnowledgeSourceResponse,
    LoginRequest,
    LoginResponse,
    RegenerateRequest,
    SalesDraftResponse,
)
from .sales_service import SalesService
from .settings import settings


logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


def build_bot() -> CustomerServiceBot:
    # 启动时一次性加载商品、营业时间和销售规范，后续请求只读内存数据。
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
    # FastAPI 的生命周期负责初始化共享资源，避免每个请求重复建连接。
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis
    app.state.event_store = RedisEventStore(redis)
    app.state.bot = build_bot()
    sales_engine, sales_sessions = await create_sales_database(settings.sales_database_url, settings.sales_upload_dir)
    app.state.sales_engine = sales_engine
    app.state.sales_sessions = sales_sessions
    app.state.sales = SalesService(settings, app.state.bot.catalog, sales_sessions)
    await app.state.sales.backfill_page_candidates()
    yield
    await sales_engine.dispose()
    await redis.aclose()


app = FastAPI(title="AI Customer Service MVP", version="0.1.0", lifespan=lifespan)


async def process_event(bot: CustomerServiceBot, event: WebhookEvent) -> None:
    # Webhook 只负责接收事件，真正回复放到后台任务中，尽快返回 202。
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
    # 健康检查只验证关键依赖 Redis；模型配置状态只作为诊断信息返回。
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
        "sales_assistant": True,
    }


@app.post("/auth/login", response_model=LoginResponse)
async def sales_login(payload: LoginRequest) -> LoginResponse:
    principal = authenticate(payload.email, payload.password)
    if not principal:
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    return LoginResponse(token=issue_token(principal.email, principal.role), email=principal.email, role=principal.role)


@app.post("/knowledge/sources", response_model=KnowledgeSourceResponse, status_code=202)
async def create_knowledge_source(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> KnowledgeSourceResponse:
    # 文件先落盘并登记任务，再异步解析，避免上传请求被 OCR 阻塞。
    require_sales_principal(request)
    data = await file.read()
    max_bytes = settings.sales_max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件不能超过 {settings.sales_max_upload_mb}MB")
    source, job = await request.app.state.sales.create_source(file.filename or "upload.bin", file.content_type or "application/octet-stream", data)
    background_tasks.add_task(request.app.state.sales.process_source, source.id, job.id)
    return KnowledgeSourceResponse(source_id=source.id, job_id=job.id, filename=source.filename, status=job.status)


@app.get("/knowledge/jobs/{job_id}", response_model=KnowledgeJobResponse)
async def knowledge_job(job_id: str, request: Request) -> KnowledgeJobResponse:
    require_sales_principal(request)
    value = await request.app.state.sales.get_job(job_id)
    if not value:
        raise HTTPException(status_code=404, detail="解析任务不存在")
    return KnowledgeJobResponse.model_validate(value)


@app.get("/knowledge/sources/{source_id}/pages")
async def knowledge_source_pages(source_id: str, request: Request) -> dict[str, Any]:
    # 页面查询只返回已登录用户可见的资料，不开放原始文件路径。
    require_sales_principal(request)
    pages = await request.app.state.sales.get_source_pages(source_id)
    if pages is None:
        raise HTTPException(status_code=404, detail="资料不存在")
    return {"source_id": source_id, "pages": pages}


@app.post("/company/interviews", response_model=InterviewResponse)
async def start_company_interview(request: Request) -> InterviewResponse:
    require_sales_principal(request, admin_only=True)
    return InterviewResponse.model_validate(await request.app.state.sales.start_interview())


@app.post("/company/interviews/{session_id}/answers", response_model=InterviewResponse)
async def answer_company_interview(session_id: str, payload: InterviewAnswerRequest, request: Request) -> InterviewResponse:
    require_sales_principal(request, admin_only=True)
    value = await request.app.state.sales.answer_interview(session_id, payload.question, payload.answer)
    if not value:
        raise HTTPException(status_code=404, detail="访谈不存在")
    return InterviewResponse.model_validate(value)


@app.post("/company/profiles/{profile_id}/publish")
async def publish_company_profile(profile_id: str, request: Request) -> dict[str, Any]:
    require_sales_principal(request, admin_only=True)
    if not await request.app.state.sales.publish_profile(profile_id):
        raise HTTPException(status_code=404, detail="企业画像不存在")
    return {"profile_id": profile_id, "status": "published"}


@app.post("/inquiries/analyze", response_model=InquiryResponse)
async def analyze_inquiry(
    request: Request,
    file: UploadFile = File(...),
    message: str = Form(default=""),
) -> InquiryResponse:
    # 询盘截图会同时生成结构化分析和可编辑的销售草稿。
    require_sales_principal(request)
    data = await file.read()
    max_bytes = settings.sales_max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"截图不能超过 {settings.sales_max_upload_mb}MB")
    analysis, draft = await request.app.state.sales.analyze_inquiry(file.filename or "inquiry.jpg", file.content_type or "image/jpeg", data, message)
    return InquiryResponse(inquiry=analysis, draft=draft)


@app.post("/sales-drafts/{draft_id}/regenerate", response_model=SalesDraftResponse)
async def regenerate_sales_draft(draft_id: str, payload: RegenerateRequest, request: Request) -> SalesDraftResponse:
    require_sales_principal(request)
    draft = await request.app.state.sales.regenerate_draft(draft_id, payload.instructions)
    if not draft:
        raise HTTPException(status_code=404, detail="销售草稿不存在")
    return draft


@app.get("/sales-drafts/{draft_id}/sources")
async def sales_draft_sources(draft_id: str, request: Request) -> dict[str, Any]:
    require_sales_principal(request)
    sources = await request.app.state.sales.draft_sources(draft_id)
    if sources is None:
        raise HTTPException(status_code=404, detail="销售草稿不存在")
    return {"draft_id": draft_id, "sources": sources}


@app.post("/chat", response_model=DirectChatResponse)
async def direct_chat(payload: DirectChatRequest, request: Request) -> DirectChatResponse:
    # 网站 H5 直接调用这条接口；Chatwoot 则走下面的 Webhook 入口。
    try:
        history = [turn.model_dump() for turn in payload.history]
        return await request.app.state.bot.direct_reply(payload.message, history)
    except Exception as exc:
        logger.exception("Direct chat failed")
        raise HTTPException(status_code=503, detail="AI service is temporarily unavailable") from exc


@app.post("/webhooks/chatwoot", status_code=202)
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    # Webhook 处理顺序：验签 -> 校验数据 -> 去重 -> 异步处理。
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
