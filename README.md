# Claude Desktop Code Session Restore

Restore Claude Desktop **Code** sessions across Claude accounts or local profiles by safely copying the local Code sidebar index and matching Claude Code JSONL transcripts.

This is a Codex skill plus a standalone Python utility. It is for Claude Desktop's **Code tab**, not ordinary Claude Chat history.

## The Pain Point

Claude Desktop Code sessions are local-first. When you switch Claude accounts, use a separate Desktop user-data profile, reinstall the app, or hit a Desktop storage migration edge case, sessions can disappear from the Code tab even though useful transcript data still exists on disk.

The practical symptoms:

- A new Claude account opens with an empty or incomplete Code sidebar.
- Old sessions still exist as local JSONL transcript files under `~/.claude/projects/`.
- Copying cookies or IndexedDB would bring along login state and is unsafe.
- Claude Desktop does not provide a documented "adopt these local Code sessions into this account" button.

This tool handles the narrow, useful case: the session transcript exists locally, and you want the current Claude Desktop Code account/profile to show it in the sidebar.

## What It Does

The restore flow:

1. Snapshots an old account/profile's local Code session index and transcripts.
2. Detects the current target account/profile's Code session index directory.
3. Copies active `local_*.json` session index entries into the target account/profile.
4. Copies or verifies matching `.jsonl` transcripts.
5. Leaves login state alone.

It never copies:

- `Cookies`
- `Local Storage`
- `IndexedDB`
- `Session Storage`
- OAuth/token files
- ordinary Claude Chat history

## How Claude Desktop Code Stores Sessions

Claude Desktop Code uses two local layers.

### 1. Sidebar Index

On macOS:

```text
~/Library/Application Support/Claude/claude-code-sessions/<accountId>/<workspaceId>/local_*.json
```

On Windows, the same pattern lives under:

```text
%APPDATA%\Claude\claude-code-sessions\<accountId>\<workspaceId>\local_*.json
```

On Linux, the default is:

```text
~/.config/Claude/claude-code-sessions/<accountId>/<workspaceId>/local_*.json
```

Each `local_*.json` file is a Desktop Code sidebar entry. The important bridge field is:

```json
{
  "sessionId": "local_<desktop-session-id>",
  "cliSessionId": "<jsonl-transcript-id>",
  "title": "Sidebar title",
  "cwd": "/path/to/project",
  "lastActivityAt": 1781144564263,
  "isArchived": false
}
```

### 2. Transcript Data

Claude Code transcripts live under:

```text
~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl
```

The `cliSessionId` in the sidebar index maps to the JSONL filename. If either side is missing, the Desktop UI can show an empty/missing session or omit it from the sidebar.

## Installation

Clone this repository:

```bash
git clone https://github.com/medqhuang/claude-desktop-code-session-restore.git
cd claude-desktop-code-session-restore
```

Use the script directly:

```bash
python3 scripts/claude_desktop_code_session_restore.py --version
```

To install it as a Codex skill, copy or symlink the repository into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills
ln -s "$PWD" ~/.codex/skills/claude-desktop-code-session-restore
```

If your Codex build uses `~/.agents/skills`, use:

```bash
mkdir -p ~/.agents/skills
ln -s "$PWD" ~/.agents/skills/claude-desktop-code-session-restore
```

Then invoke it in Codex with:

```text
Use $claude-desktop-code-session-restore to restore my previous Claude Desktop Code sessions into the current account.
```

## Standard Account Switch Workflow

### Step 1: Snapshot the Old Account

While the old account is still available:

1. Fully quit Claude Desktop.
2. Run:

```bash
python3 scripts/claude_desktop_code_session_restore.py snapshot --register
```

This copies:

```text
claude-code-sessions/
projects/
```

into:

```text
~/.claude-code-session-bridge/pre-switch-backups/<snapshot-name>/
```

and registers that snapshot as a future source profile.

### Step 2: Create the New Account's Target Index

1. Open Claude Desktop.
2. Log into the new Claude account.
3. Open the Code tab.
4. Create one blank Code session.
5. Fully quit Claude Desktop again.

This lets Claude create:

```text
claude-code-sessions/<newAccountId>/<newWorkspaceId>/
```

The restore script uses that as the target.

### Step 3: Inspect

```bash
python3 scripts/claude_desktop_code_session_restore.py scan
```

You should see at least:

- one target index directory for the new account
- one source index directory for the old account/snapshot
- transcript roots under `~/.claude/projects` and any registered snapshots

### Step 4: Dry Run

```bash
python3 scripts/claude_desktop_code_session_restore.py sync --dry-run
```

Check for:

```text
candidate sessions: N
imported sessions: N
```

or, on repeat runs:

```text
already present in target: N
```

### Step 5: Restore

```bash
python3 scripts/claude_desktop_code_session_restore.py sync
```

The tool backs up the target index before writing:

```text
~/.claude-code-session-bridge/backups/<timestamp>/target-index/...
```

### Step 6: Verify

```bash
python3 scripts/claude_desktop_code_session_restore.py verify
```

Successful output looks like:

```text
verified sessions: total=12 ok=12 missing-cli=0 missing-transcript=0 empty=0
```

### Step 7: Reopen Claude Desktop

Open Claude Desktop, go to Code, and check that prior sessions appear in the sidebar.

## Commands

### `scan`

Print discovered app-support directories, transcript roots, target index, and session counts.

```bash
python3 scripts/claude_desktop_code_session_restore.py scan
```

### `snapshot`

Back up the current profile's Code session index and transcripts.

```bash
python3 scripts/claude_desktop_code_session_restore.py snapshot --register
```

Useful options:

```bash
--name old-work-20260611
--register
--dry-run
--force
--ignore-running
```

### `register-profile`

Register an already separated profile as a future source.

```bash
python3 scripts/claude_desktop_code_session_restore.py register-profile \
  --name old-work \
  --app-support-dir "$HOME/Library/Application Support/Claude-old" \
  --claude-config-dir "$HOME/.claude-old"
```

### `sync`

Copy active source `local_*.json` indexes into the current target account and ensure matching transcripts exist.

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

Check that the target account's Code session index files point to nonempty JSONL transcripts.

```bash
python3 scripts/claude_desktop_code_session_restore.py verify
```

### `self-test`

Run a temporary, isolated migration test without touching real Claude data.

```bash
python3 scripts/claude_desktop_code_session_restore.py self-test
```

## Safety Model

- `sync` and `snapshot` refuse to run while the Claude Desktop main process appears to be running.
- Target index directories are backed up before write operations.
- Existing target files are not overwritten unless explicitly requested.
- Archived sessions are skipped by default.
- Missing transcripts cause sessions to be skipped unless explicitly allowed.
- No browser/app login state is copied.

## What This Cannot Restore

This tool cannot recover sessions if the actual transcript payload is gone.

It cannot restore:

- ordinary Claude Chat history
- server-side account ownership
- missing or deleted `.jsonl` transcripts
- live background processes
- old account MCP/OAuth authorizations
- attachments or remote resources that the new account cannot access

It can make the new account/profile's Desktop Code sidebar point at local session transcripts that already exist.

## Version And Compatibility

Current version:

```text
0.1.0
```

Tested locally on:

- macOS
- Claude Desktop Code storage using `claude-code-sessions/<accountId>/<workspaceId>/local_*.json`
- Claude Code transcripts under `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`

The script includes default path detection for macOS, Windows, and Linux, but the project was born from and verified against Claude Desktop on macOS. Claude Desktop internals are not a public stable API; run `snapshot`, `sync --dry-run`, and `verify` before relying on it.

## Related Work And Prior Art

This project is related to community discoveries around Claude Desktop Code's local session storage:

- [anthropics/claude-code#58670](https://github.com/anthropics/claude-code/issues/58670) documents that Desktop Code sidebar entries are `local_*.json` files containing `cliSessionId` fields that map to `~/.claude/projects/.../*.jsonl`.
- [anthropics/claude-code#29373](https://github.com/anthropics/claude-code/issues/29373) documents a migration issue from `local-agent-mode-sessions` to `claude-code-sessions` and a workaround that copies `local_*.json`.
- [d-kimuson/claude-code-viewer](https://github.com/d-kimuson/claude-code-viewer), [jhlee0409/claude-code-history-viewer](https://github.com/jhlee0409/claude-code-history-viewer), and similar tools read JSONL transcripts for viewing/searching.

The narrower goal here is account/profile restore for Claude Desktop Code's own sidebar, without copying login state.

## Development

Run the isolated self-test:

```bash
python3 scripts/claude_desktop_code_session_restore.py self-test
```

Run a syntax check without writing `__pycache__`:

```bash
python3 -B -m py_compile scripts/claude_desktop_code_session_restore.py
```

## License

MIT.
