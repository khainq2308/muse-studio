---
name: add-api-endpoint
description: Step-by-step recipe for adding or changing a FastAPI endpoint in apps/orchestrator with the correct api -> application -> infrastructure layering, DI wiring, error handling and tests. Use whenever the user wants a new route, a new use case, a new query, or to extend an existing resource, even if they only say "add an API for X" or "expose Y over HTTP".
---

# Add an API endpoint (MuseStudio orchestrator)

The reference vertical slice is **projects**: `api/projects.py` → `application/services.py::ProjectService` → `infrastructure/repositories.py::StudioRepository`. Copy its shape.

## Procedure
1. **Locate the domain model** in `models/domain.py` (read the columns and enums; do not guess names). If a column/table is missing, stop and use the `alembic-migrations` skill first.
2. **Repository** (`infrastructure/repositories.py`): add an async method holding the query and the `commit()` boundary. Return ORM objects or tuples, never HTTP types. One transaction per use case.
3. **Application service** (`application/`): add a method on the relevant service (or a new `<name>_service.py` class taking the repository). Validate business rules and raise `ApplicationError(status_code, detail)`. Write audit events for state-changing admin actions (`AuditEvent`, see `ProjectService.create_project`).
4. **Dependency wiring** (`api/dependencies.py`): add `get_<name>_service(repository=Depends(get_studio_repository))`.
5. **Route** (`api/<resource>.py`): Pydantic request/response models with bounds (`Field(min_length=..., max_length=..., pattern=...)`), `response_model`, explicit `status_code` for creates (201) and deletes (204). The function body only calls the service and maps to the DTO. See `references/endpoint-template.md`.
6. **Registration**: new router file → include it in `main.py` with `dependencies=_protected_dependencies`. Never register a product route without `verify_single_admin`.
7. **Tests** (`orchestrator-testing` skill): success, validation 422, not found 404, 401 with a token configured, and one abuse case (oversized/odd input, wrong project id, path chars).
8. **Verify**: `scripts/test.sh orchestrator -k <name>`, then full suite; `scripts/lint.sh`; `python3 scripts/check_layering.py`.

## Conventions
- URL prefix `/api/v1/<resource>`; ids are strings like `proj_xxxxxxxxxx` (`uuid4().hex[:10]` with a prefix).
- Errors: raise `ApplicationError`; do not return ad-hoc `{"error": ...}`. Provider failures raise `ProviderFailure` (handled in `main.py` → 502 with `category`/`retryable`).
- Responses must never contain secrets; pass any free text from providers through `SecretRedactor.redact_text`.
- Pagination for list endpoints that can grow (queue tasks, logs, audit): `limit` (1–200, default 50) and `offset`/cursor.
- Long operations do not run inside the request: enqueue a task and return its id (see `api/queue.py`).
- Do not add DB calls to legacy route files; if you must modify them, move the touched code down a layer.

## Checklist before saying "done"
- [ ] No `select(`/`db.` in the route.  - [ ] Router protected.  - [ ] Bounds on every input.
- [ ] Tests added and actually run.  - [ ] Layering check passes.  - [ ] Docs updated if behavior is user-visible.
