---
description: Reproduce CI jobs locally and fix failures without weakening checks
allowed-tools: Bash(scripts/test.sh:*), Bash(scripts/lint.sh:*), Bash(scripts/doctor.sh:*), Bash(python3 scripts/check_agent_assets.py:*), Bash(python3 scripts/check_docs_sync.py:*), Bash(python3 -m unittest:*), Read, Grep, Edit
---
Mirror .github/workflows/ci.yml locally:
1. python3 scripts/check_agent_assets.py
2. python3 scripts/check_docs_sync.py
3. python3 -m unittest discover -s scripts/tests -p 'test_*.py'
4. scripts/test.sh orchestrator (CI sets REQUIRE_FFMPEG=1)
5. scripts/test.sh media
6. scripts/test.sh web
7. scripts/lint.sh

For each failure, find the root cause and fix it without weakening checks. Re-run the failing check and the full affected suite. Missing dependencies/binaries mean NOT RUN or blocked, never PASS. Report each job's final status.
