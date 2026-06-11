# Claude Desktop Code Session Restore

Restore Claude Desktop **Code** sessions after switching Claude accounts or Desktop profiles, without copying login state.

This repository contains:

- a Codex skill (`SKILL.md`)
- a standalone Python restore utility (`scripts/claude_desktop_code_session_restore.py`)

It is for Claude Desktop's **Code** tab. It does not migrate ordinary Claude Chat history.

## Why This Exists

Claude Desktop Code sessions are stored locally. After switching accounts, using a different Desktop profile, reinstalling Claude, or hitting a Desktop storage migration bug, the Code sidebar may no longer show sessions that still exist on disk.

Typical situation:

- You log into a new Claude account.
- The Code sidebar is empty or missing old work.
- The old transcripts still exist under `~/.claude/projects/`.
- Copying `Cookies`, `IndexedDB`, or `Local Storage` would also copy login state, which is the wrong fix.

This tool restores the local Code sidebar by copying only the Code session index and matching JSONL transcripts.

## Fastest Use: Let an AI Agent Run It

The intended workflow is not "manually type ten commands." The intended workflow is:

1. Clone this repository.
2. Ask Codex, Claude Code, or another local coding agent to read `SKILL.md`.
3. Let the agent run the restore script for you.

Example prompt:

```text
Read ./SKILL.md and use this repo to restore my previous Claude Desktop Code sessions into the current Claude account.
Do not copy cookies, IndexedDB, Local Storage, or any login state.
First snapshot the old account/profile, then after I log into the new account and create one blank Code session, run the sync and verify steps.
```

If the old account is still open, tell the agent:

```text
I am still on the old Claude account. Start with the snapshot step and stop when I need to log into the new account.
```

After you log into the new account and create one blank Code session, tell the agent:

```text
The new account has one blank Code session. Continue with dry-run, sync, and verify.
```

The agent should run these phases:

```text
snapshot --register
scan
sync --dry-run
sync
verify
```

## Manual Quick Start

Clone the repo:

```bash
git clone https://github.com/medqhuang/claude-desktop-code-session-restore.git
cd claude-desktop-code-session-restore
```

Run the self-test:

```bash
python3 scripts/claude_desktop_code_session_restore.py self-test
```

With the old account still available, fully quit Claude Desktop and snapshot it:

```bash
python3 scripts/claude_desktop_code_session_restore.py snapshot --register
```

Then:

1. Open Claude Desktop.
2. Log into the new account.
3. Open the Code tab.
4. Create one blank Code session.
5. Fully quit Claude Desktop again.

Dry-run, restore, and verify:

```bash
python3 scripts/claude_desktop_code_session_restore.py scan
python3 scripts/claude_desktop_code_session_restore.py sync --dry-run
python3 scripts/claude_desktop_code_session_restore.py sync
python3 scripts/claude_desktop_code_session_restore.py verify
```

Reopen Claude Desktop. Old Code sessions should now appear in the new account's Code sidebar.

## How It Works

Claude Desktop Code uses two local storage layers.

### 1. Desktop Code sidebar index

On macOS:

```text
~/Library/Application Support/Claude/claude-code-sessions/<accountId>/<workspaceId>/local_*.json
```

On Windows:

```text
%APPDATA%\Claude\claude-code-sessions\<accountId>\<workspaceId>\local_*.json
```

On Linux:

```text
~/.config/Claude/claude-code-sessions/<accountId>/<workspaceId>/local_*.json
```

Each `local_*.json` file is one sidebar entry. The key field is `cliSessionId`:

```json
{
  "sessionId": "local_<desktop-session-id>",
  "cliSessionId": "<transcript-id>",
  "title": "Review roadmap and task consolidation",
  "cwd": "/path/to/project",
  "lastActivityAt": 1781144564263,
  "isArchived": false
}
```

### 2. Claude Code transcript files

The actual conversation transcript is stored as JSONL:

```text
~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl
```

The Desktop sidebar file points at the transcript through `cliSessionId`. If the transcript exists but the sidebar index does not, the session can be missing from Claude Desktop even though the work is still on disk.

This tool copies source `local_*.json` files into the current target account's index directory and ensures the matching `.jsonl` files are present.

## What Gets Copied

Copied or verified:

- `claude-code-sessions/<accountId>/<workspaceId>/local_*.json`
- `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`

Never copied:

- `Cookies`
- `Local Storage`
- `IndexedDB`
- `Session Storage`
- OAuth tokens
- ordinary Claude Chat history

## Command Reference

### `snapshot`

Back up the current Claude Desktop Code index and transcripts.

```bash
python3 scripts/claude_desktop_code_session_restore.py snapshot --register
```

The snapshot is written to:

```text
~/.claude-code-session-bridge/pre-switch-backups/<name>/
```

Use this before logging out of the old account.

Useful options:

```bash
--name old-work-20260611
--register
--dry-run
--force
--ignore-running
```

### `scan`

Show detected target index, source indexes, transcript roots, and session counts.

```bash
python3 scripts/claude_desktop_code_session_restore.py scan
```

### `sync`

Copy source sessions into the current target account/profile.

```bash
python3 scripts/claude_desktop_code_session_restore.py sync --dry-run
python3 scripts/claude_desktop_code_session_restore.py sync
```

Useful options:

```bash
--include-archived
--session local_<id>
--session <cliSessionId>
--overwrite-index
--overwrite-transcript
--allow-missing-transcript
--target-app-support-dir <path>
--target-claude-config-dir <path>
--source-app-support-dir <path>
--source-claude-config-dir <path>
```

### `verify`

Check that target sidebar entries point to nonempty JSONL transcripts.

```bash
python3 scripts/claude_desktop_code_session_restore.py verify
```

Healthy output:

```text
verified sessions: total=12 ok=12 missing-cli=0 missing-transcript=0 empty=0
```

### `register-profile`

Register an already separated old profile as a future source.

```bash
python3 scripts/claude_desktop_code_session_restore.py register-profile \
  --name old-work \
  --app-support-dir "$HOME/Library/Application Support/Claude-old" \
  --claude-config-dir "$HOME/.claude-old"
```

### `self-test`

Run an isolated temporary migration test. It does not touch your real Claude data.

```bash
python3 scripts/claude_desktop_code_session_restore.py self-test
```

## Safety Model

- `snapshot` and `sync` refuse to run while the Claude Desktop main process appears to be running.
- `sync` backs up the target index before writing.
- Existing target files are not overwritten unless you pass an explicit overwrite flag.
- Archived sessions are skipped by default.
- Sessions with missing transcripts are skipped by default.
- Login state is never copied.

Backups are written under:

```text
~/.claude-code-session-bridge/backups/
```

## What This Cannot Restore

This tool cannot recover a session if the transcript payload is gone.

It cannot restore:

- ordinary Claude Chat history
- account ownership on Claude's servers
- deleted `.jsonl` transcripts
- live background processes
- old account MCP/OAuth authorizations
- attachments or remote resources the new account cannot access

It only makes the current Claude Desktop Code sidebar point at local transcripts that already exist.

## Install As A Codex Skill

Clone the repo and symlink it into the Codex skills directory:

```bash
mkdir -p ~/.codex/skills
ln -s "$PWD" ~/.codex/skills/claude-desktop-code-session-restore
```

Some Codex builds use `~/.agents/skills`:

```bash
mkdir -p ~/.agents/skills
ln -s "$PWD" ~/.agents/skills/claude-desktop-code-session-restore
```

Then invoke:

```text
Use $claude-desktop-code-session-restore to restore my previous Claude Desktop Code sessions into the current account.
```

## Version And Compatibility

Current version:

```text
0.1.0
```

Tested locally on:

- macOS
- Claude Desktop Code storage using `claude-code-sessions/<accountId>/<workspaceId>/local_*.json`
- Claude Code transcripts under `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`

The script has default path detection for macOS, Windows, and Linux. The storage layout is not a public stable API, so always run:

```bash
python3 scripts/claude_desktop_code_session_restore.py self-test
python3 scripts/claude_desktop_code_session_restore.py sync --dry-run
python3 scripts/claude_desktop_code_session_restore.py verify
```

## Related Work

This project builds on public observations about Claude Desktop Code's local session storage:

- [anthropics/claude-code#58670](https://github.com/anthropics/claude-code/issues/58670) documents that Desktop Code sidebar entries are `local_*.json` files containing `cliSessionId` fields that map to `~/.claude/projects/.../*.jsonl`.
- [anthropics/claude-code#29373](https://github.com/anthropics/claude-code/issues/29373) documents a migration issue from `local-agent-mode-sessions` to `claude-code-sessions` and a workaround that copies `local_*.json`.
- [d-kimuson/claude-code-viewer](https://github.com/d-kimuson/claude-code-viewer), [jhlee0409/claude-code-history-viewer](https://github.com/jhlee0409/claude-code-history-viewer), and similar projects read JSONL transcripts for viewing and search.

This repository focuses on a narrower problem: restoring Claude Desktop Code's own sidebar across local accounts/profiles without copying login state.

## Development

Run tests:

```bash
python3 scripts/claude_desktop_code_session_restore.py self-test
python3 -B -m py_compile scripts/claude_desktop_code_session_restore.py
```

## License

MIT.
