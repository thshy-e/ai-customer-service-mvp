import pytest


@pytest.mark.parametrize(
    ("query", "product_id"),
    [
        ("我想看看桌面灯", "demo-light"),
        ("台灯多少钱", "demo-light"),
        ("阅读灯适合床头吗", "demo-light"),
        ("保温杯有多大", "demo-tumbler"),
        ("随行杯价格", "demo-tumbler"),
        ("demo tumbler", "demo-tumbler"),
    ],
)
def test_product_alias_matching(catalog, query, product_id):
    matches = catalog.find(query)
    assert matches
    assert matches[0].id == product_id


def test_unknown_product_is_not_guessed(catalog):
    assert catalog.find("我想买一个完全不存在的东西") == []


def test_faq_matches_payment(catalog):
    faq = catalog.answer_faq("支持支付宝付款吗")
    assert faq is not None
    assert "演示配置" in faq.answer


def test_catalog_tool_payload_uses_configured_prices(catalog):
    product = catalog.get("demo-light")
    assert product is not None
    assert product.tool_payload()["prices"][0] == {"label": "标准款", "price": "¥199"}

