---
description: Review the current changes with a read-only code reviewer and, for sensitive scope, a security auditor
argument-hint: "[paths or branch range, default: current diff]"
allowed-tools: Bash(git diff:*), Bash(git log:*), Bash(git status:*), Read, Grep, Glob, Task
---
Review target: supplied paths or branch range (default current diff).

1. Collect git status --short, git diff --stat and the exact relevant patch with git diff (or git diff main...HEAD). Read the adjacent implementation and tests.
2. Delegate a bounded, read-only review to code-reviewer. Supply the changed path list, relevant patch excerpts and command results so the reviewer does not need shell access.
3. Launch security-auditor in parallel only if scope touches auth, secrets, providers, uploads, backups, FFmpeg or deployment files; otherwise skip it and say so.
4. Merge findings, de-duplicate and order Blocker → Major → Minor. Every finding includes path/line, evidence, impact and concrete fix.
5. Do not edit files during review. End with a verdict and list any checks not run.
