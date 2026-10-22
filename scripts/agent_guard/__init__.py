"""Host-neutral safety and verification policy for coding-agent hook adapters.

Every host adapter (.claude/hooks, .gemini/hooks, .codex/hooks, .agents/hooks) only
translates its own event payload into these functions. Keep policy here, stdlib only.
"""
