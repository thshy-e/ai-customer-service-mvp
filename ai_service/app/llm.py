from typing import Any, Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str


class ModelReply(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ModelGateway(Protocol):
    async def run(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelReply: ...


class OpenAICompatibleGateway:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float) -> None:
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model

    async def run(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelReply:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.4,
            max_tokens=500,
        )
        message = response.choices[0].message
        calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments,
            )
            for call in (message.tool_calls or [])
        ]
        return ModelReply(text=message.content or "", tool_calls=calls)


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_business_status",
            "description": "查询当前是否在营业时间，以及下一次人工在线时间。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_products",
            "description": "按客户描述、商品名或别名查找真实商品。需要推荐或报价前必须调用。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_product",
            "description": "向客户发送指定商品的真实图片、规格和价格卡片。",
            "parameters": {
                "type": "object",
                "properties": {"product_id": {"type": "string"}},
                "required": ["product_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_to_human",
            "description": "客户要求人工、发生投诉争议或无法可靠回答时转人工。",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

