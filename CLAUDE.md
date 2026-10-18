# MuseStudio — Claude Code entrypoint

The canonical repository instructions and workflow are platform-neutral:

1. Read `AGENTS.md`.
2. Follow `docs/AGENT_WORKFLOW.md`.
3. Load the relevant shared playbook from `.agents/skills/` (the matching Claude skill mirror lives in `.claude/skills/`).

Do not duplicate architecture/security rules here. Keep this file limited to Claude Code adapter notes.

## Claude-specific capabilities

- Native subagent definitions: `.claude/agents/`.
- Native slash-command adapters: `.claude/commands/`. These commands should delegate to the shared workflow or call canonical `scripts/` tools; equivalent procedures are documented for Codex, Gemini CLI and other hosts in `docs/AGENT_WORKFLOW.md`.
- Hook registration: `.claude/settings.json`; hook event parsing / block responses in `.claude/hooks/`. These are Claude-specific envelopes. Reuse portable scripts for policy and validation instead of assuming another host can run the same hook files unchanged.
- Local permission overrides belong in ignored `.claude/settings.local.json`; never commit secrets or personal permissions.
- Run scripts from the repository root. Do not claim hooks or agents are active unless verified in the current Claude Code session.

All user-facing replies remain in Vietnamese. Code, identifiers and commit messages remain in English.