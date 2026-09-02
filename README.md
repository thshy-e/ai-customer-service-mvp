# 24 小时 AI 客服 MVP

这是一个可自托管的最小客服系统，并新增了内部 AI 外贸销售助手：Chatwoot 负责网站聊天窗口、会话工作台、营业时间、通知和人工接管；FastAPI 负责网站 AgentBot、企业资料任务、询盘截图解析和带来源的英文销售草稿。

## 已实现

- 网站/H5 客服入口与 Chatwoot Widget 嵌入
- 首次会话交互菜单：了解产品、查看价格、营业时间、联系人工
- 阿里云百炼 `qwen-plus`，并兼容其他 OpenAI 格式模型
- YAML 商品库、商品别名识别、图片和价格卡片
- 营业时间与下一次人工在线时间
- Chatwoot `pending -> open` 人工接管
- Webhook HMAC 校验、消息去重、模型异常兜底
- 无 Chatwoot token 时的本地界面预览
- 内部销售工作台：企业资料上传、企业十问、询盘截图解析、英文草稿和来源展示
- 本地 SQLite 销售数据层，可通过 `SALES_DATABASE_URL` 切换 PostgreSQL

## 项目结构

```text
ai_service/                 FastAPI AgentBot 和测试
config/                     商品、FAQ、营业时间、销售规范
web/                        H5 客服入口、内部销售工作台和演示商品素材
docker-compose.yml          Chatwoot、AI、PostgreSQL、Redis、Web
.env.example                环境变量模板
```

## 1. 本地准备

要求 Docker Desktop 可用。完整 Chatwoot 建议至少为 Docker 分配 4 核 CPU 和 8GB 内存。

```bash
cp .env.example .env
openssl rand -hex 64   # 填入 SECRET_KEY_BASE
openssl rand -hex 24   # 分别填入 POSTGRES_PASSWORD 和 REDIS_PASSWORD
```

不要把 `.env` 提交到 Git。`LLM_API_KEY` 可以先留空，此时商品、FAQ、营业时间和转人工仍可测试；未知问题会自动转人工。

先验证配置并初始化数据库：

```bash
docker compose config --quiet
docker compose up -d postgres redis
docker compose run --rm rails bundle exec rails db:chatwoot_prepare
docker compose up -d
```

启动后：

- H5 页面：<http://localhost:8080>
- 内部销售工作台：<http://localhost:8080/sales.html>
- Chatwoot：<http://localhost:3000>
- AI 健康检查：<http://localhost:8010/health>

销售工作台默认登录账号来自 `.env`：`admin@example.com / change-me`（上线前必须替换）。管理员可以上传企业资料并完成十问，业务员可以分析询盘并生成草稿。第一版没有自动发送客户消息的接口。

## 2. 配置 Chatwoot

1. 打开 Chatwoot，完成首次管理员创建。
2. 在 `Settings -> Inboxes -> Add Inbox -> Website` 创建网站收件箱，网站地址填写 `http://localhost:8080`。
3. 从 Website Inbox 安装代码中复制 `websiteToken`，填入 `.env` 的 `CHATWOOT_WEBSITE_TOKEN`。
4. 在个人资料页复制 Access Token，填入 `CHATWOOT_API_TOKEN`。
5. 在 `Settings -> Bots` 新建 AgentBot：
   - 名称：`AI 产品顾问`
   - Webhook URL：`http://ai-service:8000/webhooks/chatwoot`
6. 将 AgentBot 关联到刚才的 Website Inbox。
7. 将 Bot 页面显示的 webhook secret 填入 `CHATWOOT_WEBHOOK_SECRET`。
8. 按真实营业时间配置 Chatwoot Inbox，并同步修改 `config/business-hours.yaml`。

更新 `.env` 后重建相关容器：

```bash
docker compose up -d --force-recreate ai-service web
```

本地开发允许 `CHATWOOT_WEBHOOK_SECRET` 为空；正式部署必须填写，否则 webhook 不会进行身份校验。

## 3. 配置大模型

在阿里云百炼创建 API Key，并填写：

```dotenv
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

服务通过 OpenAI 兼容协议调用模型。切换 DeepSeek 或其他兼容模型时，只需要修改以上三个变量。

销售助手的视觉和 OCR 请求使用 `SALES_VISION_MODEL`、`SALES_OCR_MODEL`，默认是已验证可识别图像的 `qwen3-vl-plus`；英文草稿继续使用 `SALES_TEXT_MODEL`，默认是 `qwen-plus`。图册分页结果可通过登录后的 `GET /knowledge/sources/{source_id}/pages` 查看，型号候选必须人工确认后才能进入正式产品库。

重启 AI 服务：

```bash
docker compose up -d --force-recreate ai-service
curl http://localhost:8010/health
```

`llm_configured` 为 `true` 表示模型已配置。健康检查不会消耗模型额度。

## 4. 替换真实业务资料

上线前必须替换所有演示内容：

- `config/products.yaml`：商品名、别名、卖点、规格、价格和图片路径
- `config/business-hours.yaml`：营业时间、时区和节假日
- `config/faq.yaml`：付款、配送、售后等事实
- `config/sales-policy.yaml`：品牌名、销售目标和禁用话术
- `web/assets/`：真实商品图片
- `web/index.html.template`：品牌名和首页文案

本地图片放在 `web/assets/`，YAML 中使用 `/assets/文件名`。部署到公网后，将 `PUBLIC_ASSET_BASE_URL` 改为 H5 的公网 HTTPS 地址，否则 Chatwoot 商品卡片无法加载图片。

销售助手上传的 PDF、DOCX 和截图默认保存在 `sales_data` 卷的 `/app/data`。生产部署建议设置 `SALES_DATABASE_URL=postgresql+asyncpg://...` 并把文件存储替换为 OSS/MinIO；当前 MVP 已记录来源 ID 和页码，但尚未提供资料管理后台。

## 5. 验收路径

按以下顺序完成一次端到端检查：

1. 打开 H5，点击“开始咨询”。
2. 新会话显示四项菜单。
3. 输入“台灯多少钱”，收到桌面灯图片与价格卡片。
4. 输入“你们几点下班”，收到当前营业状态。
5. 输入“帮我按预算推荐”，由模型追问或推荐商品。
6. 输入“联系人工”，会话状态变为 `open`，Chatwoot 坐席可接管。
7. 重复发送同一个 webhook，确认只回复一次。
8. 临时移除 `LLM_API_KEY`，确认未知问题转人工。

## 测试

```bash
python3 -m venv .venv
.venv/bin/pip install -r ai_service/requirements-dev.txt
.venv/bin/pytest -q ai_service/tests
```

测试覆盖商品别名、价格事实源、营业时间、菜单、商品卡片、人工接管、价格防幻觉、Webhook 签名和事件去重。

## MVP 限制

- 只有一个网站入口、一个商品配置源和一个模型实例。
- 商品与营业资料变更后需要重启 `ai-service`。
- 未实现订单、支付、会员、向量检索、LangGraph 持久化工作流、自动外部发送和多渠道接入。
- Chatwoot Community Edition 本身不收 SaaS 订阅费，但服务器、域名和模型 API 仍有成本。
