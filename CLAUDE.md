# MuseStudio — Claude Code entrypoint

The canonical engineering rules and workflow are platform-neutral:

1. Read AGENTS.md.
2. Follow docs/AGENT_WORKFLOW.md.
3. Load the relevant shared playbook in .agents/skills/; Claude mirrors are under .claude/skills/.

## Claude-specific adapters

- Native subagent definitions: .claude/agents/.
- Native slash-command workflows: .claude/commands/. They should call canonical repository scripts and follow the shared workflow; equivalents for Codex, Gemini CLI and other hosts are documented in docs/AGENT_WORKFLOW.md.
- Hook registration: .claude/settings.json; event parsing and guards: .claude/hooks/. These use Claude-specific event contracts. A hook is not verified until its input/output and block behavior have been tested in a Claude runtime.
- The Read hook and edit guard block real .env* files while allowing .env.example placeholders. Hooks are defense-in-depth, not a replacement for OS permissions.
- Local permission overrides belong in ignored .claude/settings.local.json.

Reply to the user in Vietnamese unless requested otherwise. Code, identifiers and commit messages remain in English.
