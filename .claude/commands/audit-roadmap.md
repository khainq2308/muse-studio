---
description: Compare docs/roadmap claims against the actual code and report (or fix) the drift
argument-hint: "[--fix to update the docs]"
allowed-tools: Bash(python3 scripts/check_docs_sync.py:*), Bash(python3 scripts/check_layering.py:*), Read, Grep, Glob, Task
---
Run `python3 scripts/check_docs_sync.py` and `python3 scripts/check_layering.py --report`.

Produce a table: milestone/claim → status (done / backend only / not started) → evidence (file or test) → what is needed to be truly done.
If `$ARGUMENTS` contains `--fix`, delegate to the **docs-sync** agent to correct the documents; otherwise change nothing.
