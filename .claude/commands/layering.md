---
description: Pay down layering debt by moving one legacy API module's DB access into application + repository layers
argument-hint: "[pipeline|workflows|queue|batch|timeline] (default: the worst offender per scripts/check_layering.py --report)"
allowed-tools: Bash(python3 scripts/check_layering.py:*), Bash(scripts/test.sh:*), Bash(scripts/lint.sh:*), Read, Grep, Edit, Write, Task
---
Module to refactor: `$ARGUMENTS` (if empty run `python3 scripts/check_layering.py --report` and take the top file).

Refactor with the **backend-dev** sub-agent, **behavior-preserving**:
1. Run the full orchestrator tests first and record the baseline result. If already red, stop and tell me.
2. Group the module's DB calls into use cases. Create `application/<name>_service.py` (raise `ApplicationError`), move queries/commits into repository methods, inject via `api/dependencies.py`, and reduce the route functions to HTTP translation.
3. Do it in small steps, running the module's tests after each step. Do not change response shapes or status codes.
4. Finish with: tests green, `python3 scripts/check_layering.py --update-baseline`, and a before/after count of direct DB calls.
