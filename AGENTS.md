# PaperLens Project Context

This file is the project contract for coding agents working in this repository.
Read it before changing code.

## Read first

1. The newest handoff under `doc/` (currently
   `doc/开发交接-当前状态与后续任务（2026-10-28）.md`)
2. `doc/API接口文档（V1.0）.md` and `doc/paperlens-openapi.json`
3. Relevant tests under `tests/`

## Definition of Done

Before claiming work is complete, run and report the output:

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run alembic check
```

- Full suite: 364 tests, about 12 minutes on the current machine.
- Targeted tests are fine during development; the full suite is required before completion.
- Do not run two pytest processes at once; they share the `paperlens_test` database.
- Tests force `EMBEDDING_PROVIDER=none` (see `tests/conftest.py`): no local model, no network.
  Vector paths are covered with `tests/fakes.py::FakeEmbeddingProvider`.
- For migration changes, also verify:
  `APP_ENV=test uv run alembic downgrade -1 && APP_ENV=test uv run alembic upgrade head`.
- Do not claim completion if any command fails.

## Tests

- Use `tests/fakes.py::FakeLLMClient`; never call real DeepSeek or any external LLM/API in tests.
- Do not add network-dependent tests.
- Add or update tests for every behavior change.
- Do not weaken or delete existing tests to make a change pass.

## API contract and data

- The API contract is authoritative. Do not change `doc/API接口文档（V1.0）.md`,
  `doc/paperlens-openapi.json`, or route shapes without explicit approval.
- `related_repo_count`、`available_summary_levels`、`stats.repos_*` 曾是可接受的占位（0 / `[]`），
  现已在 D4/D5 接回真实值：不要把它们改回常量，修改相关逻辑时要保持真实计数并补测试。
- Do not reintroduce placeholder constants; keep these values computed.
- Current Alembic head: `0007_paper_summaries`; verify with `uv run alembic heads`.
  Chain: `0001_auth_tables → 0002_user_roles → 0003_papers → 0004_sessions_chat →
  0005_repos → 0006_chunk_embeddings → 0007_paper_summaries`.
- Never edit an applied migration. Create a new revision, autogenerate, then
  inspect the generated SQL/ops manually.
- Run `uv run alembic check` after model or migration changes.

## Workflow

- For multi-step work, produce a plan first and wait for approval.
- Implement one vertical slice at a time; do not implement a whole D4/D5 batch
  in one pass.
- Read the handoff's §9 "已知陷阱" before implementing D4 endpoints.
- Keep router ordering and batch-add edge cases consistent with the handoff and
  contract; do not reorder routes casually.
- Use the existing stack and patterns; do not add frameworks or dependencies
  without explicit approval.

## Git and files

- Do not commit or push unless explicitly asked.
- Local `develop` may be ahead of `origin/develop`; never push without explicit
  approval.
- Never commit `.env`, `var/`, `.vscode/`, PDF originals, or plan files.
- Do not modify `.cline/` or other tool configuration as part of a coding task.
- If asked to commit, use single-line messages: `feat(D4): ...`, `fix(D4): ...`,
  or `docs: ...`.

## Documentation

- Do not edit `doc/用户故事文档（V1.0）.md` or
  `doc/产品功能设计文档（V1.0）.md` without explicit approval.
- Run the doc guard before and after editing them:
  `.cline/skills/paperlens-doc-guard/scripts/check_docs.py`.
  If it is absent, do not claim the doc guard passed; ask first.
