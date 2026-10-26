# PaperLens

论文知识库问答平台。产品文档见 `doc/`。

## 当前进度

- 已完成：FastAPI 骨架、配置/日志/请求 ID、统一错误、健康检查、MySQL 双库、注册/登录/退出/当前用户。
- 数据库 schema 由 Alembic 管理（`alembic.ini` + `migrations/`），应用启动不会自动建表。
- 下一步：论文上传与 PDF 解析。

## 本地运行

```bash
uv sync
cp .env.example .env
# 编辑 .env，填入 DATABASE_URL / TEST_DATABASE_URL 的真实密码
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

- API 文档：http://127.0.0.1:8000/docs
- 存活检查：http://127.0.0.1:8000/api/v1/health/live
- 就绪检查：http://127.0.0.1:8000/api/v1/health/ready

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health/live` | 存活检查 |
| GET | `/api/v1/health/ready` | MySQL 就绪检查 |
| POST | `/api/v1/auth/register` | 注册并创建会话 |
| POST | `/api/v1/auth/login` | 登录 |
| POST | `/api/v1/auth/logout` | 退出并撤销当前会话 |
| GET | `/api/v1/auth/me` | 当前用户 |

认证使用 HttpOnly opaque session cookie；会话令牌以 SHA-256 哈希存库，密码使用 scrypt 哈希。

## 数据库迁移

```bash
# 应用迁移到最新版本
uv run alembic upgrade head

# 新建迁移（修改 app/models.py 后）
uv run alembic revision --autogenerate -m "add papers"

# 查看当前版本 / 检查模型与迁移是否一致 / 回滚一步
uv run alembic current
uv run alembic check
uv run alembic downgrade -1
```

`migrations/env.py` 从应用配置读取连接串：默认使用 `DATABASE_URL`，`APP_ENV=test` 时使用 `TEST_DATABASE_URL`。生产环境启动应用前必须先执行 `alembic upgrade head`。

如果开发库是之前用 `create_all` 建的空表，可执行一次 `uv run alembic stamp head` 建立基线；新库直接执行 `upgrade head` 即可。

## 测试

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

测试固定使用 `TEST_DATABASE_URL`（库名必须为 `paperlens_test`）。测试会话开始时删除测试库表并执行 `alembic upgrade head`，之后每个测试只清空业务表；因此测试同时验证迁移脚本，也不会读写 `paperlens` 开发库。

## 技术栈

FastAPI + Pydantic v2 + SQLAlchemy 2.0 async + asyncmy + MySQL 8 + Alembic；后续接入 Qdrant + DeepSeek API。

