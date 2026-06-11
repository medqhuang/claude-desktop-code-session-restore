---
name: claude-desktop-code-session-restore
description: Restore Claude Desktop Code sessions across Claude accounts or user-data profiles. Use when the user wants a newly logged-in Claude Desktop Code account to see previous active Code sessions, migrate or restore Claude Desktop Code session lists, copy local Code transcripts, or inspect/repair claude-code-sessions and ~/.claude/projects. This is for Claude Desktop Code, not ordinary Claude Chat history.
---

# Claude Desktop Code Session Restore

## Purpose

Use this skill to make Claude Desktop's Code tab show prior local Code sessions after switching Claude accounts or user-data profiles. The workflow only touches Code session metadata and local transcripts.

Never copy or modify Claude Desktop login state:

- Do not copy `Cookies`, `Local Storage`, `IndexedDB`, `Session Storage`, `config.json`, or OAuth/token files.
- Do not claim this migrates ordinary Claude Chat history.
- Do not treat live background processes as migrated; only the Code transcript and session index are bridged.

## Storage Model

Claude Desktop Code uses two local layers:

- Session index: `~/Library/Application Support/Claude/claude-code-sessions/<accountId>/<workspaceId>/local_*.json`
- Transcript data: `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`

The `local_*.json` files make sessions appear in the Code tab. The `.jsonl` files hold the actual transcript. Both must be present for a restored session to be useful.

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
- `sync` and `snapshot` abort if the Claude Desktop main process is running; only use `--ignore-running` after checking that the process is not actively writing session metadata.
- Existing target files are not overwritten unless `--overwrite-index` or `--overwrite-transcript` is explicitly passed.
- If a transcript is missing, the corresponding session is skipped unless `--allow-missing-transcript` is passed.
- Prefer `--dry-run` first when using non-default profile paths.

## Commands

- `scan`: show known app-support directories, transcript roots, target index, and session counts.
- `snapshot --register`: copy current `claude-code-sessions` and `projects` into `~/.claude-code-session-bridge/pre-switch-backups/` and register the snapshot as a future source.
- `sync`: copy active source `local_*.json` session indexes into the current target account and copy matching transcripts when needed.
- `verify`: check that target session indexes have matching nonempty `.jsonl` transcripts.
- `self-test`: run an isolated temp-directory migration test without touching real Claude data.

## Troubleshooting

- If no target is detected, log in to the new Claude account, open Code tab, and create one blank session so Claude Desktop creates `<accountId>/<workspaceId>`.
- If sessions appear but cannot resume, verify the matching `<cliSessionId>.jsonl` exists under the target Claude config directory's `projects/`.
- If sessions require old MCP or plugin credentials, re-authorize those tools in the new Claude account.
