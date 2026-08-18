import json
from pathlib import Path

import pytest

from app.bot import CustomerServiceBot
from app.llm import ModelReply, ToolCall
from app.models import WebhookEvent


class FakeChatwoot:
    def __init__(self):
        self.messages = []
        self.cards = []
        self.menus = []
        self.handoffs = []

    async def send_main_menu(self, conversation_id):
        self.menus.append((conversation_id, "main"))

    async def send_product_menu(self, conversation_id, products):
        self.menus.append((conversation_id, [item.id for item in products]))

    async def send_product_cards(self, conversation_id, products):
        self.cards.append((conversation_id, [item.id for item in products]))

    async def send_message(self, conversation_id, content, **kwargs):
        self.messages.append((conversation_id, content, kwargs))

    async def handoff(self, conversation_id, message):
        self.handoffs.append((conversation_id, message))

    async def recent_messages(self, conversation_id):
        return []


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)

    async def run(self, messages, tools):
        return self.replies.pop(0)


def event(content=None, event_name="message_created"):
    return WebhookEvent(
        event=event_name,
        id=10,
        content=content,
        message_type="incoming" if event_name == "message_created" else None,
        sender={"type": "contact"},
        conversation={"id": 7},
    )


def make_bot(catalog, business_hours, chatwoot, model, config_dir: Path):
    return CustomerServiceBot(
        catalog,
        business_hours,
        chatwoot,
        model,
        config_dir / "sales-policy.yaml",
    )


@pytest.mark.asyncio
async def test_conversation_created_sends_menu(catalog, business_hours, config_dir):
    chatwoot = FakeChatwoot()
    bot = make_bot(catalog, business_hours, chatwoot, None, config_dir)
    await bot.handle(event(event_name="conversation_created"))
    assert chatwoot.menus == [(7, "main")]


@pytest.mark.asyncio
async def test_product_alias_sends_exact_card(catalog, business_hours, config_dir):
    chatwoot = FakeChatwoot()
    bot = make_bot(catalog, business_hours, chatwoot, None, config_dir)
    await bot.handle(event("台灯多少钱"))
    assert chatwoot.cards == [(7, ["demo-light"])]
    assert "预算" in chatwoot.messages[-1][1]


@pytest.mark.asyncio
async def test_product_menu_choice(catalog, business_hours, config_dir):
    chatwoot = FakeChatwoot()
    bot = make_bot(catalog, business_hours, chatwoot, None, config_dir)
    await bot.handle(event("查看价格"))
    assert chatwoot.menus == [(7, ["demo-light", "demo-tumbler"])]


@pytest.mark.asyncio
async def test_human_request_hands_off(catalog, business_hours, config_dir):
    chatwoot = FakeChatwoot()
    bot = make_bot(catalog, business_hours, chatwoot, None, config_dir)
    await bot.handle(event("我要找人工客服"))
    assert len(chatwoot.handoffs) == 1


@pytest.mark.asyncio
async def test_business_question_is_deterministic(catalog, business_hours, config_dir):
    chatwoot = FakeChatwoot()
    bot = make_bot(catalog, business_hours, chatwoot, None, config_dir)
    await bot.handle(event("你们几点下班"))
    assert "营业" in chatwoot.messages[-1][1] or "人工在线" in chatwoot.messages[-1][1]


@pytest.mark.asyncio
async def test_missing_model_hands_off_unknown_question(catalog, business_hours, config_dir):
    chatwoot = FakeChatwoot()
    bot = make_bot(catalog, business_hours, chatwoot, None, config_dir)
    await bot.handle(event("帮我分析一下该怎么选"))
    assert "AI 服务尚未配置" in chatwoot.handoffs[-1][1]


@pytest.mark.asyncio
async def test_model_cannot_quote_without_product_tool(catalog, business_hours, config_dir):
    chatwoot = FakeChatwoot()
    model = FakeModel([ModelReply(text="这款现在售价¥999。")])
    bot = make_bot(catalog, business_hours, chatwoot, model, config_dir)
    await bot.handle(event("你有什么推荐"))
    assert chatwoot.messages[-1][1] == "为了避免报错价格，请先告诉我具体想了解哪一款产品。"


@pytest.mark.asyncio
async def test_model_tool_can_send_product_card(catalog, business_hours, config_dir):
    chatwoot = FakeChatwoot()
    model = FakeModel(
        [
            ModelReply(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="show_product",
                        arguments=json.dumps({"product_id": "demo-tumbler"}),
                    )
                ]
            ),
            ModelReply(text="这款有两种容量，你平时更看重便携还是容量？"),
        ]
    )
    bot = make_bot(catalog, business_hours, chatwoot, model, config_dir)
    await bot.handle(event("推荐一个适合通勤喝水的"))
    assert chatwoot.cards == [(7, ["demo-tumbler"])]
    assert "便携" in chatwoot.messages[-1][1]


@pytest.mark.asyncio
async def test_outgoing_messages_are_ignored(catalog, business_hours, config_dir):
    chatwoot = FakeChatwoot()
    bot = make_bot(catalog, business_hours, chatwoot, None, config_dir)
    outgoing = event("机器人自己的消息")
    outgoing.message_type = "outgoing"
    outgoing.sender = {"type": "user"}
    await bot.handle(outgoing)
    assert not chatwoot.messages and not chatwoot.handoffs


@pytest.mark.asyncio
async def test_direct_chat_returns_controlled_product(catalog, business_hours, config_dir):
    bot = make_bot(catalog, business_hours, FakeChatwoot(), None, config_dir)
    reply = await bot.direct_reply("我想看看台灯")
    assert reply.products[0].id == "demo-light"
    assert reply.products[0].price == "¥199"


@pytest.mark.asyncio
async def test_direct_chat_uses_model_for_open_question(catalog, business_hours, config_dir):
    model = FakeModel([ModelReply(text="你主要在书桌还是床头使用？预算大约是多少？")])
    bot = make_bot(catalog, business_hours, FakeChatwoot(), model, config_dir)
    reply = await bot.direct_reply("帮我分析一下怎么选")
    assert "预算" in reply.message
