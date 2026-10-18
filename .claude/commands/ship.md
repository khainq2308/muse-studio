---
description: Pre-PR gate for tests, lint, architecture, docs, agent kit and read-only review
argument-hint: "[commit message]"
allowed-tools: Bash(scripts/test.sh:*), Bash(scripts/lint.sh:*), Bash(python3 scripts/check_layering.py:*), Bash(python3 scripts/check_docs_sync.py:*), Bash(python3 scripts/audit_roadmap.py:*), Bash(python3 scripts/check_agent_assets.py:*), Bash(python3 -m unittest:*), Bash(git status:*), Bash(git diff:*), Bash(git branch:*), Bash(git checkout -b:*), Bash(git add:*), Bash(git commit:*), Read, Grep, Task
---
Prepare the current work for a pull request.

1. Inspect branch, git status --short and the diff. If on main/master, create a focused branch; preserve uncommitted user changes.
2. Run scripts/test.sh all, scripts/lint.sh, python3 scripts/check_layering.py, python3 scripts/check_docs_sync.py, python3 scripts/audit_roadmap.py, python3 scripts/check_agent_assets.py and python3 -m unittest discover -s scripts/tests -p 'test_*.py'. A failed required check blocks shipping; a check that cannot run must be reported as NOT RUN.
3. Inspect roadmap signals and update docs only from implementation/test evidence.
4. Run code-reviewer when the host supports subagents; fix confirmed Blocker/Major findings and rerun relevant tests.
5. Stage only intended files. Check secrets, database/storage artifacts and lockfile changes. Commit with the supplied or a Conventional Commit message.
6. Do not push, merge, deploy or migrate production. Report branch, commit, summary, test evidence and remaining risks.
