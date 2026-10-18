---
description: Review the current changes (or given paths) with code-reviewer and security-auditor in parallel
argument-hint: "[paths or branch range, default: current diff]"
allowed-tools: Bash(git diff:*), Bash(git log:*), Bash(git status:*), Read, Grep, Glob, Task
---
Review target: `$ARGUMENTS` (if empty, use the uncommitted diff, else `git diff main...HEAD`).

1. Collect the changed files with `git status --short` and `git diff --stat`.
2. Launch two sub-agents in parallel on that scope: **code-reviewer** (general review) and **security-auditor** (only if the scope touches auth, secrets, providers, uploads, backups, FFmpeg or deployment files — otherwise skip it and say so).
3. Merge their findings into one report ordered Blocker → Major → Minor, de-duplicated, each with `path:line` and a concrete fix. Do not edit any files.
4. End with a verdict and a short list of the exact commands needed to verify the fixes.
