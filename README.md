# PaperLens

论文知识库问答平台。产品文档见 `doc/`。

## 当前进度

- 已完成：FastAPI 骨架、配置/日志/请求 ID、统一错误、健康检查、MySQL 双库、登录/退出/当前用户。
- 已完成（D1）：`reader` / `admin` 角色、管理员建号/改角色/停用/重置密码、管理总览、管理后台页面（`/admin`）、AI 公共层（DeepSeek 客户端、SSE、工具循环、引用校验）。
- 已完成（D2）：管理员批量导入 PDF、SHA-256 去重、文本层解析/分页分块、失败重试与归档、就绪论文目录/详情/下载。
- 账号由管理员创建，不开放自注册；第一个管理员用 CLI 脚本创建。
- 数据库 schema 由 Alembic 管理（`alembic.ini` + `migrations/`），应用启动不会自动建表。
- 下一步：D3 PDF 查询与引用。

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

## AI 公共层

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

当前迁移 head 为 `0003_papers`（D1 为 `0002_user_roles`）。`migrations/env.py` 从应用配置读取连接串：默认使用 `DATABASE_URL`，`APP_ENV=test` 时使用 `TEST_DATABASE_URL`。生产环境启动应用前必须先执行 `alembic upgrade head`。

如果开发库是之前用 `create_all` 建的空表，可执行一次 `uv run alembic stamp head` 建立基线；新库直接执行 `upgrade head` 即可。

## 测试

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

测试固定使用 `TEST_DATABASE_URL`（库名必须为 `paperlens_test`）。测试会话开始时删除测试库表并执行 `alembic upgrade head`，之后每个测试只清空业务表；因此测试同时验证迁移脚本，也不会读写 `paperlens` 开发库。

## 技术栈

FastAPI + Pydantic v2 + SQLAlchemy 2.0 async + asyncmy + MySQL 8 + Alembic + OpenAI SDK（DeepSeek 兼容接口）；后续接入 Qdrant。

