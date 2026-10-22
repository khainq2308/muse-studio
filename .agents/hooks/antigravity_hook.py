#!/usr/bin/env python3
"""Google Antigravity hook adapter for MuseStudio's shared policy (scripts/agent_guard/).

Contract (antigravity.google/docs/hooks): JSON on stdin with `toolCall.name`/`toolCall.args`
for tool events; the payload does not name the event, so `.agents/hooks.json` passes it as
argv[1]. Decisions are JSON on stdout: PreToolUse `{"decision": "allow"|"deny", "reason"}`,
Stop `{"decision": "continue", "reason"}` keeps the agent working. Exit codes are not
documented, so this adapter always exits 0 and fails closed with an explicit deny.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_guard.files import check_edit, check_read  # noqa: E402
from agent_guard.session import session_context  # noqa: E402
from agent_guard.shell import check_command  # noqa: E402
from agent_guard.verify import completion_gate  # noqa: E402


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(_text(item) for item in value.values())
    return ""


def check_tool_call(name: object, args: dict) -> str | None:
    if name == "run_command":
        return check_command(args.get("CommandLine"))
    if name == "write_to_file":
        return check_edit(ROOT, args.get("TargetFile"), _text(args.get("CodeContent")))
    if name == "replace_file_content":
        return check_edit(ROOT, args.get("TargetFile"), _text(args.get("ReplacementContent")))
    if name == "multi_replace_file_content":
        chunks = args.get("ReplacementChunks") or []
        added = "\n".join(
            _text(chunk.get("ReplacementContent")) if isinstance(chunk, dict) else _text(chunk)
            for chunk in (chunks if isinstance(chunks, list) else [chunks])
        )
        return check_edit(ROOT, args.get("TargetFile"), added)
    if name == "view_file":
        return check_read(ROOT, args.get("AbsolutePath"), require_path=True)
    if name == "grep_search":
        includes = args.get("Includes")
        patterns = [p for p in includes if isinstance(p, str)] if isinstance(includes, list) else []
        return check_read(ROOT, args.get("SearchPath") or "", patterns)
    if name == "find_by_name":
        pattern = args.get("Pattern")
        return check_read(ROOT, args.get("SearchDirectory") or "", [pattern] if isinstance(pattern, str) else [])
    return None


def handle(event_name: str, payload: dict) -> dict:
    if event_name == "PreToolUse":
        tool_call = payload.get("toolCall")
        if not isinstance(tool_call, dict) or not isinstance(tool_call.get("args", {}), dict):
            return {"decision": "deny", "reason": "[antigravity guard] Invalid toolCall; refusing to bypass the guard."}
        message = check_tool_call(tool_call.get("name"), tool_call.get("args") or {})
        return {"decision": "deny", "reason": message} if message else {"decision": "allow"}
    if event_name == "Stop":
        if os.environ.get("MUSE_STOP_GATE", "1") == "0":
            return {}
        message = completion_gate(ROOT, session_key=str(payload.get("conversationId") or "antigravity"))
        return {"decision": "continue", "reason": message} if message else {}
    if event_name == "SessionStart":
        return {"injectSteps": [{"systemMessage": session_context(ROOT)}]}
    return {}


def main() -> int:
    event_name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = None
    if not isinstance(payload, dict):
        result = {"decision": "deny", "reason": "[antigravity guard] Invalid hook JSON."} if event_name == "PreToolUse" else {}
    else:
        result = handle(event_name, payload)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
