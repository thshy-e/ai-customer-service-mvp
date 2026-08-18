import json

import httpx
import pytest

from app.chatwoot import ChatwootClient


@pytest.mark.asyncio
async def test_product_card_uses_public_asset_url(catalog):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": 1})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ChatwootClient("http://chatwoot", 1, "token", "http://localhost:8080", http)
    await client.send_product_cards(7, [catalog.get("demo-light")])

    payload = json.loads(requests[0].content)
    assert payload["content_type"] == "cards"
    card = payload["content_attributes"]["items"][0]
    assert card["media_url"] == "http://localhost:8080/assets/demo-light.png"
    assert "¥199" in card["description"]
    await http.aclose()


@pytest.mark.asyncio
async def test_handoff_opens_conversation():
    payloads = []

    async def handler(request):
        payloads.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ChatwootClient("http://chatwoot", 1, "token", "http://localhost:8080", http)
    await client.handoff(7, "正在转人工")

    assert payloads[0][0].endswith("/messages")
    assert payloads[1][0].endswith("/toggle_status")
    assert payloads[1][1] == {"status": "open"}
    await http.aclose()


@pytest.mark.asyncio
async def test_main_menu_is_interactive():
    captured = {}

    async def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ChatwootClient("http://chatwoot", 1, "token", "http://localhost:8080", http)
    await client.send_main_menu(7)

    assert captured["content_type"] == "input_select"
    assert [item["title"] for item in captured["content_attributes"]["items"]] == [
        "了解产品",
        "查看价格",
        "营业时间",
        "联系人工",
    ]
    await http.aclose()

