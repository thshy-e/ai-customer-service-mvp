from pathlib import Path

import pytest

from app.catalog import Catalog
from app.sales_auth import authenticate, decode_token, issue_token
from app.sales_db import create_sales_database
from app.sales_service import SalesService
from app.settings import Settings


@pytest.fixture
async def sales_service(tmp_path: Path, config_dir: Path) -> SalesService:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'sales.db'}"
    engine, sessions = await create_sales_database(database_url, tmp_path / "uploads")
    service = SalesService(
        Settings(
            config_dir=config_dir,
            sales_database_url=database_url,
            sales_upload_dir=tmp_path / "uploads",
            llm_api_key="",
            sales_vision_enabled=False,
        ),
        Catalog.from_directory(config_dir),
        sessions,
    )
    yield service
    await engine.dispose()


def test_sales_auth_round_trip() -> None:
    principal = authenticate("admin@example.com", "change-me")
    assert principal and principal.role == "admin"
    token = issue_token(principal.email, principal.role)
    decoded = decode_token(token)
    assert decoded and decoded.email == principal.email and decoded.role == "admin"


def test_model_candidates_are_conservative_and_normalized() -> None:
    text = "KK-LM800 / KK HF300A / KBF500 / DMX512 / IP20"
    assert SalesService._extract_model_candidates(text) == ["KK-LM800", "KK-HF300A", "KBF500"]


@pytest.mark.asyncio
async def test_knowledge_job_keeps_image_pages_as_reviewable(sales_service: SalesService) -> None:
    source, job = await sales_service.create_source("catalog.png", "image/png", b"not-a-real-image")
    await sales_service.process_source(source.id, job.id)
    result = await sales_service.get_job(job.id)
    assert result
    assert result["status"] == "succeeded_with_warnings"
    assert result["page_count"] == 1
    assert "视觉 OCR" in result["message"]


@pytest.mark.asyncio
async def test_inquiry_draft_uses_series_match_and_never_invents_quote(sales_service: SalesService) -> None:
    analysis, draft = await sales_service.analyze_inquiry(
        "customer.png",
        "image/png",
        b"not-a-real-image",
        "Customer needs 20 Moving Head Light units for Dubai.",
    )
    assert "Moving Head Light" in analysis.products
    assert all("演示" not in product for product in analysis.products)
    assert draft.requires_human_review is True
    assert draft.cited_facts
    assert "price" not in draft.body.lower()
    assert any("价格" in warning for warning in draft.warnings)
