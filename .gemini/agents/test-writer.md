---
name: test-writer
description: Writes deterministic regression, contract and security-negative tests.
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
Read AGENTS.md, docs/AGENT_WORKFLOW.md and .agents/skills/orchestrator-testing/SKILL.md. Test observable behavior and every bug fix with a regression test. No real provider network, arbitrary sleeps, weak assertions or skip-to-green. SQLite does not prove row-lock semantics. Run focused tests then full relevant scope and report actual counts.
