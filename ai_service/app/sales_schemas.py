from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    token: str
    email: str
    role: Literal["admin", "sales"]


class KnowledgeSourceResponse(BaseModel):
    # 上传接口先返回任务编号，前端再轮询处理进度。
    source_id: str
    job_id: str
    filename: str
    status: str


class KnowledgeJobResponse(BaseModel):
    id: str
    source_id: str
    status: str
    progress: int
    message: str
    page_count: int = 0
    error: str | None = None


class InterviewResponse(BaseModel):
    session_id: str
    profile_id: str
    question_index: int
    total_questions: int
    question: str | None = None
    status: str


class InterviewAnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=4000)


class InquiryAnalysis(BaseModel):
    # 询盘字段固定为结构化数据，unknown 表示截图中没有可靠信息。
    source_image_id: str
    language: str = "unknown"
    customer_country: str = "unknown"
    customer_company: str = "unknown"
    products: list[str] = Field(default_factory=list)
    quantity: str = "unknown"
    application: str = "unknown"
    budget: str = "unknown"
    delivery_date: str = "unknown"
    certification: str = "unknown"
    known_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)


class SalesDraftResponse(BaseModel):
    # 草稿默认必须人工审核，接口不提供自动发送能力。
    id: str
    inquiry_id: str
    subject: str
    body: str
    language: str
    summary: str
    next_actions: list[str] = Field(default_factory=list)
    cited_facts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requires_human_review: bool = True
    model_name: str
    prompt_version: str


class InquiryResponse(BaseModel):
    inquiry: InquiryAnalysis
    draft: SalesDraftResponse


class RegenerateRequest(BaseModel):
    instructions: str = Field(default="", max_length=1000)
