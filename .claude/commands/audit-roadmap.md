---
description: Audit implementation-gap signals and compare them with milestone documentation
argument-hint: "[--fix to update docs]"
allowed-tools: Bash(python3 scripts/audit_roadmap.py:*), Bash(python3 scripts/check_docs_sync.py:*), Read, Grep, Glob, Agent, Task
---
Run python3 scripts/audit_roadmap.py and python3 scripts/check_docs_sync.py.

Produce a table: milestone/claim → status (complete / partial / UI missing / simulated / unverified / not started) → code and test evidence → what is required for acceptance.

The roadmap script is heuristic. Inspect implementation and tests before declaring a gap closed. If the argument contains --fix, delegate to docs-sync to make evidence-based status corrections; otherwise change nothing.
