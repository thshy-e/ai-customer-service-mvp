import re
from pathlib import Path

import yaml

from .models import FAQ, Product


def normalize(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


class Catalog:
    def __init__(self, products: list[Product], faqs: list[FAQ]) -> None:
        self.products = products
        self.faqs = faqs
        self._by_id = {product.id: product for product in products}

    @classmethod
    def from_directory(cls, config_dir: Path) -> "Catalog":
        # 商品、FAQ 分开维护，便于非开发人员只修改配置文件。
        with (config_dir / "products.yaml").open(encoding="utf-8") as file:
            product_data = yaml.safe_load(file) or {}
        with (config_dir / "faq.yaml").open(encoding="utf-8") as file:
            faq_data = yaml.safe_load(file) or {}
        return cls(
            products=[Product.model_validate(item) for item in product_data.get("products", [])],
            faqs=[FAQ.model_validate(item) for item in faq_data.get("faqs", [])],
        )

    def get(self, product_id: str) -> Product | None:
        return self._by_id.get(product_id)

    def find(self, query: str, limit: int = 3) -> list[Product]:
        # 先按名称和别名做轻量匹配，MVP 不引入向量库也能覆盖常见问法。
        needle = normalize(query)
        if not needle:
            return []

        scored: list[tuple[int, Product]] = []
        for product in self.products:
            terms = [product.id, product.name, *product.aliases]
            score = 0
            for term in (normalize(item) for item in terms):
                if not term:
                    continue
                if needle == term:
                    score = max(score, 100)
                elif term in needle:
                    score = max(score, 80 + min(len(term), 15))
                elif needle in term and len(needle) >= 2:
                    score = max(score, 60 + min(len(needle), 15))
            if score:
                scored.append((score, product))

        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [product for _, product in scored[:limit]]

    def answer_faq(self, query: str) -> FAQ | None:
        # FAQ 采用关键词命中；没有明确命中时交给模型或人工处理。
        needle = normalize(query)
        matches = [
            faq
            for faq in self.faqs
            if any(normalize(keyword) in needle for keyword in faq.keywords if normalize(keyword))
        ]
        return matches[0] if len(matches) == 1 else None

    def product_summary_for_prompt(self) -> str:
        return "\n".join(
            f"- {product.id}: {product.name}; aliases={','.join(product.aliases)}; {product.summary}"
            for product in self.products
        )
