from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pypdf import PdfReader
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .catalog import Catalog
from .sales_db import CompanyProfile, DocumentPage, IngestionJob, Inquiry, InterviewSession, KnowledgeSource, SalesDraft, new_id, utc_now
from .sales_schemas import InquiryAnalysis, SalesDraftResponse
from .settings import Settings


logger = logging.getLogger(__name__)


INTERVIEW_QUESTIONS = [
    # 固定十问先收集最小企业画像，后续再扩展为可配置问卷。
    "公司主要产品是什么？请列出最重要的产品系列。",
    "主要出口市场和客户国家有哪些？",
    "典型客户是谁？他们通常用于什么项目或场景？",
    "工厂、产能和质量控制能力如何？",
    "最有竞争力的产品卖点是什么？",
    "是否提供 OEM/ODM？可以做到哪些范围？",
    "MOQ（最小起订量）是多少？",
    "标准交期通常是多少？",
    "常用付款方式和贸易条款是什么？",
    "哪些内容绝对不能向客户承诺？",
]


# 图册型号有多种前缀，先用保守规则提取候选，后续仍需人工确认。
MODEL_PATTERN = re.compile(
    r"\b(?:KK[- ]?[A-Z]{1,8}\d{1,5}[A-Z]*|K[A-Z]{1,8}\d{1,5}[A-Z]*|"
    r"HSA\d+[A-Z]*|SPC\d+[A-Z]*|KD\d+[A-Z]*|KBS\d+[A-Z]*|KWB\d+[A-Z]*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SalesCatalogEntry:
    id: str
    name: str
    summary: str
    catalog_range: str = ""


def _extract_json(text: str) -> dict[str, Any] | None:
    # 模型有时会在 JSON 外包一层说明文字，这里只取最外层对象。
    text = text.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


class SalesService:
    def __init__(self, settings: Settings, catalog: Catalog, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 服务同时管理文件、资料解析、询盘分析和销售草稿。
        self.settings = settings
        self.catalog = catalog
        self.session_factory = session_factory
        self.series = self._load_series(settings.config_dir / "sales-catalog.yaml")
        self.client = (
            AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_seconds,
                # OCR 单页失败由我们记录，不让 SDK 自动重试拖慢整本图册。
                max_retries=0,
            )
            if settings.llm_configured
            else None
        )

    @staticmethod
    def _load_series(path: Path) -> list[dict[str, Any]]:
        # 销售目录是可版本控制的事实源，文件不存在时保持空目录运行。
        try:
            with path.open(encoding="utf-8") as file:
                return list((yaml.safe_load(file) or {}).get("series", []))
        except FileNotFoundError:
            return []

    @staticmethod
    def _extract_model_candidates(text: str) -> list[str]:
        """从 OCR 文本提取型号候选，不把候选直接当成已确认产品。"""
        found: list[str] = []
        for value in MODEL_PATTERN.findall(text or ""):
            normalized = re.sub(r"\s*[-]\s*", "-", value.upper()).replace(" ", "")
            if normalized.startswith("KK") and not normalized.startswith("KK-"):
                normalized = f"KK-{normalized[2:]}"
            if normalized not in found:
                found.append(normalized)
        return found

    async def store_file(self, filename: str, content_type: str, data: bytes, prefix: str) -> Path:
        # 只保留安全文件名，并用随机前缀避免同名文件覆盖。
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "upload.bin").name)
        target_dir = self.settings.sales_upload_dir / prefix
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{new_id()}-{safe_name}"
        target.write_bytes(data)
        return target

    async def create_source(self, filename: str, content_type: str, data: bytes) -> tuple[KnowledgeSource, IngestionJob]:
        # 上传记录和解析任务一起写入数据库，前端可用 job_id 查询进度。
        path = await self.store_file(filename, content_type, data, "knowledge")
        async with self.session_factory() as session:
            source = KnowledgeSource(
                filename=Path(filename).name,
                content_type=content_type or "application/octet-stream",
                size_bytes=len(data),
                storage_path=str(path),
                status="queued",
            )
            session.add(source)
            await session.flush()
            job = IngestionJob(source_id=source.id, status="queued", progress=0, message="等待解析")
            session.add(job)
            await session.commit()
            return source, job

    async def process_source(self, source_id: str, job_id: str) -> None:
        # 解析过程会持续更新状态；失败时保留原文件，允许之后重试。
        async with self.session_factory() as session:
            source = await session.get(KnowledgeSource, source_id)
            job = await session.get(IngestionJob, job_id)
            if not source or not job:
                return
            source.status = "processing"
            job.status = "running"
            job.progress = 10
            job.message = "读取文件和页面结构"
            await session.commit()
            try:
                pages, warning = await self._extract_document(Path(source.storage_path), source.content_type)
                for page_number, text, page_status in pages:
                    model_candidates = self._extract_model_candidates(text)
                    session.add(
                        DocumentPage(
                            source_id=source.id,
                            page_number=page_number,
                            text=text,
                            structured_data={
                                "source_document_id": source.id,
                                "source_page": page_number,
                                "model_candidates": model_candidates,
                                "review_status": "pending" if model_candidates else "needs_review",
                            },
                            status=page_status,
                        )
                    )
                source.page_count = len(pages)
                source.status = "succeeded_with_warnings" if warning else "succeeded"
                source.error = warning
                job.status = "succeeded_with_warnings" if warning else "succeeded"
                job.progress = 100
                job.message = warning or f"已完成 {len(pages)} 页解析，等待人工确认产品字段"
                await session.commit()
            except Exception:
                logger.exception("Knowledge source processing failed", extra={"source_id": source_id})
                source.status = "failed"
                source.error = "解析失败，请重试"
                job.status = "failed"
                job.progress = 100
                job.message = "解析失败，请检查文件后重试"
                await session.commit()

    async def backfill_page_candidates(self) -> int:
        """为服务升级前已经保存的 OCR 页面补写型号候选。"""
        updated = 0
        async with self.session_factory() as session:
            pages = (await session.execute(select(DocumentPage))).scalars().all()
            for page in pages:
                candidates = self._extract_model_candidates(page.text)
                data = dict(page.structured_data or {})
                if data.get("model_candidates") == candidates:
                    continue
                data["model_candidates"] = candidates
                data["review_status"] = "pending" if candidates else "needs_review"
                page.structured_data = data
                updated += 1
            if updated:
                await session.commit()
        return updated

    async def _extract_document(self, path: Path, content_type: str) -> tuple[list[tuple[int, str, str]], str | None]:
        # 不同文件类型走不同解析器；每个结果都保留页码和状态。
        suffix = path.suffix.casefold()
        if suffix == ".pdf" or content_type == "application/pdf":
            reader = PdfReader(str(path))
            pages = []
            empty_pages = 0
            for index, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if not text:
                    empty_pages += 1
                pages.append((index, text, "extracted" if text else "ocr_required"))
            if empty_pages and self.settings.sales_ocr_enabled and self.client:
                # 图册通常是图片型 PDF，必须逐页识别，否则来源页码会错位。
                ocr_pages, failed_pages = await self._try_pdf_ocr_pages(path)
                for page_number, text, status in pages:
                    if page_number in ocr_pages:
                        pages[page_number - 1] = (page_number, ocr_pages[page_number], "ocr_extracted")
                if ocr_pages:
                    warning = f"{empty_pages} 页为图片型页面，已逐页 OCR；{failed_pages} 页待人工确认"
                    return pages, warning
                return pages, f"{empty_pages} 页为图片型页面，OCR 调用失败或模型无权限，已保留页码待重试"
            if empty_pages:
                return pages, f"{empty_pages} 页为图片型页面，需要 OCR 或人工确认"
            return pages, None
        if suffix == ".docx" or content_type.endswith("wordprocessingml.document"):
            try:
                from docx import Document

                document = Document(str(path))
                text = "\n".join(paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip())
                return [(1, text, "extracted")], None
            except Exception as exc:
                return [(1, "", "failed")], f"DOCX 解析失败：{type(exc).__name__}"
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return [(1, "", "ocr_required")], "图片已保存，等待视觉 OCR 识别"
        return [(1, "", "unsupported")], "文件已保存，但当前 MVP 仅解析 PDF、DOCX 和常见图片"

    async def _try_pdf_ocr_pages(self, path: Path) -> tuple[dict[int, str], int]:
        """渲染并识别每一页，返回成功文本和失败页数。"""
        if not self.client or not self.settings.sales_ocr_model:
            return {}, 0
        try:
            # PyMuPDF 不依赖系统命令，容器中可以直接把 PDF 页面转成图片。
            import pymupdf
        except ImportError:
            logger.exception("PyMuPDF is not installed")
            return {}, 0

        texts: dict[int, str] = {}
        failed = 0
        try:
            document = pymupdf.open(str(path))
            rendered_pages: list[tuple[int, str]] = []
            for index, page in enumerate(document, start=1):
                # 原始分辨率已足够识别型号，也能控制请求体大小。
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
                rendered_pages.append((index, base64.b64encode(pixmap.tobytes("png")).decode()))
            document.close()

            semaphore = asyncio.Semaphore(max(1, self.settings.sales_ocr_concurrency))

            async def recognize(page_number: int, encoded: str) -> tuple[int, str]:
                async with semaphore:
                    # 超时只包住模型请求，不把等待并发槽位的时间算进去。
                    response = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            model=self.settings.sales_ocr_model,
                            messages=[
                                {
                                    "role": "system",
                                    "content": (
                                        "你是产品目录 OCR。只提取产品型号、产品标题和关键技术参数摘要；"
                                        "不猜测价格、库存或交期。每个产品最多输出 8 行，避免抄录重复说明。"
                                    ),
                                },
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": f"这是图册第 {page_number} 页，请输出可审核的产品索引。"},
                                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                                    ],
                                },
                            ],
                            temperature=0,
                            max_tokens=1200,
                        ),
                        self.settings.sales_ocr_page_timeout_seconds,
                    )
                    return page_number, (response.choices[0].message.content or "").strip()

            results = await asyncio.gather(
                *(
                    recognize(page_number, encoded)
                    for page_number, encoded in rendered_pages
                ),
                return_exceptions=True,
            )
            failed = 0
            for page_number, result in zip((item[0] for item in rendered_pages), results):
                if isinstance(result, tuple) and result[1]:
                    texts[page_number] = result[1]
                else:
                    failed += 1
                    if isinstance(result, Exception):
                        logger.warning(
                            "PDF page OCR failed",
                            extra={"source_name": path.name, "page": page_number, "error": type(result).__name__},
                        )
        except Exception:
            logger.exception("PDF rendering failed", extra={"source_name": path.name})
            return texts, failed or 1
        return texts, failed

    async def _try_pdf_ocr(self, path: Path) -> str:
        """兼容旧调用方：把逐页结果合并成一段文本。"""
        pages, _ = await self._try_pdf_ocr_pages(path)
        return "\n\n".join(f"[Page {number}]\n{text}" for number, text in sorted(pages.items()))

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            job = await session.get(IngestionJob, job_id)
            if not job:
                return None
            source = await session.get(KnowledgeSource, job.source_id)
            return {
                "id": job.id,
                "source_id": job.source_id,
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "page_count": source.page_count if source else 0,
                "error": source.error if source else None,
            }

    async def get_source_pages(self, source_id: str) -> list[dict[str, Any]] | None:
        """按页返回 OCR 摘要和型号候选，供人工审核页面使用。"""
        async with self.session_factory() as session:
            source = await session.get(KnowledgeSource, source_id)
            if not source:
                return None
            rows = (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.source_id == source_id).order_by(DocumentPage.page_number)
                )
            ).scalars()
            return [
                {
                    "page_number": page.page_number,
                    "status": page.status,
                    "text": page.text,
                    "model_candidates": (page.structured_data or {}).get("model_candidates", []),
                    "review_status": (page.structured_data or {}).get("review_status", "needs_review"),
                    "source_ref": {"source_id": source_id, "source_page": page.page_number},
                }
                for page in rows
            ]

    async def start_interview(self) -> dict[str, Any]:
        # 每次开始访谈都创建新的画像草稿，避免覆盖已发布版本。
        async with self.session_factory() as session:
            profile = CompanyProfile(status="draft", data={"answers": {}})
            session.add(profile)
            await session.flush()
            interview = InterviewSession(profile_id=profile.id)
            session.add(interview)
            await session.commit()
            return {
                "session_id": interview.id,
                "profile_id": profile.id,
                "question_index": 0,
                "total_questions": len(INTERVIEW_QUESTIONS),
                "question": INTERVIEW_QUESTIONS[0],
                "status": "active",
            }

    async def answer_interview(self, session_id: str, question: str, answer: str) -> dict[str, Any] | None:
        # 保存当前答案并推进游标；十问完成后进入人工审核状态。
        async with self.session_factory() as session:
            interview = await session.get(InterviewSession, session_id)
            if not interview:
                return None
            interview.answers = {**interview.answers, question: answer}
            interview.current_index += 1
            profile = await session.get(CompanyProfile, interview.profile_id)
            if profile:
                profile.data = {"answers": interview.answers}
            if interview.current_index >= len(INTERVIEW_QUESTIONS):
                interview.status = "complete"
                if profile:
                    profile.status = "reviewing"
            await session.commit()
            return {
                "session_id": interview.id,
                "profile_id": interview.profile_id,
                "question_index": interview.current_index,
                "total_questions": len(INTERVIEW_QUESTIONS),
                "question": INTERVIEW_QUESTIONS[interview.current_index] if interview.current_index < len(INTERVIEW_QUESTIONS) else None,
                "status": interview.status,
            }

    async def publish_profile(self, profile_id: str) -> bool:
        # 只有管理员显式发布后，企业画像才可作为正式销售资料。
        async with self.session_factory() as session:
            profile = await session.get(CompanyProfile, profile_id)
            if not profile:
                return False
            profile.status = "published"
            await session.commit()
            return True

    async def analyze_inquiry(self, filename: str, content_type: str, data: bytes, message: str = "") -> tuple[InquiryAnalysis, SalesDraftResponse]:
        # 原图、结构化分析和草稿一次关联保存，方便回看来源和修改记录。
        path = await self.store_file(filename, content_type, data, "inquiries")
        analysis_data = await self._vision_analysis(path, content_type, message)
        inquiry_id = new_id()
        analysis_data["source_image_id"] = inquiry_id
        analysis_data = self._normalize_analysis(analysis_data, message, filename)
        matched = self._match_products(" ".join([message, filename, *analysis_data.get("products", [])]))
        analysis_data["products"] = list(dict.fromkeys(analysis_data.get("products", []) + [product.name for product in matched]))
        analysis_data["source_refs"] = [
            {"type": "inquiry_image", "source_id": inquiry_id, "label": Path(filename).name},
            *[
                {
                    "type": "product_catalog",
                    "source_id": product.id,
                    "label": f"config/sales-catalog.yaml · {product.name}" + (f" · 图册编号 {product.catalog_range}" if getattr(product, "catalog_range", "") else ""),
                }
                for product in matched
            ],
        ]
        analysis = InquiryAnalysis.model_validate(analysis_data)
        draft = await self._create_draft(inquiry_id, analysis, matched)
        async with self.session_factory() as session:
            session.add(Inquiry(id=inquiry_id, source_image_path=str(path), original_filename=Path(filename).name, analysis=analysis.model_dump()))
            session.add(SalesDraft(**draft.model_dump()))
            await session.commit()
        return analysis, draft

    async def _vision_analysis(self, path: Path, content_type: str, message: str) -> dict[str, Any]:
        # 视觉模型不可用时返回明确的 unknown，而不是猜测客户需求。
        fallback = {
            "language": "unknown",
            "customer_country": "unknown",
            "customer_company": "unknown",
            "products": [],
            "quantity": "unknown",
            "application": "unknown",
            "budget": "unknown",
            "delivery_date": "unknown",
            "certification": "unknown",
            "known_requirements": [message] if message else [],
            "missing_requirements": ["产品型号", "数量", "目的地", "交期"],
            "confidence": 0.2,
        }
        if not (self.client and self.settings.sales_vision_enabled):
            return fallback
        try:
            encoded = base64.b64encode(path.read_bytes()).decode()
            data_uri = f"data:{content_type or 'image/jpeg'};base64,{encoded}"
            prompt = (
                "You are an export sales inquiry analyst. Read the attached customer inquiry screenshot. "
                "Return JSON only with keys language, customer_country, customer_company, products, quantity, "
                "application, budget, delivery_date, certification, known_requirements, missing_requirements, confidence. "
                "Use unknown instead of guessing. Do not invent prices or delivery promises."
            )
            response = await self.client.chat.completions.create(
                model=self.settings.sales_vision_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": [{"type": "text", "text": message or "Analyze this inquiry."}, {"type": "image_url", "image_url": {"url": data_uri}}]},
                ],
                temperature=0,
                max_tokens=900,
            )
            parsed = _extract_json(response.choices[0].message.content or "")
            return {**fallback, **(parsed or {})} if parsed else fallback
        except Exception:
            logger.exception("Vision analysis failed", extra={"source_name": path.name})
            fallback["missing_requirements"] = ["视觉模型暂时不可用，请人工确认截图内容"]
            return fallback

    @staticmethod
    def _normalize_analysis(value: dict[str, Any], message: str, filename: str) -> dict[str, Any]:
        # 统一模型输出的数据类型，保证后续 Pydantic 校验和前端展示稳定。
        result = dict(value)
        result["products"] = [str(item) for item in result.get("products", []) if str(item).strip()]
        for key in ("language", "customer_country", "customer_company", "quantity", "application", "budget", "delivery_date", "certification"):
            result[key] = str(result.get(key) or "unknown")
        result["known_requirements"] = [str(item) for item in result.get("known_requirements", []) if str(item).strip()]
        result["missing_requirements"] = [str(item) for item in result.get("missing_requirements", []) if str(item).strip()]
        try:
            result["confidence"] = max(0, min(1, float(result.get("confidence", 0))))
        except (TypeError, ValueError):
            result["confidence"] = 0
        if message and message not in result["known_requirements"]:
            result["known_requirements"].append(message)
        if not result["known_requirements"]:
            result["known_requirements"].append(f"原始文件：{filename}")
        return result

    def _match_products(self, query: str) -> list[Any]:
        # 先匹配外贸销售目录，再回退到网站演示目录，避免系列名称被泛词覆盖。
        needle = query.casefold()
        found: list[SalesCatalogEntry] = []
        for item in self.series:
            terms = [item.get("name", ""), *item.get("aliases", [])]
            if any(str(term).casefold() in needle for term in terms if term):
                found.append(SalesCatalogEntry(str(item.get("id")), str(item.get("name")), str(item.get("summary")), str(item.get("catalog_range", ""))))
        if found:
            return found[:3]
        # Keep the existing website catalog available for local/demo inquiries,
        # but never let a broad alias such as "light" override a Kook series.
        return self.catalog.find(query, limit=3)

    async def _create_draft(self, inquiry_id: str, analysis: InquiryAnalysis, products: list[Any]) -> SalesDraftResponse:
        # 先生成安全固定模板，再让模型润色；事实和待确认项始终由代码控制。
        warnings = ["价格、MOQ、库存、交期和付款条件未从图册推断，需业务员确认后发送。"]
        if analysis.confidence < 0.7:
            warnings.append("询盘识别置信度较低，建议先向客户确认产品型号、数量和目的地。")
        cited_facts: list[dict[str, Any]] = []
        product_lines: list[str] = []
        if products:
            for product in products:
                fact = {"type": "catalog", "source_id": product.id, "label": f"config/sales-catalog.yaml · {product.name}"}
                if getattr(product, "catalog_range", ""):
                    fact["catalog_range"] = product.catalog_range
                    fact["label"] += f" · 图册编号 {product.catalog_range}"
                cited_facts.append(fact)
                product_lines.append(f"- {product.name}: {product.summary}")
        else:
            product_lines.append("- Product model: to be confirmed")
        missing = ", ".join(analysis.missing_requirements[:5]) or "product model and quantity"
        body = (
            "Hello,\n\n"
            "Thank you for your inquiry. We would be happy to recommend the right lighting solution for your project.\n\n"
            f"Based on the information received:\n{chr(10).join(product_lines)}\n\n"
            f"Could you please confirm {missing}? Once confirmed, we will check the latest quotation and lead time for you.\n\n"
            "Best regards,\nSales Team"
        )
        model_name = "deterministic-fallback"
        if self.client and self.settings.llm_configured:
            try:
                prompt = (
                    "Write a concise professional English export-sales reply. Only use the supplied facts; "
                    "never invent price, MOQ, stock, lead time, payment or certifications. Return plain text.\n\n"
                    f"Inquiry: {analysis.model_dump_json()}\nFacts: {json.dumps(cited_facts, ensure_ascii=False)}\nDraft:\n{body}"
                )
                response = await self.client.chat.completions.create(
                    model=self.settings.sales_text_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=700,
                )
                generated = (response.choices[0].message.content or "").strip()
                if generated:
                    body = generated
                    model_name = self.settings.sales_text_model
            except Exception:
                logger.exception("Sales draft generation failed", extra={"inquiry_id": inquiry_id})
                warnings.append("文本模型暂时不可用，已使用固定安全模板。")
        return SalesDraftResponse(
            id=new_id(),
            inquiry_id=inquiry_id,
            subject="Re: Your lighting inquiry",
            body=body,
            language="English",
            summary=f"识别到 {len(products)} 个可能相关产品；待确认字段：{missing}。",
            next_actions=[f"确认：{missing}", "确认后查询最新价格、MOQ 和交期", "人工审核英文草稿后复制发送"],
            cited_facts=cited_facts,
            warnings=warnings,
            requires_human_review=True,
            model_name=model_name,
            prompt_version="sales-p0-v1",
        )

    async def regenerate_draft(self, draft_id: str, instructions: str = "") -> SalesDraftResponse | None:
        # MVP 的重新生成先记录业务员备注，不自动向客户发送任何消息。
        async with self.session_factory() as session:
            draft = await session.get(SalesDraft, draft_id)
            if not draft:
                return None
            if instructions:
                draft.body = f"{draft.body}\n\n[业务员备注：{instructions}]"
                draft.updated_at = utc_now()
                await session.commit()
            return SalesDraftResponse.model_validate(draft, from_attributes=True)

    async def draft_sources(self, draft_id: str) -> list[dict[str, Any]] | None:
        # 单独提供事实来源接口，让前端可以展示“这句话来自哪里”。
        async with self.session_factory() as session:
            draft = await session.get(SalesDraft, draft_id)
            return draft.cited_facts if draft else None
