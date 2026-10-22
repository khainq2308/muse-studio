---
description: Reproduce CI jobs locally and fix failures without weakening checks
argument-hint: "[job name, default: all jobs]"
allowed-tools: Bash(scripts/test.sh:*), Bash(scripts/lint.sh:*), Bash(scripts/doctor.sh:*), Bash(bash scripts/check_openapi.sh:*), Bash(python3 scripts/check_agent_assets.py:*), Bash(python3 scripts/check_docs_sync.py:*), Bash(python3 scripts/audit_openapi_responses.py:*), Bash(python3 scripts/audit_api_response_models.py:*), Bash(python3 -m unittest:*), Bash(npm run lint:*), Bash(npm run format:check:*), Bash(npm test:*), Bash(npm run build:*), Bash(npm run test:e2e:*), Bash(docker compose -f docker-compose.prod.yml config:*), Bash(git status:*), Bash(git diff:*), Read, Grep, Glob, Edit, Write
---
Read .github/workflows/ci.yml first; it is the source of truth and may have changed. Mirror each job locally (default: all jobs, or the one named in `$ARGUMENTS`):

| CI job | Local command(s) |
|---|---|
| Agent kit | python3 scripts/check_agent_assets.py; python3 scripts/check_docs_sync.py; scripts/lint.sh --python-only; python3 -m unittest discover -s scripts/tests -p 'test_*.py'; docker compose -f docker-compose.prod.yml config --quiet |
| Python tests | REQUIRE_FFMPEG=1 scripts/test.sh orchestrator |
| PostgreSQL integration / muse2api contract | need a PostgreSQL service or the muse2api mock gateway; run only if available, otherwise NOT RUN |
| OpenAPI + frontend types | bash scripts/check_openapi.sh; python3 scripts/audit_openapi_responses.py --strict; the `audit_api_response_models.py --report --require-typed ...` line exactly as in ci.yml |
| Media engine | scripts/test.sh media |
| Studio web | in apps/studio-web: npm run lint; npm run format:check; npm test; npm run build; npm run test:e2e (needs Playwright browsers) |

Production image build, nginx smoke test and dependency audits need Docker/network; report them as NOT RUN unless they actually ran.

For each failure, find the root cause and fix it without weakening checks. Re-run the failing check and the full affected suite. Missing dependencies, services or binaries mean NOT RUN or blocked, never PASS. Report each job's final status.
