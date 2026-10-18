---
description: Add an API endpoint end-to-end (schema, route, application service, repository, tests) with correct layering
argument-hint: "<description, e.g. 'GET /api/v1/projects/{id}/shots filtered by approval state'>"
---
Implement this endpoint: `$ARGUMENTS`

Use the **backend-dev** sub-agent and the `add-api-endpoint` skill. Steps:
1. Inspect existing models/schemas to confirm names and states; ask me only if the requirement is genuinely ambiguous.
2. Add repository methods, application-service use case, Pydantic schemas, and the thin route; register dependencies in `api/dependencies.py`. No DB calls in the route.
3. Write tests (happy path, validation error, 404, auth 401 when `STUDIO_ADMIN_TOKEN` is set, and one negative/abuse case).
4. Run `scripts/test.sh orchestrator`, `scripts/lint.sh`, `python3 scripts/check_layering.py`.
5. Summarize: files changed, route contract (method, path, request, response, errors), and real test output.
