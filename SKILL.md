---
name: claude-desktop-code-session-restore
description: Restore Claude Desktop Code sessions across Claude accounts or user-data profiles on macOS, adopt plain Claude Code CLI JSONL sessions into Claude Desktop Code, or export a Desktop Code session to a Claude Code CLI resume command. Use when the user wants a newly logged-in Claude Desktop Code account to see previous active Code sessions, migrate or restore Claude Desktop Code session lists, make CLI-only sessions appear in Desktop Code, continue/resume a Desktop Code session in the plain Claude Code CLI or terminal, copy local Code transcripts, or inspect/repair claude-code-sessions and ~/.claude/projects. This is for Claude Desktop Code, not ordinary Claude Chat history.
---

# Claude Desktop Code Session Restore

## Purpose

Use this skill to make Claude Desktop's Code tab show prior local Code sessions after switching Claude accounts/user-data profiles on macOS, to make plain Claude Code CLI sessions appear in Desktop Code, or to export a Desktop Code session back to a Claude Code CLI resume command. The workflow only touches Code session metadata and local transcripts.

This release is macOS-only. If the user is not on macOS, do not run restore commands unless they explicitly accept unsupported experimental use with explicit paths.

Never copy or modify Claude Desktop login state:

- Do not copy `Cookies`, `Local Storage`, `IndexedDB`, `Session Storage`, `config.json`, or OAuth/token files.
- Do not claim this migrates ordinary Claude Chat history.
- Do not treat live background processes as migrated; only the Code transcript and session index are bridged.
- Do not paste real account IDs, workspace IDs, `local_*.json` names, `cliSessionId` values, transcript text, or private `/Users/<name>/...` paths into public output.

## macOS Storage Model

Claude Desktop Code uses two local layers:

- Session index: `~/Library/Application Support/Claude/claude-code-sessions/<accountId>/<workspaceId>/local_*.json`
- Transcript data: `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`
- Tool state and backups: `~/.claude-desktop-code-session-restore/`

The `local_*.json` files make sessions appear in the Code tab. The `.jsonl` files hold the actual transcript. Both must be present for a restored session to be useful.

For CLI-only sessions, the transcript already exists but no Desktop `local_*.json` points at it. Use `adopt-cli` to create that Desktop sidebar entry from the current target account's own newest index template.

## Default Workflow

1. With the old account still available, ask the user to fully quit Claude Desktop, then snapshot and register the current local Code state:

```bash
python3 scripts/claude_desktop_code_session_restore.py snapshot --register
```

2. Ask the user to log into the new Claude account, open the Code tab, create one blank Code session, and fully quit Claude Desktop again.
3. Scan the detected source and target state:

```bash
python3 scripts/claude_desktop_code_session_restore.py scan
```

4. Dry-run, then sync:

```bash
python3 scripts/claude_desktop_code_session_restore.py sync --dry-run
python3 scripts/claude_desktop_code_session_restore.py sync
```

5. Verify every target index has a matching transcript:

```bash
python3 scripts/claude_desktop_code_session_restore.py verify
```

6. Restart Claude Desktop and ask the user to check the Code tab.

## Adopt CLI-Only Sessions

Use this when the user has a Claude Code CLI session under `~/.claude/projects/.../<cliSessionId>.jsonl` that is not visible in Claude Desktop Code.

1. Identify the CLI session id. It is the JSONL filename without `.jsonl`. Prefer deriving it from a title/search over transcript metadata without pasting transcript contents.
2. Ensure the target Claude Desktop account has at least one blank Code session; `adopt-cli` uses that session index as a schema template.
3. Dry-run first. This is read-only and can run while Claude Desktop is open:

```bash
python3 scripts/claude_desktop_code_session_restore.py adopt-cli --session <cliSessionId> --dry-run
```

4. Ask the user to fully quit Claude Desktop, then write the index:

```bash
python3 scripts/claude_desktop_code_session_restore.py adopt-cli --session <cliSessionId>
python3 scripts/claude_desktop_code_session_restore.py verify --session <cliSessionId>
```

5. Restart Claude Desktop and ask the user to check the Code tab.

Use `--all` only when the user explicitly wants every non-indexed CLI transcript adopted. For bulk work, dry-run first and consider `--limit <n>`.

## Export A Desktop Session To The CLI

Use this (the inverse of `adopt-cli`) when the user wants to continue a Claude Desktop Code session in the plain Claude Code CLI/terminal.

The Desktop transcript already lives at `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl` — the same file the CLI resumes from — so on the same machine/config this needs no copying. `export-cli` reads the Desktop index to resolve `cliSessionId` and `cwd`, then prints the resume command. It does not touch the Desktop index and is safe to run while Claude Desktop is open.

1. Identify the session by its Desktop sidebar title, its `local_...` id, or its `cliSessionId`.
2. Print the resume command:

```bash
python3 scripts/claude_desktop_code_session_restore.py export-cli --title "session title"
python3 scripts/claude_desktop_code_session_restore.py export-cli --session <cliSessionId>
```

3. Give the user the printed `cd <cwd> && claude --resume <cliSessionId>` command to run in their terminal.

To move the session into a different CLI config dir (a separate `CLAUDE_CONFIG_DIR`/profile) rather than the current `~/.claude`, add `--to-config-dir <path>`; dry-run first. The printed command is then prefixed with `CLAUDE_CONFIG_DIR=<path>`. List many at once with `--all` (respects `--include-archived`, `--limit <n>`).

## Separate Profiles

If the old account used a different Claude user-data directory or the new account uses a different `CLAUDE_CONFIG_DIR`, pass paths explicitly:

```bash
python3 scripts/claude_desktop_code_session_restore.py sync \
  --target-app-support-dir "$HOME/Library/Application Support/Claude-new" \
  --target-claude-config-dir "$HOME/.claude-new" \
  --source-app-support-dir "$HOME/Library/Application Support/Claude-old" \
  --source-claude-config-dir "$HOME/.claude-old"
```

## Repeated Account Switching

Register known profiles once, then future `sync` calls include them as sources. Prefer `snapshot --register` when the profile is the current Claude state:

```bash
python3 scripts/claude_desktop_code_session_restore.py snapshot \
  --name old-work-20260611 \
  --register
```

If the old profile is already stored in separate directories, register paths explicitly:

```bash
python3 scripts/claude_desktop_code_session_restore.py register-profile \
  --name old-work \
  --app-support-dir "$HOME/Library/Application Support/Claude-old" \
  --claude-config-dir "$HOME/.claude-old"
```

## Safety Rules

- Default import is active sessions only: `isArchived != true`.
- The script creates a backup of the target Code session index before copying.
- `sync`, `snapshot`, and real `adopt-cli` writes abort if the Claude Desktop main process is running; only use `--ignore-running` after checking that the process is not actively writing session metadata. `adopt-cli --dry-run` is read-only.
- Existing target files are not overwritten unless `--overwrite-index` or `--overwrite-transcript` is explicitly passed.
- If a transcript is missing, the corresponding session is skipped unless `--allow-missing-transcript` is passed.
- Prefer `--dry-run` first when using non-default profile paths.

## Commands

- `scan`: show known app-support directories, transcript roots, target index, and session counts.
- `snapshot --register`: copy current `claude-code-sessions` and `projects` into `~/.claude-desktop-code-session-restore/pre-switch-backups/` and register the snapshot as a future source.
- `sync`: copy active source `local_*.json` session indexes into the current target account and copy matching transcripts when needed.
- `adopt-cli --session <cliSessionId>`: create a Desktop `local_*.json` index for a plain Claude Code CLI JSONL transcript.
- `export-cli --title <substring> | --session <id> | --all`: print the `claude --resume <cliSessionId>` command to continue a Desktop Code session in the CLI; read-only unless `--to-config-dir` is passed.
- `verify`: check that target session indexes have matching nonempty `.jsonl` transcripts.
- `self-test`: run an isolated temp-directory migration test without touching real Claude data.

## Troubleshooting

- If no target is detected, log in to the new Claude account, open Code tab, and create one blank session so Claude Desktop creates `<accountId>/<workspaceId>`.
- If sessions appear but cannot resume, verify the matching `<cliSessionId>.jsonl` exists under the target Claude config directory's `projects/`.
- If `adopt-cli` says a target template is missing, create one blank Desktop Code session in the target account and fully quit Claude Desktop.
- If adopting a CLI session, use the JSONL filename stem as `<cliSessionId>`, not a Desktop `local_...` id.
- If sessions require old MCP or plugin credentials, re-authorize those tools in the new Claude account.
