from collections.abc import Iterable
from typing import Any

import httpx

from .models import Product


class ChatwootError(RuntimeError):
    pass


class ChatwootClient:
    def __init__(
        self,
        base_url: str,
        account_id: int,
        api_token: str,
        public_asset_base_url: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.api_token = api_token
        self.public_asset_base_url = public_asset_base_url.rstrip("/")
        self.http = http_client or httpx.AsyncClient(timeout=15)

    @property
    def headers(self) -> dict[str, str]:
        return {"api_access_token": self.api_token, "Content-Type": "application/json"}

    def _conversation_url(self, conversation_id: int, suffix: str = "") -> str:
        root = f"{self.base_url}/api/v1/accounts/{self.account_id}/conversations/{conversation_id}"
        return f"{root}/{suffix}" if suffix else root

    async def send_message(
        self,
        conversation_id: int,
        content: str,
        *,
        content_type: str = "text",
        content_attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "content": content,
            "message_type": "outgoing",
            "private": False,
            "content_type": content_type,
        }
        if content_attributes is not None:
            payload["content_attributes"] = content_attributes
        response = await self.http.post(
            self._conversation_url(conversation_id, "messages"),
            headers=self.headers,
            json=payload,
        )
        if response.is_error:
            raise ChatwootError(f"Chatwoot message failed: {response.status_code} {response.text[:300]}")
        return response.json()

    async def send_main_menu(self, conversation_id: int) -> None:
        items = [
            {"title": "了解产品", "value": "了解产品"},
            {"title": "查看价格", "value": "查看价格"},
            {"title": "营业时间", "value": "营业时间"},
            {"title": "联系人工", "value": "联系人工"},
        ]
        await self.send_message(
            conversation_id,
            "你好，我是在线产品顾问。请选择一项，或直接告诉我你想了解什么。",
            content_type="input_select",
            content_attributes={"items": items},
        )

    async def send_product_menu(self, conversation_id: int, products: Iterable[Product]) -> None:
        items = [{"title": item.name, "value": f"我想了解{item.name}"} for item in products]
        await self.send_message(
            conversation_id,
            "请选择想了解的产品：",
            content_type="input_select",
            content_attributes={"items": items},
        )

    async def send_product_cards(self, conversation_id: int, products: Iterable[Product]) -> None:
        cards = []
        for product in products:
            prices = "；".join(f"{row.label} {row.price}" for row in product.price_rows)
            features = "、".join(product.features)
            description = f"{product.summary}\n价格：{prices}"
            if features:
                description += f"\n特点：{features}"
            image_url = product.image
            if image_url.startswith("/"):
                image_url = f"{self.public_asset_base_url}{image_url}"
            cards.append(
                {
                    "media_url": image_url,
                    "title": product.name,
                    "description": description,
                    "actions": [
                        {
                            "type": "postback",
                            "text": product.cta_label,
                            "payload": f"product:{product.id}",
                        }
                    ],
                }
            )
        await self.send_message(
            conversation_id,
            "为你找到以下产品：",
            content_type="cards",
            content_attributes={"items": cards},
        )

    async def handoff(self, conversation_id: int, message: str) -> None:
        await self.send_message(conversation_id, message)
        response = await self.http.post(
            self._conversation_url(conversation_id, "toggle_status"),
            headers=self.headers,
            json={"status": "open"},
        )
        if response.is_error:
            raise ChatwootError(f"Chatwoot handoff failed: {response.status_code} {response.text[:300]}")

    async def recent_messages(self, conversation_id: int, limit: int = 12) -> list[dict[str, str]]:
        response = await self.http.get(self._conversation_url(conversation_id), headers=self.headers)
        if response.is_error:
            return []
        data = response.json()
        messages = data.get("messages") or data.get("payload", {}).get("messages") or []
        history: list[dict[str, str]] = []
        for item in messages[-limit:]:
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            message_type = item.get("message_type")
            role = "user" if message_type in (0, "0", "incoming") else "assistant"
            history.append({"role": role, "content": content})
        return history

