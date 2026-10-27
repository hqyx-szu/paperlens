# PaperLens API 接口文档（V1.1）

> 文档版本：1.1（V1.0 勘误与补全）
> 文件说明：文件名沿用 V1.0 以保持既有引用；内容版本为 1.1。
> 状态：**目标契约（Target Contract）**，用于前后端并行开发、测试和验收
> 日期：2026-10-26
> 代码基线：P001（`0001_auth_tables`，13 个测试通过）
> 语义来源：`doc/产品功能设计文档（V1.0）.md`、`doc/用户故事文档（V1.0）.md`
> 机器可读契约：应用启动后访问 `/openapi.json`；本文档是人工契约，二者不一致时以本文档为准，并由实现方修正代码
> 适用范围：本地开发（不使用 Docker）、V1.0 P0 功能
> 变更摘要：V1.1 修正了错误响应 `details`、SSE 流前错误、仓库权限与对话可见性、PDF 查询、导入校验、分页与状态机等边界规则；完整变更见 §19

---

## 0. 阅读说明

1. 本文档描述的是 **V1.0 要实现的目标行为**，不是当前代码的现状。当前代码与本文档的差异集中在 §16。
2. 所有接口默认前缀 `/api/v1`；本文档标题写完整路径，例如 `POST /api/v1/auth/login`。
3. 字段名统一 `snake_case`；枚举值统一英文小写；用户可见的错误消息统一中文。
4. 请求体默认拒绝未定义字段（`extra="forbid"`），多传字段返回 422。
5. 未特别说明时，所有接口都需要登录；未登录返回 401。
6. 本文档中的「必须」是验收条件；「建议」是实现建议，不阻塞验收。
7. 修改接口必须先改本文档并通知 PM，再改代码；不允许代码先行、文档后补。

### 0.1 本契约做出的关键决定

| 决定 | 取值 | 说明 |
| --- | --- | --- |
| 公开注册 | **不提供** | 账号只能由管理员创建；现有 `POST /auth/register` 删除 |
| 登录失败 | 统一 401 `invalid_credentials` | 不区分账号不存在、密码错误、账号停用，避免账号枚举 |
| 时间格式 | RFC 3339 UTC，带 `Z` | 例：`2026-10-26T08:00:00Z`；数据库存 naive UTC，API 层补 `Z` |
| 分页 | 实体列表用 `page/page_size`；消息用游标 | 见 §1.6 |
| 四维生成 | **同步** `POST`，最长 60 秒 | 前端显示 loading；超时 504 |
| 内部检索预算 | Top 8 论文、Top 20 证据、回答最多 12 条引用 | 不暴露给用户，不是产品数量限制 |
| 未知请求字段 | 422 | 请求模型 `extra="forbid"` |
| 权限不足 | 私有资源统一 404；角色不足用 403 | 见 §1.8 |
| 幂等键 | 不提供 | 重复操作按各接口定义处理 |
| 错误 `details` | 始终返回；无内容时为 `null` | 不是省略字段，见 §1.7 |
| PDF 查询文本 | 内部截取前 2000 字符用于检索 | 不保存文件、不入库；`query` 返回文件名 |
| 空会话/空仓库对话 | 409 前置拒绝 | 不写用户消息和助手消息，见 §11.5 |
| 公开仓库对话可见性 | 只有发起者可见 | 创建者、管理员、其他用户都不可见 |
| 归档 | **单向操作** | 不提供取消归档；归档不删除文件/片段/四维，旧会话、旧仓库、下载仍可用 |
| Star | 仅公开且非自己的仓库 | 私有仓库返回 400 `repo_not_public`；自己的公开仓库返回 400 `cannot_star_own_repo` |

---

## 1. 通用约定

### 1.1 Base URL 与版本

- 本地：`http://127.0.0.1:8000/api/v1`
- 生产：由部署域名决定，路径不变
- 版本：路径版本 `/api/v1`；新增字段、新增枚举值属于兼容变更；删除字段、改变语义必须升 `/api/v2`

### 1.2 认证与会话

- 认证方式：**HttpOnly opaque session cookie**，不使用 JWT、不使用 Bearer Token。
- Cookie 名：`paperlens_session`（由 `SESSION_COOKIE_NAME` 配置）。
- Cookie 属性：
  - `HttpOnly`
  - `SameSite=Lax`（由 `COOKIE_SAMESITE` 配置）
  - `Path=/`
  - `Max-Age=604800`（7 天，由 `SESSION_TTL_DAYS` 配置）
  - `Secure` 本地为 `false`，生产必须为 `true`
- 登录成功创建**新会话**；同一用户可以有多个设备会话。
- 退出只删除**当前会话**；管理员重置密码删除该用户**全部会话**。
- 会话固定 7 天过期，不滚动续期。
- 会话过期、被撤销、用户被停用：下一次请求返回 401 `unauthorized`。
- 浏览器前端必须使用 `credentials: "include"`；CORS 已允许携带 Cookie。

### 1.3 请求与响应格式

- JSON 请求/响应：`Content-Type: application/json`，请求体与响应体均为 UTF-8 编码
- 文件上传：`multipart/form-data`
- 文件下载：二进制流，`application/pdf`
- 流式回答：`text/event-stream; charset=utf-8`
- 所有文本 UTF-8，支持中文。
- 请求体中的未知字段返回 422 `validation_error`。
- 响应中的可选字段：无值时为 `null`，不省略字段。

### 1.4 时间、ID、文本

| 项目 | 约定 |
| --- | --- |
| 时间 | RFC 3339 UTC，`YYYY-MM-DDTHH:MM:SSZ`；秒级精度；无时区偏移 |
| ID | 正整数，BigInteger；JSON number |
| 邮箱 | 不区分大小写，入库统一小写；最大 255 字符 |
| 文本 | 默认去除首尾空白；空字符串按空值处理。例外：仓库 `description` 的空字符串保存为 `""`，不以 `null` 返回 |
| 密码 | 新建/重置密码 8–128 字符；登录只做 1–128 字符长度校验（兼容历史密码策略）；响应中永不返回密码或哈希 |
| 搜索词 | 按字面量处理，`%` 和 `_` 不作为 SQL 通配符 |
| 排序 | 每个列表接口单独定义；分页必须有唯一 tiebreaker（通常是 `id`） |

### 1.5 状态码

| 状态码 | 用途 |
| --- | --- |
| 200 | 查询成功、更新成功、操作成功 |
| 201 | 创建资源成功（用户、仓库、搜索会话） |
| 202 | 已接受异步处理（论文导入、重试） |
| 204 | 删除/退出成功，无响应体 |
| 400 | 业务规则不满足（如不能操作自己、不能 star 自己的仓库） |
| 401 | 未登录、会话失效、登录失败 |
| 403 | 已登录但无权执行该操作（角色或归属不足） |
| 404 | 资源不存在，或私有资源无权访问（避免泄露存在性） |
| 405 | 方法不允许 |
| 409 | 冲突或状态不允许（邮箱重复、仓库名重复、论文未就绪、空仓库/空会话对话、处理中修改元数据） |
| 413 | 上传文件过大 |
| 415 | 文件类型不支持 |
| 422 | 参数校验失败、PDF 无可读文字、PDF 页数超限 |
| 429 | 预留：请求过于频繁（V1.0 未启用） |
| 500 | 未捕获内部错误 |
| 502 | 上游模型失败 |
| 503 | 数据库、模型或检索服务不可用 |
| 504 | 模型超时、工具循环超限 |

### 1.6 分页

#### 1.6.1 实体列表分页（page/page_size）

适用：用户、论文、会话、仓库、仓库论文、相关仓库、后台仓库、会话列表等。

请求参数：

| 参数 | 类型 | 默认 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `page` | int | 1 | ≥1 | 页码 |
| `page_size` | int | 20 | 1–100 | 每页条数；超过 100 返回 422 |

响应：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 20
}
```

规则：

- `total` 是满足过滤条件的总数，不受分页影响。
- 页码超过最大页：返回 `200` + 空 `items`，不返回 404。
- 排序必须稳定：例如 `created_at DESC, id DESC`。
- 列表接口不提供用户自定义排序和任意筛选；只提供各接口明确列出的固定查询参数（如 `q`、`status`、`scope`）。

#### 1.6.2 消息游标分页（before_id）

适用：会话消息、单篇论文消息、仓库消息、仓库对话消息。

请求参数：

| 参数 | 类型 | 默认 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `limit` | int | 50 | 1–200 | 每页条数 |
| `before_id` | int\|null | null | ≥1 | 只返回 `id < before_id` 的消息；首次不传 |

响应：

```json
{
  "items": [],
  "has_more": false,
  "next_before_id": null
}
```

规则：

- `items` 按 `id DESC` 返回（最新的在前）；前端展示时自行反转。
- `has_more=true` 当且仅当还存在 `id < next_before_id` 的消息；`next_before_id` 是当前页最后一条（最小 id）的 id，下一次请求传它。
- `has_more=false` 时，`next_before_id=null`。
- 会话/仓库/论文本身不存在或无权访问时仍返回对应 404；只有「资源可见但对话或消息为空」时返回 200 + 空数组。

### 1.7 错误格式

所有错误统一返回：

```json
{
  "error": {
    "code": "invalid_credentials",
    "message": "邮箱或密码错误",
    "details": null
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `error.code` | string | 稳定英文错误码，前端据此判断 |
| `error.message` | string | 用户可读中文消息，不包含内部字段、SQL、堆栈 |
| `error.details` | any\|null | 参数校验时为数组 `[{"field":"body.password","message":"..."}]`；其他情况必须为 `null`，字段不可省略 |

规则：

- `details` 字段始终存在；无内容时为 `null`，不是省略。
- `details[].field` 使用 `loc` 拼接，例如 `body.email`、`query.page`；数组下标用数字，例如 `body.paper_ids.0`。
- 响应体不包含异常堆栈、SQL、文件路径、模型提示词。
- 404 不返回资源名称、数量、创建者等元信息。
- 401 不说明账号是否存在。
- 403 只用于「已登录但角色/权限不足」。
- 私有资源对无权限用户统一返回 404。

### 1.8 权限与 403/404 规则

| 场景 | 返回 |
| --- | --- |
| 未登录访问业务接口 | 401 `unauthorized` |
| 普通用户访问 `/admin/*` | 403 `forbidden` |
| 普通用户访问他人私有仓库 | 404 `repo_not_found` |
| 普通用户访问他人会话 | 404 `session_not_found` |
| 普通用户访问他人对话 | 404 `conversation_not_found` |
| 普通用户编辑他人公开仓库 | 403 `forbidden` |
| 管理员编辑公开仓库 | 403 `forbidden`（管理员若是创建者，按创建者处理） |
| 管理员（非创建者）查看公开仓库他人对话 | 403 `forbidden`；管理员若是创建者，按创建者规则返回 404 |
| 普通用户访问处理中/失败的论文详情 | 404 `paper_not_found` |
| 私有仓库不能 star | 400 `repo_not_public`（仓库创建者/管理员）；无权限者 404 |
| star 自己的公开仓库 | 400 `cannot_star_own_repo`；自己的私有仓库按上一行返回 `repo_not_public` |
| 管理后台接口但角色不是 admin | 403 `forbidden` |

### 1.9 幂等与并发

- 不提供 `Idempotency-Key`。
- `POST /repos/{id}/star`、`DELETE /repos/{id}/star`、`DELETE /repos/{id}/papers/{paper_id}` 是幂等的。
- 重复添加论文到仓库：忽略，返回在 `ignored` 中。
- 重复导入同一文件：按 SHA-256 识别，复用已有论文，`duplicate=true`。
- 同一四维级别并发生成：允许重复调用模型，最终只保留一份缓存；后写覆盖先写。
- 更新接口采用最后写入生效（last-write-wins），不提供 ETag/If-Match。

### 1.10 文件上传与下载

| 项目 | 约定 |
| --- | --- |
| 论文导入字段 | `files`，可多个 |
| PDF 查询字段 | `file`，单个 |
| 允许类型 | `application/pdf`，文件头必须是 `%PDF-` |
| 单文件上限 | 20 MB |
| 单次导入文件数 | 1–20 |
| 单次导入总大小 | 100 MB |
| PDF 查询页数 | ≤ 50 页 |
| PDF 查询检索文本 | 提取文字后内部截取前 2000 字符；不保存、不入库 |
| 存储位置 | 本地 `var/papers/<paper_id>/`，不暴露在 Web 根目录 |
| 文件名 | 只取上传文件名的 basename；原始文件名只用于展示，存储名使用哈希，防止路径穿越 |
| 下载 | `Content-Disposition: attachment`；文件名按 RFC 5987 编码，支持中文；同时提供 ASCII fallback |
| PDF 查询 | **只提取文字，不保存文件、不入库、不创建论文** |

### 1.11 SSE 流式契约

#### 1.11.1 请求

- 方法：`POST`
- `Content-Type: application/json`
- `Accept: text/event-stream`
- 认证：Cookie（浏览器使用 `fetch` + `ReadableStream`，不要用 `EventSource`，因为需要 POST 和 Cookie）
- 请求体：`{"message": "..."}`，1–2000 字符

#### 1.11.2 响应

- HTTP 200，`Content-Type: text/event-stream; charset=utf-8`
- 响应头：
  - `Cache-Control: no-cache`
  - `X-Accel-Buffering: no`
  - `Connection: keep-alive`
- 每 15 秒发送一次心跳注释：`: ping\n\n`（客户端忽略）
- 每个事件格式：

```text
event: <name>
data: <json>
<空行>
```

#### 1.11.3 事件

| 事件 | 时机 | data |
| --- | --- | --- |
| `meta` | 流开始，第一个事件 | `{conversation_id, user_message_id, scope, scope_id}` |
| `token` | 生成过程中，可多次 | `{text}`，增量文本 |
| `citation` | 引用确定时，可多次 | `Citation` 对象 |
| `done` | 正常完成，最后一个事件 | `{assistant_message_id, status: "complete"}` |
| `error` | 流开始后发生错误，终止事件 | `{code, message}` |

规则：

- `scope` 取值：`session`、`repo`、`paper`。
- `citation` 可能先于引用它的 `token` 到达，前端按 `index` 缓存，不要假设顺序。
- 引用编号 `index` 从 1 开始，连续、不重复（1..N），在同一条回答内唯一、稳定；正文可以只使用其中一部分，但不能出现未定义的编号。
- 固定拒答文案：`根据当前资料，没有足够证据回答这个问题。` 拒答不是错误事件。
- 客户端主动断开 = 停止生成；服务端保存已生成内容，消息 `status=stopped`。断开后不会收到 `done`。
- 正常完成一定以 `done` 结束。
- `error` 是终止事件，之后关闭连接；不会再有 `done`。`error.code` 使用 §15 中的错误码。
- 流开始前的错误（400/401/403/404/409/413/415/422/429/502/503/504）返回普通 JSON 错误，不是 SSE；此时不写入用户消息和助手消息。
- 流开始后的错误只能通过 `error` 事件返回，HTTP 状态仍为 200。
- 客户端不要自动重连；重连会产生重复用户消息。

#### 1.11.4 引用对象 Citation

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `index` | int | 从 1 开始，对应回答中的 `[n]` |
| `paper_id` | int | 论文 ID |
| `chunk_id` | int | 原文片段 ID，前端可用于定位 |
| `page` | int\|null | PDF 页码，1 开始；未知为 null |
| `quote` | string | 原文摘录，≤500 字符，必须来自该片段 |

### 1.12 CORS、CSRF 与限流

- CORS 允许来源：`http://localhost:5173`、`http://127.0.0.1:5173`（由 `CORS_ORIGINS` 配置）。
- `allow_credentials=true`；前端必须带 Cookie。
- 暴露响应头：`X-Request-ID`。
- CSRF：V1.0 **不提供 CSRF token**，依赖 `SameSite=Lax` + 同源前端。上线到公网前必须补 CSRF 防护。
- 限流：V1.0 **不启用**；429 `too_many_requests` 为预留错误码。
- 不提供公开 API、API Key、多租户。

### 1.13 请求 ID

- 客户端可传 `X-Request-ID`（≤128 字符）；超过 128 字符时服务端截断到前 128 字符。
- 服务端原样回传；未传、为空或全空白则生成 UUID hex。
- 所有响应（含错误和 SSE）都带 `X-Request-ID`。
- 日志按该 ID 关联，不向客户端返回堆栈。

### 1.14 内部预算（不是产品限制）

为了控制模型上下文和成本，服务端内部执行以下预算；**不提供用户设置，不写入界面**：

| 项目 | 内部上限 |
| --- | --- |
| 一次全局检索返回论文 | 8 篇 |
| 一次全局检索保存证据 | 20 条 |
| 一条回答最多引用 | 12 条 |
| 仓库 Agent 最多工具步数 | 5 步 |
| 会话/单篇对话最多工具步数 | 4 步 |
| 单次模型输出 | 2048 tokens（可配置） |

`paper_count`、`evidence_count` 返回实际值，不承诺等于全库相关论文总数。

---

## 2. 枚举与公共数据结构

### 2.1 枚举

#### UserRole

| 值 | 说明 |
| --- | --- |
| `reader` | 普通用户 |
| `admin` | 管理员 |

#### PaperStatus

| 值 | 说明 |
| --- | --- |
| `queued` | 排队 |
| `parsing` | 解析中 |
| `indexing` | 索引中 |
| `ready` | 就绪，可搜索 |
| `failed` | 失败，可重试 |

状态流转：

```text
queued → parsing → indexing → ready
            │          │
            └──────────┴──→ failed
failed ──（管理员重试）──→ queued
```

失败可发生在 `parsing` 或 `indexing`；重试后回到 `queued`，重新解析并替换该论文的原文片段。归档是独立字段 `is_archived`，不改变 `status`。

#### QueryType

| 值 | 说明 |
| --- | --- |
| `text` | 文字查询 |
| `pdf` | PDF 查询 |

#### RepoVisibility

| 值 | 说明 |
| --- | --- |
| `public` | 公开仓库 |
| `private` | 私有仓库 |

#### RepoScope

| 值 | 说明 |
| --- | --- |
| `mine` | 我创建的仓库（含公开和私有） |
| `public` | 所有公开仓库（含自己的公开仓库） |

#### RepoPaperAddReason

| 值 | 说明 |
| --- | --- |
| `duplicate` | 论文已在仓库中 |
| `not_ready` | 论文不是 `ready` |
| `archived` | 论文已归档且不在仓库中 |
| `not_found` | 论文不存在 |

#### MessageRole

| 值 | 说明 |
| --- | --- |
| `system` | 系统消息 |
| `user` | 用户消息 |
| `assistant` | 助手消息 |

#### MessageStatus

| 值 | 说明 |
| --- | --- |
| `complete` | 完成 |
| `stopped` | 用户停止/断开，保留已生成内容 |
| `error` | 生成失败，保留已生成内容（可能为空） |

#### SummaryLevel

| 值 | 说明 |
| --- | --- |
| `brief` | 简要 |
| `standard` | 标准（默认） |
| `deep` | 深入 |

#### ChatScope

| 值 | 说明 |
| --- | --- |
| `session` | 本次会话证据 |
| `repo` | 当前仓库 |
| `paper` | 单篇论文 |

### 2.2 UserBrief

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 用户 ID |
| `display_name` | string\|null | 显示名 |

说明：`UserBrief` 用于公开/半公开场景（仓库创建者、对话发起者），**不包含邮箱**，避免公开仓库泄露用户邮箱。需要邮箱的后台列表使用 `UserRead`；后台仓库列表额外返回 `owner_email`。

### 2.3 UserRead

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 用户 ID |
| `email` | string | 邮箱 |
| `display_name` | string\|null | 显示名 |
| `role` | UserRole | 角色 |
| `is_active` | bool | 是否启用 |
| `created_at` | string | 创建时间，UTC `Z` |

### 2.4 PaperBrief

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 论文 ID |
| `title` | string | 标题 |
| `authors` | string[] | 作者列表，可为空数组 |
| `year` | int\|null | 年份 |
| `venue` | string\|null | 期刊/会议 |
| `is_archived` | bool | 是否已归档 |

### 2.5 Citation

见 §1.11.4。

### 2.6 Message

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 消息 ID |
| `role` | MessageRole | system/user/assistant |
| `content` | string | 消息正文 |
| `citations` | Citation[] | 引用，按 `index` 升序；user/system 为空数组 |
| `status` | MessageStatus | 消息状态 |
| `created_at` | string | 创建时间，UTC `Z` |

### 2.7 分页响应

实体列表：

```json
{"items": [], "total": 0, "page": 1, "page_size": 20}
```

消息列表：

```json
{"items": [], "has_more": false, "next_before_id": null}
```

分页类型与元素类型：

| 类型 | `items` 元素 | 使用接口 |
| --- | --- | --- |
| `AdminUserPage` | `UserRead` | `GET /admin/users` |
| `PaperPage` | `PaperBrief` | `GET /papers`、`GET /repos/{id}/papers` |
| `AdminPaperPage` | `AdminPaperListItem` | `GET /admin/papers` |
| `SessionPage` | `SessionSummary` | `GET /sessions` |
| `RepoPage` | `RepoSummary` | `GET /repos` |
| `RelatedRepoPage` | `RelatedRepoItem` | `GET /repos/related` |
| `ConversationPage` | `ConversationItem` | `GET /repos/{id}/conversations` |
| `AdminRepoPage` | `AdminRepoListItem` | `GET /admin/repos` |
| `MessagePage` | `Message` | 所有消息接口（游标分页） |

### 2.8 ErrorResponse

见 §1.7。

### 2.9 请求/响应模型索引

| 模型 | 用途 | 定义 |
| --- | --- | --- |
| `UserBrief` | 仓库创建者、对话发起者 | §2.2 |
| `UserRead` | 登录用户、后台用户 | §2.3 |
| `PaperBrief` | 全局目录、会话论文、仓库论文 | §2.4 |
| `Citation` | 回答与四维的引用 | §1.11.4 |
| `Message` | 对话消息 | §2.6 |
| `PaperDetail` | 用户端论文详情 | §8.2 |
| `SummaryRead` | 四维内容 | §8.6 |
| `SessionSummary` | 最近会话列表项 | §9.4 |
| `SessionDetail` | 会话工作区初始数据 | §9.3 |
| `RepoSummary` | 仓库列表项 | §10.1 |
| `RepoPermissions` | 当前用户对仓库的权限 | §10.1 |
| `RepoDetail` | 仓库详情 | §10.1 |
| `RelatedRepoItem` | 相关仓库卡片 | §10.12 |
| `ConversationItem` | 仓库对话列表项 | §10.14 |
| `AdminPaperListItem` | 后台论文列表项 | §7.2 |
| `AdminPaperDetail` | 后台论文详情 | §7.3 |
| `AdminRepoListItem` | 后台仓库列表项 | §12.1 |
| `AdminStats` | 管理总览 | §6.5 |
| `ImportItem` / `ImportResponse` | 论文导入结果 | §7.1 |
| `RepoPaperAddResult` | 批量添加论文结果 | §10.8 |
| `StarResult` | Star 操作结果 | §10.10 |
| `LoginRequest` | 登录 | §5.1 |
| `AdminUserCreate` | 创建用户 | §6.2 |
| `AdminUserUpdate` | 修改用户 | §6.3 |
| `PasswordReset` | 重置密码 | §6.4 |
| `AdminPaperUpdate` | 修正论文元数据 | §7.4 |
| `SummaryGenerateRequest` | 生成四维 | §8.5 |
| `SearchRequest` | 文字查询 | §9.1 |
| `ChatRequest` | 三种对话的请求体 | §11.1 |
| `RepoCreate` / `RepoUpdate` | 创建/修改仓库 | §10.2 / §10.5 |
| `RepoPaperAddRequest` | 批量添加论文 | §10.8 |
| 实体分页 / 消息游标分页 | 所有列表 | §1.6 / §2.7 |
| `ErrorResponse` | 所有错误 | §1.7 |

---

## 3. 接口总览

| # | 方法 | 路径 | 认证 | 负责人 | 说明 |
| ---: | --- | --- | --- | --- | --- |
| 1 | GET | `/` | 否 | D1 | 应用信息 |
| 2 | GET | `/api/v1/health/live` | 否 | D1 | 存活检查 |
| 3 | GET | `/api/v1/health/ready` | 否 | D1 | 就绪检查 |
| 4 | POST | `/api/v1/auth/login` | 否 | D1 | 登录 |
| 5 | POST | `/api/v1/auth/logout` | 是 | D1 | 退出当前会话 |
| 6 | GET | `/api/v1/auth/me` | 是 | D1 | 当前用户 |
| 7 | GET | `/api/v1/admin/users` | admin | D1 | 用户列表 |
| 8 | POST | `/api/v1/admin/users` | admin | D1 | 创建用户 |
| 9 | PATCH | `/api/v1/admin/users/{user_id}` | admin | D1 | 修改用户 |
| 10 | POST | `/api/v1/admin/users/{user_id}/reset-password` | admin | D1 | 重置密码 |
| 11 | GET | `/api/v1/admin/stats` | admin | D1 | 管理总览 |
| 12 | POST | `/api/v1/admin/papers/import` | admin | D2 | 导入 PDF |
| 13 | GET | `/api/v1/admin/papers` | admin | D2 | 后台论文列表 |
| 14 | GET | `/api/v1/admin/papers/{paper_id}` | admin | D2 | 后台论文详情 |
| 15 | PATCH | `/api/v1/admin/papers/{paper_id}` | admin | D2 | 修正元数据 |
| 16 | POST | `/api/v1/admin/papers/{paper_id}/retry` | admin | D2 | 失败重试 |
| 17 | POST | `/api/v1/admin/papers/{paper_id}/archive` | admin | D2 | 归档论文 |
| 18 | GET | `/api/v1/papers` | 是 | D2 | 用户端论文搜索 |
| 19 | GET | `/api/v1/papers/{paper_id}` | 是 | D2 | 论文详情 |
| 20 | GET | `/api/v1/papers/{paper_id}/download` | 是 | D2 | 下载原文 |
| 21 | GET | `/api/v1/papers/{paper_id}/summaries` | 是 | D5 | 读取四维 |
| 22 | POST | `/api/v1/papers/{paper_id}/summaries/generate` | 是 | D5 | 生成四维 |
| 23 | POST | `/api/v1/papers/{paper_id}/chat/stream` | 是 | D3 | 单篇对话（SSE） |
| 24 | GET | `/api/v1/papers/{paper_id}/messages` | 是 | D3 | 单篇对话历史 |
| 25 | POST | `/api/v1/search` | 是 | D3 | 文字全局查询 |
| 26 | POST | `/api/v1/search/pdf` | 是 | D3 | PDF 全局查询 |
| 27 | GET | `/api/v1/sessions` | 是 | D3 | 最近会话 |
| 28 | GET | `/api/v1/sessions/{session_id}` | 是 | D3 | 会话详情（三视图数据） |
| 29 | POST | `/api/v1/sessions/{session_id}/chat/stream` | 是 | D3 | 会话对话（SSE） |
| 30 | GET | `/api/v1/sessions/{session_id}/messages` | 是 | D3 | 会话消息历史 |
| 31 | POST | `/api/v1/repos` | 是 | D4 | 创建仓库 |
| 32 | GET | `/api/v1/repos` | 是 | D4 | 仓库列表 |
| 33 | GET | `/api/v1/repos/{repo_id}` | 是 | D4 | 仓库详情 |
| 34 | PATCH | `/api/v1/repos/{repo_id}` | 是 | D4 | 修改仓库 |
| 35 | DELETE | `/api/v1/repos/{repo_id}` | 是 | D4 | 删除仓库 |
| 36 | GET | `/api/v1/repos/{repo_id}/papers` | 是 | D4 | 仓库论文列表 |
| 37 | POST | `/api/v1/repos/{repo_id}/papers` | 是 | D4 | 添加论文 |
| 38 | DELETE | `/api/v1/repos/{repo_id}/papers/{paper_id}` | 是 | D4 | 移除论文 |
| 39 | POST | `/api/v1/repos/{repo_id}/star` | 是 | D4 | Star |
| 40 | DELETE | `/api/v1/repos/{repo_id}/star` | 是 | D4 | 取消 Star |
| 41 | GET | `/api/v1/repos/related` | 是 | D4 | 相关仓库 |
| 42 | POST | `/api/v1/repos/{repo_id}/chat/stream` | 是 | D4 | 仓库 Agent（SSE） |
| 43 | GET | `/api/v1/repos/{repo_id}/messages` | 是 | D4 | 我的仓库对话历史 |
| 44 | GET | `/api/v1/repos/{repo_id}/conversations` | 是 | D4 | 仓库对话列表 |
| 45 | GET | `/api/v1/repos/{repo_id}/conversations/{conversation_id}/messages` | 是 | D4 | 指定对话消息 |
| 46 | GET | `/api/v1/admin/repos` | admin | D4 | 后台仓库列表 |

> 明确不存在的接口：`POST /auth/register`、用户上传论文、删除用户、取消归档、物理删除论文、会话删除、消息编辑/删除、仓库成员邀请、公开仓库审核、分享链接。

> 路由实现注意：`GET /repos/related` 与 `GET /repos/{repo_id}` 同方法、同前缀。必须保证 `related` 不会被解析为 `repo_id`（`repo_id` 使用整数路径转换器，或先注册 `related`）。其余 `{id}` 路径参数均为正整数，非法值返回 422。

---

## 4. Meta 与健康检查

### 4.1 GET `/`

- 认证：否
- 响应：`200`

```json
{"name": "PaperLens", "version": "0.1.0", "docs": "/docs"}
```

- 错误：无业务错误。

### 4.2 GET `/api/v1/health/live`

- 认证：否
- 响应：`200`

```json
{"status": "ok", "service": "paperlens", "version": "0.1.0"}
```

### 4.3 GET `/api/v1/health/ready`

- 认证：否
- 行为：执行数据库 `SELECT 1`
- 响应：`200`

```json
{"status": "ok", "database": "ok"}
```

- 错误：`503 database_unavailable`
- 注意：只检查数据库连接，不检查迁移版本、模型服务、磁盘空间。

### 4.4 GET `/openapi.json`、`/docs`

- 认证：否
- 由 FastAPI 提供；实现必须保证 OpenAPI 与本文档一致。

### 4.5 Apifox / OpenAPI 导入

- 完整契约快照：`doc/paperlens-openapi.json`（OpenAPI 3.0.3，39 个路径、46 个操作），由本文档生成，覆盖尚未实现的接口。
- Apifox：项目 → 导入数据 → OpenAPI/Swagger → 选择该文件；导入后可生成 Mock、目录和模型。
- 运行时 `/openapi.json` 只包含已实现接口；不要把它当作完整契约。两者不一致时以本文档为准。
- 认证为 Cookie：先调用 `POST /api/v1/auth/login`，Apifox 会保存 `paperlens_session`；若未自动保存，在 Cookie/环境变量中手动设置该 Cookie。
- SSE 接口在 Apifox 中可能只显示为普通响应；调试流式事件建议用 §17.2 的 `fetch` 示例或 `curl -N`。

---

## 5. 认证

### 5.1 POST `/api/v1/auth/login`

- 认证：游客
- 请求：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `email` | string | 是 | 合法邮箱，≤255 |
| `password` | string | 是 | 1–128 |

- 成功：`200`

```json
{
  "user": {
    "id": 1,
    "email": "alice@example.com",
    "display_name": "Alice",
    "role": "reader",
    "is_active": true,
    "created_at": "2026-10-26T08:00:00Z"
  }
}
```

- 响应头：`Set-Cookie: paperlens_session=<opaque>; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800`
- 错误：
  - `401 invalid_credentials`：邮箱不存在、密码错误、账号被停用，统一同一消息「邮箱或密码错误」
  - `422 validation_error`
- 说明：
  - 邮箱转小写后查询。
  - 每次登录创建新会话，不撤销旧会话。
  - 不返回会话令牌。

### 5.2 POST `/api/v1/auth/logout`

- 认证：是
- 请求体：无
- 成功：`204`，无响应体；删除当前会话并清除 Cookie
- 错误：`401 unauthorized`
- 说明：只撤销当前 Cookie 对应会话，不影响其他设备。

### 5.3 GET `/api/v1/auth/me`

- 认证：是
- 响应：`200 UserRead`
- 错误：`401 unauthorized`
- 说明：角色变更、停用状态在下一次请求生效。

### 5.4 已移除：POST `/api/v1/auth/register`

- 产品规则：账号由管理员创建，不开放自注册。
- 该接口在 V1.0 中**不存在**；访问应返回 404 `not_found`。
- 初始化第一个管理员使用 CLI，不在 API 中提供：

```bash
uv run python -m scripts.create_admin --email admin@example.com
```

---

## 6. 管理后台：用户与总览

### 6.1 GET `/api/v1/admin/users`

- 认证：admin
- 查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `q` | string | "" | 匹配邮箱或显示名，≤200；空字符串不过滤 |
| `page` | int | 1 | ≥1 |
| `page_size` | int | 20 | 1–100 |

- 响应：`200 AdminUserPage`

```json
{
  "items": [
    {
      "id": 1,
      "email": "alice@example.com",
      "display_name": "Alice",
      "role": "reader",
      "is_active": true,
      "created_at": "2026-10-26T08:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

- 排序：`id DESC`
- 错误：`401`、`403 forbidden`、`422`

### 6.2 POST `/api/v1/admin/users`

- 认证：admin
- 请求：`AdminUserCreate`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `email` | string | 是 | 合法邮箱，≤255；入库小写 |
| `password` | string | 是 | 8–128 |
| `display_name` | string\|null | 否 | 1–64；为 `null`、空字符串或全空白时，取邮箱 `@` 前部分并截断到 64 字符 |
| `role` | UserRole | 否 | 默认 `reader` |

- 成功：`201 UserRead`
- 错误：
  - `409 email_taken`
  - `401`、`403`、`422`
- 说明：不自动登录新用户；响应不含密码；邮箱入库前转小写。

### 6.3 PATCH `/api/v1/admin/users/{user_id}`

- 认证：admin
- 路径参数：`user_id` int，≥1
- 请求：`AdminUserUpdate`，请求体必须至少包含一个键；空对象 `{}` 返回 `400 bad_request`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `display_name` | string\|null | 否 | 1–64；传 `null` 清空；传空字符串按 `null` 处理 |
| `role` | UserRole | 否 | `reader` / `admin`，不能为 `null` |
| `is_active` | bool | 否 | `false` 表示停用 |

- 成功：`200 UserRead`
- 错误：
  - `400 bad_request`：空更新
  - `400 cannot_demote_self`：把自己从 `admin` 降为 `reader`
  - `400 cannot_disable_self`：把自己停用
  - `400 last_admin`：操作后系统将没有可用管理员
  - `404 user_not_found`
  - `401`、`403`、`422`
- 规则：
  - 「可用管理员」= `role=admin AND is_active=true` 的用户。
  - 只有目标当前是可用管理员、且本次操作会使其不再是可用管理员时，才检查 `last_admin`；若操作后可用管理员数为 0，返回 `400 last_admin`。
  - 多个规则同时命中时，按 `cannot_demote_self` → `cannot_disable_self` → `last_admin` 的顺序返回。
  - 管理员可以修改自己的显示名；修改角色/启停受上述自保护规则限制。
  - 停用用户时删除其全部 `user_sessions`，旧 Cookie 下一次请求返回 401；重新启用后必须重新登录。角色修改立即生效。
  - 不删除用户；不修改邮箱和密码（密码用 §6.4）。

### 6.4 POST `/api/v1/admin/users/{user_id}/reset-password`

- 认证：admin
- 请求：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `new_password` | string | 是 | 8–128 |

- 成功：`204`，无响应体
- 行为：更新密码哈希，删除该用户全部 `user_sessions`
- 错误：`404 user_not_found`、`401`、`403`、`422`
- 说明：管理员重置自己的密码会撤销自己的当前会话，下一次请求需重新登录。

### 6.5 GET `/api/v1/admin/stats`

- 认证：admin
- 响应：`200 AdminStats`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `users_total` | int | 用户总数，含停用 |
| `users_active` | int | 启用用户数 |
| `papers_total` | int | 论文总数，含归档 |
| `papers_ready` | int | `status=ready`，含归档 |
| `papers_processing` | int | `status in (queued, parsing, indexing)` |
| `papers_failed` | int | `status=failed` |
| `papers_archived` | int | `is_archived=true` |
| `repos_total` | int | 仓库总数，含私有 |
| `repos_private` | int | 私有仓库数 |
| `generated_at` | string | 统计时间，UTC `Z` |

- `papers_archived` 是 `papers_ready` 的子集；`papers_processing`、`papers_failed` 不包含归档论文。
- `repos_private` 是 `repos_total` 的子集；`users_active` 是 `users_total` 的子集。
- 错误：`401`、`403`

---

## 7. 管理后台：论文目录

### 7.1 POST `/api/v1/admin/papers/import`

- 认证：admin
- Content-Type：`multipart/form-data`
- 请求字段：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `files` | file[] | 是 | 1–20 个 PDF，每个 ≤20MB，总 ≤100MB |

- 成功：`202 ImportResponse`

```json
{
  "items": [
    {
      "filename": "attention.pdf",
      "paper_id": 12,
      "status": "queued",
      "duplicate": false,
      "is_archived": false,
      "error": null
    }
  ],
  "created": 1,
  "duplicates": 0,
  "rejected": 0
}
```

- `ImportItem`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `filename` | string | 本次上传文件名的 basename |
| `paper_id` | int\|null | 被拒绝时为 null |
| `status` | PaperStatus\|null | 新导入为 `queued`；重复时为已有状态；拒绝时为 null |
| `duplicate` | bool | 是否命中已有论文（SHA-256） |
| `is_archived` | bool | 重复论文是否已归档 |
| `error` | string\|null | 单文件被拒绝的原因，用户可读；`created`/`duplicate` 项为 null |

- 错误（请求级）：
  - `413 file_too_large`：本次请求文件总量 >100MB
  - `422 validation_error`：没有 `files`、文件数为 0 或 >20
  - `401`、`403`
- 请求级校验顺序：先校验 `files` 存在且数量 1–20（失败 422），再校验总大小 ≤100MB（失败 413）；请求级失败时不保存任何文件、不创建任何论文。
- 说明：
  - 单文件类型错误（非 PDF）、单文件 >20MB、文件名异常，**不拒绝整个请求**，放在 `items` 中返回 `rejected`，`error` 写原因；导入接口不会返回 415。
  - 被拒绝项固定为 `paper_id=null`、`status=null`、`duplicate=false`、`is_archived=false`、`error=原因`。
  - `items` 顺序与 `files` 顺序一致，长度相等；`created + duplicates + rejected = items.length`。
  - 即使全部文件都被拒绝，仍返回 `202`，`created=0`、`rejected=N`；管理员根据 `items` 修正后重传。
  - 同一请求内出现两个相同 SHA-256 的文件：按 `files` 顺序，第一个创建，后续标记 `duplicate=true`。
  - 文件名取上传文件名的 basename；basename 为空或长度 >255 视为异常，放入 `rejected`。
  - 重复文件复用已有记录，不重复创建、不重复存储；重复的失败论文不会被自动重试，需管理员单独调用 retry。
  - 并发导入同一 SHA-256 由数据库唯一索引兜底；后到者按 `duplicate=true` 处理。
  - 新论文的初始 `title` 为文件名去扩展名（截断到 500 字符；为空时用「未命名论文」），`authors=[]`、`year=null`、`venue=null`、`abstract=null`；解析成功后覆盖。
  - 导入后异步解析；接口只保证已入队。
  - 不直接加入任何仓库。

### 7.2 GET `/api/v1/admin/papers`

- 认证：admin
- 查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `q` | string | "" | 匹配标题/摘要，≤200；空字符串或全空白不过滤 |
| `status` | PaperStatus\|null | null | 精确过滤 |
| `archived` | bool\|null | null | `true` 仅归档；`false` 仅未归档；不传全部 |
| `page` | int | 1 | ≥1 |
| `page_size` | int | 20 | 1–100 |

- 响应：`200 AdminPaperPage`

`AdminPaperListItem`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 论文 ID |
| `title` | string | 标题 |
| `authors` | string[] | 作者 |
| `year` | int\|null | 年份 |
| `venue` | string\|null | 期刊/会议 |
| `status` | PaperStatus | 处理状态 |
| `is_archived` | bool | 是否归档 |
| `file_name` | string\|null | 原始文件名（basename） |
| `error_message` | string\|null | 失败原因 |
| `created_at` | string | 创建时间 |
| `updated_at` | string | 更新时间 |

- 排序：`created_at DESC, id DESC`
- 错误：`401`、`403`、`422`

### 7.3 GET `/api/v1/admin/papers/{paper_id}`

- 认证：admin
- 响应：`200 AdminPaperDetail`

在 `AdminPaperListItem` 基础上增加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `abstract` | string\|null | 摘要 |
| `doi` | string\|null | DOI，可为空 |
| `chunk_count` | int | 原文片段数 |
| `archived_at` | string\|null | 归档时间 |
| `file` | object\|null | 文件信息，见下 |

`file`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `original_filename` | string | 原始文件名 |
| `size` | int | 字节 |
| `mime` | string | `application/pdf` |
| `sha256` | string | 文件哈希 |
| `uploaded_at` | string | 上传时间 |

- 错误：`404 paper_not_found`、`401`、`403`

### 7.4 PATCH `/api/v1/admin/papers/{paper_id}`

- 认证：admin
- 请求：至少一个字段

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `title` | string | 否 | 1–500，不能全空白 |
| `authors` | string[] | 否 | ≤50 项，每项 1–200 |
| `year` | int\|null | 否 | 1900–2100 |
| `venue` | string\|null | 否 | ≤255 |
| `abstract` | string\|null | 否 | ≤20000 |

- 成功：`200 AdminPaperDetail`
- 错误：`400 bad_request`（空更新）、`404 paper_not_found`、`409 invalid_state`（论文处于 `queued/parsing/indexing`）、`401`、`403`、`422`
- 说明：
  - 只改元数据，不改 `status`、`is_archived`、文件。
  - 允许修改 `ready`、`failed` 和已归档论文；处理中的论文不允许修改，避免解析结果覆盖人工修正。
  - `year`、`venue`、`abstract` 传 `null` 表示清空；传空字符串按 `null` 处理。
  - `authors` 传 `[]` 表示清空，不能为 `null`；`title` 不能为 `null`。
  - 成功更新后刷新 `updated_at`。
  - DOI 不提供编辑；由解析结果决定。

### 7.5 POST `/api/v1/admin/papers/{paper_id}/retry`

- 认证：admin
- 请求体：无
- 成功：`202 AdminPaperDetail`，状态变为 `queued`
- 错误：
  - `404 paper_not_found`
  - `409 invalid_state`：当前不是 `failed`，或论文已归档
  - `401`、`403`
- 说明：
  - 只有 `status=failed` 且 `is_archived=false` 可以重试；其他状态（含已归档）返回 409。
  - 重试将 `status` 置为 `queued`，清空 `error_message`，保留文件；重新解析成功后替换该论文的原文片段。
  - 重试不改变 `is_archived`；正常数据中归档论文一定是 `ready`，不会出现归档且 `failed`。
  - 重试按正常解析流程处理，可能覆盖自动提取的元数据字段；管理员的手工修正应在重试完成后确认。

### 7.6 POST `/api/v1/admin/papers/{paper_id}/archive`

- 认证：admin
- 请求体：无
- 成功：`200 AdminPaperDetail`
- 错误：
  - `404 paper_not_found`
  - `409 invalid_state`：只有 `status=ready` 可以归档；`queued/parsing/indexing/failed` 均不能归档
  - `401`、`403`
- 行为：
  - 归档时写入 `archived_at` 并刷新 `updated_at`。
  - 已归档且 `ready` 再次调用：幂等返回 200；再次调用不改变 `archived_at`。
  - 归档后不进入新搜索、不能新加入仓库；旧会话、旧仓库、旧引用仍可打开。
  - 文件、片段、四维缓存保留；文件存在时仍可下载。
  - 不物理删除论文。
  - 不提供“取消归档”；归档后该论文不再进入新检索/新仓库，但始终可读。

### 7.7 论文处理状态与失败语义

- 后台列表/详情轮询即可，不提供 WebSocket。
- 处理时限：`PAPER_PROCESSING_TIMEOUT_MINUTES`，默认 30 分钟。
- 服务重启时，处理开始超过时限的 `parsing/indexing` 必须标记为 `failed`，原因写「处理中断，请重试」。
- 失败原因必须是用户可读文本，不包含堆栈、SQL、路径。
- 处理中的论文不出现在用户端 `GET /papers`、全局搜索、可加入仓库列表中。
- 所有“新搜索/新加仓库”入口必须走统一的 `visible_papers()` 条件：`status=ready AND is_archived=false`；详情/下载/四维/对话对归档论文单独放行。
- 解析只支持带文本层的 PDF；扫描件/图片型 PDF 直接失败，不做 OCR。
- **`ready` 不变量**：一篇论文只有在文件已保存、至少抽取到一个文本片段、索引完成时才允许标记 `ready`。否则必须是 `failed`。因此正常数据下 `ready`（含归档）论文可下载、可对话、可生成四维；若运维手工删除了存储文件，`can_download` 会变为 false，需修复文件。
- 归档不改变 `status`；归档论文始终是 `ready`。

---

## 8. 论文（用户端）与四维总结

### 8.1 GET `/api/v1/papers`

- 认证：是
- 用途：用户在主页/添加论文弹窗中搜索全局目录
- 查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `q` | string | "" | 标题/摘要关键词，≤200；支持中文，服务端可翻译为英文检索 |
| `page` | int | 1 | ≥1 |
| `page_size` | int | 20 | 1–100 |

- 响应：`200 PaperPage`，`items` 为 `PaperBrief`
- 过滤：只返回 `status=ready` 且 `is_archived=false`
- 排序：有 `q` 时相关度降序、`id DESC`；无 `q` 时 `id DESC`
- 错误：`401`、`422`
- 说明：这是用户端目录，不返回处理中/失败/归档论文。

### 8.2 GET `/api/v1/papers/{paper_id}`

- 认证：是
- 响应：`200 PaperDetail`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 论文 ID |
| `title` | string | 标题 |
| `authors` | string[] | 作者 |
| `year` | int\|null | 年份 |
| `venue` | string\|null | 期刊/会议 |
| `abstract` | string\|null | 摘要 |
| `doi` | string\|null | DOI |
| `is_archived` | bool | 是否归档 |
| `has_file` | bool | 是否存在文件记录 |
| `can_download` | bool | 当前是否可下载 |
| `can_chat` | bool | 是否有可用原文片段 |
| `available_summary_levels` | SummaryLevel[] | 已缓存的四维档位，按 `brief, standard, deep` 顺序 |
| `created_at` | string | 创建时间 |

- 字段语义：
  - `has_file`：数据库中存在该论文的文件记录。
  - `can_download`：文件记录存在且存储文件当前可读；文件被人工删除时为 false。
  - `can_chat`：存在至少一个可检索原文片段；按 `ready` 不变量，正常 `ready` 论文为 true。
  - `available_summary_levels`：只统计当前缓存键（`content_hash + prompt_version + model`）下有效的缓存；过期缓存不计入。
- 错误：
  - `404 paper_not_found`：不存在，或 `status` 不是 `ready`
  - `401`
- 说明：
  - 所有登录用户都可查看全局论文详情，不受它被放入哪个仓库影响。
  - 归档论文返回 `is_archived=true`，仍可查看；正常数据中归档论文一定是 `ready`。
  - 处理中/失败论文在用户端统一按不存在处理（管理员在后台查看）。

### 8.3 GET `/api/v1/papers/{paper_id}/download`

- 认证：是
- 成功：`200`，二进制 PDF
  - `Content-Type: application/pdf`
  - `Content-Disposition: attachment; filename="paper.pdf"; filename*=UTF-8''<encoded>`（`filename` 为 ASCII fallback）
- 错误：
  - `404 paper_not_found`：论文不可见
  - `404 file_not_found`：没有可下载文件
  - `401`
- 说明：
  - 归档论文只要文件保留仍可下载。
  - 不提供用户上传、替换、删除文件。

### 8.4 GET `/api/v1/papers/{paper_id}/summaries`

- 认证：是
- 查询参数：

| 参数 | 类型 | 默认 | 约束 |
| --- | --- | --- | --- |
| `level` | SummaryLevel | `standard` | `brief/standard/deep` |

- 成功：`200 SummaryRead`

```json
{
  "paper_id": 12,
  "level": "standard",
  "content": {
    "problem": "论文要解决的问题",
    "prior_work": "前人研究",
    "method_innovation": "方法与创新",
    "limitations": "不足"
  },
  "citations": [
    {
      "index": 1,
      "paper_id": 12,
      "chunk_id": 345,
      "page": 5,
      "quote": "原文摘录"
    }
  ],
  "generated_at": "2026-10-26T08:00:00Z"
}
```

- 错误：
  - `404 paper_not_found`：论文不存在、处理中/失败（用户端不可见），优先于其他错误判断
  - `404 summary_not_generated`：论文可见，但该 `level` 没有缓存
  - `401`、`422`
- 说明：
  - 四维是论文级内容，跨会话、仓库、详情共享。
  - 缓存键为 `paper_id + level + content_hash + prompt_version + model`；论文内容或提示词版本变化后旧缓存失效，重新生成。
  - 归档论文已缓存的四维仍可读取。
  - 信息不足时对应字段为「论文未明确说明」，不是 null。

### 8.5 POST `/api/v1/papers/{paper_id}/summaries/generate`

- 认证：是
- 请求：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `level` | SummaryLevel | 否 | 默认 `standard` |

- 成功：`200 SummaryRead`
- 行为：
  - 若缓存已存在，直接返回缓存，不重复调用模型。
  - 若不存在，同步生成、保存缓存、返回。
  - 最长等待 60 秒。
- 错误：
  - `404 paper_not_found`：论文不存在、处理中/失败（用户端不可见），优先于其他错误判断
  - `409 paper_not_ready`：论文可见但没有任何原文片段（仅用于 `ready` 数据异常）
  - `502 llm_failed`：模型返回失败
  - `503 llm_unavailable`：未配置模型服务
  - `504 llm_timeout`：模型超时
  - `401`、`422`
- 说明：
  - 四维只使用论文自身内容，不受会话证据范围限制。
  - 不使用用户级覆盖；所有用户看到同一份。
  - 缓存键与 §8.4 相同；缓存命中时直接返回，不调用模型。
  - 归档论文（一定是 `ready`，有片段）允许生成；已缓存的直接返回。
  - 并发请求可能重复生成，最终缓存以后写为准。

### 8.6 SummaryRead 结构

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `paper_id` | int | 论文 ID |
| `level` | SummaryLevel | 档位 |
| `content` | object | 四个固定字段，均为 string |
| `content.problem` | string | 问题 |
| `content.prior_work` | string | 前人研究 |
| `content.method_innovation` | string | 方法/创新 |
| `content.limitations` | string | 不足 |
| `citations` | Citation[] | 证据，均来自同一篇论文，按 `index` 升序 |
| `generated_at` | string | 生成时间 |

---

## 9. 全局搜索与会话

### 9.1 POST `/api/v1/search`

- 认证：是
- 请求：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `query` | string | 是 | 1–2000，去除首尾空白后不能为空 |

- 成功：`201 SessionDetail`
- 错误：
  - `401`
  - `422 validation_error`
  - `503 search_unavailable`：检索服务/数据库不可用；不创建空会话
- 行为：
  - 在**全局论文目录**检索，不限于用户仓库。
  - 一次查询创建一个会话，保存查询、论文、证据快照。
  - 没有结果**也创建空会话**，`has_evidence=false`。
  - 自动创建该会话的对话和一条系统消息。
  - 会话属于当前用户，其他用户（含管理员）不可访问。
  - 后续全局目录新增/归档不会修改旧会话的论文集合和证据。
- 说明：
  - 服务端可对中文 query 做英文翻译/扩展，仅用于检索；`query` 字段仍返回原始中文。
  - 翻译/扩展失败时回退为原始 `query` 检索，不因翻译失败返回 5xx。
  - 检索内部预算见 §1.14。

### 9.2 POST `/api/v1/search/pdf`

- 认证：是
- Content-Type：`multipart/form-data`
- 请求字段：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `file` | file | 是 | 单个 PDF，≤20MB，≤50 页，必须有文本层 |

- 成功：`201 SessionDetail`
- 错误：
  - `413 file_too_large`
  - `415 unsupported_media_type`
  - `422 validation_error`：缺少 `file` 或文件为空
  - `422 pdf_no_text`：加密、损坏或没有可提取文字（含仅空白；扫描件/图片型 PDF）
  - `422 pdf_too_many_pages`
  - `503 search_unavailable`
  - `401`
- 校验顺序：文件大小（413）→ 类型（415）→ 页数（422 `pdf_too_many_pages`）→ 可提取文字（422 `pdf_no_text`）。
- 行为：
  - 只提取文字作为本次查询，**不保存文件、不入库、不创建论文、不加入仓库**。
  - 提取文字后内部截取前 2000 字符用于检索；`query` 字段为原始文件名（用于展示），`query_type=pdf`。
  - 文件名为空时 `query` 使用 `"PDF 查询"`。
  - 提取成功但无检索结果：创建空会话。
  - 提取失败：不创建会话，停留主页。

### 9.3 SessionDetail

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 会话 ID |
| `query` | string | 原始查询；PDF 查询为文件名 |
| `query_type` | QueryType | text/pdf |
| `paper_count` | int | 本次会话论文数，等于 `papers.length` |
| `evidence_count` | int | 证据数 |
| `related_repo_count` | int | 当前相关公开仓库数量（实时计算），与 `GET /repos/related?session_id=` 的 `total` 同口径 |
| `has_evidence` | bool | 是否有证据；false 时对话输入不可用 |
| `system_message` | string | 对话顶部系统消息 |
| `conversation_id` | int | 当前用户在该会话的对话 ID（创建会话时已创建） |
| `papers` | PaperBrief[] | 会话快照中的论文，按相关度降序，最多 8 篇 |
| `created_at` | string | 创建时间 |

- 说明：
  - `papers` 的**集合**是快照，后续新增/归档不会改变它；每篇论文的元数据（标题、作者、年份、venue、摘要）和 `is_archived` 读取当前值，因此管理员修正元数据或归档后会反映在旧会话中。
  - `evidence` 也是快照；V1.0 不对 `ready` 论文重新解析，重试只针对 `failed`，因此会话证据的 `chunk` 不会被替换。
  - 归档论文仍出现在旧会话中，`is_archived=true`。
  - `related_repo_count` 是实时推荐数量，不是快照；目录/Star 变化后可能改变。
  - `has_evidence=false` 时，`related_repo_count` 固定为 0，与产品「三视图均为空状态」一致。
  - `system_message` 始终存在，为固定文案：非空会话为「我会只根据本次查询的论文证据回答，并在没有足够证据时拒答。」；空会话为「没有找到足够证据回答这次查询。你可以修改查询，或去公开仓库看看。」前端应始终展示它。
  - `conversation_id` 始终存在；会话创建时即创建对话和系统消息。

### 9.4 GET `/api/v1/sessions`

- 认证：是
- 查询参数：`page`、`page_size`
- 响应：`200 SessionPage`，`items` 为 `SessionSummary`

`SessionSummary`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 会话 ID |
| `query` | string | 原始查询 |
| `query_type` | QueryType | text/pdf |
| `paper_count` | int | 论文数 |
| `evidence_count` | int | 证据数 |
| `created_at` | string | 创建时间 |

- 过滤：仅当前用户
- 排序：`created_at DESC, id DESC`
- 错误：`401`、`422`
- 说明：管理员也不能查看他人的会话。

### 9.5 GET `/api/v1/sessions/{session_id}`

- 认证：是
- 权限：会话所有者；其他人（含管理员）404
- 响应：`200 SessionDetail`
- 错误：`404 session_not_found`、`401`
- 说明：用于会话工作区三视图的初始加载；四维按需调用论文四维接口。

### 9.6 POST `/api/v1/sessions/{session_id}/chat/stream`

- 认证：是
- 权限：会话所有者
- 请求：`{"message": "..."}`，1–2000
- 成功：`200 text/event-stream`，事件见 §1.11
- 错误（流开始前）：
  - `404 session_not_found`
  - `409 no_evidence`：`has_evidence=false`，输入不可用
  - `401`、`422`
- 行为：
  - 只使用该会话保存的论文和证据快照，不重新检索全局目录。
  - 支持多轮追问。
  - 事实性内容必须带引用；无有效引用时返回固定拒答文案。
  - 问到会话范围外：说明范围不够，建议新查询或去仓库。
  - 不做 Agent 多步全局检索。
- SSE `meta.scope="session"`，`scope_id=session_id`。

### 9.7 GET `/api/v1/sessions/{session_id}/messages`

- 认证：是
- 权限：会话所有者
- 查询：`limit`、`before_id`（§1.6.2）
- 响应：`200 MessagePage`
- 说明：
  - 包含系统消息（`role=system`，`id` 最小）；最新一页可能不包含它，前端用 `SessionDetail.system_message` 显示欢迎语。
  - 消息按 `id DESC` 返回。
- 错误：`404 session_not_found`、`401`、`422`

---

## 10. 仓库、Star 与相关仓库

### 10.1 仓库数据结构

#### RepoSummary

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 仓库 ID |
| `name` | string | 名称 |
| `description` | string | 简介，可为空字符串 |
| `visibility` | RepoVisibility | public/private |
| `owner` | UserBrief | 创建者 |
| `paper_count` | int | 论文数 |
| `star_count` | int | Star 数；私有仓库固定为 0 |
| `is_starred` | bool | 当前用户是否已 star；私有仓库固定 false |
| `created_at` | string | 创建时间 |
| `updated_at` | string | 更新时间：仓库元数据变更、论文加入/移除时更新；Star 和对话不更新它 |

#### RepoPermissions

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `can_edit` | bool | 改名称/简介/可见性 |
| `can_delete` | bool | 删除仓库 |
| `can_manage_papers` | bool | 添加/移除论文 |
| `can_star` | bool | 可 star/取消 star |
| `can_chat` | bool | 可发起仓库对话 |

#### RepoDetail

`RepoSummary` + `permissions: RepoPermissions`。

字段语义：

- `paper_count`：仓库论文关系数，包含已归档但仍在仓库中的论文。
- `star_count`：公开仓库的全部 star 记录数，包含已停用账号的 star；私有仓库固定为 0。
- `is_starred`：当前用户是否 star；自己的仓库和私有仓库固定为 false。
- `permissions` 只表示权限，不表示资源状态；空仓库的 `can_chat` 仍为 true，调用对话接口返回 409 `repo_empty`。

### 10.2 POST `/api/v1/repos`

- 认证：是
- 请求：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | 1–64，去首尾空白后非空 |
| `description` | string | 否 | 0–500，默认 "" |
| `visibility` | RepoVisibility | 否 | 默认 `private` |

- 成功：`201 RepoDetail`
- 错误：
  - `409 repo_name_taken`：同一创建者下名称重复
  - `401`、`422`
- 说明：
  - 仓库归创建者所有。
  - `name` 去除首尾空白后保存；同一创建者下名称唯一，唯一性按不区分大小写判断（与 MySQL 默认 `utf8mb4_0900_ai_ci` 一致）；不同用户可同名。
  - `description` 去除首尾空白后保存，空字符串保存为 `""`。
  - 创建接口本身不接受 `paper_ids`；创建后调用 `POST /repos/{repo_id}/papers` 加入论文。
  - 不提供成员邀请、多人协作、分享链接。

### 10.3 GET `/api/v1/repos`

- 认证：是
- 查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `scope` | RepoScope | `mine` | `mine` 我创建的（含私有）；`public` 所有公开仓库 |
| `page` | int | 1 | ≥1 |
| `page_size` | int | 20 | 1–100 |

- 响应：`200 RepoPage`，`items` 为 `RepoSummary`
- 排序：`updated_at DESC, id DESC`
- 说明：
  - `scope=public` 包含自己的公开仓库；自己的仓库 `is_starred=false`、`can_star=false`（在详情中体现）。
  - `scope=mine` 包含公开和私有。
  - `scope` 取值见 `RepoScope`；其他值返回 422。
  - 不提供自定义筛选、排序参数；仅提供 `scope` 和分页。
- 错误：`401`、`422`

### 10.4 GET `/api/v1/repos/{repo_id}`

- 认证：是
- 权限：
  - 公开仓库：所有登录用户
  - 私有仓库：创建者、管理员
  - 其他：404
- 响应：`200 RepoDetail`
- 错误：`404 repo_not_found`、`401`
- 说明：
  - 管理员对公开仓库只读：`can_edit=false`、`can_delete=false`、`can_manage_papers=false`，除非管理员就是创建者。
  - 管理员对私有仓库：拥有管理权限。
  - 私有仓库的 `star_count=0`、`is_starred=false`、`can_star=false`；历史 star 记录保留但不展示。

#### 10.4.1 `permissions` 真值表

| 当前用户 / 仓库 | `can_edit` | `can_delete` | `can_manage_papers` | `can_star` | `can_chat` |
| --- | :---: | :---: | :---: | :---: | :---: |
| 创建者（公开） | true | true | true | false | true |
| 其他普通用户（公开） | false | false | false | true | true |
| 管理员（公开，非创建者） | false | false | false | true | true |
| 创建者（私有） | true | true | true | false | true |
| 管理员（私有，非创建者） | true | true | true | false | true |
| 其他用户（私有） | —（404） | —（404） | —（404） | —（404） | —（404） |

### 10.5 PATCH `/api/v1/repos/{repo_id}`

- 认证：是
- 权限：创建者；或管理员且仓库为私有
- 请求：至少一个字段；空对象 `{}` 返回 `400 bad_request`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `name` | string | 否 | 1–64，不能为 `null` |
| `description` | string | 否 | 0–500，不能为 `null`；空字符串保存为 `""` |
| `visibility` | RepoVisibility | 否 | public/private，不能为 `null` |

- 成功：`200 RepoDetail`
- 错误：
  - `400 bad_request`：空更新
  - `404 repo_not_found`
  - `403 forbidden`：普通用户修改他人公开仓库；管理员修改他人公开仓库（非创建者）。他人私有仓库对无权限用户统一 404，不返回 403
  - `409 repo_name_taken`
  - `401`、`422`
- 规则：
  - 成功更新后刷新 `updated_at`（即使值未变化，也视为一次成功写入）。
  - 名称唯一性按不区分大小写判断；改成自己当前名称不报 409。
- 可见性规则：
  - 公开转私有：从公开列表/推荐消失；已有 star 记录保留但隐藏；普通用户访问 404；管理员获得管理权；历史公开对话按私有仓库规则对创建者/管理员可见。
  - 私有转公开：star 数恢复展示；管理员立即回到只读，编辑权归创建者；非发起者的历史对话重新对创建者/管理员隐藏。
  - 响应中的权限按变更后的状态计算。

### 10.6 DELETE `/api/v1/repos/{repo_id}`

- 认证：是
- 权限：创建者；或管理员且仓库为私有
- 成功：`204`
- 错误：`404 repo_not_found`、`403 forbidden`、`401`
- 行为：
  - 删除仓库论文关系、star 关系，以及该仓库的**全部**对话和消息（包括其他用户在公开仓库发起的对话）。
  - **不删除全局论文、论文文件、论文片段、四维缓存。**
  - 再次删除返回 404。
  - 管理员（非创建者）不能删除公开仓库；管理员若是创建者，按创建者处理。

### 10.7 GET `/api/v1/repos/{repo_id}/papers`

- 认证：是
- 权限：同 `GET /repos/{repo_id}`
- 查询：`page`、`page_size`
- 响应：`200 PaperPage`，`items` 为 `PaperBrief`
- 排序：`added_at DESC, paper_id DESC`
- 说明：
  - 包含已归档但仍在仓库中的论文，`is_archived=true`。
  - `total` 为仓库论文关系总数，包含已归档论文。
  - 不包含已移除论文。
- 错误：`404 repo_not_found`、`401`、`422`

### 10.8 POST `/api/v1/repos/{repo_id}/papers`

- 认证：是
- 权限：创建者；或管理员且仓库为私有
- 请求：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `paper_ids` | int[] | 是 | 1–100 个，去重后至少 1 个 |

- 成功：`200 RepoPaperAddResult`

```json
{
  "added": [
    {
      "id": 12,
      "title": "Attention Is All You Need",
      "authors": ["Vaswani"],
      "year": 2017,
      "venue": "NeurIPS",
      "is_archived": false
    }
  ],
  "ignored": [
    {"paper_id": 13, "reason": "duplicate"},
    {"paper_id": 14, "reason": "archived"},
    {"paper_id": 99, "reason": "not_found"}
  ]
}
```

- `reason` 枚举：`duplicate`、`not_ready`、`archived`、`not_found`
- 错误：
  - `404 repo_not_found`
  - `403 forbidden`
  - `401`、`422`
- 规则：
  - `paper_ids` 先去重并保留首次出现顺序；每项必须 ≥1；去重后至少 1 个、最多 100 个。
  - 只能添加 `status=ready` 且未归档论文。
  - 重复添加忽略，不产生重复关系。
  - `ignored.reason` 判定顺序：`not_found` → `duplicate` → `not_ready` → `archived`。
  - `added` 与 `ignored` 均按请求中首次出现的顺序返回。
  - 一次请求是批量操作，单个论文不合格不影响其他论文。
  - 批量添加不是单一资源创建，因此成功返回 `200`，不是 `201`。
  - 至少加入一篇时刷新仓库 `updated_at`；全部被忽略时不刷新。
  - 仓库论文数量不设产品上限；单次请求最多 100 个只是批量限制。

### 10.9 DELETE `/api/v1/repos/{repo_id}/papers/{paper_id}`

- 认证：是
- 权限：创建者；或管理员且仓库为私有
- 成功：`204`
- 错误：`404 repo_not_found`、`403 forbidden`、`401`、`422`（`paper_id` 非正整数）
- 规则：
  - 幂等：论文不存在或不在仓库中也返回 204。
  - 只有实际删除关系时才刷新仓库 `updated_at`。
  - 只移除关系，不删除全局论文。
  - 以后新的仓库对话不再使用该论文；已有消息和引用保留，引用仍可打开全局详情。

### 10.10 POST `/api/v1/repos/{repo_id}/star`

- 认证：是
- 权限：公开仓库、且不是自己的仓库
- 请求体：无
- 成功：`200 StarResult`

```json
{"starred": true, "star_count": 7}
```

`StarResult`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `starred` | bool | 操作后当前用户是否已 star |
| `star_count` | int | 操作后的公开仓库 star 总数 |

- 错误：
  - `404 repo_not_found`：不存在，或私有仓库对无权限用户不可见（优先判断）
  - `400 repo_not_public`：私有仓库（创建者/管理员）
  - `400 cannot_star_own_repo`：自己的公开仓库
  - `401`
- 错误判定顺序：可见性 → 是否私有 → 是否自己的公开仓库。
- 说明：幂等；重复 star 不产生重复记录；`star_count` 返回操作后的公开仓库 star 总数（含已停用账号的 star）。

### 10.11 DELETE `/api/v1/repos/{repo_id}/star`

- 认证：是
- 权限：公开仓库、且不是自己的仓库
- 成功：`200 StarResult`，`starred=false`
- 错误：同 §10.10
- 说明：
  - 幂等；未 star 也返回成功。
  - 私有仓库上的取消 star 同样返回 400 `repo_not_public`；历史 star 记录保留，仓库转回公开后仍显示。

### 10.12 GET `/api/v1/repos/related`

- 认证：是
- 查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `session_id` | int | 是 | 会话 ID，必须是当前用户的会话 |
| `page` | int | 否 | 默认 1 |
| `page_size` | int | 否 | 默认 20，1–100 |

- 响应：`200 RelatedRepoPage`

`RelatedRepoItem`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 仓库 ID |
| `name` | string | 名称 |
| `description` | string | 简介 |
| `owner` | UserBrief | 创建者 |
| `paper_count` | int | 论文数，含归档（与 `RepoSummary.paper_count` 同口径） |
| `star_count` | int | Star 数 |
| `is_starred` | bool | 当前用户是否已 star |
| `reason` | string | 推荐理由，中文，非空，≤100 字 |

- 过滤：
  - 只返回公开仓库
  - 排除当前用户自己的仓库
  - 必须与本次查询词、会话论文的标题/摘要相关；相关性用仓库名、简介、仓库内论文标题与这些关键词的重合度计算
  - 若 `session.has_evidence=false`，返回空页（`total=0`），与产品空状态一致
- 排序：相关性降序、`star_count DESC`、`id ASC`；Star 只能加分，不能把无关仓库推上来。
- 相关性口径：查询词/会话论文关键词与仓库名、简介、仓库论文标题的重合度；Star 只做 `+0.2 * log(1 + star_count)` 的小幅加分，不能单独决定入选。
- `total`：满足条件的相关公开仓库总数。
- 错误：`404 session_not_found`、`401`、`422`
- 说明：
  - `reason` 必须解释相关性，不能只写「热门」；非空，≤100 字。
  - 实现时必须保证 `GET /repos/related` 不被 `GET /repos/{repo_id}` 吞掉（见 §3 路由注意）。

### 10.13 仓库对话与对话可见性

| 场景 | 可查看对话的人 |
| --- | --- |
| 公开仓库 | 只有发起对话的用户；创建者、管理员、其他用户都看不到 |
| 私有仓库 | 创建者和管理员可以查看全部对话；普通用户无权访问仓库 |
| 会话 | 只有会话所有者；管理员不可见 |
| 单篇论文 | 只有发起者；其他用户（含管理员）不可见 |

对应接口：

- `GET /repos/{repo_id}/messages`：返回**当前用户**在该仓库的对话消息。
- `GET /repos/{repo_id}/conversations`：
  - 公开仓库：只返回当前用户的对话（0 或 1 条）。
  - 私有仓库的创建者/管理员：返回该仓库全部用户的对话。
- `GET /repos/{repo_id}/conversations/{conversation_id}/messages`：
  - 公开仓库：只有该对话发起者。
  - 私有仓库：创建者、管理员、对话发起者。

错误语义：

- 仓库不存在，或当前用户无权访问仓库：404 `repo_not_found`。
- 对话不属于该仓库：404 `conversation_not_found`。
- 公开仓库中查看他人对话：
  - 管理员（非创建者、非发起者）：403 `forbidden`（§1.8、§12.2 的明确规则）。
  - 创建者或其他非发起者：404 `conversation_not_found`（避免泄露对话存在性）。
- 私有仓库：创建者/管理员可看全部；其他用户先因仓库不可见得到 404 `repo_not_found`。

可见性变更：

- 公开转私有：历史对话按私有仓库规则对创建者/管理员可见。
- 私有转公开：非发起者的历史对话对创建者/管理员隐藏。

### 10.14 GET `/api/v1/repos/{repo_id}/conversations`

- 认证：是
- 权限：同仓库详情
- 查询：`page`、`page_size`
- 响应：`200 ConversationPage`

`ConversationItem`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 对话 ID |
| `user` | UserBrief | 发起者 |
| `message_count` | int | 消息数 |
| `last_message_at` | string\|null | 最后消息时间 |
| `created_at` | string | 创建时间 |

- 排序：`last_message_at DESC, id DESC`；`last_message_at=null` 排最后。
- 说明：
  - `message_count` 统计该对话全部消息（含系统消息，如果存在）。
  - `last_message_at` 为最后一条消息时间；无消息时为 null。
  - 公开仓库只包含当前用户；私有仓库创建者/管理员包含全部用户。
- 错误：`404 repo_not_found`、`401`、`422`

### 10.15 GET `/api/v1/repos/{repo_id}/messages`

- 认证：是
- 权限：同仓库详情
- 查询：`limit`、`before_id`
- 响应：`200 MessagePage`（当前用户对话）
- 说明：
  - 没有对话时返回空列表，不创建对话。
  - 即使当前用户是私有仓库创建者/管理员，本接口也只返回自己的对话；查看全部对话用 §10.14 + §10.16。
- 错误：`404 repo_not_found`、`401`、`422`

### 10.16 GET `/api/v1/repos/{repo_id}/conversations/{conversation_id}/messages`

- 认证：是
- 权限：见 §10.13
- 查询：`limit`、`before_id`
- 响应：`200 MessagePage`
- 错误：
  - `404 repo_not_found`：仓库不存在，或当前用户无权访问该仓库
  - `404 conversation_not_found`：对话不属于该仓库，或非管理员（含创建者）在公开仓库查看他人对话（不泄露对话是否存在）
  - `403 forbidden`：管理员（非创建者）在公开仓库查看他人对话（§10.13、§12.2）
  - `401`、`422`
- 说明：对话发起者始终可查看；私有仓库创建者/管理员始终可查看全部。

---

## 11. 对话（SSE）

### 11.1 共享行为

三个对话接口共用 §1.11 的 SSE 契约：

| 接口 | scope | 检索范围 | Agent | 无证据/空仓库 |
| --- | --- | --- | --- | --- |
| `POST /sessions/{id}/chat/stream` | `session` | 会话保存的论文和证据 | 否（可调用会话内证据工具） | 409 `no_evidence` |
| `POST /repos/{id}/chat/stream` | `repo` | 当前仓库全部论文 | 是，只读，最多 5 步 | 409 `repo_empty` |
| `POST /papers/{id}/chat/stream` | `paper` | 当前论文 | 否（可调用单篇检索工具） | 409 `paper_not_ready` |

共同规则：

- 每个用户在每个范围有且只有一条对话：`(user_id, scope_type, scope_id)` 唯一。
- 对话范围只由 URL 决定（`/sessions/{id}`、`/repos/{id}`、`/papers/{id}`），请求体不接受 `scope`、`scope_id`、`repo_id`、`paper_id`；多传字段 422。
- `message` 为 1–2000 字符，去除首尾空白后不能为空；空白消息返回 422，不写任何消息。
- 会话对话在创建会话时已创建（§9.1）；仓库/论文对话在**首次流开始**时创建。流开始前失败不创建新对话。`meta.conversation_id` 返回。
- 用户消息在流开始时保存；助手消息在完成/停止/错误时保存。
- 所有事实性回答必须带引用；引用必须属于本次范围。
- 无足够证据时返回**固定拒答文案**：`根据当前资料，没有足够证据回答这个问题。`（`status=complete`、`citations=[]`），不是 `error` 事件，不编造。
- 模型不可用：流开始前 503 `llm_unavailable`；流开始后 `error` 事件。
- 模型调用失败：流开始前 502 `llm_failed`；流开始后 `error` 事件。
- 工具循环超限：流开始前 504 `tool_loop_exceeded`；流开始后 `error` 事件。
- 流开始前返回 JSON 错误时，不写用户消息和助手消息（§11.5）。
- 不显示命中论文列表（仓库页左侧已有列表；会话页有论文总结视图）。
- 不提供消息编辑、删除、重新生成。

#### 11.1.1 引用校验规则（服务端必须执行）

1. `citation.index` 从 1 开始、连续、不重复（1..N）；正文中的每个 `[n]` 必须落在 1..N 内。正文可以只使用部分引用，但不能出现未定义的编号。
2. `citation.paper_id` 必须属于本次范围：会话快照 / 当前仓库 / 当前论文。
3. `citation.chunk_id` 必须属于对应论文，且 `quote` 必须是该 chunk 的真实子串。
4. 越界、跨范围、找不到 quote 的引用必须丢弃，不能把无效引用发给前端；丢弃后把剩余引用重新连续编号为 1..N，并同步修正正文中的 `[n]`。
5. 回答中的事实性内容没有任何有效引用时，重试一次；仍失败则改为固定拒答文案。
6. 引用只用于支撑事实；建议、过渡句、拒答文案不需要引用。
7. 前端只使用 `citation.index` 关联正文 `[n]`，不自行生成引用。
8. 引用校验必须在发送任何 `token` 之前完成；已发送的 token 不得撤回。服务端可以缓冲完整回答、校验后再分块发送 `token`（前端不应假设逐字实时到达）。

#### 11.1.2 请求体 ChatRequest

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `message` | string | 是 | 1–2000 字符；去除首尾空白后非空 |

请求体只允许 `message`；多传字段返回 422。

### 11.2 POST `/api/v1/papers/{paper_id}/chat/stream`

- 认证：是
- 权限：所有登录用户，论文必须可见且可对话
- 请求：`{"message": "..."}`，1–2000
- 成功：`200 text/event-stream`
- 错误：
  - `404 paper_not_found`：不存在、处理中/失败（用户端不可见）
  - `409 paper_not_ready`：没有可检索片段
  - `401`、`422`
- 行为：
  - 只使用当前论文内容，不访问全局目录、其他论文、仓库。
  - 归档论文只要片段存在仍可对话。
  - 对话按 `(user_id, paper_id)` 唯一。
  - 不能从单篇对话加入仓库或触发全局搜索。
- SSE `meta.scope="paper"`，`scope_id=paper_id`。

### 11.3 POST `/api/v1/repos/{repo_id}/chat/stream`

- 认证：是
- 权限：公开仓库所有登录用户；私有仓库创建者/管理员；其他 404
- 请求：`{"message": "..."}`，1–2000
- 成功：`200 text/event-stream`
- 错误：
  - `404 repo_not_found`
  - `409 repo_empty`：仓库没有论文；或仓库有论文但全部没有可用片段（数据异常，防御性处理）
  - `401`、`422`
- 行为：
  - 范围是整个当前仓库，包含已归档但仍在仓库中的论文，不包含已移除论文。
  - Agent 可多步查找、比较、汇总，最多 5 步。
  - 只读：不能新增、修改、删除任何数据；不能访问全局目录、其他仓库、互联网、外部工具。
  - 回答带引用；无证据拒答。
  - 公开仓库对话只属于发起者；私有仓库对话创建者/管理员可见。
- SSE `meta.scope="repo"`，`scope_id=repo_id`。

### 11.4 GET `/api/v1/papers/{paper_id}/messages`

- 认证：是
- 权限：论文可见
- 查询：`limit`、`before_id`
- 响应：`200 MessagePage`（当前用户对话）
- 说明：没有对话时返回空列表。
- 错误：`404 paper_not_found`、`401`、`422`

### 11.5 对话消息持久化规则

| 事件 | 用户消息 | 助手消息 |
| --- | --- | --- |
| 流开始（通过全部前置校验） | 立即写入，`status=complete` | 尚未写入 |
| 流开始前返回 JSON 错误（400/401/403/404/409/413/415/422/429/502/503/504） | 不写入 | 不写入；不创建新对话 |
| 正常完成 | 保留 | 写入，`status=complete`，含引用 |
| 客户端断开 | 保留 | 写入已生成内容，`status=stopped` |
| 模型/工具错误 | 保留 | 写入已生成内容，`status=error` |
| 无证据/空仓库/论文无片段 | 不写入（请求 409） | 不写入 |

---

## 12. 管理后台：仓库

### 12.1 GET `/api/v1/admin/repos`

- 认证：admin
- 查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `q` | string | "" | 匹配仓库名、创建者邮箱/显示名，≤200 |
| `visibility` | RepoVisibility\|null | null | 精确过滤 |
| `page` | int | 1 | ≥1 |
| `page_size` | int | 20 | 1–100 |

- 响应：`200 AdminRepoPage`

`AdminRepoListItem`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 仓库 ID |
| `name` | string | 名称 |
| `visibility` | RepoVisibility | 公开/私有 |
| `owner` | UserBrief | 创建者 |
| `owner_email` | string | 创建者邮箱（仅后台返回，便于搜索与联系） |
| `paper_count` | int | 论文关系数，包含已归档论文 |
| `star_count` | int | Star 数；私有仓库固定 0 |
| `updated_at` | string | 更新时间 |

- 排序：`updated_at DESC, id DESC`
- 错误：`401`、`403`、`422`

### 12.2 管理私有仓库的操作复用

管理员对私有仓库的详情、编辑、删除、增删论文、发起对话、查看对话，复用以下接口：

- `GET /repos/{repo_id}`
- `PATCH /repos/{repo_id}`
- `DELETE /repos/{repo_id}`
- `GET /repos/{repo_id}/papers`
- `POST /repos/{repo_id}/papers`
- `DELETE /repos/{repo_id}/papers/{paper_id}`
- `POST /repos/{repo_id}/chat/stream`
- `GET /repos/{repo_id}/conversations`
- `GET /repos/{repo_id}/conversations/{conversation_id}/messages`

规则：

- 仓库必须是私有；管理员（非创建者）对公开仓库只读，编辑/删除/增删论文等管理操作返回 403；Star、发起对话仍按普通登录用户规则执行。
- 管理员删除私有仓库：允许。
- 管理员查看私有仓库全部对话：允许。
- 管理员可以对私有仓库发起对话；对话按 §10.13 可见性规则。
- 管理员（非创建者）不能查看公开仓库他人对话：`GET /repos/{id}/conversations` 只返回管理员自己的对话；指定他人对话返回 403；管理员若是创建者，按创建者规则返回 404。
- 管理员若同时是仓库创建者，按创建者权限处理。

---

## 13. 权限矩阵

> 「管理员（公开）」指管理员不是该仓库创建者；「管理员（私有）」指管理员管理他人私有仓库。管理员若是创建者，按「创建者」列处理。

| 操作 | 游客 | 普通用户（非创建者） | 创建者 | 管理员（公开） | 管理员（私有） |
| --- | :---: | :---: | :---: | :---: | :---: |
| 登录/退出/当前用户 | 登录 | ✅ | ✅ | ✅ | ✅ |
| 全局搜索、查看会话 | ❌ | ✅（自己的） | ✅ | ✅（自己的） | ✅（自己的） |
| 查看全局论文详情/下载/四维/单篇对话 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 创建仓库 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 查看公开仓库 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 编辑公开仓库 | ❌ | ❌ | ✅ | ❌ | ❌ |
| 查看私有仓库 | ❌ | ❌ | ✅ | ❌ | ✅ |
| 编辑/删私有仓库、增删论文 | ❌ | ❌ | ✅ | ❌ | ✅ |
| 删除公开仓库 | ❌ | ❌ | ✅ | ❌ | ❌ |
| Star 公开仓库 | ❌ | ✅ | ❌（自己的） | ✅ | —（私有不可 Star） |
| 公开仓库对话 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 私有仓库对话 | ❌ | ❌ | ✅ | ❌ | ✅ |
| 查看公开仓库他人对话 | ❌ | ❌ | ❌ | ❌ | ❌ |
| 查看私有仓库全部对话 | ❌ | ❌ | ✅ | ❌ | ✅ |
| 单篇论文对话 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 导入/维护论文目录 | ❌ | ❌ | ❌ | ✅ | ✅ |
| 管理用户、查看总览 | ❌ | ❌ | ❌ | ✅ | ✅ |
| 查看后台仓库列表 | ❌ | ❌ | ❌ | ✅ | ✅ |

> `—` 表示该「用户角色 / 仓库可见性」组合不适用；`❌` 表示可访问但无权执行。
> 管理员（非创建者）在公开仓库查看他人对话时返回 403 `forbidden`；其他非发起者（含创建者）返回 404 `conversation_not_found`。见 §10.13。
> 管理员（公开）与管理员（私有）两列只对“仓库相关操作”有区别；对全局/后台能力，二者都是 admin。

---

## 14. 需求追踪矩阵

| 用户故事 | 接口 | 说明 |
| --- | --- | --- |
| AUTH-01 登录 | `POST /auth/login` | 失败统一 401 |
| AUTH-02 退出与当前用户 | `POST /auth/logout`、`GET /auth/me` | 会话过期 401 |
| AUTH-03 角色与权限边界 | §13 + 所有接口权限规则 | 服务端判断 |
| AUTH-04 用户管理 | `/admin/users*` | 创建/改角色/停用/重置密码 |
| KB-01 管理员导入论文 | `POST /admin/papers/import` | 多文件、去重 |
| KB-02 解析、索引、失败重试 | `GET /admin/papers*`、`POST .../retry` | 状态机 |
| KB-03 论文目录维护 | `GET /admin/papers`、`PATCH /admin/papers/{id}`、`POST /admin/papers/{id}/archive` | 搜索、改元数据、归档 |
| SRCH-01 主页文字/PDF 查询 | `POST /search`、`POST /search/pdf` | PDF 不入库 |
| SRCH-02 全局 RAG 与会话创建 | `POST /search`、`GET /sessions/{id}` | 快照、空会话 |
| SRCH-03 会话工作区三视图 | `GET /sessions/{id}`、`GET /papers/{id}/summaries`、`GET /repos/related` | 对话/论文总结/相关仓库 |
| REPO-01 创建与设置仓库 | `POST/PATCH/DELETE /repos*` | 公开/私有、删除 |
| REPO-02 添加/移除论文 | `POST/DELETE /repos/{id}/papers*` | 只加就绪论文 |
| REPO-03 仓库页面与论文列表 | `GET /repos/{id}`、`GET /repos/{id}/papers` | 权限、列表 |
| REPO-04 仓库对话与只读 Agent | `POST /repos/{id}/chat/stream` | 多步、只读 |
| REPO-05 Star 与相关仓库 | `/repos/{id}/star`、`GET /repos/related` | 相关性优先 |
| VIEW-01 论文四维总结 | `GET/POST /papers/{id}/summaries*` | 论文级缓存 |
| VIEW-02 论文详情与下载 | `GET /papers/{id}`、`GET .../download` | 归档可读 |
| QA-01 会话内对话 | `POST /sessions/{id}/chat/stream` | 快照范围 |
| QA-02 单篇论文对话 | `POST /papers/{id}/chat/stream` | 单篇范围 |
| ADM-01 管理总览与仓库管理 | `GET /admin/stats`、`GET /admin/repos`、`GET /repos/{id}`、`PATCH/DELETE /repos/{id}`、`/repos/{id}/papers*`、`/repos/{id}/conversations*` | 含私有仓库管理 |

---

## 15. 错误码目录

| HTTP | code | 用户消息（示例） | 使用场景 |
| ---: | --- | --- | --- |
| 400 | `bad_request` | 请求不合法 | 空更新、无法执行的业务操作 |
| 400 | `cannot_demote_self` | 不能修改自己的管理员角色 | 管理员自降级 |
| 400 | `cannot_disable_self` | 不能停用自己 | 管理员自停用 |
| 400 | `last_admin` | 至少保留一个可用管理员 | 移除最后一个管理员 |
| 400 | `cannot_star_own_repo` | 不能 Star 自己的仓库 | Star 自己的公开仓库 |
| 400 | `repo_not_public` | 私有仓库不能 Star | 私有仓库 star |
| 401 | `unauthorized` | 请先登录 | 未登录/会话失效 |
| 401 | `invalid_credentials` | 邮箱或密码错误 | 登录失败（含停用账号） |
| 403 | `forbidden` | 无权访问 | 角色不足、编辑他人资源 |
| 404 | `not_found` | 资源不存在 | 未知路由、不存在的资源 |
| 404 | `user_not_found` | 用户不存在 | 管理员操作不存在用户 |
| 404 | `paper_not_found` | 论文不存在 | 论文不可见/不存在 |
| 404 | `file_not_found` | 原文文件不存在 | 无可下载文件 |
| 404 | `session_not_found` | 会话不存在 | 不存在或非本人 |
| 404 | `repo_not_found` | 仓库不存在 | 不存在或私有不可见 |
| 404 | `conversation_not_found` | 对话不存在 | 对话不属于该仓库；公开仓库中非发起者（含创建者）越权查看 |
| 404 | `summary_not_generated` | 该论文还没有生成此详略的四维总结 | 四维未缓存 |
| 405 | `method_not_allowed` | 请求方法不允许 | 方法错误 |
| 409 | `email_taken` | 该邮箱已注册 | 创建用户重复 |
| 409 | `repo_name_taken` | 仓库名称已存在 | 同创建者重名 |
| 409 | `invalid_state` | 当前状态不允许此操作 | 重试/归档状态错误、处理中修改元数据 |
| 409 | `paper_not_ready` | 论文还没有可用的原文内容 | 四维/对话依赖片段 |
| 409 | `repo_empty` | 仓库还没有可用的论文，不能对话 | 空仓库，或仓库论文均无可用片段 |
| 409 | `no_evidence` | 没有足够证据进行对话 | 空会话对话 |
| 413 | `file_too_large` | 文件超过大小限制 | 上传超限 |
| 415 | `unsupported_media_type` | 只支持 PDF 文件 | 非 PDF |
| 422 | `validation_error` | 请求参数不合法 | 字段校验失败 |
| 422 | `pdf_no_text` | 无法读取 PDF 文字，请改用文字查询 | PDF 无文本层 |
| 422 | `pdf_too_many_pages` | PDF 页数超过限制 | 超过 50 页 |
| 429 | `too_many_requests` | 请求过于频繁，请稍后重试 | 预留，V1.0 未启用 |
| 500 | `internal_error` | 服务暂时不可用，请稍后重试 | 未捕获异常 |
| 502 | `llm_failed` | AI 生成失败，请稍后重试 | 模型返回错误 |
| 503 | `database_unavailable` | 数据库暂不可用 | ready 检查失败 |
| 503 | `llm_unavailable` | AI 服务未配置或暂不可用 | 无 Key/上游不可用 |
| 503 | `search_unavailable` | 检索服务暂不可用 | 检索失败 |
| 504 | `llm_timeout` | AI 生成超时，请稍后重试 | 模型超时 |
| 504 | `tool_loop_exceeded` | AI 查询步骤过多 | 工具循环超限 |

> `user_disabled`（403）是 P001 的旧行为，V1.0 目标契约已移除；停用账号登录统一返回 401 `invalid_credentials`（见 §16）。
> `unknown_tool` 是 AI 内部工具错误，不直接暴露为 API 错误码；工具循环中由服务端转换为 `error` 事件或 `tool_loop_exceeded`。

---

## 16. 当前代码与目标契约的差异

> 这一节用于 PM 验收和 D1–D5 排任务；不是接口行为。

| # | 当前状态 | 目标契约 | 负责人 | 动作 |
| ---: | --- | --- | --- | --- |
| 1 | `POST /auth/register` 公开可用 | 不存在 | D1 | 删除接口、`RegisterRequest`、README 条目；改测试 |
| 2 | `users` 无 `role` | `role` 字段 | D1 | 迁移 `0002_user_roles` |
| 3 | `UserRead` 无 `role` | 返回 `role` | D1 | 改 schema |
| 4 | 无 `require_admin` | 所有 admin 接口依赖 | D1 | 加依赖并接入 |
| 5 | 无管理员初始化 | CLI 创建第一个 admin | D1 | `scripts/create_admin.py` |
| 6 | 无管理后台接口 | `/admin/users`、`/admin/stats` | D1 | 新增 |
| 7 | 停用用户登录返回 403 `user_disabled` | 401 `invalid_credentials` | D1 | 改登录逻辑与测试 |
| 8 | 时间序列化无 `Z` | RFC 3339 UTC `Z` | D1 | 统一 response schema 序列化 |
| 9 | 列表无分页 | `page/page_size` 或游标 | D1–D5 | 按模块实现 |
| 10 | 无论文/文件/片段表 | `papers/paper_files/paper_chunks` | D2 | 迁移 `0003_papers` |
| 11 | 无导入/解析 | `/admin/papers/*` | D2 | 实现 |
| 12 | 无会话/消息表 | `search_sessions/session_papers/session_evidence/conversations/messages` | D3 | 迁移 `0004_sessions_chat` |
| 13 | 无搜索/会话/对话 | `/search`、`/sessions/*` | D3 | 实现 |
| 14 | 无仓库表 | `repos/repo_papers/repo_stars` | D4 | 迁移 `0005_repos` |
| 15 | 无仓库/Star/Agent | `/repos/*` | D4 | 实现 |
| 16 | 无四维表 | `paper_summaries` | D5 | 迁移 `0006_paper_summaries` |
| 17 | 无四维接口 | `/papers/{id}/summaries*` | D5 | 实现 |
| 18 | 无前端 | 9 个页面 | D5 + 各模块 | 实现 |
| 19 | `RequestContextMiddleware` 用 `BaseHTTPMiddleware` | SSE 不得被缓冲 | D1 | 验证；必要时改纯 ASGI |
| 20 | `tests/conftest.py` 写死清空 users/user_sessions | 动态清空所有表 | D1 | 改为 `Base.metadata.sorted_tables` |
| 21 | `ready` 只检查 `SELECT 1` | 契约保持 | D1 | 文档写明，不扩展 |
| 22 | 无 AI 客户端/SSE/工具循环 | `app/core/ai/` | D1 | 实现 |
| 23 | 无 `openai` 依赖 | 增加 `openai` | D1 | `uv add openai` |
| 24 | 无 `.env` 模型配置 | `DEEPSEEK_*`、`LLM_*` | D1 | 更新 `.env.example` |
| 25 | 无 CSRF/限流 | 契约明确不提供 | PM | 写入验收风险 |
| 26 | 无评测脚本 | 课程可能要求 | PM/D3 | 10–20 题最小评测 |
| 27 | 无 Docker | 本期不做 | PM | 课程实验四/五再补 |
| 28 | `error_response` 在 `details=None` 时省略字段 | `details` 始终存在，值为 `null` | D1 | 修改错误响应构造 |
| 29 | `LoginRequest.email` 无 255 上限 | ≤255 | D1 | 增加 `max_length=255` |
| 30 | `COOKIE_SECURE` 不会随 `APP_ENV=production` 自动为 true | 生产必须 `true` | D1/PM | 启动校验或部署检查 |
| 31 | 团队分工文档写 `GET/POST /papers/{id}/summary`（单数） | 本契约统一 `/summaries`（复数） | PM/D5 | 更新任务书与前端调用 |
| 32 | 后台仓库列表无 `owner_email` | 返回 `owner_email` | D4 | 增加字段 |
| 33 | 无 `PAPER_PROCESSING_TIMEOUT_MINUTES` 与启动恢复 | 超时标记失败 | D2 | 增加配置与启动恢复 |
| 34 | 四维缓存无版本键 | `content_hash + prompt_version + model` | D5 | 实现缓存键 |
| 35 | Pydantic 默认忽略未知字段 | 请求体未知字段返回 422 | D1 | 请求模型设 `extra="forbid"` |

### 16.1 迁移顺序

| 迁移 | 负责人 | 内容 | 前置 |
| --- | --- | --- | --- |
| `0002_user_roles` | D1 | `users.role` | `0001` |
| `0003_papers` | D2 | 论文、文件、片段、FULLTEXT | `0002` |
| `0004_sessions_chat` | D3 | 会话、证据、对话、消息 | `0003` |
| `0005_repos` | D4 | 仓库、仓库论文、star | `0003` |
| `0006_paper_summaries` | D5 | 四维缓存 | `0003` |

### 16.2 与其他文档的差异（以本契约为准）

| 文档 | 差异 | 处理 |
| --- | --- | --- |
| `团队分工与一周冲刺计划` | D5 API 写 `GET/POST /papers/{id}/summary`（单数） | 本契约统一 `/summaries`（复数），更新任务书与前端调用 |
| `团队分工与一周冲刺计划` | `AdminStats` 示例只列了部分字段 | 以 §6.5 全字段为准，表落地后补齐 |
| `D1开发指南-账户权限与AI基础设施` | SSE 示例是简化版，事件数据与本文档不完全一致 | 以 §1.11、§11 为准；D1 实现时对齐 |
| `设计评审-可行性与Agent评估` | 建议本周延后“管理员查看私有仓库对话” | 本契约保留该能力；若本周降级，PM 在验收表标注“未覆盖”，不改变契约 |
| `设计评审-可行性与Agent评估` | 建议“工具阶段不流式，最终回答再流式” | 属于实现建议，不改变 SSE 对外事件契约 |

---

## 17. 前端接入示例

### 17.1 登录

```js
const res = await fetch("/api/v1/auth/login", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});
const body = await res.json();
if (!res.ok) throw new Error(body.error.message);
```

### 17.2 SSE（POST + Cookie + 停止）

```js
const controller = new AbortController();
const res = await fetch(`/api/v1/sessions/${id}/chat/stream`, {
  method: "POST",
  credentials: "include",
  headers: {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  },
  body: JSON.stringify({ message }),
  signal: controller.signal,
});
if (!res.ok) {
  const body = await res.json();
  throw new Error(body.error.message);
}
if (!res.body) throw new Error("浏览器不支持流式响应");

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  let index;
  while ((index = buffer.indexOf("\n\n")) >= 0) {
    const frame = buffer.slice(0, index);
    buffer = buffer.slice(index + 2);
    if (frame.startsWith(":")) continue; // 心跳

    let event = "message";
    const dataLines = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    const data = JSON.parse(dataLines.join("\n"));
    // meta/token/citation/done/error
  }
}
```

### 17.3 错误处理

```js
if (!res.ok) {
  const body = await res.json().catch(() => null);
  const message = body?.error?.message ?? "请求失败，请稍后重试";
  // 只展示 message，不展示 code/堆栈
}
```

---

## 18. 明确不纳入 V1.0

- 用户自注册、找回密码、修改自己的密码（仅管理员重置）
- 用户上传论文、仓库上传论文、PDF 入库
- Word/图片/扫描件/OCR
- 年份、期刊级别、论文类型筛选
- 自定义排序、检索档位、每轮证据数设置
- 仓库成员邀请、多人协作、评论、分享链接
- 公开仓库审核、举报、运营后台
- 论文图谱、引用关系图、研究演化图
- 自动评测 UI、指标看板（只允许脚本）
- SSO、LDAP、TOTP、多因素认证
- 审计日志、导出、移动端
- 部署、容器化、监控、压测
- 会话删除、消息编辑/删除/重新生成
- 用户删除、论文物理删除、取消归档
- 管理员编辑公开仓库、查看公开仓库他人对话
- API Key、公开 API、多租户
- CSRF token、限流（V1.0 明确缺失，公网部署前必须补）
- 幂等键、ETag/If-Match、Webhook

---

## 19. 变更记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1.0 | 2026-10-26 | 初版：覆盖 V1.0 P0 全部接口、SSE、权限、错误码、需求追踪、当前代码差异 |
| 1.1 | 2026-10-26 | 勘误与补全：① `error.details` 始终返回、无值为 null；② 补齐 SSE 流前 502/504 与流前错误不落库；③ 修正 `PaperStatus` 重试方向；④ 增加 `RepoScope`、`RepoPaperAddReason` 枚举与模型索引；⑤ 补齐仓库权限真值表、公开仓库他人对话 403/404 规则、可见性变更影响；⑥ 补齐 PDF 查询 2000 字符、导入请求级校验顺序与拒绝项字段、论文初始标题；⑦ 补齐分页 `has_more`、排序 null、空值/清空语义；⑧ 增加 `owner_email`、缓存键、处理超时、四维 404/409 优先级；⑨ 增加与其他文档的差异表与验收项；⑩ 补齐 PDF 查询缺文件 422、消息空列表与资源 404 的边界、私有仓库写操作 404 优先级、Star 权限矩阵与验收口径、未知请求字段 422 |

---

## 20. 验收方式（给 PM）

1. 打开 `/openapi.json`，逐项核对本文档 §3 的路径、方法、请求/响应模型是否都存在。
2. 每个接口至少测试：正常、未登录 401、无权限 403/404、参数错误 422、资源不存在 404。
3. 重点回归：
   - 普通用户访问 `/admin/*` → 403；
   - 他人私有仓库 → 404，且响应不含名称；
   - 管理员（非创建者）编辑公开仓库 → 403；管理员若是创建者按创建者处理；
   - 公开仓库他人对话：管理员（非创建者）→ 403，其他非发起者（含创建者）→ 404；
   - 私有仓库创建者/管理员可见全部对话；
   - `POST /search` 无结果 → 201 空会话，`related_repo_count=0`；
   - `POST /sessions/{id}/chat/stream` 无证据 → 409，且不写任何消息；
   - SSE 事件顺序与 `done`；流开始前错误返回 JSON 且不落库；
   - 引用编号连续且与 `citation.index` 一致；
   - 归档论文：新搜索不可见、旧会话可打开、文件可下载、缓存四维可读；
   - 停用/重置密码后旧 Cookie 失效。
4. 边界回归：
   - 任意错误响应（非校验错误）的 `error.details` 必须是 `null`，不能缺少字段；
   - `PATCH /admin/users/{id}` 空对象 → 400；`display_name:null` 清空；最后可用管理员保护；
   - `PATCH /admin/papers/{id}` 处理中 → 409；`year/venue/abstract:null` 清空；`authors:[]` 清空；
   - 导入：文件数 0/>20 → 422；总大小 >100MB → 413；被拒绝项五个字段固定；`created+duplicates+rejected=items.length`；
   - `GET /repos/related` 不被 `/repos/{repo_id}` 吞掉；空会话返回空页；
   - 自己的公开仓库 `is_starred=false`；创建者/管理员对私有仓库 star → 400，无权限用户 → 404；管理员（非创建者）公开仓库他人对话 → 403；
   - `POST /repos/{id}/papers` 的 `ignored.reason` 顺序与请求顺序；
   - 四维：论文不可见 → 404 `paper_not_found`；可见但无缓存 → 404 `summary_not_generated`；
   - 后台仓库列表包含 `owner_email`。
5. 任何实现与本文档不一致，先改文档或改代码，不允许两边各说各话。
