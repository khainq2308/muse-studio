---
description: Pre-PR checklist - tests, lint, layering, docs, review, then commit on a feature branch (never push to main)
argument-hint: "[commit message]"
allowed-tools: Bash(scripts/test.sh:*), Bash(scripts/lint.sh:*), Bash(python3 scripts/check_layering.py:*), Bash(python3 scripts/check_docs_sync.py:*), Bash(git status:*), Bash(git diff:*), Bash(git branch:*), Bash(git checkout -b:*), Bash(git add:*), Bash(git commit:*), Read, Grep, Task
---
Prepare the current work for a pull request.

1. `git branch --show-current`: if on main/master, create `feat/<short-slug>` first.
2. Run `scripts/test.sh all`, `scripts/lint.sh`, `python3 scripts/check_layering.py`. Any failure stops the process — fix or report.
3. Run `python3 scripts/check_docs_sync.py`; if this change alters behavior or milestone status, update the docs (docs-sync agent).
4. Run the **code-reviewer** sub-agent on the diff; fix every Blocker and Major finding.
5. Stage only intended files (check `git status` for stray files such as `.db`, `storage/`, `.env`), commit with message `$ARGUMENTS` (or a conventional-commit message you write from the diff).
6. Do NOT push. Print the branch name, the commit, and the exact `git push -u origin <branch>` command for me to run, plus a PR description (summary, test evidence, risks).
