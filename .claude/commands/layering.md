---
description: Pay down layering debt by moving one legacy API module's DB access into application and repository layers
argument-hint: "[pipeline|workflows|queue|batch|timeline]"
allowed-tools: Bash(python3 scripts/check_layering.py:*), Bash(scripts/test.sh:*), Bash(scripts/lint.sh:*), Read, Grep, Edit, Write, Task
---
Choose the named module, or run python3 scripts/check_layering.py --report and select the largest count.

1. Run orchestrator tests first and record the actual baseline. If already failing, stop and report.
2. Move touched use cases into application services and queries/commits into repositories. Wire dependencies in api/dependencies.py; keep route handlers HTTP-only.
3. Refactor in small steps and run related tests after each.
4. Run scripts/test.sh orchestrator, scripts/lint.sh and python3 scripts/check_layering.py.
5. Use --update-baseline only after proving total debt decreased and no file regressed. The checker rejects baseline updates that do not reduce the count. Review the resulting baseline diff; never use it to silence regressions.
