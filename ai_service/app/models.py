from typing import Any, Literal

from pydantic import BaseModel, Field


class PriceRow(BaseModel):
    label: str
    price: str


class Product(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    summary: str
    image: str
    price_rows: list[PriceRow]
    features: list[str] = Field(default_factory=list)
    cta_label: str = "咨询这款"

    def tool_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "prices": [row.model_dump() for row in self.price_rows],
            "features": self.features,
        }


class FAQ(BaseModel):
    question: str
    answer: str
    keywords: list[str] = Field(default_factory=list)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class DirectChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)


class ProductPreview(BaseModel):
    id: str
    name: str
    summary: str
    image: str
    price: str
    features: list[str]


class DirectChatResponse(BaseModel):
    message: str
    products: list[ProductPreview] = Field(default_factory=list)
    handoff: bool = False


class WebhookEvent(BaseModel):
    event: str
    id: int | str | None = None
    content: str | None = None
    message_type: str | int | None = None
    private: bool = False
    sender: dict[str, Any] | None = None
    conversation: dict[str, Any] | None = None
    inbox: dict[str, Any] | None = None
    account: dict[str, Any] | None = None

    @property
    def conversation_id(self) -> int | None:
        source = self.conversation or {}
        value = source.get("id") or source.get("display_id")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def is_incoming_contact_message(self) -> bool:
        incoming = self.message_type in ("incoming", 0, "0")
        sender_type = str((self.sender or {}).get("type", "")).lower()
        return (
            self.event == "message_created"
            and incoming
            and not self.private
            and sender_type not in {"user", "agentbot", "agent_bot"}
        )
