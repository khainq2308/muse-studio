---
description: Run the test suites (all, or a scope) and fix failures
argument-hint: "[orchestrator|media|web|all] [pytest args]"
allowed-tools: Bash(scripts/test.sh:*), Bash(scripts/doctor.sh:*), Read, Grep, Edit
---
Run the tests for scope: `$ARGUMENTS` (default: all).

1. Run `scripts/test.sh $ARGUMENTS`. If tools are missing, run `scripts/doctor.sh` and tell me exactly what is missing instead of guessing.
2. If anything fails: read the failing test and the code under test, find the root cause (do not weaken assertions or skip tests to make them pass), fix the code, and re-run only the failing tests, then the whole scope.
3. Report: command run, pass/fail counts as printed, and any failure you did NOT fix with the reason.
