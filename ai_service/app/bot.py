import json
import re
from pathlib import Path
from typing import Any

import yaml

from .business import BusinessHours
from .catalog import Catalog
from .chatwoot import ChatwootClient
from .llm import ModelGateway, TOOLS
from .models import DirectChatResponse, Product, ProductPreview, WebhookEvent


PRICE_PATTERN = re.compile(r"(?:[¥￥]\s*\d|\d+(?:\.\d+)?\s*元|RMB\s*\d)", re.IGNORECASE)
HUMAN_TERMS = ("人工", "真人", "转客服", "客服人员", "投诉")
BUSINESS_TERMS = ("营业时间", "几点上班", "几点下班", "什么时候有人", "工作时间")
PRODUCT_MENU_TERMS = ("了解产品", "查看价格", "有什么产品", "产品列表", "价格表")


class CustomerServiceBot:
    def __init__(
        self,
        catalog: Catalog,
        business_hours: BusinessHours,
        chatwoot: ChatwootClient,
        model: ModelGateway | None,
        sales_policy_path: Path,
    ) -> None:
        self.catalog = catalog
        self.business_hours = business_hours
        self.chatwoot = chatwoot
        self.model = model
        with sales_policy_path.open(encoding="utf-8") as file:
            self.sales_policy = yaml.safe_load(file) or {}

    async def handle(self, event: WebhookEvent) -> None:
        conversation_id = event.conversation_id
        if not conversation_id:
            return

        if event.event == "conversation_created":
            await self.chatwoot.send_main_menu(conversation_id)
            return

        if not event.is_incoming_contact_message:
            return

        content = (event.content or "").strip()
        if not content:
            return

        if any(term in content for term in HUMAN_TERMS):
            await self._handoff(conversation_id)
            return

        if any(term in content for term in BUSINESS_TERMS):
            await self.chatwoot.send_message(conversation_id, self.business_hours.status().message)
            return

        if any(term in content for term in PRODUCT_MENU_TERMS):
            await self.chatwoot.send_product_menu(conversation_id, self.catalog.products)
            return

        product_id = self._postback_product_id(content)
        product_matches = [self.catalog.get(product_id)] if product_id else self.catalog.find(content)
        product_matches = [item for item in product_matches if item is not None]
        if len(product_matches) == 1:
            await self.chatwoot.send_product_cards(conversation_id, product_matches)
            await self.chatwoot.send_message(
                conversation_id,
                "你更关注使用场景、规格还是预算？告诉我后，我可以继续帮你判断是否合适。",
            )
            return
        if len(product_matches) > 1:
            await self.chatwoot.send_product_menu(conversation_id, product_matches)
            return

        faq = self.catalog.answer_faq(content)
        if faq:
            await self.chatwoot.send_message(conversation_id, faq.answer)
            return

        if self.model is None:
            await self.chatwoot.handoff(
                conversation_id,
                "AI 服务尚未配置好。我已将会话转给人工客服，营业时间内会尽快回复你。",
            )
            return

        await self._run_model(conversation_id, content)

    async def direct_reply(
        self, content: str, history: list[dict[str, str]] | None = None
    ) -> DirectChatResponse:
        content = content.strip()
        if any(term in content for term in HUMAN_TERMS):
            status = self.business_hours.status()
            message = (
                "好的，我现在为你转接人工客服，请稍候。"
                if status.is_open
                else f"我已记录你的人工咨询请求。{status.message}"
            )
            return DirectChatResponse(message=message, handoff=True)
        if any(term in content for term in BUSINESS_TERMS):
            return DirectChatResponse(message=self.business_hours.status().message)

        matches = self.catalog.find(content)
        if len(matches) == 1:
            product = matches[0]
            return DirectChatResponse(
                message="我找到了这款商品。你更关注使用场景、规格还是预算？",
                products=[self._product_preview(product)],
            )
        if len(matches) > 1 or any(term in content for term in PRODUCT_MENU_TERMS):
            products = matches or self.catalog.products
            return DirectChatResponse(
                message="目前可以了解这些商品，选择一款我再为你介绍。",
                products=[self._product_preview(item) for item in products],
            )

        faq = self.catalog.answer_faq(content)
        if faq:
            return DirectChatResponse(message=faq.answer)
        if self.model is None:
            return DirectChatResponse(
                message="AI 服务暂时不可用，我已记录你的问题，请联系人工客服。",
                handoff=True,
            )
        return await self._direct_model_reply(content, history or [])

    async def _direct_model_reply(
        self, content: str, history: list[dict[str, str]]
    ) -> DirectChatResponse:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            *history[-10:],
            {"role": "user", "content": content},
        ]
        product_data_retrieved = False
        shown_products: list[Product] = []

        for _ in range(4):
            reply = await self.model.run(messages, TOOLS)
            assistant_message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
            if reply.tool_calls:
                assistant_message["tool_calls"] = [
                    {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                    for call in reply.tool_calls
                ]
            messages.append(assistant_message)

            if not reply.tool_calls:
                text = reply.text.strip() or "你可以再补充一下使用场景和预算。"
                if PRICE_PATTERN.search(text) and not product_data_retrieved:
                    text = "为了避免报错价格，请先告诉我具体想了解哪一款产品。"
                text = re.sub(r"[*_#`]", "", text)
                return DirectChatResponse(
                    message=text[:1200],
                    products=[self._product_preview(item) for item in shown_products],
                )

            for call in reply.tool_calls:
                try:
                    arguments = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                handed_off = False
                if call.name == "get_business_status":
                    result = self.business_hours.status().model_dump(mode="json")
                elif call.name == "find_products":
                    products = self.catalog.find(str(arguments.get("query", "")))
                    result = {"products": [item.tool_payload() for item in products]}
                    product_data_retrieved = product_data_retrieved or bool(products)
                elif call.name == "show_product":
                    product = self.catalog.get(str(arguments.get("product_id", "")))
                    result = {"product": product.tool_payload()} if product else {"error": "product_not_found"}
                    if product:
                        shown_products.append(product)
                        product_data_retrieved = True
                elif call.name == "handoff_to_human":
                    result = {"handed_off": True}
                    handed_off = True
                else:
                    result = {"error": "unknown_tool"}
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": json.dumps(result, ensure_ascii=False)})
                if handed_off:
                    return DirectChatResponse(message="好的，我已为你记录人工咨询请求。", handoff=True)

        return DirectChatResponse(message="这个问题需要人工进一步确认，我已帮你记录。", handoff=True)

    @staticmethod
    def _product_preview(product: Product) -> ProductPreview:
        return ProductPreview(
            id=product.id,
            name=product.name,
            summary=product.summary,
            image=product.image,
            price=product.price_rows[0].price if product.price_rows else "价格待确认",
            features=product.features,
        )

    async def _run_model(self, conversation_id: int, content: str) -> None:
        history = await self.chatwoot.recent_messages(conversation_id)
        if not history or history[-1].get("content") != content:
            history.append({"role": "user", "content": content})

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            *history[-12:],
        ]
        product_data_retrieved = False

        for _ in range(4):
            reply = await self.model.run(messages, TOOLS)
            assistant_message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
            if reply.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in reply.tool_calls
                ]
            messages.append(assistant_message)

            if not reply.tool_calls:
                text = reply.text.strip()
                if PRICE_PATTERN.search(text) and not product_data_retrieved:
                    text = "为了避免报错价格，请先告诉我具体想了解哪一款产品。"
                if text:
                    await self.chatwoot.send_message(conversation_id, text[:1200])
                return

            for call in reply.tool_calls:
                try:
                    arguments = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result, retrieved, handed_off = await self._execute_tool(
                    conversation_id, call.name, arguments
                )
                product_data_retrieved = product_data_retrieved or retrieved
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                if handed_off:
                    return

        await self._handoff(conversation_id)

    async def _execute_tool(
        self, conversation_id: int, name: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], bool, bool]:
        if name == "get_business_status":
            return self.business_hours.status().model_dump(mode="json"), False, False

        if name == "find_products":
            products = self.catalog.find(str(arguments.get("query", "")))
            return {"products": [item.tool_payload() for item in products]}, bool(products), False

        if name == "show_product":
            product = self.catalog.get(str(arguments.get("product_id", "")))
            if not product:
                return {"error": "product_not_found"}, False, False
            await self.chatwoot.send_product_cards(conversation_id, [product])
            return {"sent": True, "product": product.tool_payload()}, True, False

        if name == "handoff_to_human":
            await self._handoff(conversation_id)
            return {"handed_off": True}, False, True

        return {"error": "unknown_tool"}, False, False

    async def _handoff(self, conversation_id: int) -> None:
        status = self.business_hours.status()
        if status.is_open:
            message = "好的，我现在为你转接人工客服，请稍候。"
        else:
            message = f"我已记录你的人工咨询请求。{status.message}"
        await self.chatwoot.handoff(conversation_id, message)

    @staticmethod
    def _postback_product_id(content: str) -> str | None:
        return content.removeprefix("product:") if content.startswith("product:") else None

    def _system_prompt(self) -> str:
        policy = yaml.safe_dump(self.sales_policy, allow_unicode=True, sort_keys=False)
        return f"""你是网站在线产品顾问。用自然、专业、简洁的中文沟通。

销售规范：
{policy}

可识别的商品目录（这里只用于识别；价格必须通过 find_products 或 show_product 获取）：
{self.catalog.product_summary_for_prompt()}

规则：
1. 推荐或报价前必须调用 find_products；展示某款商品必须调用 show_product。
2. 不得自行编造价格、图片、库存、优惠、营业时间或政策。
3. 客户意图模糊时，优先追问使用场景、预算和关键偏好。
4. 客户要求人工、投诉或你无法可靠回答时，调用 handoff_to_human。
5. 每次回复不超过 180 个汉字，使用纯文本，不使用 Markdown 符号。
"""
