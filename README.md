# PaperLens

论文知识库问答平台。产品文档见 `doc/`。

## 当前进度

- 已完成：FastAPI 骨架、配置/日志/请求 ID、统一错误、健康检查、MySQL 双库、登录/退出/当前用户。
- 已完成（D1）：`reader` / `admin` 角色、管理员建号/改角色/停用/重置密码、管理总览、管理后台页面（`/admin`）、AI 公共层（DeepSeek 客户端、SSE、工具循环、引用校验）。
- 已完成（D2）：管理员批量导入 PDF、SHA-256 去重、文本层解析/分页分块、失败重试与归档、就绪论文目录/详情/下载。
- 已完成（D3）：文字/PDF 全局搜索、会话与证据快照、最近会话/详情/消息游标分页、会话与单篇论文 SSE 对话、引用校验、零构建用户端 `/app`。
- 已完成（D4）：仓库 CRUD、仓库论文增删、Star、相关仓库推荐、仓库只读 Agent（SSE）、仓库对话可见性、后台仓库列表、`admin/stats` 与 `related_repo_count` 接回。
- 已完成（D5 后端）：论文四维总结（三档缓存）、`available_summary_levels` 接回。
- 已完成（向量 RAG）：本地 `bge-small-zh-v1.5` 片段向量 + 关键词 RRF 混合检索（`EMBEDDING_PROVIDER=local`）。
- 账号由管理员创建，不开放自注册；第一个管理员用 CLI 脚本创建。
- 数据库 schema 由 Alembic 管理（`alembic.ini` + `migrations/`），应用启动不会自动建表。
- 契约 46 个接口全部实现；DeepSeek Key 已配置并完成真实对话/四维联调；两个零构建页面已补全
  （后台总览/侧栏导航/仓库管理、用户端主页与仓库/论文详情、引用定位、9 条文档路由的 307 深链），
  见「页面地图」；正式 React 前端（可选）未做。

## 本地运行

```bash
uv sync

# 1) 复制环境变量模板（按操作系统二选一）
cp .env.example .env              # Linux / macOS
Copy-Item .env.example .env       # Windows PowerShell

# 2) 编辑 .env，填入 DATABASE_URL / TEST_DATABASE_URL 的真实密码

uv run alembic upgrade head
uv run python -m scripts.create_admin --email admin@example.com
uv run uvicorn app.main:app --reload
```

- 管理后台：http://127.0.0.1:8000/admin （登录页 `/login`，需管理员账号）
- 用户端演示页：http://127.0.0.1:8000/app （零构建搜索/会话/对话）
- API 文档：http://127.0.0.1:8000/docs
- 存活检查：http://127.0.0.1:8000/api/v1/health/live
- 就绪检查：http://127.0.0.1:8000/api/v1/health/ready

### 在 VS Code 中运行

`.vscode/` 是个人 IDE 配置，已写入 `.gitignore`（不入库），新克隆的仓库里没有这个目录。首次使用时自建，只需做一次。

1. 用 VS Code 打开**仓库根目录**（`paperlens`，不是它的上一级）。
2. `Ctrl+Shift+P` → `Python: Select Interpreter` → 选择虚拟环境里的解释器（按操作系统）：
   - Linux / macOS：`.venv/bin/python`
   - Windows：`.venv\Scripts\python.exe`
3. 新建 `.vscode/launch.json`，粘贴下方内容（跨平台，Windows 和 Linux 通用，路径全部用 `${workspaceFolder}`）。
4. `Ctrl+Shift+D` 打开「运行和调试」，选择 `PaperLens API (reload)`，按 `F5`。
5. 打开 http://127.0.0.1:8000/admin 。

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "PaperLens API (reload)",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"],
      "cwd": "${workspaceFolder}",
      "envFile": "${workspaceFolder}/.env",
      "console": "integratedTerminal",
      "justMyCode": true
    },
    {
      "name": "PaperLens API (debug, no reload)",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["app.main:app", "--host", "127.0.0.1", "--port", "8000"],
      "cwd": "${workspaceFolder}",
      "envFile": "${workspaceFolder}/.env",
      "console": "integratedTerminal",
      "justMyCode": true
    },
    {
      "name": "PaperLens tests",
      "type": "debugpy",
      "request": "launch",
      "module": "pytest",
      "args": ["-q"],
      "cwd": "${workspaceFolder}",
      "envFile": "${workspaceFolder}/.env",
      "console": "integratedTerminal",
      "justMyCode": true
    }
  ]
}
```

三份配置的用途：

| 配置 | 用途 |
| --- | --- |
| `PaperLens API (reload)` | 日常开发，改代码自动重启 |
| `PaperLens API (debug, no reload)` | 打断点调试（`--reload` 会另起子进程，断点不稳定） |
| `PaperLens tests` | 直接运行全部测试 |

推荐扩展（同样不入库，需要手动安装）：`ms-python.python`、`ms-python.debugpy`、`charliermarsh.ruff`。

想让 VS Code 左侧测试面板可用，再新建 `.vscode/settings.json`，其中 `python.defaultInterpreterPath` 按操作系统填写：

| 系统 | `python.defaultInterpreterPath` |
| --- | --- |
| Linux / macOS | `${workspaceFolder}/.venv/bin/python` |
| Windows | `${workspaceFolder}/.venv/Scripts/python.exe` |

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "python.testing.pytestArgs": ["tests"]
}
```

不要用「Run Python File」运行 `app/main.py`：它只创建应用对象，不会启动服务器。

## 常用操作速查

**导入论文（管理员）**：三种等价方式，选一种。

```bash
# 1) 浏览器：/admin → “论文目录” → 导入 PDF（1–20 个，单个 ≤20 MB，总量 ≤100 MB）
# 2) 命令行（需要已登录的 Cookie）
curl -b 'paperlens_session=<cookie>' -X POST http://127.0.0.1:8000/api/v1/admin/papers/import \
  -F 'files=@paper1.pdf' -F 'files=@paper2.pdf'
# 3) 看处理进度
curl -b 'paperlens_session=<cookie>' 'http://127.0.0.1:8000/api/v1/admin/papers?status=ready'
```

导入后自动：`queued → parsing → indexing → ready`，解析完成即写入片段与**向量**（`EMBEDDING_PROVIDER=local`）；
同一 SHA-256 只保留一份；扫描件/无文本层会失败并可重试；失败可在后台“重试”。

**向量索引（本地模型）**

```bash
# 首次：下载模型（HF 官方站不可达时用镜像）
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 uv run python -c \
  "from huggingface_hub import snapshot_download as d; d('BAAI/bge-small-zh-v1.5', local_dir='var/models/bge-small-zh-v1.5')"

# .env
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL_PATH=/绝对路径/var/models/bge-small-zh-v1.5

# 为历史论文补向量（--limit 0 表示全部）
uv run python -m scripts.embed_chunks

# 语义排序冒烟（不写库、不调用 LLM）
uv run python -m scripts.rag_smoke
uv run python -m scripts.rag_smoke --query "有没有不用循环网络的翻译模型？"
```

关掉向量：`EMBEDDING_PROVIDER=none`，检索退回纯关键词（行为与 D3 一致，测试也跑在 none 上）。

**四维总结**

```bash
# 读取（未生成 → 404 summary_not_generated）
curl -b 'paperlens_session=<cookie>' 'http://127.0.0.1:8000/api/v1/papers/1/summaries?level=standard'
# 生成（同步，命中缓存直接返回；需要 DEEPSEEK_API_KEY）
curl -b 'paperlens_session=<cookie>' -X POST http://127.0.0.1:8000/api/v1/papers/1/summaries/generate \
  -H 'Content-Type: application/json' -d '{"level":"deep"}'
```

缓存键 `paper_id + level + content_hash + prompt_version + model`；改提示词必须升 `SUMMARY_PROMPT_VERSION`
（`app/services/summaries.py`），否则旧缓存会被继续使用。

**仓库 / 对话 / 相关仓库**

- 页面：`/app` → “我的仓库”可建仓库、加/移论文、Star、仓库对话；会话页“相关仓库”与“论文总结”两个视图。
- 会话/仓库/单篇对话都是 SSE：`meta → citation(0..N) → token(0..N) → done`；流前错误是普通 JSON，
  流中是 `event: error`；客户端断开保存 `status=stopped`。
- 没有 `DEEPSEEK_API_KEY` 时对话/四维返回 503 `llm_unavailable`，其余接口不受影响。

**管理员与测试账号**

```bash
uv run python -m scripts.create_admin --email admin@example.com   # 创建或提升为 admin
```

**跑测试（串行，勿并发）**

```bash
uv run pytest -q          # 当前 320 项，本机约 11 分钟
```

> 全量测试与手工 `alembic upgrade` 都会操作 `paperlens_test`，不要同时跑两个 pytest。

## 页面地图

两个零构建单页（同源 fetch，无外部资源、无打包步骤），由 `app/main.py` 直接托管。
页面内部用 hash 路由；产品设计 §4.1 里的路径形式由服务端 307 别名跳转（`/sessions/1` → `/app#/sessions/1`），
所以文档中的 URL 可以直接打开。

| 地址 | 路径别名（307） | 内容 |
| --- | --- | --- |
| `/login` | — | 登录，成功后写 `paperlens_session` Cookie |
| `/admin` 或 `/admin#/overview` | `/admin/overview` | 总览：9 个数字（用户/论文各状态/仓库）+ 4 个快捷入口 |
| `/admin#/papers` | `/admin/papers` | 论文目录：导入 PDF、筛选、查看/编辑、重试、归档 |
| `/admin#/repos` | `/admin/repos` | 仓库管理：全站仓库（含私有）、私有仓库可管理 |
| `/admin#/users` | `/admin/users` | 用户管理：创建、改显示名/角色、启停、重置密码 |
| `/app` | — | 主页：查询框、PDF 查询、最近会话、我的仓库、公开仓库、管理员入口 |
| `/app#/sessions/{id}` | `/sessions/{id}` | 会话：对话（SSE）、论文总结、相关仓库 |
| `/app#/repos` | — | 我的仓库：创建、我的/公开列表、star |
| `/app#/repos/{id}` | `/repos/{id}` | 仓库页：左侧论文列表 + 右侧可收起对话面板、star、编辑/删除 |
| `/app#/papers/{id}` | `/papers/{id}` | 论文详情：元数据 + 原文下载、四维三档、单篇对话、引用定位 |
| `/app#/papers/{id}?page=3&quote=…` | `/papers/{id}?page=3&quote=…` | 引用定位：页码 + 摘录高亮 + 一键复制（不渲染 PDF 页面） |

**管理员能管什么**：私有仓库可改名/改简介/改可见性/删除/增删论文/查看全部用户对话；
公开仓库对管理员**只读**（契约权限真值表），列表里显示「只读」而非管理入口。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health/live` | 存活检查 |
| GET | `/api/v1/health/ready` | MySQL 就绪检查 |
| POST | `/api/v1/auth/login` | 登录 |
| POST | `/api/v1/auth/logout` | 退出并撤销当前会话 |
| GET | `/api/v1/auth/me` | 当前用户（含 `role`） |
| GET | `/api/v1/admin/users` | 用户列表（搜索、分页） |
| POST | `/api/v1/admin/users` | 创建用户 |
| PATCH | `/api/v1/admin/users/{id}` | 改显示名、角色、启停 |
| POST | `/api/v1/admin/users/{id}/reset-password` | 重置密码并撤销全部会话 |
| GET | `/api/v1/admin/stats` | 管理总览（含论文状态计数） |
| POST | `/api/v1/admin/papers/import` | 批量导入 PDF（1–20 个，202） |
| GET | `/api/v1/admin/papers` | 管理端论文列表（搜索/状态/归档/分页） |
| GET | `/api/v1/admin/papers/{id}` | 管理端论文详情 |
| PATCH | `/api/v1/admin/papers/{id}` | 修正标题/作者/年份/venue/摘要 |
| POST | `/api/v1/admin/papers/{id}/retry` | 重试失败且未归档论文 |
| POST | `/api/v1/admin/papers/{id}/archive` | 归档就绪论文（幂等） |
| GET | `/api/v1/papers` | 用户目录（ready 且未归档，支持 q/分页） |
| GET | `/api/v1/papers/{id}` | 用户论文详情（归档仍可查看） |
| GET | `/api/v1/papers/{id}/download` | 下载 PDF（归档仍可下载） |
| POST | `/api/v1/search` | 文字全局查询，201 会话详情 |
| POST | `/api/v1/search/pdf` | PDF 文本临时检索，201 会话详情 |
| GET | `/api/v1/sessions` | 当前用户最近会话 |
| GET | `/api/v1/sessions/{id}` | 会话详情（论文/证据/对话快照） |
| GET | `/api/v1/sessions/{id}/messages` | 会话消息游标分页 |
| POST | `/api/v1/sessions/{id}/chat/stream` | 会话 SSE 对话 |
| POST | `/api/v1/papers/{id}/chat/stream` | 单篇论文 SSE 对话 |
| GET | `/api/v1/papers/{id}/messages` | 单篇论文对话历史 |
| GET | `/api/v1/papers/{id}/summaries` | 读取四维总结（`level` 默认 standard，未缓存 404） |
| POST | `/api/v1/papers/{id}/summaries/generate` | 生成四维总结（命中缓存直接返回） |
| POST | `/api/v1/repos` | 创建仓库（201，默认 private） |
| GET | `/api/v1/repos` | 仓库列表（`scope=mine/public`） |
| GET | `/api/v1/repos/related` | 会话相关仓库推荐（相关性优先，star 加分） |
| GET | `/api/v1/repos/{id}` | 仓库详情（含 permissions 真值表） |
| PATCH | `/api/v1/repos/{id}` | 改名称/简介/可见性（空对象 400） |
| DELETE | `/api/v1/repos/{id}` | 删除仓库并清理其全部对话（不删论文） |
| GET | `/api/v1/repos/{id}/papers` | 仓库论文列表（含已归档） |
| POST | `/api/v1/repos/{id}/papers` | 批量添加论文（200 + added/ignored） |
| DELETE | `/api/v1/repos/{id}/papers/{paper_id}` | 移除论文（204 幂等） |
| POST/DELETE | `/api/v1/repos/{id}/star` | Star / 取消 Star（幂等） |
| POST | `/api/v1/repos/{id}/chat/stream` | 仓库只读 Agent SSE |
| GET | `/api/v1/repos/{id}/messages` | 我在该仓库的对话消息 |
| GET | `/api/v1/repos/{id}/conversations` | 仓库对话列表（私有仓库创建者/管理员看全部） |
| GET | `/api/v1/repos/{id}/conversations/{cid}/messages` | 指定对话消息 |
| GET | `/api/v1/admin/repos` | 后台仓库列表（含 owner_email） |

认证使用 HttpOnly opaque session cookie；会话令牌以 SHA-256 哈希存库，密码使用 scrypt 哈希。
`/api/v1/admin/*` 全部要求 `role=admin`：普通用户返回 403 `forbidden`，未登录返回 401。
公开注册接口 `POST /api/v1/auth/register` 已移除（404 `not_found`）。
用户目录/详情/下载要求已登录；目录只返回 `ready` 且未归档论文。

管理后台页面是零构建的单文件应用（`web/admin/index.html`），由 FastAPI 在 `/admin` 直接提供，
与接口同源，登录 Cookie 无需额外跨域配置。页面包含登录、总览卡片、用户搜索/分页、
创建用户、改角色、启停、重置密码，以及“论文目录”页签中的批量导入、状态轮询、
详情查看、元数据修正、失败重试和归档。D5 的前端脚手架就绪后可替换为 React 版本，接口不变。

## 论文导入与解析（D2）

```bash
# 迁移到 D2 schema（papers / paper_files / paper_chunks + ngram FULLTEXT）
uv run alembic upgrade head
```

在 `.env` 中可配置：

```dotenv
PAPER_STORAGE_DIR=var/papers
PAPER_PROCESSING_TIMEOUT_MINUTES=30
```

- 存储路径默认为 `var/papers/<paper_id>/<sha256>.pdf`，上传临时文件在 `var/papers/.tmp/`；
  `var/` 已加入 `.gitignore`，PDF 原文不入库。
- 管理后台“论文目录”→“导入 PDF”一次选择 1–20 个文件；单文件 ≤20 MB，单次总量 ≤100 MB。
  同一 SHA-256 文件只保留一份，重复导入返回已有论文。
- 后台处理状态为 `queued → parsing → indexing → ready`，失败为 `failed`；前端在存在处理中论文时每 3 秒刷新列表。
- 解析只处理 PDF 文本层，不做 OCR；扫描件/图片型 PDF 会失败并显示可读原因。
- 失败且未归档论文可重试；就绪论文可归档。归档只影响用户目录，不影响详情/下载。
- 服务启动时会将超过 `PAPER_PROCESSING_TIMEOUT_MINUTES` 的 `parsing/indexing` 标记为失败（“处理中断，请重试”）。

## 搜索、会话与引用（D3）

- 文字查询使用 MySQL ngram FULLTEXT 检索 `paper_chunks.content`；单字符查询回退到转义后的 `LIKE`。只检索 `ready` 且未归档论文。
- 单次查询预算：最多 8 篇论文、20 条证据；正文无命中时用标题/摘要命中，并取命中论文前 2 个 chunk 作为证据。
- 会话保存论文 ID/rank 与证据快照（chunk、页码、quote）；后续新增/归档论文不改变旧会话，详情中的论文元数据取当前值。
- 回答先生成并缓冲，服务端按正文首次出现顺序校验/重排 `[n]`，最多 12 条引用；无效引用重试一次，仍无有效引用则返回固定拒答文案。
- SSE 事件顺序：`meta` → `citation` → `token` → `done`；流前错误返回普通 JSON，流中错误发送 `event: error`。断线尽量保存 `stopped` 消息。
- `/app` 是零构建临时用户端，覆盖登录、文字/PDF 查询、最近会话、三页签、流式对话、停止和引用弹窗；D5 可替换为正式前端。
- D3 不接 Qdrant、不做查询翻译、OCR 或四维总结；`related_repo_count` 由 D4 接入相关仓库计数。
- **D4 已交付**：仓库 CRUD、Star、相关仓库推荐、仓库只读 Agent（SSE）、后台仓库列表；
  `GET /admin/stats` 的 `repos_total`/`repos_private` 与 `SessionDetail.related_repo_count` 已接回真实计数。
- **向量检索（真正的 RAG）**：`EMBEDDING_PROVIDER=local` 时用本地 `sentence-transformers` 模型
  （默认 `BAAI/bge-small-zh-v1.5`）为每个 `paper_chunk` 生成 512 维归一化向量，存 `chunk_embeddings`；
  检索时向量召回（余弦）+ MySQL ngram FULLTEXT 用 RRF 融合，仓库 Agent 的 `search_repo` 同样融合。
  未启用向量（`EMBEDDING_PROVIDER=none`）时行为与 D3 完全一致。
- 向量回填：`uv run python -m scripts.embed_chunks`；语义排序冒烟：`uv run python -m scripts.rag_smoke`。


`app/core/ai/` 供 D2–D5 复用，不依赖具体业务：

- `client.py`：`LLMClient` 抽象 + `DeepSeekClient`（懒加载，未配置 Key 时返回 503 `llm_unavailable`）；
- `streaming.py`：`sse()`、`sse_response()`、`with_heartbeat()`；
- `tools.py`：`ToolRegistry` + `run_tool_loop()`（工具阶段非流式，最终回答由调用方流式输出）；
- `prompts.py`：系统提示、固定拒答文案、`Citation` 与引用校验。

在 `.env` 中配置 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`LLM_TIMEOUT_SECONDS`、`LLM_MAX_TOOL_STEPS`、`LLM_MAX_OUTPUT_TOKENS`。测试使用 `tests/fakes.py` 的假客户端，不调用真实 API。

冒烟检查（不经过 HTTP）：

```bash
uv run python -m scripts.ai_smoke                      # 未配置 Key 时应输出 503 llm_unavailable
uv run python -m scripts.ai_smoke --stream             # 配置 Key 后流式打印模型回答
uv run python -m scripts.ai_smoke --message "你好"      # 自定义问题
```

## 数据库迁移

```bash
# 应用迁移到最新版本
uv run alembic upgrade head

# 新建迁移（修改 app/models/ 后，记得在 app/models/__init__.py 导入新模型）
uv run alembic revision --autogenerate -m "add papers"

# 查看当前版本 / 检查模型与迁移是否一致 / 回滚一步
uv run alembic current
uv run alembic check
uv run alembic downgrade -1
```

当前迁移 head 为 `0007_paper_summaries`，链：
`0001_auth_tables → 0002_user_roles → 0003_papers → 0004_sessions_chat →
0005_repos → 0006_chunk_embeddings → 0007_paper_summaries`。`migrations/env.py` 从应用配置读取连接串：默认使用 `DATABASE_URL`，`APP_ENV=test` 时使用 `TEST_DATABASE_URL`。生产环境启动应用前必须先执行 `alembic upgrade head`。

MySQL 注意：`downgrade()` 只按反序 `drop_table`，不要写 `drop_index`（会触发 1553 外键索引错误）；FULLTEXT/ngram 索引需手写迁移。

如果开发库是之前用 `create_all` 建的空表，可执行一次 `uv run alembic stamp head` 建立基线；新库直接执行 `upgrade head` 即可。

## 测试

```bash
uv run pytest -q                 # 当前 320 项，本机约 11 分钟，串行
uv run ruff check .
uv run ruff format --check .
uv run alembic check              # 模型与迁移一致
```

测试固定使用 `TEST_DATABASE_URL`（库名必须为 `paperlens_test`）。测试会话开始时删除测试库表并执行 `alembic upgrade head`，之后每个测试只清空业务表；因此测试同时验证迁移脚本，也不会读写 `paperlens` 开发库。

- 不要同时跑两个 pytest（共用 `paperlens_test`），也不要在测试运行时手工 `alembic upgrade`。
- 测试强制 `EMBEDDING_PROVIDER=none`（`tests/conftest.py`）：不加载本地模型、不联网；
  向量路径用 `tests/fakes.py::FakeEmbeddingProvider` 覆盖。对话一律用 `FakeLLMClient`，不打真实模型。
- 迁移改动额外验证：`APP_ENV=test uv run alembic downgrade -1 && APP_ENV=test uv run alembic upgrade head`。

## 技术栈

FastAPI + Pydantic v2 + SQLAlchemy 2.0 async + asyncmy + MySQL 8 + Alembic + OpenAI SDK（DeepSeek 兼容接口）
+ sentence-transformers（本地 embedding，CPU 推理）+ MySQL 向量列（float32，余弦检索）。
不引入向量数据库：课程规模下片段向量直接存 MySQL，检索在应用层做余弦 + RRF 融合。

