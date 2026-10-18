# MuseStudio — Gemini CLI entrypoint

Before acting, read and follow **`AGENTS.md`** and **`docs/AGENT_WORKFLOW.md`**. They are the canonical repository-wide engineering contract and execution workflow. Do not invent a separate set of coding rules in this file.

## Gemini CLI adapter notes

- Shared Agent Skills are in `.agents/skills/`. Gemini CLI discovers workspace skills there when the repository is trusted; use `/skills list` to confirm discovery and `/skills reload` after adding skills.
- Run repository scripts from the repo root. Do not assume a Claude slash command such as `/close-gap` exists in Gemini CLI; use the matching shared skill, read `docs/AGENT_WORKFLOW.md`, and run the canonical `scripts/*.sh` / `scripts/*.py` command.
- When native subagents are enabled, delegate using the specialist roles and task boundaries in `docs/AGENT_WORKFLOW.md`. Do not let agents edit the same files concurrently. If a native subagent is unavailable, work sequentially.
- Hook events and output schemas differ by host. Treat `.claude/settings.json` and `.claude/hooks/` as Claude-only adapters, not Gemini configuration. Any Gemini hook must explicitly translate Gemini's event payload and block/continue result while reusing the same repository scripts and policies.
- Before claiming the workspace skills or hooks are active, verify them in the running CLI; repository files alone do not prove the host loaded them.
- Report in Vietnamese, with real test evidence. Be explicit when browser-visible behavior, provider capability, or external services were not verified.
