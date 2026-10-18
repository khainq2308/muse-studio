# Codex read-only sandbox validation

The `.codex/agents/*.toml` files declare `sandbox_mode = "read-only"`, but repository configuration alone is not evidence that the active Codex runtime enforces that mode for a spawned agent. Enforcement can depend on the Codex version and how the parent session was launched.

## Required operating rule

- Run the reviewer and security-auditor profiles only from a parent session whose effective sandbox is explicitly configured as read-only.
- If the effective parent/agent sandbox cannot be confirmed, do not run either profile against the canonical working tree. Use a disposable isolated checkout with credentials, local `.env` files, production access and mounted user data removed.
- Do not treat the agent's promise not to edit files as a security boundary.

## Canary procedure for each runtime/version upgrade

1. Create a disposable test checkout containing no secrets or valuable data.
2. Launch the same Codex CLI/IDE flow and custom profile used by the team.
3. Ask the profile to create a uniquely named file inside that disposable checkout as a permission canary. Do not run this experiment in the working project.
4. Confirm that the host denies the write and that the file does not exist. A refusal in generated text is not sufficient evidence; inspect the filesystem result.
5. Record the Codex version, launch mode, effective sandbox setting, result and date in the team's runtime validation notes.
6. If the write succeeds or the outcome is ambiguous, classify the profile as **not verified read-only**. Keep it away from sensitive workspaces and use an externally enforced read-only/isolated environment until the runtime behavior is resolved.

This is a manual host-runtime gate: repository CI can check the declared profile settings and documentation, but cannot prove that a developer's installed Codex runtime enforces them.
