#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
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
DEFAULT_BRIDGE_ROOT = Path.home() / ".claude-code-session-bridge"
VERSION = "0.1.0"


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


def expand_path(raw: str | os.PathLike[str]) -> Path:
    return Path(raw).expanduser().resolve()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


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


def self_test(args: argparse.Namespace) -> int:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ccsb-self-test-") as raw_tmp:
        tmp = Path(raw_tmp)
        source_app = tmp / "source-app"
        source_config = tmp / "source-config"
        target_app = tmp / "target-app"
        target_config = tmp / "target-config"
        bridge_root = tmp / "bridge"
        source_index = source_app / "claude-code-sessions" / "old-account" / "old-workspace"
        target_index = target_app / "claude-code-sessions" / "new-account" / "new-workspace"
        source_project = source_config / "projects" / "-tmp-project"
        source_index.mkdir(parents=True)
        target_index.mkdir(parents=True)
        source_project.mkdir(parents=True)
        (target_app / "cowork-enabled-cli-ops.json").write_text('{"ownerAccountId":"new-account"}\n', encoding="utf-8")
        (target_app / "bridge-state.json").write_text('{"new-workspace:new-account":{}}\n', encoding="utf-8")
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

        sync_args = argparse.Namespace(
            bridge_root=str(bridge_root),
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
        print("self-test passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bridge Claude Desktop Code session indexes and transcripts across accounts."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--bridge-root", default=str(DEFAULT_BRIDGE_ROOT))
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

    p_verify = sub.add_parser("verify", help="Verify target Code session indexes have matching transcripts.")
    common(p_verify)
    p_verify.add_argument("--session", default="", help="Verify only this local session id or cliSessionId.")
    p_verify.set_defaults(func=verify)

    p_snapshot = sub.add_parser("snapshot", help="Back up the current Claude Code index and transcripts.")
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
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
