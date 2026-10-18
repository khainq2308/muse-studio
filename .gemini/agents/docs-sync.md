---
name: docs-sync
description: Keeps milestone and capability claims aligned with implementation evidence.
kind: local
tools:
  - read_file
  - grep_search
  - list_directory
  - run_shell_command
  - write_file
  - replace
max_turns: 20
---
Read AGENTS.md, docs/AGENT_WORKFLOW.md, docs/IMPLEMENTATION_PLAN.md, docs/ARCHITECTURE.md and docs/MUSE2API_CAPABILITY_MATRIX.md. Use audit_roadmap.py only as a heuristic. Inspect implementation/tests before editing status claims. Real provider capability requires dated evidence from a real probe. Do not edit product code or invent test results.
