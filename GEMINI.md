# MuseStudio — Gemini CLI entrypoint

Before acting, read and follow **`AGENTS.md`** and **`docs/AGENT_WORKFLOW.md`**. They are the canonical repository-wide engineering contract and execution workflow. Do not invent a separate set of coding rules in this file.

## Gemini CLI adapter notes

- Shared Agent Skills are in `.agents/skills/`. Verify discovery with `/skills list`; use `/skills reload` after adding skills.
- Native project subagents live in `.gemini/agents/*.md`. Verify them with `/agents list` and reload with `/agents reload` after editing profiles. Reviewer and security-auditor are configured with read-only tools; keep them read-only during delegation.
- Run repository scripts from the repo root. Do not assume a Claude slash command such as `/close-gap` exists in Gemini CLI; use the matching shared skill, read `docs/AGENT_WORKFLOW.md`, and run the canonical `scripts/*.sh` / `scripts/*.py` command.
- When native subagents are enabled, delegate using the specialist roles and task boundaries in `docs/AGENT_WORKFLOW.md`. Do not let agents edit the same files concurrently. If a native subagent is unavailable, work sequentially.
- `.gemini/settings.json` registers a Gemini-native `BeforeTool` hook adapter at `.gemini/hooks/before_tool.py`. It adapts shell, `read_file`, `read_many_files`, `grep_search`, `glob`, `write_file` and `replace` events to the shared policy in `scripts/agent_guard/`; real `.env*` access is blocked, except `.env.example` placeholders. The adapter maps any guard failure to Gemini's blocking exit code 2. Verify in a real CLI with `/hooks panel` and both allowed and blocked cases; repository tests do not prove runtime activation.
- The Gemini adapter calls the same shared guards as the Claude, Codex and Antigravity adapters, as defense-in-depth. Hooks run with user privileges and are not an OS isolation boundary; review them before trusting a checkout.
- Before claiming the workspace skills or hooks are active, verify them in the running CLI; repository files alone do not prove the host loaded them.
- Report in Vietnamese, with real test evidence. Be explicit when browser-visible behavior, provider capability, or external services were not verified.
