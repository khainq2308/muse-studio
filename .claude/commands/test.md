---
description: Run test suites for a selected scope and repair failures
argument-hint: "[orchestrator|media|web|all] [pytest args]"
allowed-tools: Bash(scripts/test.sh:*), Bash(scripts/doctor.sh:*), Read, Grep, Glob, Edit, Write
---
Run scripts/test.sh with the requested scope (default: all). Pytest arguments apply only to one selected pytest scope; all intentionally runs every suite without filters.

If tools are missing, run scripts/doctor.sh and state what is unavailable. For failures, inspect the failing test and code, fix the root cause, then rerun the failing test and the full affected scope. Do not weaken assertions or skip tests to get green. Report actual commands, counts, and checks not run.
