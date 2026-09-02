from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 所有运行参数集中在这里，环境变量可以覆盖默认值。
    app_env: str = "development"
    log_level: str = "INFO"
    config_dir: Path = Path("/app/config")
    redis_url: str = "redis://localhost:6379/0"

    chatwoot_api_url: str = "http://localhost:3000"
    chatwoot_account_id: int = 1
    chatwoot_api_token: str = ""
    chatwoot_webhook_secret: str = ""

    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_timeout_seconds: float = 25

    public_asset_base_url: str = "http://localhost:8080"

    # 销售助手本地默认使用 SQLite，部署到服务器时可切换 PostgreSQL。
    sales_database_url: str = "sqlite+aiosqlite:///./data/sales.db"
    sales_upload_dir: Path = Path("./data/uploads")
    sales_session_secret: str = ""
    sales_admin_email: str = "admin@example.com"
    sales_admin_password: str = "change-me"
    sales_user_email: str = "sales@example.com"
    sales_user_password: str = "change-me"
    sales_max_upload_mb: int = 100
    sales_vision_enabled: bool = True
    sales_ocr_enabled: bool = True
    sales_ocr_concurrency: int = 3
    sales_ocr_page_timeout_seconds: float = 35
    # 视觉任务不能使用纯文本模型；当前 MVP 默认使用已验证可用的视觉模型。
    sales_vision_model: str = "qwen3-vl-plus"
    sales_ocr_model: str = "qwen3-vl-plus"
    sales_text_model: str = "qwen-plus"
    sales_fast_model: str = "qwen3.7-flash"
    sales_embedding_model: str = "qwen3.7-text-embedding"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model)


settings = Settings()
