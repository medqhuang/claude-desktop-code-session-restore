#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


def default_app_support_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Claude"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude"


DEFAULT_APP_SUPPORT = default_app_support_dir()
DEFAULT_CLAUDE_CONFIG = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
DEFAULT_STATE_ROOT = Path(
    os.environ.get(
        "CLAUDE_DESKTOP_CODE_SESSION_RESTORE_HOME",
        Path.home() / ".claude-desktop-code-session-restore",
    )
)
VERSION = "0.3.0"


@dataclass(frozen=True)
class Profile:
    name: str
    app_support_dir: Path | None = None
    claude_config_dir: Path | None = None


@dataclass(frozen=True)
class IndexDir:
    account_id: str
    workspace_id: str
    path: Path
    source: str


@dataclass
class SessionRecord:
    index_dir: IndexDir
    path: Path
    data: dict

    @property
    def session_id(self) -> str:
        value = self.data.get("sessionId")
        return str(value) if value else self.path.stem

    @property
    def cli_session_id(self) -> str | None:
        value = self.data.get("cliSessionId")
        return str(value) if value else None

    @property
    def is_archived(self) -> bool:
        return bool(self.data.get("isArchived", False))

    @property
    def last_activity(self) -> int:
        for key in ("lastActivityAt", "lastFocusedAt", "createdAt"):
            value = self.data.get(key)
            if isinstance(value, int):
                return value
        return 0

    @property
    def title(self) -> str:
        value = self.data.get("title")
        return str(value) if value else ""


@dataclass
class CliTranscript:
    cli_session_id: str
    path: Path
    cwd: str | None
    title: str
    title_source: str
    created_at: int
    last_activity_at: int
    completed_turns: int
    line_count: int


def expand_path(raw: str | os.PathLike[str]) -> Path:
    return Path(raw).expanduser().resolve()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def timestamp_to_ms(value: object, fallback: int) -> int:
    if isinstance(value, int):
        return value if value > 10_000_000_000 else value * 1000
    if isinstance(value, float):
        raw = int(value)
        return raw if raw > 10_000_000_000 else raw * 1000
    if isinstance(value, str) and value:
        try:
            return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return fallback
    return fallback


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def same_file_content(a: Path, b: Path) -> bool:
    if not a.exists() or not b.exists():
        return False
    if a.stat().st_size != b.stat().st_size:
        return False
    return sha256(a) == sha256(b)


def path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_rel(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return Path(path.name)


def resume_command(cwd: str | None, cli_session_id: str, config_dir: Path | None = None) -> str:
    """Build the shell command that resumes a session in the Claude Code CLI.

    The transcript is the same JSONL the Desktop Code entry points at, so resuming
    is just `claude --resume <cliSessionId>` run from the session's cwd. A non-default
    CLI config dir is passed through CLAUDE_CONFIG_DIR.
    """
    cmd = f"claude --resume {shlex.quote(cli_session_id)}"
    if config_dir is not None:
        cmd = f"CLAUDE_CONFIG_DIR={shlex.quote(str(config_dir))} {cmd}"
    if cwd:
        cmd = f"cd {shlex.quote(cwd)} && {cmd}"
    return cmd


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")


def bridge_config_path(bridge_root: Path) -> Path:
    return bridge_root / "profiles.json"


def load_registered_profiles(bridge_root: Path) -> list[Profile]:
    cfg = bridge_config_path(bridge_root)
    if not cfg.exists():
        return []
    data = load_json(cfg)
    profiles = []
    for name, raw in data.get("profiles", {}).items():
        if not isinstance(raw, dict):
            continue
        app_support = raw.get("app_support_dir")
        claude_config = raw.get("claude_config_dir")
        profiles.append(
            Profile(
                name=str(name),
                app_support_dir=expand_path(app_support) if app_support else None,
                claude_config_dir=expand_path(claude_config) if claude_config else None,
            )
        )
    return profiles


def register_profile(args: argparse.Namespace) -> int:
    bridge_root = expand_path(args.bridge_root)
    cfg = bridge_config_path(bridge_root)
    data = load_json(cfg) if cfg.exists() else {"profiles": {}}
    profiles = data.setdefault("profiles", {})
    profiles[args.name] = {
        "app_support_dir": str(expand_path(args.app_support_dir)),
        "claude_config_dir": str(expand_path(args.claude_config_dir)),
    }
    save_json(cfg, data)
    print(f"registered profile {args.name}: {cfg}")
    return 0


def claude_processes() -> list[str]:
    """Best-effort detection of a live Claude Desktop main process.

    Crashpad helpers are intentionally ignored; they can linger after quit and do
    not write Code session metadata.
    """
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["pgrep", "-fl", "Claude.app/Contents/MacOS/Claude"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return []
        return [
            line
            for line in result.stdout.splitlines()
            if "chrome_crashpad_handler" not in line and "pgrep -fl" not in line
        ]
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Claude.exe"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return []
        return [line for line in result.stdout.splitlines() if line.lower().startswith("claude.exe")]
    return []


def assert_claude_not_running(ignore_running: bool) -> None:
    processes = claude_processes()
    if not processes:
        return
    msg = (
        "Claude Desktop appears to be running. Fully quit it before modifying "
        "Code session metadata, or pass --ignore-running if you know this is safe.\n"
        + "\n".join(f"  {line}" for line in processes)
    )
    if ignore_running:
        print(f"warning: {msg}", file=sys.stderr)
        return
    raise SystemExit(msg)


def owner_account_id(app_support_dir: Path) -> str | None:
    path = app_support_dir / "cowork-enabled-cli-ops.json"
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except Exception:
        return None
    value = data.get("ownerAccountId")
    return str(value) if value else None


def workspace_from_bridge_state(app_support_dir: Path, account_id: str) -> str | None:
    path = app_support_dir / "bridge-state.json"
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except Exception:
        return None
    for key in data:
        if not isinstance(key, str) or ":" not in key:
            continue
        left, right = key.split(":", 1)
        if right == account_id:
            return left
        if left == account_id:
            return right
    return None


def discover_index_dirs(app_support_dirs: list[Path]) -> list[IndexDir]:
    out: list[IndexDir] = []
    seen: set[Path] = set()
    for app_dir in app_support_dirs:
        root = app_dir / "claude-code-sessions"
        if not root.exists():
            continue
        for account_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for workspace_dir in sorted(p for p in account_dir.iterdir() if p.is_dir()):
                real = workspace_dir.resolve()
                if real in seen:
                    continue
                seen.add(real)
                out.append(
                    IndexDir(
                        account_id=account_dir.name,
                        workspace_id=workspace_dir.name,
                        path=workspace_dir,
                        source=str(app_dir),
                    )
                )
    return out


def newest_index_dir(index_dirs: list[IndexDir]) -> IndexDir | None:
    if not index_dirs:
        return None
    return max(index_dirs, key=lambda d: d.path.stat().st_mtime)


def detect_target_index(args: argparse.Namespace, app_support_dir: Path) -> IndexDir:
    if args.target_index:
        target = expand_path(args.target_index)
        parts = target.parts
        account_id = parts[-2] if len(parts) >= 2 else "unknown-account"
        workspace_id = parts[-1] if len(parts) >= 1 else "unknown-workspace"
        return IndexDir(account_id, workspace_id, target, str(app_support_dir))

    root = app_support_dir / "claude-code-sessions"
    account_id = owner_account_id(app_support_dir)
    if account_id:
        workspace_id = workspace_from_bridge_state(app_support_dir, account_id)
        account_dir = root / account_id
        if not workspace_id and account_dir.exists():
            children = [p for p in account_dir.iterdir() if p.is_dir()]
            if children:
                workspace_id = max(children, key=lambda p: p.stat().st_mtime).name
        if workspace_id:
            return IndexDir(
                account_id=account_id,
                workspace_id=workspace_id,
                path=root / account_id / workspace_id,
                source=str(app_support_dir),
            )

    index_dirs = discover_index_dirs([app_support_dir])
    newest = newest_index_dir(index_dirs)
    if newest:
        return newest

    raise SystemExit(
        "No target Claude Code session index found. Log in to the target account, "
        "open Claude Desktop Code, create one blank session, then rerun."
    )


def collect_app_support_dirs(args: argparse.Namespace, target_app_support: Path) -> list[Path]:
    paths = [target_app_support]
    if not args.no_registered_profiles:
        for profile in load_registered_profiles(expand_path(args.bridge_root)):
            if profile.app_support_dir:
                paths.append(profile.app_support_dir)
    for raw in args.source_app_support_dir or []:
        paths.append(expand_path(raw))
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        real = path.resolve()
        if real not in seen:
            seen.add(real)
            deduped.append(path)
    return deduped


def collect_project_roots(args: argparse.Namespace, target_config: Path) -> list[Path]:
    roots = [target_config / "projects"]
    if not args.no_registered_profiles:
        for profile in load_registered_profiles(expand_path(args.bridge_root)):
            if profile.claude_config_dir:
                roots.append(profile.claude_config_dir / "projects")
    for raw in args.source_claude_config_dir or []:
        roots.append(expand_path(raw) / "projects")
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        real = root.resolve()
        if real not in seen:
            seen.add(real)
            deduped.append(root)
    return deduped


def load_sessions(index_dirs: list[IndexDir], include_archived: bool) -> list[SessionRecord]:
    sessions: list[SessionRecord] = []
    for index_dir in index_dirs:
        for path in sorted(index_dir.path.glob("local_*.json")):
            try:
                data = load_json(path)
            except Exception as exc:
                print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
                continue
            record = SessionRecord(index_dir=index_dir, path=path, data=data)
            if record.is_archived and not include_archived:
                continue
            sessions.append(record)
    sessions.sort(key=lambda r: r.last_activity, reverse=True)
    return sessions


def build_transcript_map(project_roots: list[Path]) -> dict[str, Path]:
    by_id: dict[str, Path] = {}
    for root in project_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            by_id.setdefault(path.stem, path)
    return by_id


def parse_cli_transcript(path: Path) -> CliTranscript | None:
    if not path.exists() or path.stat().st_size == 0:
        return None

    fallback_ms = int(path.stat().st_mtime * 1000)
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None
    cwd: str | None = None
    ai_title: str | None = None
    custom_title: str | None = None
    completed_turns = 0
    line_count = 0

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            line_count += 1

            raw_ts = item.get("timestamp")
            ts_ms = timestamp_to_ms(raw_ts, fallback_ms)
            first_seen_ms = ts_ms if first_seen_ms is None else min(first_seen_ms, ts_ms)
            last_seen_ms = ts_ms if last_seen_ms is None else max(last_seen_ms, ts_ms)

            if not cwd and isinstance(item.get("cwd"), str) and item["cwd"]:
                cwd = item["cwd"]

            item_type = item.get("type")
            if item_type == "assistant":
                completed_turns += 1
            elif item_type == "ai-title" and isinstance(item.get("aiTitle"), str) and item["aiTitle"]:
                ai_title = item["aiTitle"]
            elif item_type == "custom-title" and isinstance(item.get("customTitle"), str) and item["customTitle"]:
                custom_title = item["customTitle"]

    if line_count == 0:
        return None

    title = custom_title or ai_title or f"Claude Code session {path.stem[:8]}"
    title_source = "custom" if custom_title else "auto"
    return CliTranscript(
        cli_session_id=path.stem,
        path=path,
        cwd=cwd,
        title=title,
        title_source=title_source,
        created_at=first_seen_ms if first_seen_ms is not None else fallback_ms,
        last_activity_at=last_seen_ms if last_seen_ms is not None else fallback_ms,
        completed_turns=completed_turns,
        line_count=line_count,
    )


def find_transcript(cli_session_id: str, project_roots: list[Path]) -> Path | None:
    for root in project_roots:
        if not root.exists():
            continue
        candidate_matches = list(root.rglob(f"{cli_session_id}.jsonl"))
        if candidate_matches:
            return candidate_matches[0]
    return None


def backup_target_index(target: IndexDir, bridge_root: Path, dry_run: bool) -> Path | None:
    if not target.path.exists():
        return None
    dest = bridge_root / "backups" / stamp() / "target-index" / target.account_id / target.workspace_id
    if dry_run:
        print(f"dry-run: would back up target index {target.path} -> {dest}")
        return dest
    shutil.copytree(target.path, dest, dirs_exist_ok=True)
    return dest


def copy_index_file(record: SessionRecord, target: IndexDir, args: argparse.Namespace) -> str:
    dest = target.path / record.path.name
    existed_before = dest.exists()
    if dest.exists():
        if same_file_content(record.path, dest):
            return "index-exists-identical"
        if not args.overwrite_index:
            return "index-conflict"
    if args.dry_run:
        action = "overwrite" if dest.exists() else "copy"
        print(f"dry-run: would {action} index {record.path} -> {dest}")
        return "index-copied"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(record.path, dest)
    return "index-overwritten" if existed_before else "index-copied"


def copy_transcript(
    record: SessionRecord,
    transcript_map: dict[str, Path],
    project_roots: list[Path],
    target_projects: Path,
    args: argparse.Namespace,
) -> str:
    cli_id = record.cli_session_id
    if not cli_id:
        return "no-cli-session-id"
    src = transcript_map.get(cli_id)
    if not src:
        return "transcript-missing"

    source_root = next((root for root in project_roots if root.exists() and path_is_relative_to(src, root)), None)
    rel = safe_rel(src, source_root) if source_root else Path(src.name)
    dest = target_projects / rel
    existed_before = dest.exists()

    if dest.exists():
        if same_file_content(src, dest):
            return "transcript-exists-identical"
        if not args.overwrite_transcript:
            return "transcript-conflict"

    if args.dry_run:
        action = "overwrite" if dest.exists() else "copy"
        print(f"dry-run: would {action} transcript {src} -> {dest}")
        return "transcript-copied"

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return "transcript-overwritten" if existed_before else "transcript-copied"


def newest_template_record(target: IndexDir) -> SessionRecord:
    sessions = load_sessions([target], include_archived=True)
    if not sessions:
        raise SystemExit(
            "Target index has no local_*.json template. Open Claude Desktop Code, "
            "create one blank session in the target account, fully quit Claude Desktop, then rerun."
        )
    return sessions[0]


def build_desktop_index_from_cli(transcript: CliTranscript, template: SessionRecord) -> tuple[str, dict]:
    local_session_id = f"local_{uuid.uuid4()}"
    cwd = transcript.cwd or str(Path.home())
    record = dict(template.data)
    record.update(
        {
            "sessionId": local_session_id,
            "cliSessionId": transcript.cli_session_id,
            "title": transcript.title,
            "titleSource": transcript.title_source,
            "cwd": cwd,
            "originCwd": cwd,
            "createdAt": transcript.created_at,
            "lastActivityAt": transcript.last_activity_at,
            "lastFocusedAt": transcript.last_activity_at,
            "completedTurns": transcript.completed_turns,
            "isArchived": False,
        }
    )

    # These fields are specific to scheduled/running Desktop tasks and should
    # not be inherited when adopting a plain CLI transcript.
    for key in ("scheduledTaskId", "planPath"):
        record.pop(key, None)
    return local_session_id, record


def copy_cli_transcript_to_target(
    transcript: CliTranscript,
    project_roots: list[Path],
    target_projects: Path,
    args: argparse.Namespace,
) -> str:
    source_root = next(
        (root for root in project_roots if root.exists() and path_is_relative_to(transcript.path, root)),
        None,
    )
    rel = safe_rel(transcript.path, source_root) if source_root else Path(transcript.path.name)
    dest = target_projects / rel
    existed_before = dest.exists()

    if dest.resolve() == transcript.path.resolve():
        return "transcript-already-in-target"
    if dest.exists():
        if same_file_content(transcript.path, dest):
            return "transcript-exists-identical"
        if not args.overwrite_transcript:
            return "transcript-conflict"

    if args.dry_run:
        action = "overwrite" if dest.exists() else "copy"
        print(f"dry-run: would {action} transcript {transcript.path} -> {dest}")
        return "transcript-copied"

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(transcript.path, dest)
    return "transcript-overwritten" if existed_before else "transcript-copied"


def write_adopted_index(
    transcript: CliTranscript,
    target: IndexDir,
    template: SessionRecord,
    args: argparse.Namespace,
) -> str:
    local_session_id, record = build_desktop_index_from_cli(transcript, template)
    dest = target.path / f"{local_session_id}.json"

    if args.dry_run:
        print(f"dry-run: would write index {dest}")
        print(
            "dry-run: "
            f"cliSessionId={transcript.cli_session_id} title={transcript.title!r} "
            f"cwd={transcript.cwd!r} completedTurns={transcript.completed_turns}"
        )
        return "index-written"

    while dest.exists():
        local_session_id, record = build_desktop_index_from_cli(transcript, template)
        dest = target.path / f"{local_session_id}.json"
    save_json(dest, record)
    return "index-written"


def scan(args: argparse.Namespace) -> int:
    target_app = expand_path(args.target_app_support_dir)
    target_config = expand_path(args.target_claude_config_dir)
    app_dirs = collect_app_support_dirs(args, target_app)
    project_roots = collect_project_roots(args, target_config)
    index_dirs = discover_index_dirs(app_dirs)
    target = None
    try:
        target = detect_target_index(args, target_app)
    except SystemExit as exc:
        print(f"target: {exc}")

    print("app support dirs:")
    for path in app_dirs:
        print(f"  - {path}")
    print("project roots:")
    for path in project_roots:
        print(f"  - {path}")
    if target:
        print(f"target index: {target.path}")

    transcript_map = build_transcript_map(project_roots)
    print(f"transcripts found: {len(transcript_map)}")
    print("index dirs:")
    for index in index_dirs:
        sessions = load_sessions([index], include_archived=args.include_archived)
        active = sum(1 for s in sessions if not s.is_archived)
        archived = sum(1 for s in sessions if s.is_archived)
        marker = " (target)" if target and index.path.resolve() == target.path.resolve() else ""
        print(
            f"  - {index.path}{marker}\n"
            f"    account={index.account_id} workspace={index.workspace_id} "
            f"sessions={len(sessions)} active={active} archived={archived}"
        )
    return 0


def snapshot(args: argparse.Namespace) -> int:
    assert_claude_not_running(args.ignore_running)
    target_app = expand_path(args.target_app_support_dir)
    target_config = expand_path(args.target_claude_config_dir)
    bridge_root = expand_path(args.bridge_root)
    name = args.name or f"profile-{stamp()}"
    dest = bridge_root / "pre-switch-backups" / name
    if dest.exists() and not args.force:
        raise SystemExit(f"snapshot destination exists: {dest} (pass --force to overwrite)")

    sessions_src = target_app / "claude-code-sessions"
    projects_src = target_config / "projects"
    if not sessions_src.exists():
        raise SystemExit(f"missing Code session index: {sessions_src}")
    if not projects_src.exists():
        raise SystemExit(f"missing Claude projects transcript directory: {projects_src}")

    if args.dry_run:
        print(f"dry-run: would snapshot {sessions_src} -> {dest / 'claude-code-sessions'}")
        print(f"dry-run: would snapshot {projects_src} -> {dest / 'projects'}")
        if args.register:
            print(f"dry-run: would register profile {name}")
        return 0

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(sessions_src, dest / "claude-code-sessions")
    shutil.copytree(projects_src, dest / "projects")
    print(f"snapshot: {dest}")

    if args.register:
        cfg = bridge_config_path(bridge_root)
        data = load_json(cfg) if cfg.exists() else {"profiles": {}}
        profiles = data.setdefault("profiles", {})
        profiles[name] = {
            "app_support_dir": str(dest),
            "claude_config_dir": str(dest),
        }
        save_json(cfg, data)
        print(f"registered profile {name}: {cfg}")
    return 0


def verify(args: argparse.Namespace) -> int:
    target_app = expand_path(args.target_app_support_dir)
    target_config = expand_path(args.target_claude_config_dir)
    target = detect_target_index(args, target_app)
    project_roots = collect_project_roots(args, target_config)
    sessions = load_sessions([target], include_archived=True)
    if args.session:
        sessions = [s for s in sessions if s.session_id == args.session or s.cli_session_id == args.session]

    ok = 0
    missing_cli = 0
    missing_transcript = 0
    stale = 0
    for record in sessions:
        cli_id = record.cli_session_id
        if not cli_id:
            missing_cli += 1
            print(f"missing cliSessionId: {record.path}")
            continue
        transcript = find_transcript(cli_id, project_roots)
        if not transcript:
            missing_transcript += 1
            print(f"missing transcript: {record.path} cliSessionId={cli_id}")
            continue
        if transcript.stat().st_size == 0:
            stale += 1
            print(f"empty transcript: {transcript}")
            continue
        ok += 1

    total = len(sessions)
    print(f"verified sessions: total={total} ok={ok} missing-cli={missing_cli} missing-transcript={missing_transcript} empty={stale}")
    if missing_cli or missing_transcript or stale:
        return 1
    return 0


def sync(args: argparse.Namespace) -> int:
    assert_claude_not_running(args.ignore_running)
    target_app = expand_path(args.target_app_support_dir)
    target_config = expand_path(args.target_claude_config_dir)
    target_projects = target_config / "projects"
    target = detect_target_index(args, target_app)
    app_dirs = collect_app_support_dirs(args, target_app)
    project_roots = collect_project_roots(args, target_config)
    index_dirs = discover_index_dirs(app_dirs)
    source_dirs = [d for d in index_dirs if d.path.resolve() != target.path.resolve()]
    sessions = load_sessions(source_dirs, include_archived=args.include_archived)
    if args.session:
        sessions = [s for s in sessions if s.session_id == args.session or s.cli_session_id == args.session]
    transcript_map = build_transcript_map(project_roots)
    target_session_ids = {s.session_id for s in load_sessions([target], include_archived=True)}

    print(f"target index: {target.path}")
    print(f"target projects: {target_projects}")
    print(f"source index dirs: {len(source_dirs)}")
    print(f"candidate sessions: {len(sessions)}")

    backup = backup_target_index(target, expand_path(args.bridge_root), args.dry_run)
    if backup:
        print(f"backup: {backup}")

    imported = 0
    already_present = 0
    skipped: dict[str, int] = {}
    seen_session_ids: set[str] = set()

    for record in sessions:
        if record.session_id in target_session_ids:
            already_present += 1
            continue
        if record.session_id in seen_session_ids:
            skipped["duplicate-session-id"] = skipped.get("duplicate-session-id", 0) + 1
            continue
        seen_session_ids.add(record.session_id)

        transcript_status = copy_transcript(record, transcript_map, project_roots, target_projects, args)
        if transcript_status in {"transcript-missing", "no-cli-session-id", "transcript-conflict"} and not args.allow_missing_transcript:
            skipped[transcript_status] = skipped.get(transcript_status, 0) + 1
            continue

        index_status = copy_index_file(record, target, args)
        if index_status == "index-conflict":
            skipped[index_status] = skipped.get(index_status, 0) + 1
            continue

        imported += 1

    print(f"imported sessions: {imported}")
    if already_present:
        print(f"already present in target: {already_present}")
    if skipped:
        print("skipped:")
        for key in sorted(skipped):
            print(f"  {key}: {skipped[key]}")
    if args.dry_run:
        print("dry-run complete; no files were changed")
    else:
        print("sync complete; restart Claude Desktop before checking the Code tab")
    return 0


def adopt_cli(args: argparse.Namespace) -> int:
    if not args.dry_run:
        assert_claude_not_running(args.ignore_running)
    if not args.all and not args.session:
        raise SystemExit("Pass --session <cliSessionId> to adopt specific CLI sessions, or pass --all explicitly.")

    target_app = expand_path(args.target_app_support_dir)
    target_config = expand_path(args.target_claude_config_dir)
    target_projects = target_config / "projects"
    target = detect_target_index(args, target_app)
    project_roots = collect_project_roots(args, target_config)
    transcript_map = build_transcript_map(project_roots)
    target_sessions = load_sessions([target], include_archived=True)
    target_cli_ids = {s.cli_session_id for s in target_sessions if s.cli_session_id}
    template = newest_template_record(target)

    if args.session:
        requested = list(dict.fromkeys(args.session))
        missing = [session_id for session_id in requested if session_id not in transcript_map]
        candidates = [(session_id, transcript_map[session_id]) for session_id in requested if session_id in transcript_map]
    else:
        missing = []
        candidates = sorted(transcript_map.items(), key=lambda item: item[1].stat().st_mtime, reverse=True)
        if args.limit:
            candidates = candidates[: args.limit]

    print(f"target index: {target.path}")
    print(f"target projects: {target_projects}")
    print(f"cli transcripts found: {len(transcript_map)}")
    print(f"candidate transcripts: {len(candidates)}")
    if missing:
        print("missing requested transcripts:")
        for session_id in missing:
            print(f"  {session_id}")

    backup = backup_target_index(target, expand_path(args.bridge_root), args.dry_run)
    if backup:
        print(f"backup: {backup}")

    adopted = 0
    already_indexed = 0
    skipped: dict[str, int] = {}
    for cli_id, path in candidates:
        if cli_id in target_cli_ids:
            already_indexed += 1
            continue

        transcript = parse_cli_transcript(path)
        if transcript is None:
            skipped["empty-or-unreadable-transcript"] = skipped.get("empty-or-unreadable-transcript", 0) + 1
            continue
        if not transcript.cwd and not args.allow_missing_cwd:
            skipped["missing-cwd"] = skipped.get("missing-cwd", 0) + 1
            continue

        transcript_status = copy_cli_transcript_to_target(transcript, project_roots, target_projects, args)
        if transcript_status == "transcript-conflict":
            skipped[transcript_status] = skipped.get(transcript_status, 0) + 1
            continue

        index_status = write_adopted_index(transcript, target, template, args)
        if index_status == "index-written":
            adopted += 1
            target_cli_ids.add(cli_id)

    print(f"adopted cli sessions: {adopted}")
    if already_indexed:
        print(f"already indexed in target: {already_indexed}")
    if skipped:
        print("skipped:")
        for key in sorted(skipped):
            print(f"  {key}: {skipped[key]}")
    if args.dry_run:
        print("dry-run complete; no files were changed")
    else:
        print("adopt-cli complete; restart Claude Desktop before checking the Code tab")

    return 1 if missing else 0


def copy_transcript_to_config(
    transcript_path: Path,
    project_roots: list[Path],
    dest_config: Path,
    args: argparse.Namespace,
) -> str:
    source_root = next(
        (root for root in project_roots if root.exists() and path_is_relative_to(transcript_path, root)),
        None,
    )
    rel = safe_rel(transcript_path, source_root) if source_root else Path(transcript_path.name)
    dest = dest_config / "projects" / rel
    existed_before = dest.exists()

    if dest.resolve() == transcript_path.resolve():
        return "transcript-already-in-target"
    if dest.exists():
        if same_file_content(transcript_path, dest):
            return "transcript-exists-identical"
        if not args.overwrite_transcript:
            return "transcript-conflict"

    if args.dry_run:
        action = "overwrite" if dest.exists() else "copy"
        print(f"  dry-run: would {action} transcript {transcript_path} -> {dest}")
        return "transcript-copied"

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(transcript_path, dest)
    return "transcript-overwritten" if existed_before else "transcript-copied"


def export_cli(args: argparse.Namespace) -> int:
    """Scenario three: turn a Claude Desktop Code session into a CLI resume command.

    The Desktop transcript already lives under ~/.claude/projects, so on the same
    machine/config this is read-only: it looks up cliSessionId + cwd from the Desktop
    index and prints `claude --resume ...`. Pass --to-config-dir to also copy the
    transcript into a different CLI config dir (a separate CLAUDE_CONFIG_DIR/profile).
    """
    target_app = expand_path(args.target_app_support_dir)
    target_config = expand_path(args.target_claude_config_dir)
    target = detect_target_index(args, target_app)
    project_roots = collect_project_roots(args, target_config)

    to_config_dir = expand_path(args.to_config_dir) if args.to_config_dir else None
    copy_requested = to_config_dir is not None and to_config_dir.resolve() != target_config.resolve()

    requested = list(dict.fromkeys(args.session)) if args.session else []
    title_selectors = [t.lower() for t in (args.title or []) if t]
    if not args.all and not requested and not title_selectors:
        raise SystemExit(
            "Pass --session <local id|cliSessionId> and/or --title <substring> to export "
            "specific Desktop Code sessions, or pass --all explicitly."
        )

    sessions = load_sessions([target], include_archived=args.include_archived)

    def is_selected(record: SessionRecord) -> bool:
        if args.all:
            return True
        if record.session_id in requested or (record.cli_session_id and record.cli_session_id in requested):
            return True
        if title_selectors and any(t in record.title.lower() for t in title_selectors):
            return True
        return False

    chosen_all = [r for r in sessions if is_selected(r)]
    matched_ids = {r.session_id for r in chosen_all}
    matched_ids |= {r.cli_session_id for r in chosen_all if r.cli_session_id}
    missing_selectors = [s for s in requested if s not in matched_ids]
    chosen = chosen_all[: args.limit] if args.limit else chosen_all

    print(f"desktop index: {target.path}")
    print("transcript roots:")
    for root in project_roots:
        print(f"  - {root}")
    if copy_requested:
        print(f"copy transcripts into: {to_config_dir / 'projects'}")
    shown = f" (showing {len(chosen)})" if len(chosen) != len(chosen_all) else ""
    print(f"matched sessions: {len(chosen_all)}{shown}")
    if missing_selectors:
        print("no desktop session matched:")
        for sel in missing_selectors:
            print(f"  {sel}")

    ok = 0
    missing_cli = 0
    missing_transcript = 0
    empty = 0
    copied = 0
    copy_conflict = 0
    for record in chosen:
        title = record.title or "(untitled)"
        cli_id = record.cli_session_id
        if not cli_id:
            missing_cli += 1
            print(f"\n- {title}\n  skip: no cliSessionId in {record.path.name}")
            continue
        cwd = record.data.get("cwd") or record.data.get("originCwd")
        transcript = find_transcript(cli_id, project_roots)
        if not transcript:
            missing_transcript += 1
            print(f"\n- {title}\n  skip: no transcript {cli_id}.jsonl under transcript roots")
            continue
        if transcript.stat().st_size == 0:
            empty += 1
            print(f"\n- {title}\n  skip: empty transcript {transcript}")
            continue

        if copy_requested:
            status = copy_transcript_to_config(transcript, project_roots, to_config_dir, args)
            if status == "transcript-conflict":
                copy_conflict += 1
                print(f"\n- {title}\n  skip: transcript already differs in {to_config_dir}; pass --overwrite-transcript")
                continue
            if status in {"transcript-copied", "transcript-overwritten"}:
                copied += 1

        ok += 1
        print(f"\n- {title}")
        print(f"  cliSessionId: {cli_id}")
        print(f"  cwd: {cwd or '(unknown)'}")
        if cwd and not Path(cwd).exists():
            print("  note: cwd path is missing on this machine; recreate it or cd elsewhere before resuming")
        print(f"  resume: {resume_command(cwd, cli_id, to_config_dir if copy_requested else None)}")

    summary = (
        f"\nexport summary: matched={len(chosen_all)} resumable={ok} "
        f"missing-cli={missing_cli} missing-transcript={missing_transcript} empty={empty}"
    )
    if copy_requested:
        summary += f" copied={copied} copy-conflict={copy_conflict}"
    print(summary)
    if args.dry_run and copy_requested:
        print("dry-run complete; no files were changed")
    if missing_selectors or missing_cli or missing_transcript or empty or copy_conflict:
        return 1
    return 0


def self_test(args: argparse.Namespace) -> int:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="cdcsr-self-test-") as raw_tmp:
        tmp = Path(raw_tmp)
        source_app = tmp / "source-app"
        source_config = tmp / "source-config"
        target_app = tmp / "target-app"
        target_config = tmp / "target-config"
        state_root = tmp / "state"
        source_index = source_app / "claude-code-sessions" / "old-account" / "old-workspace"
        target_index = target_app / "claude-code-sessions" / "new-account" / "new-workspace"
        source_project = source_config / "projects" / "-tmp-project"
        cli_only_project = target_config / "projects" / "-tmp-cli-project"
        source_index.mkdir(parents=True)
        target_index.mkdir(parents=True)
        source_project.mkdir(parents=True)
        cli_only_project.mkdir(parents=True)
        (target_app / "cowork-enabled-cli-ops.json").write_text('{"ownerAccountId":"new-account"}\n', encoding="utf-8")
        (target_app / "bridge-state.json").write_text('{"new-workspace:new-account":{}}\n', encoding="utf-8")
        target_template = {
            "sessionId": "local_template-session",
            "cliSessionId": "template-cli-session",
            "title": "Template session",
            "cwd": str(tmp / "target-project"),
            "originCwd": str(tmp / "target-project"),
            "createdAt": 1,
            "lastActivityAt": 1,
            "isArchived": False,
            "model": "claude-test",
            "permissionMode": "ask",
        }
        save_json(target_index / "local_template-session.json", target_template)
        session = {
            "sessionId": "local_test-session",
            "cliSessionId": "cli-test-session",
            "title": "Self test session",
            "cwd": str(tmp / "project"),
            "createdAt": 1,
            "lastActivityAt": 2,
            "isArchived": False,
        }
        save_json(source_index / "local_test-session.json", session)
        (source_project / "cli-test-session.jsonl").write_text(
            '{"type":"ai-title","sessionId":"cli-test-session","aiTitle":"Self test session"}\n',
            encoding="utf-8",
        )
        (cli_only_project / "cli-only-session.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "user",
                            "sessionId": "cli-only-session",
                            "timestamp": "2026-01-01T00:00:00.000Z",
                            "cwd": str(tmp / "cli-project"),
                            "content": "hello",
                        }
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "sessionId": "cli-only-session",
                            "timestamp": "2026-01-01T00:00:01.000Z",
                            "cwd": str(tmp / "cli-project"),
                            "content": "world",
                        }
                    ),
                    json.dumps(
                        {
                            "type": "ai-title",
                            "sessionId": "cli-only-session",
                            "timestamp": "2026-01-01T00:00:02.000Z",
                            "cwd": str(tmp / "cli-project"),
                            "aiTitle": "CLI only self test",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        sync_args = argparse.Namespace(
            bridge_root=str(state_root),
            target_app_support_dir=str(target_app),
            target_claude_config_dir=str(target_config),
            target_index="",
            source_app_support_dir=[str(source_app)],
            source_claude_config_dir=[str(source_config)],
            no_registered_profiles=True,
            include_archived=False,
            dry_run=False,
            allow_missing_transcript=False,
            overwrite_index=False,
            overwrite_transcript=False,
            ignore_running=True,
            session="",
        )
        sync(sync_args)

        expected_index = target_index / "local_test-session.json"
        expected_transcript = target_config / "projects" / "-tmp-project" / "cli-test-session.jsonl"
        if not expected_index.exists():
            raise SystemExit(f"self-test failed: missing {expected_index}")
        if not expected_transcript.exists():
            raise SystemExit(f"self-test failed: missing {expected_transcript}")
        adopt_args = argparse.Namespace(
            bridge_root=str(state_root),
            target_app_support_dir=str(target_app),
            target_claude_config_dir=str(target_config),
            target_index="",
            source_claude_config_dir=[],
            no_registered_profiles=True,
            dry_run=False,
            overwrite_transcript=False,
            ignore_running=True,
            session=["cli-only-session"],
            all=False,
            limit=0,
            allow_missing_cwd=False,
        )
        adopt_cli(adopt_args)
        adopted = [
            load_json(path)
            for path in target_index.glob("local_*.json")
            if load_json(path).get("cliSessionId") == "cli-only-session"
        ]
        if len(adopted) != 1:
            raise SystemExit(f"self-test failed: expected one adopted CLI session, found {len(adopted)}")
        if adopted[0].get("title") != "CLI only self test":
            raise SystemExit("self-test failed: adopted CLI title mismatch")

        # Scenario three: export a Desktop session back into a CLI resume command.
        export_args = argparse.Namespace(
            bridge_root=str(state_root),
            target_app_support_dir=str(target_app),
            target_claude_config_dir=str(target_config),
            target_index="",
            source_app_support_dir=[],
            source_claude_config_dir=[],
            no_registered_profiles=True,
            include_archived=False,
            session=["cli-only-session"],
            title=[],
            all=False,
            limit=0,
            to_config_dir="",
            overwrite_transcript=False,
            dry_run=False,
        )
        if export_cli(export_args) != 0:
            raise SystemExit("self-test failed: export-cli could not resume the adopted CLI session")
        cmd = resume_command(str(tmp / "cli-project"), "cli-only-session", None)
        if "claude --resume cli-only-session" not in cmd or not cmd.startswith("cd "):
            raise SystemExit(f"self-test failed: unexpected resume command: {cmd}")

        print("self-test passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore or adopt Claude Desktop Code session indexes and transcripts on macOS."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument(
        "--state-root",
        dest="bridge_root",
        default=str(DEFAULT_STATE_ROOT),
        metavar="STATE_ROOT",
        help="Directory for restore state, backups, and registered source profiles.",
    )
    parser.add_argument("--bridge-root", dest="bridge_root", help=argparse.SUPPRESS)
    parser.add_argument(
        "--allow-unsupported-platform",
        action="store_true",
        help="Run outside macOS anyway. This is untested; prefer explicit profile paths.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--target-app-support-dir", default=str(DEFAULT_APP_SUPPORT))
        p.add_argument("--target-claude-config-dir", default=str(DEFAULT_CLAUDE_CONFIG))
        p.add_argument("--target-index", default="")
        p.add_argument("--source-app-support-dir", action="append", default=[])
        p.add_argument("--source-claude-config-dir", action="append", default=[])
        p.add_argument("--no-registered-profiles", action="store_true")
        p.add_argument("--include-archived", action="store_true")

    p_scan = sub.add_parser("scan", help="Inspect available Claude Desktop Code indexes.")
    common(p_scan)
    p_scan.set_defaults(func=scan)

    p_sync = sub.add_parser("sync", help="Copy active source Code sessions into the target index.")
    common(p_sync)
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.add_argument("--allow-missing-transcript", action="store_true")
    p_sync.add_argument("--overwrite-index", action="store_true")
    p_sync.add_argument("--overwrite-transcript", action="store_true")
    p_sync.add_argument("--ignore-running", action="store_true")
    p_sync.add_argument("--session", default="", help="Import only this local session id or cliSessionId.")
    p_sync.set_defaults(func=sync)

    p_adopt = sub.add_parser("adopt-cli", help="Make plain Claude Code CLI JSONL sessions appear in Desktop Code.")
    p_adopt.add_argument("--target-app-support-dir", default=str(DEFAULT_APP_SUPPORT))
    p_adopt.add_argument("--target-claude-config-dir", default=str(DEFAULT_CLAUDE_CONFIG))
    p_adopt.add_argument("--target-index", default="")
    p_adopt.add_argument("--source-claude-config-dir", action="append", default=[])
    p_adopt.add_argument("--no-registered-profiles", action="store_true")
    p_adopt.add_argument("--dry-run", action="store_true")
    p_adopt.add_argument("--overwrite-transcript", action="store_true")
    p_adopt.add_argument("--ignore-running", action="store_true")
    p_adopt.add_argument("--session", action="append", default=[], help="Adopt this CLI session id. May be repeated.")
    p_adopt.add_argument("--all", action="store_true", help="Adopt every non-indexed CLI transcript found.")
    p_adopt.add_argument("--limit", type=int, default=0, help="With --all, adopt at most this many newest transcripts.")
    p_adopt.add_argument("--allow-missing-cwd", action="store_true", help="Adopt transcripts without a cwd field.")
    p_adopt.set_defaults(func=adopt_cli)

    p_export = sub.add_parser(
        "export-cli",
        help="Print the Claude Code CLI command to resume a Desktop Code session; optionally copy its transcript into another CLI config dir.",
    )
    common(p_export)
    p_export.add_argument(
        "--session", action="append", default=[], help="Export this Desktop local session id or cliSessionId. May be repeated."
    )
    p_export.add_argument(
        "--title", action="append", default=[], help="Export Desktop sessions whose title contains this substring (case-insensitive). May be repeated."
    )
    p_export.add_argument("--all", action="store_true", help="Export every active Desktop Code session.")
    p_export.add_argument("--limit", type=int, default=0, help="Show at most this many newest matched sessions.")
    p_export.add_argument(
        "--to-config-dir",
        default="",
        help="Copy each transcript into this CLI config dir's projects/ (for a different CLAUDE_CONFIG_DIR or profile).",
    )
    p_export.add_argument("--overwrite-transcript", action="store_true")
    p_export.add_argument("--dry-run", action="store_true")
    p_export.set_defaults(func=export_cli)

    p_verify = sub.add_parser("verify", help="Verify target Code session indexes have matching transcripts.")
    common(p_verify)
    p_verify.add_argument("--session", default="", help="Verify only this local session id or cliSessionId.")
    p_verify.set_defaults(func=verify)

    p_snapshot = sub.add_parser("snapshot", help="Back up the current Claude Desktop Code index and transcripts.")
    p_snapshot.add_argument("--target-app-support-dir", default=str(DEFAULT_APP_SUPPORT))
    p_snapshot.add_argument("--target-claude-config-dir", default=str(DEFAULT_CLAUDE_CONFIG))
    p_snapshot.add_argument("--name", default="")
    p_snapshot.add_argument("--register", action="store_true", help="Register this snapshot as a future source profile.")
    p_snapshot.add_argument("--dry-run", action="store_true")
    p_snapshot.add_argument("--force", action="store_true")
    p_snapshot.add_argument("--ignore-running", action="store_true")
    p_snapshot.set_defaults(func=snapshot)

    p_register = sub.add_parser("register-profile", help="Remember a Claude profile as a future source.")
    p_register.add_argument("--name", required=True)
    p_register.add_argument("--app-support-dir", required=True)
    p_register.add_argument("--claude-config-dir", required=True)
    p_register.set_defaults(func=register_profile)

    p_self_test = sub.add_parser("self-test", help="Run an isolated temp-directory migration test.")
    p_self_test.set_defaults(func=self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if sys.platform != "darwin" and args.cmd != "self-test":
        if not args.allow_unsupported_platform:
            raise SystemExit(
                "This release is macOS-only. Run self-test anywhere, or pass "
                "--allow-unsupported-platform with explicit profile paths if you are intentionally experimenting."
            )
        print(
            "warning: non-macOS execution is unsupported in this release; use explicit profile paths and verify carefully",
            file=sys.stderr,
        )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
