# Claude Desktop Code Session Restore

Restore Claude Desktop **Code** sessions on macOS after switching Claude accounts or Desktop profiles, make plain Claude Code CLI sessions appear in Claude Desktop Code, or turn a Desktop Code session into a plain Claude Code CLI resume command, without copying login state.

This repository contains:

- a Codex skill (`SKILL.md`)
- a standalone Python restore utility (`scripts/claude_desktop_code_session_restore.py`)

It is for Claude Desktop's **Code** tab. It does not migrate ordinary Claude Chat history.

## Support Status

This release is **macOS-only**.

It was built and tested against Claude Desktop for macOS, where Code sessions are stored under `~/Library/Application Support/Claude` and transcripts are stored under `~/.claude/projects`.

Windows and Linux are not supported in this release. The script intentionally refuses non-macOS restore commands unless you pass `--allow-unsupported-platform`; treat that as experimental and use explicit paths.

## Why This Exists

Claude Desktop Code sessions are stored locally. After switching accounts, using a different Desktop profile, reinstalling Claude, or hitting a Desktop storage migration bug, the Code sidebar may no longer show sessions that still exist on disk.

Typical situation:

- You log into a new Claude account.
- The Code sidebar is empty or missing old work.
- The old transcripts still exist under `~/.claude/projects/`.
- Copying `Cookies`, `IndexedDB`, or `Local Storage` would also copy login state, which is the wrong fix.

This tool restores the local Code sidebar by copying or creating only the Code session index and matching JSONL transcripts.

## Fastest Use: Let an AI Agent Run It

The intended workflow is not "manually type ten commands." The intended workflow is:

1. Clone this repository.
2. Ask Codex, Claude Code, or another local coding agent to read `SKILL.md`.
3. Let the agent run the restore script for you.

Example prompt:

```text
Read ./SKILL.md and use this repo to restore my previous Claude Desktop Code sessions into the current Claude account.
I am on macOS.
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

To adopt a plain Claude Code CLI session into Claude Desktop Code, use:

```text
Read ./SKILL.md and use this repo to make my Claude Code CLI session appear in Claude Desktop Code.
I am on macOS.
The CLI session id is <cliSessionId>.
Do not copy cookies, IndexedDB, Local Storage, or any login state.
Run adopt-cli with a dry run first, then ask me to quit Claude Desktop before writing the Desktop Code index.
```

## Manual Quick Start For macOS

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

To adopt one plain Claude Code CLI session into Desktop Code:

```bash
python3 scripts/claude_desktop_code_session_restore.py adopt-cli --session <cliSessionId> --dry-run
```

Fully quit Claude Desktop, then run:

```bash
python3 scripts/claude_desktop_code_session_restore.py adopt-cli --session <cliSessionId>
python3 scripts/claude_desktop_code_session_restore.py verify --session <cliSessionId>
```

`<cliSessionId>` is the `.jsonl` filename without `.jsonl` under `~/.claude/projects/<encoded-cwd>/`.

To take one Desktop Code session into the plain Claude Code CLI, look it up by title and print the resume command:

```bash
python3 scripts/claude_desktop_code_session_restore.py export-cli --title "my session title"
```

This is read-only on the same machine: the Desktop transcript already lives under `~/.claude/projects`, so `export-cli` just resolves the session's `cliSessionId` and `cwd` and prints the exact `cd <cwd> && claude --resume <cliSessionId>` command. Run that command to continue the conversation in the terminal. Desktop can stay open. To copy the transcript into a different CLI config dir first (a separate `CLAUDE_CONFIG_DIR` or profile), add `--to-config-dir <path>` (use `--dry-run` first).

## How It Works

Claude Desktop Code on macOS uses two local storage layers.

### 1. Desktop Code sidebar index

```text
~/Library/Application Support/Claude/claude-code-sessions/<accountId>/<workspaceId>/local_*.json
```

Each `local_*.json` file is one sidebar entry. The key field is `cliSessionId`:

```json
{
  "sessionId": "local_<desktop-session-id>",
  "cliSessionId": "<transcript-id>",
  "title": "Example Code session",
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

For account/profile restore, this tool copies source `local_*.json` files into the current target account's index directory and ensures the matching `.jsonl` files are present.

For CLI adoption, this tool reads an existing `.jsonl` transcript, derives a new Desktop `local_*.json` sidebar entry from the current target account's own Desktop template, and points that entry at the existing transcript through `cliSessionId`.

## What Gets Copied

Copied, created, or verified:

- `claude-code-sessions/<accountId>/<workspaceId>/local_*.json`
- `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`

`sync` copies existing Desktop `local_*.json` entries from another account/profile. `adopt-cli` creates new `local_*.json` entries for CLI-only transcripts. If the CLI transcript already lives under the target `~/.claude/projects`, the transcript is not copied; the new Desktop index simply points at it.

Never copied:

- `Cookies`
- `Local Storage`
- `IndexedDB`
- `Session Storage`
- OAuth tokens
- ordinary Claude Chat history

## Privacy And Redaction

The repository documentation uses placeholders such as `<accountId>`, `<workspaceId>`, `<cliSessionId>`, and `/path/to/project`. It should not contain real local paths, real Claude account names, real workspace IDs, or real session IDs.

When opening an issue or sharing logs, redact:

- your macOS username from `/Users/<you>/...` paths
- Claude account IDs and workspace IDs under `claude-code-sessions/`
- `local_*.json` filenames if they contain real session identifiers
- `cliSessionId` values and `.jsonl` filenames
- project `cwd` values if the path or folder name is private
- transcript contents from `~/.claude/projects/**/*.jsonl`

`scan`, `sync --dry-run`, and `verify` may print local paths. That output is useful for debugging, but it is not automatically sanitized.

## Command Reference

Global options must appear before the subcommand:

```bash
python3 scripts/claude_desktop_code_session_restore.py --state-root <path> sync --dry-run
python3 scripts/claude_desktop_code_session_restore.py --allow-unsupported-platform scan --target-app-support-dir <path> --target-claude-config-dir <path>
```

### `snapshot`

Back up the current Claude Desktop Code index and transcripts.

```bash
python3 scripts/claude_desktop_code_session_restore.py snapshot --register
```

The snapshot is written to:

```text
~/.claude-desktop-code-session-restore/pre-switch-backups/<name>/
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

### `adopt-cli`

Create Desktop Code sidebar entries for plain Claude Code CLI JSONL transcripts.

```bash
python3 scripts/claude_desktop_code_session_restore.py adopt-cli --session <cliSessionId> --dry-run
python3 scripts/claude_desktop_code_session_restore.py adopt-cli --session <cliSessionId>
```

Adopt several specific CLI sessions:

```bash
python3 scripts/claude_desktop_code_session_restore.py adopt-cli \
  --session <cliSessionId-1> \
  --session <cliSessionId-2>
```

Adopt every non-indexed CLI transcript only when you really mean it:

```bash
python3 scripts/claude_desktop_code_session_restore.py adopt-cli --all --dry-run
python3 scripts/claude_desktop_code_session_restore.py adopt-cli --all
```

Useful options:

```bash
--source-claude-config-dir <path>
--overwrite-transcript
--allow-missing-cwd
--limit <n>
--ignore-running
```

Notes:

- `adopt-cli` refuses to run without either `--session <cliSessionId>` or explicit `--all`.
- It uses the newest target Desktop `local_*.json` as a schema template, then replaces `sessionId`, `cliSessionId`, title, cwd, timestamps, and turn count.
- It skips CLI transcripts that are already indexed in the target Desktop account.
- It skips empty transcripts and, by default, transcripts without a `cwd` field.

### `export-cli`

Print the Claude Code CLI command to resume a Desktop Code session. This is the inverse of `adopt-cli`.

```bash
python3 scripts/claude_desktop_code_session_restore.py export-cli --title "my session title"
python3 scripts/claude_desktop_code_session_restore.py export-cli --session <cliSessionId>
python3 scripts/claude_desktop_code_session_restore.py export-cli --all --limit 10
```

On the same machine this is read-only. Because the Desktop transcript already lives under `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl` — the exact file the CLI resumes from — `export-cli` only reads the Desktop index to resolve `cliSessionId` and `cwd`, then prints `cd <cwd> && claude --resume <cliSessionId>`. It does not modify the Desktop index, so it is safe to run while Claude Desktop is open.

Select sessions with `--session` (Desktop `local_...` id or `cliSessionId`, repeatable), `--title` (case-insensitive substring, repeatable), or `--all`.

Useful options:

```bash
--session <local_id-or-cliSessionId>
--title <substring>
--all
--limit <n>
--include-archived
--to-config-dir <path>
--overwrite-transcript
--dry-run
```

`--to-config-dir <path>` also copies each transcript into that CLI config dir's `projects/` (for a different `CLAUDE_CONFIG_DIR` or profile); the printed command is then prefixed with `CLAUDE_CONFIG_DIR=<path>`. Without it, nothing is written. Use `--dry-run` to preview copies.

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

- `snapshot`, `sync`, and real `adopt-cli` writes refuse to run while the Claude Desktop main process appears to be running. `adopt-cli --dry-run` is read-only.
- `sync` and `adopt-cli` back up the target index before writing.
- Existing target files are not overwritten unless you pass an explicit overwrite flag.
- Archived sessions are skipped by default.
- Sessions with missing transcripts are skipped by default.
- Login state is never copied.

Backups are written under:

```text
~/.claude-desktop-code-session-restore/backups/
```

The tool also stores registered source profiles under:

```text
~/.claude-desktop-code-session-restore/profiles.json
```

That file contains local paths only. It does not contain cookies, tokens, or transcript text.

## Common Failure Points

- Claude Desktop must be fully quit before `snapshot` and `sync`; otherwise Desktop can overwrite or race the local index files.
- The new account must create one blank Code session first. This gives the script a concrete target `<accountId>/<workspaceId>` directory.
- If `scan` shows no source sessions, snapshot the old account before logging out, or register an old profile with explicit paths.
- If `sync` imports zero sessions, they may already exist in the target, be archived, or be missing transcripts. Use `--include-archived` only if you intentionally want archived sessions.
- If restored sessions appear but cannot resume, run `verify` and check for missing or empty `.jsonl` transcript files.
- For `adopt-cli`, the target account must already have at least one blank Desktop Code session. The tool uses it as a safe schema template.
- For `adopt-cli`, use the JSONL filename stem as `<cliSessionId>`, not the Desktop `local_...` id.
- MCP servers, OAuth credentials, and remote resources are not migrated. Re-authorize them in the new account if a restored session needs them.

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
0.3.0
```

Supported and tested on:

- macOS only
- Claude Desktop Code storage using `claude-code-sessions/<accountId>/<workspaceId>/local_*.json`
- Claude Code transcripts under `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`
- CLI-only Claude Code JSONL adoption into Desktop Code via `adopt-cli`
- Desktop Code sessions exported to a Claude Code CLI resume command via `export-cli`

The storage layout is not a public stable API. Always run the relevant checks:

```bash
python3 scripts/claude_desktop_code_session_restore.py self-test
python3 scripts/claude_desktop_code_session_restore.py sync --dry-run
python3 scripts/claude_desktop_code_session_restore.py verify
```

For CLI adoption, replace the sync dry-run with:

```bash
python3 scripts/claude_desktop_code_session_restore.py adopt-cli --session <cliSessionId> --dry-run
```

On non-macOS systems, restore commands fail fast by default. You can pass `--allow-unsupported-platform` for experiments with explicit paths, but that path is not supported by this release.

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
