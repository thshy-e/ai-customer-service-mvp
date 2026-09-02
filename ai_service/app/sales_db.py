from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, JSON, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    # 数据库统一保存 UTC，展示时再按用户时区转换。
    return datetime.now(timezone.utc)


def new_id() -> str:
    # 使用 UUID，避免多进程或多实例同时创建记录时发生碰撞。
    return str(uuid4())


class SalesBase(DeclarativeBase):
    pass


class SalesUser(SalesBase):
    __tablename__ = "users"
    __table_args__ = {"schema": "sales"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default="sales")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeSource(SalesBase):
    __tablename__ = "knowledge_sources"
    __table_args__ = {"schema": "sales"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class IngestionJob(SalesBase):
    __tablename__ = "ingestion_jobs"
    __table_args__ = {"schema": "sales"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="等待处理")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class DocumentPage(SalesBase):
    __tablename__ = "document_pages"
    __table_args__ = {"schema": "sales"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text, default="")
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="extracted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CompanyProfile(SalesBase):
    __tablename__ = "company_profiles"
    __table_args__ = {"schema": "sales"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class InterviewSession(SalesBase):
    __tablename__ = "company_interview_sessions"
    __table_args__ = {"schema": "sales"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(String(36), index=True)
    current_index: Mapped[int] = mapped_column(Integer, default=0)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Inquiry(SalesBase):
    __tablename__ = "inquiries"
    __table_args__ = {"schema": "sales"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_image_path: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="analyzed", index=True)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SalesDraft(SalesBase):
    __tablename__ = "sales_drafts"
    __table_args__ = {"schema": "sales"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    inquiry_id: Mapped[str] = mapped_column(String(36), index=True)
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(32), default="English")
    summary: Mapped[str] = mapped_column(Text, default="")
    next_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    cited_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=True)
    model_name: Mapped[str] = mapped_column(String(120), default="deterministic-fallback")
    prompt_version: Mapped[str] = mapped_column(String(32), default="sales-p0-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


async def create_sales_database(database_url: str, upload_dir: Path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    # 启动时创建 MVP 所需表；正式迁移前不删除已有数据。
    upload_dir.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_async_engine(database_url, connect_args=connect_args)
    async with engine.begin() as connection:
        if database_url.startswith("postgres"):
            # PostgreSQL 使用独立 sales schema，避免和 Chatwoot 表混在一起。
            await connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS sales")
        else:
            # SQLite 不支持 schema，清掉 schema 名后复用同一套模型定义。
            for table in SalesBase.metadata.tables.values():
                table.schema = None
        await connection.run_sync(SalesBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    # 容器重启会中断后台 OCR，把遗留的 running 任务改成可重试状态。
    async with sessions() as session:
        await session.execute(
            update(IngestionJob)
            .where(IngestionJob.status == "running")
            .values(status="failed", progress=100, message="服务重启中断了任务，请重新上传或重试",)
        )
        await session.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.status == "processing")
            .values(status="failed", error="服务重启中断了解析，请重新上传或重试")
        )
        await session.commit()
    return engine, sessions
