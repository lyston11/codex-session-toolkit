import io
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(SRC_DIR))

from codex_session_toolkit.paths import CodexPaths  # noqa: E402
from codex_session_toolkit.commands import run_cli  # noqa: E402
from codex_session_toolkit.errors import ToolkitError  # noqa: E402
from codex_session_toolkit.models import BundleSummary  # noqa: E402
from codex_session_toolkit.presenters.reports import print_batch_import_result  # noqa: E402
from codex_session_toolkit.services.backups import delete_session_backup, list_session_backups, restore_session_backup  # noqa: E402
from codex_session_toolkit.services.browse import get_bundle_summaries, get_project_session_summaries, get_session_summaries, validate_bundles  # noqa: E402
from codex_session_toolkit.services.bundle_management import delete_bundle_summaries  # noqa: E402
from codex_session_toolkit.services.clone import clone_to_provider, delete_migrated_original_sessions, list_migrated_original_sessions  # noqa: E402
from codex_session_toolkit.services.exporting import export_active_desktop_all, export_desktop_all, export_project_sessions, export_selected_sessions, export_session  # noqa: E402
from codex_session_toolkit.services.github_sync import (  # noqa: E402
    _conflict_paths,
    _git_proxy_env,
    _git_status_paths,
    _group_bundle_changes,
    _normalize_git_relative_path,
    configure_github_proxy,
    connect_bundles_to_github,
    get_github_sync_status,
    pull_bundles_from_github,
    sync_bundles_to_github,
)
from codex_session_toolkit.services.archived_sessions import delete_archived_sessions  # noqa: E402
from codex_session_toolkit.services.importing import import_desktop_all, import_selected_bundles, import_session  # noqa: E402
from codex_session_toolkit.services.provider import detect_provider  # noqa: E402
from codex_session_toolkit.services.repair import repair_desktop  # noqa: E402
from codex_session_toolkit.services.skills_transfer import delete_local_skill, delete_local_skills, export_skills, import_skill_bundle, list_skill_bundles, list_local_skills  # noqa: E402
from codex_session_toolkit.support import default_local_project_target, iso_to_epoch_ms, machine_label_to_key  # noqa: E402
from codex_session_toolkit.stores import bundles as legacy_bundles  # noqa: E402
from codex_session_toolkit.stores.bundle_scanner import collect_known_bundle_summaries, latest_distinct_bundle_summaries  # noqa: E402
from codex_session_toolkit.stores.desktop_state import (  # noqa: E402
    ensure_sidebar_workspace_visibility,
    ensure_sidebar_thread_state,
    merge_workspace_root,
    pin_desktop_thread_ids,
    promote_desktop_thread_ids_for_sidebar,
    promote_workspace_threads_for_sidebar,
    repair_blank_thread_sources,
)
from codex_session_toolkit.stores.session_files import iter_session_files, read_session_payload  # noqa: E402
from codex_session_toolkit.stores.skills import SkillDescriptor, SkillsManifest, compute_skill_directory_hash, infer_skill_source_root, read_skills_manifest, write_skills_manifest  # noqa: E402
from codex_session_toolkit.validation import load_manifest, write_manifest  # noqa: E402


@contextmanager
def pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextmanager
def env_override(key: str, value: str):
    previous = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def write_config(home: Path, provider: str) -> None:
    code_dir = home / ".codex"
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "config.toml").write_text(f'model_provider = "{provider}"\n', encoding="utf-8")


def write_state_file(home: Path) -> None:
    state_file = home / ".codex" / ".codex-global-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "electron-saved-workspace-roots": [],
                "active-workspace-roots": [],
                "project-order": [],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def create_threads_db(home: Path) -> Path:
    return create_threads_db_file(home / ".codex" / "state_0001.sqlite")


def create_numbered_threads_db(home: Path, version: int) -> Path:
    return create_threads_db_file(home / ".codex" / f"state_{version}.sqlite")


def create_threads_db_file(db_path: Path) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        create table threads (
            id text primary key,
            rollout_path text,
            created_at integer,
            updated_at integer,
            source text,
            model_provider text,
            cwd text,
            title text,
            sandbox_policy text,
            approval_mode text,
            tokens_used integer,
            has_user_event integer,
            archived integer,
            archived_at integer,
            cli_version text,
            first_user_message text,
            memory_mode text,
            model text,
            reasoning_effort text,
            created_at_ms integer,
            updated_at_ms integer,
            thread_source text
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def write_history(home: Path, session_id: str, text: str) -> None:
    history_file = home / ".codex" / "history.jsonl"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"session_id": session_id, "text": text}
    with history_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":")) + "\n")


def write_session(
    home: Path,
    session_id: str,
    *,
    provider: str,
    source: str,
    originator: str,
    cwd: Path,
    archived: bool = False,
    timestamp: str = "2026-04-10T10:00:00Z",
    user_message: str = "",
    explicit_thread_name: str = "",
    include_env_context: bool = False,
) -> Path:
    base = home / ".codex" / ("archived_sessions" if archived else "sessions") / "2026" / "04" / "10"
    base.mkdir(parents=True, exist_ok=True)
    rollout = base / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
    lines = [
        {
            "timestamp": timestamp,
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "model_provider": provider,
                "source": source,
                "originator": originator,
                "cwd": str(cwd),
                "timestamp": timestamp,
                "cli_version": "0.1.0",
            },
        },
    ]
    if include_env_context:
        lines.append(
            {
                "timestamp": "2026-04-10T10:04:30Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "<environment_context>\n  <cwd>/tmp</cwd>\n</environment_context>"}],
                },
            }
        )
    if user_message:
        lines.append(
            {
                "timestamp": "2026-04-10T10:04:45Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_message}],
                },
            }
        )
    if explicit_thread_name:
        lines.append(
            {
                "timestamp": "2026-04-10T10:04:50Z",
                "type": "event_msg",
                "payload": {
                    "type": "thread_name_updated",
                    "thread_id": session_id,
                    "thread_name": explicit_thread_name,
                },
            }
        )
    lines.extend(
        [
            {
                "timestamp": "2026-04-10T10:05:00Z",
                "type": "turn_context",
                "payload": {
                    "sandbox_policy": {"mode": "workspace-write"},
                    "approval_policy": "on-request",
                    "model": "gpt-5",
                    "effort": "medium",
                },
            },
            {
                "timestamp": "2026-04-10T10:06:00Z",
                "type": "message",
                "payload": {"role": "assistant", "text": "reply"},
            },
        ]
    )
    with rollout.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    return rollout


def write_cloned_session(
    home: Path,
    session_id: str,
    *,
    provider: str,
    source: str,
    originator: str,
    cwd: Path,
    cloned_from: str,
    original_provider: str,
) -> Path:
    rollout = write_session(
        home,
        session_id,
        provider=provider,
        source=source,
        originator=originator,
        cwd=cwd,
    )
    records = []
    with rollout.open("r", encoding="utf-8") as fh:
        for raw in fh:
            obj = json.loads(raw)
            if obj.get("type") == "session_meta":
                payload = obj.setdefault("payload", {})
                payload["cloned_from"] = cloned_from
                payload["original_provider"] = original_provider
            records.append(obj)
    with rollout.open("w", encoding="utf-8") as fh:
        for obj in records:
            fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
    return rollout


def write_bundle_manifest(
    bundle_dir: Path,
    *,
    session_id: str,
    relative_path: str = "",
    export_machine: str = "",
    export_machine_key: str = "",
    exported_at: str = "2026-04-11T10:00:00Z",
    updated_at: str = "2026-04-11T10:00:00Z",
    thread_name: str = "",
    first_user_message: str = "",
    session_cwd: str = "",
    session_kind: str = "desktop",
) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = bundle_dir / "manifest.env"
    relative_path = relative_path or f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
    values = {
        "SESSION_ID": session_id,
        "RELATIVE_PATH": relative_path,
        "EXPORTED_AT": exported_at,
        "UPDATED_AT": updated_at,
        "THREAD_NAME": thread_name,
        "FIRST_USER_MESSAGE": first_user_message,
        "SESSION_CWD": session_cwd,
        "SESSION_SOURCE": "vscode",
        "SESSION_ORIGINATOR": "Codex Desktop",
        "SESSION_KIND": session_kind,
    }
    if export_machine:
        values["EXPORT_MACHINE"] = export_machine
    if export_machine_key:
        values["EXPORT_MACHINE_KEY"] = export_machine_key

    with manifest_path.open("w", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={shlex.quote(value)}\n")


def write_bundled_session_file(
    bundle_dir: Path,
    session_id: str,
    *,
    cwd: Path,
    provider: str = "test-provider",
    source: str = "vscode",
    originator: str = "Codex Desktop",
    timestamp: str = "2026-04-10T10:00:00Z",
    user_message: str = "",
    explicit_thread_name: str = "",
) -> Path:
    codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
    codex_dir.mkdir(parents=True, exist_ok=True)
    session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
    lines = [
        {
            "timestamp": timestamp,
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "model_provider": provider,
                "source": source,
                "originator": originator,
                "cwd": str(cwd),
                "timestamp": timestamp,
                "cli_version": "0.1.0",
            },
        }
    ]
    if user_message:
        lines.append(
            {
                "timestamp": "2026-04-10T10:04:45Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_message}],
                },
            }
        )
    if explicit_thread_name:
        lines.append(
            {
                "timestamp": "2026-04-10T10:04:50Z",
                "type": "event_msg",
                "payload": {
                    "type": "thread_name_updated",
                    "thread_id": session_id,
                    "thread_name": explicit_thread_name,
                },
            }
        )
    with session_file.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    return session_file


def write_session_with_skills(
    home: Path,
    session_id: str,
    *,
    provider: str,
    source: str,
    originator: str,
    cwd: Path,
    skill_entries: list,
    archived: bool = False,
    timestamp: str = "2026-04-10T10:00:00Z",
    used_skill_names=None,
) -> Path:
    base = home / ".codex" / ("archived_sessions" if archived else "sessions") / "2026" / "04" / "10"
    base.mkdir(parents=True, exist_ok=True)
    rollout = base / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
    skills_lines = []
    for entry in skill_entries:
        skills_lines.append(f"- {entry['name']}: {entry.get('description', 'A skill')} (file: {entry['file']})")
    skills_block = (
        "<skills_instructions>\n## Skills\n### Available skills\n"
        + "\n".join(skills_lines)
        + "\n### How to use skills\n</skills_instructions>"
    )
    if used_skill_names is None:
        used_skill_names = [entry["name"] for entry in skill_entries]
    usage_text = " ".join(f'Skill(skill="{name}")' for name in used_skill_names)
    lines = [
        {
            "timestamp": timestamp,
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "model_provider": provider,
                "source": source,
                "originator": originator,
                "cwd": str(cwd),
                "timestamp": timestamp,
                "cli_version": "0.1.0",
            },
        },
        {
            "timestamp": "2026-04-10T10:01:00Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [
                    {"type": "input_text", "text": "<permissions instructions>\nallowed"},
                    {"type": "input_text", "text": "<collaboration_mode>\nstandard"},
                    {"type": "input_text", "text": skills_block},
                ],
            },
        },
        {
            "timestamp": "2026-04-10T10:05:00Z",
            "type": "turn_context",
            "payload": {"sandbox_policy": {"mode": "workspace-write"}},
        },
    ]
    if usage_text:
        lines.append(
            {
                "timestamp": "2026-04-10T10:05:30Z",
                "type": "message",
                "payload": {"role": "assistant", "text": usage_text},
            }
        )
    lines.append(
        {
            "timestamp": "2026-04-10T10:06:00Z",
            "type": "message",
            "payload": {"role": "assistant", "text": "reply"},
        }
    )
    with rollout.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    return rollout


def write_test_skill(skills_root: Path, skill_name: str, content: str = "test skill") -> Path:
    skill_dir = skills_root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


class CoreWorkflowTests(unittest.TestCase):
    def test_session_summaries_use_first_meaningful_user_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_config(home, "source-provider")

            session_id = "10101010-1010-1010-1010-101010101010"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=Path("/Users/example/project-a"),
                archived=True,
                user_message="https://github.com/xiaotian2333/newapi-checkin.git 把这个醒目拉下来看看",
                include_env_context=True,
            )

            summaries = get_session_summaries(CodexPaths(home=home))
            self.assertEqual(len(summaries), 1)
            self.assertEqual(
                summaries[0].preview,
                "https://github.com/xiaotian2333/newapi-checkin.git 把这个醒目拉下来看看",
            )

    def test_session_summaries_expose_desktop_thread_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_config(home, "source-provider")
            db_path = create_threads_db(home)

            session_id = "12121212-1212-1212-1212-121212121212"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=Path("/Users/example/project-title"),
                user_message="Long first user prompt that should only be the fallback preview",
            )
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, title, first_user_message) values (?, ?, ?)",
                (
                    session_id,
                    "Desktop short title",
                    "Long first user prompt that should only be the fallback preview",
                ),
            )
            conn.commit()
            conn.close()

            summaries = get_session_summaries(CodexPaths(home=home))

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].thread_name, "Desktop short title")
            self.assertNotEqual(summaries[0].thread_name, summaries[0].preview)

    def test_latest_state_db_uses_numeric_desktop_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            older_db = create_numbered_threads_db(home, 9)
            latest_db = create_numbered_threads_db(home, 10)

            self.assertEqual(CodexPaths(home=home).latest_state_db(), latest_db)
            self.assertLess(latest_db.name, older_db.name)

    def test_iso_to_epoch_ms_preserves_desktop_millisecond_precision(self) -> None:
        self.assertEqual(
            iso_to_epoch_ms("2026-04-10T10:00:00.468Z"),
            1775815200468,
        )

    def test_desktop_workspace_roots_preserve_nested_projects(self) -> None:
        data = {
            "electron-saved-workspace-roots": ["/Users/example/Projects"],
            "active-workspace-roots": ["/Users/example/Projects"],
            "project-order": ["/Users/example/Projects"],
        }

        merge_workspace_root(data, "/Users/example/Projects/demo")
        merge_workspace_root(data, "/Users/example/Projects/demo")

        self.assertEqual(
            data["electron-saved-workspace-roots"],
            ["/Users/example/Projects", "/Users/example/Projects/demo"],
        )
        self.assertEqual(
            data["active-workspace-roots"],
            ["/Users/example/Projects", "/Users/example/Projects/demo"],
        )
        self.assertEqual(
            data["project-order"],
            ["/Users/example/Projects", "/Users/example/Projects/demo"],
        )

    def test_desktop_sidebar_visibility_expands_sections_filter_and_groups(self) -> None:
        data = {
            "electron-persisted-atom-state": {
                "sidebar-collapsed-sections-v1": {
                    "chats": True,
                    "pinned": True,
                    "threads": True,
                    "custom": True,
                },
                "sidebar-workspace-filter-v2": "current",
                "sidebar-collapsed-groups": {
                    "/Users/example/Projects/demo": True,
                    "/Users/example/Projects/other": True,
                },
            }
        }

        changed = ensure_sidebar_workspace_visibility(
            data,
            ["/Users/example/Projects/demo"],
            reset_workspace_filter=True,
        )

        self.assertTrue(changed)
        persisted = data["electron-persisted-atom-state"]
        self.assertEqual(
            persisted["sidebar-collapsed-sections-v1"],
            {
                "chats": False,
                "pinned": False,
                "threads": False,
                "custom": True,
            },
        )
        self.assertEqual(persisted["sidebar-workspace-filter-v2"], "all")
        self.assertEqual(
            persisted["sidebar-collapsed-groups"],
            {"/Users/example/Projects/other": True},
        )

    def test_repair_blank_thread_sources_updates_only_managed_user_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            db_path = create_threads_db(home)
            managed_rollout = home / ".codex" / "sessions" / "2026" / "04" / "10" / "rollout-managed.jsonl"
            outside_rollout = Path(tmpdir) / "outside" / "rollout-outside.jsonl"

            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, rollout_path, source, thread_source) values (?, ?, ?, ?)",
                ("managed", str(managed_rollout), "vscode", ""),
            )
            conn.execute(
                "insert into threads (id, rollout_path, source, thread_source) values (?, ?, ?, ?)",
                ("subagent", str(managed_rollout), '{"parentThreadId":"managed"}', ""),
            )
            conn.execute(
                "insert into threads (id, rollout_path, source, thread_source) values (?, ?, ?, ?)",
                ("outside", str(outside_rollout), "vscode", ""),
            )
            conn.commit()
            conn.close()

            repaired = repair_blank_thread_sources(
                db_path,
                managed_roots=(home / ".codex" / "sessions", home / ".codex" / "archived_sessions"),
            )

            self.assertEqual(repaired, 1)
            conn = sqlite3.connect(db_path)
            rows = conn.execute("select id, thread_source from threads order by id").fetchall()
            conn.close()
            self.assertEqual(
                rows,
                [
                    ("managed", "user"),
                    ("outside", ""),
                    ("subagent", ""),
                ],
            )

    def test_pin_desktop_thread_ids_prepends_unique_ids(self) -> None:
        data = {"pinned-thread-ids": ["existing", "target-a"]}

        pinned = pin_desktop_thread_ids(data, ["target-a", "target-b", "", "target-b"])

        self.assertEqual(pinned, 2)
        self.assertEqual(data["pinned-thread-ids"], ["target-a", "target-b", "existing"])

    def test_ensure_sidebar_thread_state_promotes_project_orders_hints_and_pins(self) -> None:
        data = {
            "electron-saved-workspace-roots": ["/Users/example/Projects"],
            "active-workspace-roots": ["/Users/example/Projects"],
            "project-order": ["/Users/example/Projects"],
            "thread-workspace-root-hints": {"old-thread": "/Users/example/Projects/old"},
            "sidebar-project-thread-orders": {
                "/Users/example/Projects/demo": {"threadIds": ["old-demo", "thread-a"]},
            },
            "pinned-thread-ids": ["old-pinned", "thread-b"],
            "electron-persisted-atom-state": {
                "sidebar-collapsed-sections-v1": {"chats": True, "pinned": True, "threads": True},
                "sidebar-workspace-filter-v2": "current",
                "sidebar-collapsed-groups": {"/Users/example/Projects/demo": True},
            },
        }

        visible_count, pinned_count = ensure_sidebar_thread_state(
            data,
            [
                ("thread-a", "/Users/example/Projects/demo"),
                ("thread-b", "/Users/example/Projects/demo"),
                ("thread-c", "/Users/example/Projects/other"),
                ("thread-a", "/Users/example/Projects/demo"),
            ],
            reset_workspace_filter=True,
            pin_threads=True,
        )

        self.assertEqual(visible_count, 3)
        self.assertEqual(pinned_count, 3)
        self.assertEqual(
            data["active-workspace-roots"],
            [
                "/Users/example/Projects/demo",
                "/Users/example/Projects/other",
                "/Users/example/Projects",
            ],
        )
        self.assertEqual(
            data["project-order"],
            [
                "/Users/example/Projects/demo",
                "/Users/example/Projects/other",
                "/Users/example/Projects",
            ],
        )
        self.assertEqual(data["thread-workspace-root-hints"]["thread-a"], "/Users/example/Projects/demo")
        self.assertEqual(data["thread-workspace-root-hints"]["thread-c"], "/Users/example/Projects/other")
        self.assertEqual(
            data["sidebar-project-thread-orders"]["/Users/example/Projects/demo"]["threadIds"],
            ["thread-a", "thread-b", "old-demo"],
        )
        self.assertEqual(
            data["sidebar-project-thread-orders"]["/Users/example/Projects/other"]["threadIds"],
            ["thread-c"],
        )
        self.assertEqual(data["pinned-thread-ids"], ["thread-a", "thread-b", "thread-c", "old-pinned"])
        persisted = data["electron-persisted-atom-state"]
        self.assertEqual(persisted["sidebar-workspace-filter-v2"], "all")
        self.assertEqual(persisted["sidebar-collapsed-groups"], {})

    def test_promote_workspace_threads_for_sidebar_uses_one_recent_managed_thread_per_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            workspace = Path(tmpdir) / "workspace"
            workspace_a = workspace / "project-a"
            workspace_b = workspace / "project-b"
            workspace_a.mkdir(parents=True)
            workspace_b.mkdir()
            db_path = create_threads_db(home)
            managed_rollout_a1 = home / ".codex" / "sessions" / "2026" / "04" / "10" / "rollout-a1.jsonl"
            managed_rollout_a2 = home / ".codex" / "sessions" / "2026" / "04" / "10" / "rollout-a2.jsonl"
            managed_rollout_b1 = home / ".codex" / "sessions" / "2026" / "04" / "10" / "rollout-b1.jsonl"
            managed_rollout_archived = home / ".codex" / "sessions" / "2026" / "04" / "10" / "rollout-archived.jsonl"
            outside_rollout = Path(tmpdir) / "outside" / "rollout-outside.jsonl"

            conn = sqlite3.connect(db_path)
            rows = [
                ("a-old", managed_rollout_a1, str(workspace_a), 10, 10000, "vscode", 0),
                ("a-new", managed_rollout_a2, str(workspace_a), 20, 20000, "vscode", 0),
                ("b-new", managed_rollout_b1, str(workspace_b), 30, 30000, "vscode", 0),
                ("b-archived", managed_rollout_archived, str(workspace_b), 40, 40000, "vscode", 1),
                ("b-subagent", managed_rollout_b1, str(workspace_b), 50, 50000, '{"parentThreadId":"b-new"}', 0),
                ("outside", outside_rollout, str(workspace_a), 60, 60000, "vscode", 0),
            ]
            for session_id, rollout_path, cwd, updated_at, updated_at_ms, source, archived in rows:
                conn.execute(
                    """
                    insert into threads (
                        id, rollout_path, cwd, updated_at, updated_at_ms, source, archived
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, str(rollout_path), cwd, updated_at, updated_at_ms, source, archived),
                )
            conn.commit()
            conn.close()

            promoted = promote_workspace_threads_for_sidebar(
                db_path,
                [str(workspace_a), str(workspace_b), str(workspace_a)],
                managed_roots=(home / ".codex" / "sessions", home / ".codex" / "archived_sessions"),
                base_updated_at=1000,
            )

            self.assertEqual(promoted, ["a-new", "b-new"])
            conn = sqlite3.connect(db_path)
            updated_rows = conn.execute(
                "select id, updated_at, updated_at_ms from threads where id in ('a-new', 'b-new') order by updated_at desc"
            ).fetchall()
            ignored_rows = conn.execute(
                "select id, updated_at from threads where id in ('b-archived', 'b-subagent', 'outside') order by id"
            ).fetchall()
            conn.close()
            self.assertEqual(updated_rows, [("a-new", 1000, 1000000), ("b-new", 999, 999000)])
            self.assertEqual(ignored_rows, [("b-archived", 40), ("b-subagent", 50), ("outside", 60)])

    def test_promote_desktop_thread_ids_for_sidebar_refreshes_each_managed_active_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            db_path = create_threads_db(home)
            managed_rollout = home / ".codex" / "sessions" / "2026" / "04" / "10" / "rollout-managed.jsonl"
            archived_rollout = home / ".codex" / "archived_sessions" / "2026" / "04" / "10" / "rollout-archived.jsonl"
            outside_rollout = Path(tmpdir) / "outside" / "rollout-outside.jsonl"

            conn = sqlite3.connect(db_path)
            rows = [
                ("managed-a", managed_rollout, 10, 10000, "vscode", 0),
                ("managed-b", managed_rollout, 11, 11000, "vscode", 0),
                ("archived", archived_rollout, 12, 12000, "vscode", 1),
                ("subagent", managed_rollout, 13, 13000, '{"parentThreadId":"managed-a"}', 0),
                ("outside", outside_rollout, 14, 14000, "vscode", 0),
            ]
            for session_id, rollout_path, updated_at, updated_at_ms, source, archived in rows:
                conn.execute(
                    """
                    insert into threads (
                        id, rollout_path, updated_at, updated_at_ms, source, archived
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, str(rollout_path), updated_at, updated_at_ms, source, archived),
                )
            conn.commit()
            conn.close()

            promoted = promote_desktop_thread_ids_for_sidebar(
                db_path,
                ["managed-a", "missing", "managed-b", "managed-a", "archived", "subagent", "outside"],
                managed_roots=(home / ".codex" / "sessions", home / ".codex" / "archived_sessions"),
                base_updated_at=2000,
            )

            self.assertEqual(promoted, ["managed-a", "managed-b"])
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "select id, updated_at, updated_at_ms from threads order by updated_at desc, id"
            ).fetchall()
            conn.close()
            self.assertEqual(
                rows,
                [
                    ("managed-a", 2000, 2000000),
                    ("managed-b", 1999, 1999000),
                    ("outside", 14, 14000),
                    ("subagent", 13, 13000),
                    ("archived", 12, 12000),
                ],
            )

    def test_session_summaries_do_not_full_parse_rollouts_for_list_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_config(home, "source-provider")

            session_id = "15151515-1515-1515-1515-151515151515"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=Path("/Users/example/project-fast"),
                user_message="fast list preview",
            )

            with patch("codex_session_toolkit.stores.session_files.parse_session_file") as full_parse:
                summaries = get_session_summaries(CodexPaths(home=home), limit=20)

            full_parse.assert_not_called()
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].preview, "fast list preview")

    def test_session_summaries_fall_back_to_workspace_name_for_windows_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_config(home, "source-provider")

            session_id = "20202020-2020-2020-2020-202020202020"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=r"C:\Users\Alice\Projects\Cherry-Studio",
                archived=True,
            )

            summaries = get_session_summaries(CodexPaths(home=home))
            self.assertEqual(len(summaries), 1)
            self.assertIn("Cherry-Studio", summaries[0].preview)
            self.assertIn("2026-04-10 10:00", summaries[0].preview)

    def test_connect_bundles_to_github_dry_run_previews_without_git_writes(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            bundle_root = workspace / "codex_bundles"
            workspace.mkdir(exist_ok=True)

            with pushd(workspace):
                result = connect_bundles_to_github(
                    CodexPaths(home=home),
                    "git@github.com:example/codex-bundles.git",
                    dry_run=True,
                )

            self.assertFalse((bundle_root / ".git").exists())
            self.assertTrue(result.initialized_repo)
            self.assertTrue(result.configured_remote)
            self.assertEqual(result.remote_url, "git@github.com:example/codex-bundles.git")

    def test_configure_github_proxy_connects_status_env_and_disconnects(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()

            with pushd(workspace):
                result = configure_github_proxy(CodexPaths(home=home), "127.0.0.1:7890")
                status = get_github_sync_status(CodexPaths(home=home))
                proxy_env = _git_proxy_env(workspace / "codex_bundles")
                disconnect_result = configure_github_proxy(CodexPaths(home=home), disconnect=True)
                disconnected_status = get_github_sync_status(CodexPaths(home=home))

            self.assertTrue(result.initialized_repo)
            self.assertTrue(result.configured_proxy)
            self.assertEqual(result.proxy_url, "http://127.0.0.1:7890")
            self.assertIn("127.0.0.1:7890", result.ssh_proxy_command)
            self.assertTrue(status.proxy_enabled)
            self.assertEqual(status.proxy_url, "http://127.0.0.1:7890")
            self.assertEqual(proxy_env["https_proxy"], "http://127.0.0.1:7890")
            self.assertIn("GIT_SSH_COMMAND", proxy_env)
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(workspace / "codex_bundles"), "config", "--local", "--get", "http.proxy"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                ).stdout.strip(),
                "",
            )
            self.assertTrue(disconnect_result.disconnected)
            self.assertTrue(disconnect_result.cleared_proxy)
            self.assertFalse(disconnected_status.proxy_enabled)
            self.assertEqual(disconnected_status.proxy_url, "")

    def test_github_proxy_before_connect_keeps_target_branch(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "remote.git"
            workspace.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                configure_github_proxy(CodexPaths(home=home), "socks5://127.0.0.1:7890")
                connect_bundles_to_github(CodexPaths(home=home), str(remote), branch="bundle-main")
                status = get_github_sync_status(CodexPaths(home=home))

            self.assertEqual(status.branch, "bundle-main")
            self.assertTrue(status.proxy_enabled)

    def test_github_sync_normalizes_windows_relative_paths(self) -> None:
        raw_status = "\n".join(
            [
                r" M machine-a\sessions\single\20260502\demo-session\manifest.env",
                r"?? machine-a\skills\all\20260502\demo-skill\SKILL.md",
                r"R  machine-a\old\skill.md -> machine-a\skills\all\20260502\renamed\SKILL.md",
                r" A machine-a//notes\note.md",
            ]
        )

        with patch("codex_session_toolkit.services.github_sync._git_output", return_value=raw_status):
            status_paths = _git_status_paths(Path("/bundle"))

        self.assertEqual(
            status_paths,
            [
                "machine-a/notes/note.md",
                "machine-a/sessions/single/20260502/demo-session/manifest.env",
                "machine-a/skills/all/20260502/demo-skill/SKILL.md",
                "machine-a/skills/all/20260502/renamed/SKILL.md",
            ],
        )
        groups = _group_bundle_changes(
            [
                r"machine-a\sessions\single\20260502\demo-session\manifest.env",
                r"/machine-a//skills\all\20260502\demo-skill\SKILL.md",
                r".\machine-a\meta\manifest.json",
            ]
        )
        self.assertEqual(groups.sessions, ["machine-a/sessions/single/20260502/demo-session/manifest.env"])
        self.assertEqual(groups.skills, ["machine-a/skills/all/20260502/demo-skill/SKILL.md"])
        self.assertEqual(groups.other, ["machine-a/meta/manifest.json"])
        self.assertEqual(_normalize_git_relative_path(r"\machine-a//skills\demo\SKILL.md"), "machine-a/skills/demo/SKILL.md")

    def test_github_sync_normalizes_windows_conflict_paths(self) -> None:
        raw_status = "\n".join(
            [
                r"UU machine-a\sessions\single\20260502\demo-session\manifest.env",
                r"AA machine-a\skills\all\20260502\demo-skill\SKILL.md",
                r" M machine-a\notes\note.md",
            ]
        )

        with patch("codex_session_toolkit.services.github_sync._git_output", return_value=raw_status):
            conflict_paths = _conflict_paths(Path("/bundle"))

        self.assertEqual(
            conflict_paths,
            [
                "machine-a/sessions/single/20260502/demo-session/manifest.env",
                "machine-a/skills/all/20260502/demo-skill/SKILL.md",
            ],
        )

    def test_sync_bundles_to_github_initializes_commits_and_pushes(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "remote.git"
            bundle_root = workspace / "codex_bundles"
            session_file = bundle_root / "machine-a" / "sessions" / "single" / "20260502" / "demo-session" / "manifest.env"
            skill_file = bundle_root / "machine-a" / "skills" / "all" / "20260502" / "demo-skill" / "SKILL.md"
            workspace.mkdir()
            session_file.parent.mkdir(parents=True)
            skill_file.parent.mkdir(parents=True)
            session_file.write_text("SESSION_ID=demo\n", encoding="utf-8")
            skill_file.write_text("# demo skill\n", encoding="utf-8")
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                connect_result = connect_bundles_to_github(
                    CodexPaths(home=home),
                    str(remote),
                    branch="main",
                )
                result = sync_bundles_to_github(
                    CodexPaths(home=home),
                    branch="main",
                    message="Sync test bundles",
                )

            self.assertTrue(connect_result.initialized_repo)
            self.assertTrue(connect_result.configured_remote)
            self.assertFalse(result.initialized_repo)
            self.assertFalse(result.configured_remote)
            self.assertEqual(
                result.changed_files,
                [
                    "machine-a/sessions/single/20260502/demo-session/manifest.env",
                    "machine-a/skills/all/20260502/demo-skill/SKILL.md",
                ],
            )
            self.assertEqual(result.session_changed_files, ["machine-a/sessions/single/20260502/demo-session/manifest.env"])
            self.assertEqual(result.skill_changed_files, ["machine-a/skills/all/20260502/demo-skill/SKILL.md"])
            self.assertTrue(result.committed)
            self.assertTrue(result.commit_hash)
            self.assertTrue(result.remote_checked)
            self.assertTrue(result.pushed)
            remote_session_content = subprocess.run(
                ["git", "--git-dir", str(remote), "show", "main:machine-a/sessions/single/20260502/demo-session/manifest.env"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout
            remote_skill_content = subprocess.run(
                ["git", "--git-dir", str(remote), "show", "main:machine-a/skills/all/20260502/demo-skill/SKILL.md"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout
            self.assertEqual(remote_session_content, "SESSION_ID=demo\n")
            self.assertEqual(remote_skill_content, "# demo skill\n")

    def test_connect_github_can_push_after_connect_for_tui_first_sync(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "remote.git"
            bundle_root = workspace / "codex_bundles"
            session_path = "machine-a/sessions/single/20260502/demo-session/manifest.env"
            session_file = bundle_root / session_path
            workspace.mkdir()
            session_file.parent.mkdir(parents=True)
            session_file.write_text("SESSION_ID=demo\n", encoding="utf-8")
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace), redirect_stdout(io.StringIO()):
                exit_code = run_cli(
                    [
                        "connect-github",
                        str(remote),
                        "--branch",
                        "main",
                        "--push-after-connect",
                        "--message",
                        "Initial bundle sync",
                    ],
                    paths=CodexPaths(home=home),
                )

            self.assertEqual(exit_code, 0)
            remote_content = subprocess.run(
                ["git", "--git-dir", str(remote), "show", f"main:{session_path}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout
            self.assertEqual(remote_content, "SESSION_ID=demo\n")

    def test_sync_bundles_to_github_requires_connected_bundle_repo(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            bundle_root = workspace / "codex_bundles"
            bundle_root.mkdir(parents=True)

            with pushd(workspace):
                with self.assertRaises(ToolkitError):
                    sync_bundles_to_github(CodexPaths(home=home), dry_run=True)

    def test_github_sync_status_reports_tui_connection_state(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "remote.git"
            bundle_root = workspace / "codex_bundles"
            session_file = bundle_root / "machine-a" / "sessions" / "single" / "20260502" / "demo-session" / "manifest.env"
            skill_file = bundle_root / "machine-a" / "skills" / "all" / "20260502" / "demo-skill" / "SKILL.md"
            workspace.mkdir()
            session_file.parent.mkdir(parents=True)
            skill_file.parent.mkdir(parents=True)
            session_file.write_text("SESSION_ID=demo\n", encoding="utf-8")
            skill_file.write_text("# demo skill\n", encoding="utf-8")
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                initial_status = get_github_sync_status(CodexPaths(home=home))
                connect_bundles_to_github(CodexPaths(home=home), str(remote), branch="main")
                connected_status = get_github_sync_status(CodexPaths(home=home))

            self.assertFalse(initial_status.is_connected)
            self.assertFalse(initial_status.is_git_repo)
            self.assertTrue(connected_status.is_connected)
            self.assertTrue(connected_status.is_git_repo)
            self.assertFalse(connected_status.remote_checked)
            self.assertEqual(connected_status.remote_url, str(remote))
            self.assertEqual(connected_status.session_changed_files, ["machine-a/sessions/single/20260502/demo-session/manifest.env"])
            self.assertEqual(connected_status.skill_changed_files, ["machine-a/skills/all/20260502/demo-skill/SKILL.md"])

    def test_connect_bundles_to_github_rejects_project_source_remote(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "source.git"
            workspace.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "init"], cwd=workspace, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=workspace, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                with self.assertRaises(ToolkitError):
                    connect_bundles_to_github(CodexPaths(home=home), str(remote))

    def test_sync_bundles_to_github_rejects_project_source_remote(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "source.git"
            bundle_root = workspace / "codex_bundles"
            workspace.mkdir()
            bundle_root.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "init"], cwd=workspace, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=workspace, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "init"], cwd=bundle_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=bundle_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                status = get_github_sync_status(CodexPaths(home=home), check_remote=True)
                with self.assertRaises(ToolkitError):
                    sync_bundles_to_github(CodexPaths(home=home), dry_run=True)

            self.assertFalse(status.is_connected)
            self.assertTrue(status.uses_project_source_remote)
            self.assertEqual(status.project_remote_url, str(remote))

    def test_sync_bundles_to_github_stops_on_remote_conflict(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "remote.git"
            other_clone = Path(tmpdir) / "other"
            bundle_root = workspace / "codex_bundles"
            session_path = "machine-a/sessions/single/20260502/demo-session/manifest.env"
            session_file = bundle_root / session_path
            workspace.mkdir()
            session_file.parent.mkdir(parents=True)
            session_file.write_text("SESSION_ID=v1\n", encoding="utf-8")
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                connect_bundles_to_github(CodexPaths(home=home), str(remote), branch="main")
                first_result = sync_bundles_to_github(CodexPaths(home=home), branch="main", message="Initial bundles")

            self.assertTrue(first_result.pushed)
            subprocess.run(["git", "clone", str(remote), str(other_clone)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            (other_clone / session_path).write_text("SESSION_ID=remote-v2\n", encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "Remote Device"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "config", "user.email", "remote-device@example.local"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "add", "-A"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "commit", "-m", "Remote bundle update"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            session_file.write_text("SESSION_ID=local-v2\n", encoding="utf-8")
            with pushd(workspace):
                result = sync_bundles_to_github(CodexPaths(home=home), branch="main", message="Local bundle update")

            self.assertTrue(result.committed)
            self.assertTrue(result.remote_checked)
            self.assertTrue(result.conflict)
            self.assertFalse(result.pushed)
            self.assertEqual(result.skipped_reason, "merge_conflict")
            self.assertEqual(result.conflict_files, [session_path])
            self.assertEqual(session_file.read_text(encoding="utf-8"), "SESSION_ID=local-v2\n")

    def test_sync_bundles_to_github_merges_non_conflicting_remote_changes(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "remote.git"
            other_clone = Path(tmpdir) / "other"
            bundle_root = workspace / "codex_bundles"
            first_session_path = "machine-a/sessions/single/20260502/session-a/manifest.env"
            second_session_path = "machine-a/sessions/single/20260502/session-b/manifest.env"
            skill_path = "machine-a/skills/all/20260502/demo-skill/SKILL.md"
            first_session_file = bundle_root / first_session_path
            second_session_file = bundle_root / second_session_path
            workspace.mkdir()
            first_session_file.parent.mkdir(parents=True)
            first_session_file.write_text("SESSION_ID=session-a\n", encoding="utf-8")
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                connect_bundles_to_github(CodexPaths(home=home), str(remote), branch="main")
                sync_bundles_to_github(CodexPaths(home=home), branch="main", message="Initial bundles")

            subprocess.run(["git", "clone", str(remote), str(other_clone)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            (other_clone / skill_path).parent.mkdir(parents=True)
            (other_clone / skill_path).write_text("# remote skill\n", encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "Remote Device"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "config", "user.email", "remote-device@example.local"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "add", "-A"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "commit", "-m", "Remote skill update"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            second_session_file.parent.mkdir(parents=True)
            second_session_file.write_text("SESSION_ID=session-b\n", encoding="utf-8")
            with pushd(workspace):
                result = sync_bundles_to_github(CodexPaths(home=home), branch="main", message="Local session update")

            self.assertFalse(result.conflict)
            self.assertTrue(result.merged_remote)
            self.assertTrue(result.pushed)
            remote_skill_content = subprocess.run(
                ["git", "--git-dir", str(remote), "show", f"main:{skill_path}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout
            remote_session_content = subprocess.run(
                ["git", "--git-dir", str(remote), "show", f"main:{second_session_path}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout
            self.assertEqual(remote_skill_content, "# remote skill\n")
            self.assertEqual(remote_session_content, "SESSION_ID=session-b\n")

    def test_pull_bundles_from_github_updates_local_bundle_workspace(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "remote.git"
            other_clone = Path(tmpdir) / "other"
            bundle_root = workspace / "codex_bundles"
            skill_path = "machine-a/skills/all/20260502/demo-skill/SKILL.md"
            skill_file = bundle_root / skill_path
            workspace.mkdir()
            skill_file.parent.mkdir(parents=True)
            skill_file.write_text("# local skill\n", encoding="utf-8")
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                connect_bundles_to_github(CodexPaths(home=home), str(remote), branch="main")
                sync_bundles_to_github(CodexPaths(home=home), branch="main", message="Initial skill bundle")

            subprocess.run(["git", "clone", str(remote), str(other_clone)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            (other_clone / skill_path).write_text("# remote skill v2\n", encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "Remote Device"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "config", "user.email", "remote-device@example.local"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "add", "-A"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "commit", "-m", "Remote skill update"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                status = get_github_sync_status(CodexPaths(home=home), check_remote=True)
                result = pull_bundles_from_github(CodexPaths(home=home), branch="main")

            self.assertTrue(status.remote_checked)
            self.assertTrue(status.remote_branch_exists)
            self.assertEqual(status.remote_ahead_count, 1)
            self.assertTrue(status.remote_updated_at)
            self.assertTrue(result.pulled)
            self.assertFalse(result.conflict)
            self.assertEqual(result.remote_ahead_count, 1)
            self.assertEqual(skill_file.read_text(encoding="utf-8"), "# remote skill v2\n")

    def test_pull_bundles_from_github_blocks_when_local_changes_would_be_overwritten(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git executable is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            remote = Path(tmpdir) / "remote.git"
            other_clone = Path(tmpdir) / "other"
            bundle_root = workspace / "codex_bundles"
            session_path = "machine-a/sessions/single/20260502/demo-session/manifest.env"
            session_file = bundle_root / session_path
            workspace.mkdir()
            session_file.parent.mkdir(parents=True)
            session_file.write_text("SESSION_ID=v1\n", encoding="utf-8")
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            with pushd(workspace):
                connect_bundles_to_github(CodexPaths(home=home), str(remote), branch="main")
                sync_bundles_to_github(CodexPaths(home=home), branch="main", message="Initial bundles")

            subprocess.run(["git", "clone", str(remote), str(other_clone)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            (other_clone / session_path).write_text("SESSION_ID=remote-v2\n", encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "Remote Device"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "config", "user.email", "remote-device@example.local"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "add", "-A"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "commit", "-m", "Remote bundle update"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=other_clone, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            session_file.write_text("SESSION_ID=local-uncommitted\n", encoding="utf-8")
            with pushd(workspace):
                result = pull_bundles_from_github(CodexPaths(home=home), branch="main")

            self.assertFalse(result.pulled)
            self.assertFalse(result.conflict)
            self.assertEqual(result.remote_ahead_count, 1)
            self.assertEqual(result.skipped_reason, "local_changes_block_pull")
            self.assertEqual(result.changed_files, [session_path])
            self.assertEqual(session_file.read_text(encoding="utf-8"), "SESSION_ID=local-uncommitted\n")

    def test_collect_known_bundle_summaries_infers_export_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            paths = CodexPaths(home=home)

            new_single = (
                workspace
                / "codex_bundles"
                / "MacBook-Pro-A"
                / "sessions"
                / "single"
                / "20260411-100000-000001"
                / "aaaa1111-1111-1111-1111-111111111111"
            )
            legacy_cli = (
                workspace
                / "codex_sessions"
                / "bundles"
                / "cli_batches"
                / "20260410-100000-000001"
                / "bbbb2222-2222-2222-2222-222222222222"
            )
            custom_dir = (
                workspace
                / "codex_sessions"
                / "bundles"
                / "manual_drop"
                / "cccc3333-3333-3333-3333-333333333333"
            )
            desktop_active = (
                workspace
                / "codex_bundles"
                / "Studio-Mac"
                / "sessions"
                / "active"
                / "20260411-110000-000001"
                / "dddd4444-4444-4444-4444-444444444444"
            )

            write_bundle_manifest(
                new_single,
                session_id="aaaa1111-1111-1111-1111-111111111111",
                export_machine="MacBook-Pro-A",
                export_machine_key="MacBook-Pro-A",
                thread_name="single export",
            )
            write_bundle_manifest(
                legacy_cli,
                session_id="bbbb2222-2222-2222-2222-222222222222",
                thread_name="legacy batch",
                session_kind="cli",
            )
            write_bundle_manifest(
                custom_dir,
                session_id="cccc3333-3333-3333-3333-333333333333",
                export_machine="Manual-Mac",
                export_machine_key="Manual-Mac",
                thread_name="custom layout",
            )
            write_bundle_manifest(
                desktop_active,
                session_id="dddd4444-4444-4444-4444-444444444444",
                export_machine="Studio-Mac",
                export_machine_key="Studio-Mac",
                thread_name="desktop active",
            )

            with pushd(workspace):
                summaries = collect_known_bundle_summaries(paths, limit=None)
                single_only = collect_known_bundle_summaries(paths, limit=None, export_group_filter="single")

            by_id = {summary.session_id: summary for summary in summaries}
            self.assertEqual(by_id["aaaa1111-1111-1111-1111-111111111111"].export_group, "single")
            self.assertEqual(by_id["aaaa1111-1111-1111-1111-111111111111"].export_group_label, "single")
            self.assertEqual(by_id["bbbb2222-2222-2222-2222-222222222222"].export_group, "cli")
            self.assertEqual(by_id["bbbb2222-2222-2222-2222-222222222222"].export_group_label, "cli")
            self.assertEqual(by_id["cccc3333-3333-3333-3333-333333333333"].export_group, "custom")
            self.assertEqual(by_id["cccc3333-3333-3333-3333-333333333333"].export_group_label, "自定义目录")
            self.assertEqual(by_id["dddd4444-4444-4444-4444-444444444444"].export_group, "active")
            self.assertEqual([item.session_id for item in single_only], ["aaaa1111-1111-1111-1111-111111111111"])

    def test_bundle_scanner_and_legacy_facade_agree_on_skills_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            paths = CodexPaths(home=home)

            bundle_dir = (
                workspace
                / "codex_bundles"
                / "MacBook-Pro-A"
                / "sessions"
                / "single"
                / "20260411-100000-000001"
                / "aaaa1111-1111-1111-1111-111111111111"
            )
            write_bundle_manifest(
                bundle_dir,
                session_id="aaaa1111-1111-1111-1111-111111111111",
                export_machine="MacBook-Pro-A",
                export_machine_key="MacBook-Pro-A",
                thread_name="single export",
            )
            write_skills_manifest(
                SkillsManifest(
                    available_skill_count=2,
                    used_skill_count=1,
                    bundled_skill_count=1,
                    skills=(),
                ),
                bundle_dir,
            )

            with pushd(workspace):
                scanner_summary = collect_known_bundle_summaries(paths, limit=None)[0]
                facade_summary = legacy_bundles.collect_known_bundle_summaries(paths, limit=None)[0]

            self.assertTrue(scanner_summary.has_skills_manifest)
            self.assertEqual(scanner_summary.bundled_skill_count, 1)
            self.assertEqual(scanner_summary.used_skill_count, 1)
            self.assertEqual(
                (
                    scanner_summary.session_id,
                    scanner_summary.export_group,
                    scanner_summary.has_skills_manifest,
                    scanner_summary.bundled_skill_count,
                    scanner_summary.used_skill_count,
                ),
                (
                    facade_summary.session_id,
                    facade_summary.export_group,
                    facade_summary.has_skills_manifest,
                    facade_summary.bundled_skill_count,
                    facade_summary.used_skill_count,
                ),
            )

    def test_latest_distinct_bundle_summaries_keeps_newest_per_machine_and_session(self) -> None:
        rows = [
            BundleSummary(
                source_group="desktop",
                session_id="session-a",
                bundle_dir=Path("/tmp/new"),
                relative_path="sessions/x",
                updated_at="2026-04-11T10:00:00Z",
                exported_at="2026-04-11T10:00:00Z",
                thread_name="new",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-1",
                source_machine_key="machine-1",
            ),
            BundleSummary(
                source_group="desktop",
                session_id="session-a",
                bundle_dir=Path("/tmp/old"),
                relative_path="sessions/x",
                updated_at="2026-04-10T10:00:00Z",
                exported_at="2026-04-10T10:00:00Z",
                thread_name="old",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-1",
                source_machine_key="machine-1",
            ),
            BundleSummary(
                source_group="desktop",
                session_id="session-a",
                bundle_dir=Path("/tmp/other-machine"),
                relative_path="sessions/x",
                updated_at="2026-04-09T10:00:00Z",
                exported_at="2026-04-09T10:00:00Z",
                thread_name="other-machine",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-2",
                source_machine_key="machine-2",
            ),
        ]

        latest = latest_distinct_bundle_summaries(rows)
        self.assertEqual([item.bundle_dir for item in latest], [Path("/tmp/new"), Path("/tmp/other-machine")])

    def test_latest_distinct_bundle_summaries_ignores_root_group_for_same_machine(self) -> None:
        rows = [
            BundleSummary(
                source_group="bundle",
                session_id="session-a",
                bundle_dir=Path("/tmp/single"),
                relative_path="sessions/x",
                updated_at="2026-04-11T09:00:00Z",
                exported_at="2026-04-11T09:00:00Z",
                thread_name="single export",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-1",
                source_machine_key="machine-1",
                export_group="single",
                export_group_label="single",
            ),
            BundleSummary(
                source_group="desktop",
                session_id="session-a",
                bundle_dir=Path("/tmp/desktop-active"),
                relative_path="sessions/x",
                updated_at="2026-04-11T10:00:00Z",
                exported_at="2026-04-11T10:00:00Z",
                thread_name="desktop active",
                session_cwd="/tmp/a",
                session_kind="desktop",
                source_machine="machine-1",
                source_machine_key="machine-1",
                export_group="active",
                export_group_label="active",
            ),
        ]

        latest = latest_distinct_bundle_summaries(rows)
        self.assertEqual([item.bundle_dir for item in latest], [Path("/tmp/desktop-active")])

    def test_delete_bundle_summaries_deletes_only_valid_known_bundle_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            paths = CodexPaths(home=home)
            session_id = "11111111-2222-4333-8444-555555555555"
            bundle_dir = (
                workspace
                / "codex_bundles"
                / "machine-a"
                / "sessions"
                / "single"
                / "20260411-100000"
                / session_id
            )
            write_bundle_manifest(bundle_dir, session_id=session_id)
            outside_dir = Path(tmpdir) / "outside-bundle"
            outside_dir.mkdir()
            write_bundle_manifest(outside_dir, session_id="22222222-3333-4444-8555-666666666666")

            with pushd(workspace):
                summary = collect_known_bundle_summaries(paths, limit=None)[0]
                dry_run = delete_bundle_summaries(paths, [summary], dry_run=True)
                self.assertFalse(dry_run[0].deleted)
                self.assertTrue(bundle_dir.exists())

                deleted = delete_bundle_summaries(paths, [summary])
                self.assertTrue(deleted[0].deleted)
                self.assertFalse(bundle_dir.exists())

                outside_summary = BundleSummary(
                    source_group="bundle",
                    session_id="22222222-3333-4444-8555-666666666666",
                    bundle_dir=outside_dir,
                    relative_path="sessions/x",
                    updated_at="2026-04-11T10:00:00Z",
                    exported_at="2026-04-11T10:00:00Z",
                    thread_name="outside",
                    session_cwd="/tmp/project",
                    session_kind="desktop",
                )
                outside_result = delete_bundle_summaries(paths, [outside_summary])
                self.assertFalse(outside_result[0].deleted)
                self.assertIn("outside known bundle workspaces", outside_result[0].error)
                self.assertTrue(outside_dir.exists())

    def test_clone_to_provider_creates_lineage_preserving_clone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "target-provider")
            original_cwd = workspace / "project-a"
            original_cwd.mkdir()
            original_id = "11111111-1111-1111-1111-111111111111"
            write_session(
                home,
                original_id,
                provider="old-provider",
                source="cli",
                originator="codex_cli_rs",
                cwd=original_cwd,
            )
            write_history(home, original_id, "hello clone")
            paths = CodexPaths(home=home)

            with pushd(workspace):
                result = clone_to_provider(paths)

            self.assertEqual(result.stats["cloned"], 1)
            sessions = list(iter_session_files(paths, active_only=True))
            self.assertEqual(len(sessions), 2)
            cloned_file = next(path for path in sessions if original_id not in path.name)
            cloned_payload = read_session_payload(cloned_file)
            self.assertEqual(cloned_payload["model_provider"], "target-provider")
            self.assertEqual(cloned_payload["cloned_from"], original_id)
            self.assertEqual(cloned_payload["original_provider"], "old-provider")

    def test_delete_migrated_original_sessions_removes_only_lineage_matched_old_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "target-provider")
            write_state_file(home)
            db_path = create_threads_db(home)

            original_id = "11111111-2222-4333-8444-555555555555"
            cloned_id = "22222222-3333-4444-8555-666666666666"
            untouched_old_id = "33333333-4444-4555-8666-777777777777"
            target_native_id = "44444444-5555-4666-8777-888888888888"
            original_file = write_session(
                home,
                original_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                user_message="old provider original",
            )
            cloned_file = write_cloned_session(
                home,
                cloned_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                cloned_from=original_id,
                original_provider="old-provider",
            )
            untouched_old_file = write_session(
                home,
                untouched_old_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
            )
            target_native_file = write_session(
                home,
                target_native_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
            )
            (home / ".codex" / "session_index.jsonl").write_text(
                "\n".join(
                    json.dumps(
                        {
                            "id": session_id,
                            "thread_name": session_id,
                            "updated_at": "2026-04-10T10:06:00Z",
                        },
                        separators=(",", ":"),
                    )
                    for session_id in [original_id, cloned_id, untouched_old_id, target_native_id]
                )
                + "\n",
                encoding="utf-8",
            )

            conn = sqlite3.connect(db_path)
            for session_id, rollout_path, provider in [
                (original_id, original_file, "old-provider"),
                (cloned_id, cloned_file, "target-provider"),
                (untouched_old_id, untouched_old_file, "old-provider"),
                (target_native_id, target_native_file, "target-provider"),
            ]:
                conn.execute(
                    "insert into threads (id, rollout_path, source, model_provider, cwd) values (?, ?, ?, ?, ?)",
                    (session_id, str(rollout_path), "vscode", provider, str(workspace)),
                )
            conn.commit()
            conn.close()

            paths = CodexPaths(home=home)
            candidates = list_migrated_original_sessions(paths)
            self.assertEqual([candidate.session_id for candidate in candidates], [original_id])
            self.assertEqual(candidates[0].cloned_session_id, cloned_id)

            dry_run = delete_migrated_original_sessions(paths, dry_run=True)
            self.assertEqual([candidate.session_id for candidate in dry_run.candidates], [original_id])
            self.assertEqual(dry_run.index_entries_removed, 1)
            self.assertEqual(dry_run.thread_rows_removed, 1)
            self.assertTrue(original_file.exists())

            result = delete_migrated_original_sessions(paths)
            self.assertEqual(result.deleted_files, [original_file])
            self.assertEqual(result.session_ids, [original_id])
            self.assertEqual(result.index_entries_removed, 1)
            self.assertEqual(result.thread_rows_removed, 1)
            self.assertFalse(original_file.exists())
            self.assertTrue(cloned_file.exists())
            self.assertTrue(untouched_old_file.exists())
            self.assertTrue(target_native_file.exists())

            index_ids = [
                json.loads(raw)["id"]
                for raw in (home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(index_ids, [cloned_id, untouched_old_id, target_native_id])

            conn = sqlite3.connect(db_path)
            thread_ids = [row[0] for row in conn.execute("select id from threads order by id").fetchall()]
            conn.close()
            self.assertEqual(thread_ids, [cloned_id, untouched_old_id, target_native_id])

    def test_project_session_listing_and_export_grouping(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "source-provider")

            project_root = workspace / "project-a"
            nested_root = project_root / "packages" / "ui"
            other_root = workspace / "project-b"
            project_root.mkdir(parents=True)
            nested_root.mkdir(parents=True)
            other_root.mkdir(parents=True)

            project_session_id = "12341234-1234-1234-1234-123412341234"
            nested_session_id = "23452345-2345-2345-2345-234523452345"
            other_session_id = "34563456-3456-3456-3456-345634563456"

            write_session(
                home,
                project_session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=project_root,
                user_message="project root session",
            )
            write_session(
                home,
                nested_session_id,
                provider="source-provider",
                source="cli",
                originator="codex_cli_rs",
                cwd=nested_root,
                user_message="nested project session",
            )
            write_session(
                home,
                other_session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=other_root,
                user_message="other project session",
            )
            write_history(home, project_session_id, "project root history")
            write_history(home, nested_session_id, "nested project history")
            write_history(home, other_session_id, "other project history")

            paths = CodexPaths(home=home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Studio-Mac"):
                summaries = get_project_session_summaries(paths, project_path=str(project_root), limit=None)
                export_result = export_project_sessions(paths, str(project_root))
                bundle_summaries = get_bundle_summaries(paths, source_group="bundle")

            self.assertEqual(
                [summary.session_id for summary in summaries],
                [nested_session_id, project_session_id],
            )
            self.assertEqual(sorted(export_result.success_ids), sorted([project_session_id, nested_session_id]))
            self.assertEqual(export_result.selection_label, "project-a")
            self.assertEqual(export_result.export_group, "project")
            self.assertIn("project", export_result.export_root.parts)
            self.assertIn("project-a", export_result.export_root.parts)

            by_id = {summary.session_id: summary for summary in bundle_summaries}
            self.assertEqual(by_id[project_session_id].export_group, "project")
            self.assertEqual(by_id[nested_session_id].export_group, "project")
            self.assertEqual(by_id[project_session_id].project_key, "project-a")
            self.assertEqual(by_id[project_session_id].project_label, "project-a")
            self.assertEqual(by_id[project_session_id].project_path, str(project_root))
            self.assertNotIn(other_session_id, by_id)

    def test_default_local_project_target_prefers_exact_path_then_same_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            toolkit_dir = workspace / "codex-session-toolkit"
            sibling_project = workspace / "project-a"
            exact_project = workspace / "exact-project"
            toolkit_dir.mkdir(parents=True)
            sibling_project.mkdir()
            exact_project.mkdir()

            with pushd(toolkit_dir):
                target_path, status = default_local_project_target("exact-project", str(exact_project))
                self.assertEqual((str(Path(target_path).resolve()), status), (str(exact_project.resolve()), "same_path"))

                sibling_target, sibling_status = default_local_project_target("project-a", str(workspace / "missing-project-a"))
                self.assertEqual((str(Path(sibling_target).resolve()), sibling_status), (str(sibling_project.resolve()), "same_name"))

    def test_detect_provider_falls_back_to_latest_desktop_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            db_path = create_threads_db(home)
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, updated_at, model_provider) values (?, ?, ?)",
                ("older", 100, "custom"),
            )
            conn.execute(
                "insert into threads (id, updated_at, model_provider) values (?, ?, ?)",
                ("newer", 200, "account-provider"),
            )
            conn.commit()
            conn.close()

            self.assertEqual(detect_provider(CodexPaths(home=home)), "account-provider")

    def test_detect_provider_falls_back_to_latest_session_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_session(
                home,
                "30303030-3030-3030-3030-303030303030",
                provider="session-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=Path("/Users/example/project-a"),
            )

            self.assertEqual(detect_provider(CodexPaths(home=home)), "session-provider")

    def test_import_desktop_all_filters_project_and_remaps_cwd_to_target_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            source_project = workspace / "source-project"
            nested_project = source_project / "packages" / "ui"
            other_project = workspace / "other-project"
            target_project = workspace / "local-project"
            nested_project.mkdir(parents=True)
            other_project.mkdir()

            source_root_session = "45674567-4567-4567-4567-456745674567"
            source_nested_session = "56785678-5678-5678-5678-567856785678"
            other_session = "67896789-6789-6789-6789-678967896789"

            write_session(
                src_home,
                source_root_session,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=source_project,
                timestamp="2026-04-10T10:00:00Z",
            )
            write_history(src_home, source_root_session, "source root history")
            write_session(
                src_home,
                source_nested_session,
                provider="source-provider",
                source="cli",
                originator="codex_cli_rs",
                cwd=nested_project,
                timestamp="2026-04-10T10:05:00Z",
            )
            write_history(src_home, source_nested_session, "source nested history")
            write_session(
                src_home,
                other_session,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=other_project,
                timestamp="2026-04-10T10:10:00Z",
            )
            write_history(src_home, other_session, "other project history")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Work-Laptop"):
                export_project_sessions(src_paths, str(source_project))
                export_project_sessions(src_paths, str(other_project))

            with pushd(workspace):
                result = import_desktop_all(
                    dst_paths,
                    machine_filter=machine_label_to_key("Work-Laptop"),
                    project_filter="source-project",
                    target_project_path=str(target_project),
                    desktop_visible=True,
                )

            self.assertEqual(sorted(path.name for path in result.success_dirs), sorted([source_root_session, source_nested_session]))
            self.assertEqual(result.project_filter, "source-project")
            self.assertEqual(result.project_label, "source-project")
            self.assertEqual(result.project_source_path, str(source_project))
            self.assertEqual(result.target_project_path, str(target_project))
            self.assertTrue(target_project.is_dir())
            self.assertTrue((target_project / "packages" / "ui").is_dir())

            imported_sessions = list(iter_session_files(dst_paths, active_only=False))
            self.assertEqual(len(imported_sessions), 2)
            payload_by_id = {read_session_payload(path)["id"]: read_session_payload(path) for path in imported_sessions}
            self.assertEqual(payload_by_id[source_root_session]["cwd"], str(target_project))
            self.assertEqual(payload_by_id[source_nested_session]["cwd"], str(target_project / "packages" / "ui"))
            self.assertNotIn(other_session, payload_by_id)

            state_data = json.loads((dst_home / ".codex" / ".codex-global-state.json").read_text(encoding="utf-8"))
            self.assertIn(str(target_project), state_data["electron-saved-workspace-roots"])
            self.assertIn(str(target_project / "packages" / "ui"), state_data["electron-saved-workspace-roots"])

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            rows = conn.execute("select id, cwd, thread_source from threads order by id").fetchall()
            time_rows = conn.execute(
                "select id, created_at_ms, updated_at_ms from threads order by id"
            ).fetchall()
            conn.close()
            self.assertEqual(
                rows,
                [
                    (source_root_session, str(target_project), "user"),
                    (source_nested_session, str(target_project / "packages" / "ui"), "user"),
                ],
            )
            self.assertEqual([row[0] for row in time_rows], [source_root_session, source_nested_session])
            self.assertTrue(all(isinstance(row[1], int) and row[1] > 0 for row in time_rows))
            self.assertTrue(all(isinstance(row[2], int) and row[2] >= row[1] for row in time_rows))

    def test_import_selected_bundles_supports_multiple_paths_and_project_remap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            source_project = workspace / "source-project"
            nested_project = source_project / "packages" / "ui"
            target_project = workspace / "local-project"
            nested_project.mkdir(parents=True)

            root_session = "11111111-aaaa-4aaa-8aaa-111111111111"
            nested_session = "22222222-bbbb-4bbb-8bbb-222222222222"
            write_session(
                src_home,
                root_session,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=source_project,
                timestamp="2026-04-10T10:00:00Z",
            )
            write_history(src_home, root_session, "root project history")
            write_session(
                src_home,
                nested_session,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=nested_project,
                timestamp="2026-04-10T10:05:00Z",
            )
            write_history(src_home, nested_session, "nested project history")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Work-Laptop"):
                export_result = export_project_sessions(src_paths, str(source_project))

            selected_bundle_dirs = [str(path) for path in sorted(export_result.export_root.iterdir()) if path.is_dir()]
            with pushd(workspace):
                result = import_selected_bundles(
                    dst_paths,
                    selected_bundle_dirs,
                    project_filter="source-project",
                    target_project_path=str(target_project),
                    desktop_visible=True,
                )

            self.assertEqual(sorted(path.name for path in result.success_dirs), [root_session, nested_session])
            self.assertEqual(result.project_filter, "source-project")
            self.assertEqual(result.target_project_path, str(target_project))
            self.assertTrue(target_project.is_dir())
            self.assertTrue((target_project / "packages" / "ui").is_dir())

            imported_sessions = list(iter_session_files(dst_paths, active_only=False))
            payload_by_id = {read_session_payload(path)["id"]: read_session_payload(path) for path in imported_sessions}
            self.assertEqual(payload_by_id[root_session]["cwd"], str(target_project))
            self.assertEqual(payload_by_id[nested_session]["cwd"], str(target_project / "packages" / "ui"))

    def test_export_validate_and_import_roundtrip_updates_desktop_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "22222222-2222-2222-2222-222222222222"
            missing_cwd = workspace / "missing-project"
            (dst_home / ".codex" / ".codex-global-state.json").write_text(
                json.dumps(
                    {
                        "electron-saved-workspace-roots": [str(workspace)],
                        "active-workspace-roots": [str(workspace)],
                        "project-order": [str(workspace)],
                        "electron-persisted-atom-state": {
                            "sidebar-collapsed-sections-v1": {
                                "chats": True,
                                "pinned": True,
                                "threads": True,
                            },
                            "sidebar-workspace-filter-v2": "current",
                            "sidebar-collapsed-groups": {
                                str(missing_cwd): True,
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=missing_cwd,
            )
            write_history(src_home, session_id, "roundtrip bundle")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MacBook-Pro-A"):
                export_result = export_session(src_paths, session_id)
                validation = validate_bundles(src_paths, source_group="bundle")
                summaries = get_bundle_summaries(src_paths, source_group="bundle")
                machine_filtered = get_bundle_summaries(
                    src_paths,
                    source_group="bundle",
                    machine_filter=machine_label_to_key("MacBook-Pro-A"),
                )
                import_result = import_session(dst_paths, str(export_result.bundle_dir), desktop_visible=True)

            self.assertEqual(len(validation.valid_results), 1)
            self.assertEqual(validation.invalid_results, [])
            self.assertEqual(len(summaries), 1)
            self.assertEqual(len(machine_filtered), 1)
            self.assertEqual(summaries[0].source_machine, "MacBook-Pro-A")
            self.assertEqual(summaries[0].source_machine_key, machine_label_to_key("MacBook-Pro-A"))
            self.assertTrue(import_result.created_workspace_dir)
            self.assertTrue(import_result.desktop_registered)
            self.assertTrue(import_result.thread_row_upserted)
            self.assertTrue(missing_cwd.is_dir())

            target_session = dst_home / ".codex" / export_result.relative_path
            self.assertTrue(target_session.is_file())
            self.assertIn(machine_label_to_key("MacBook-Pro-A"), export_result.bundle_dir.parts)

            state_data = json.loads((dst_home / ".codex" / ".codex-global-state.json").read_text(encoding="utf-8"))
            self.assertIn(str(missing_cwd), state_data["electron-saved-workspace-roots"])
            self.assertIn(str(missing_cwd), state_data["active-workspace-roots"])
            self.assertIn(str(missing_cwd), state_data["project-order"])
            persisted_state = state_data["electron-persisted-atom-state"]
            self.assertEqual(
                persisted_state["sidebar-collapsed-sections-v1"],
                {
                    "chats": False,
                    "pinned": False,
                    "threads": False,
                },
            )
            self.assertEqual(persisted_state["sidebar-workspace-filter-v2"], "all")
            self.assertEqual(persisted_state["sidebar-collapsed-groups"], {})

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute(
                "select source, thread_source, model_provider, cwd from threads where id = ?",
                (session_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(row, ("vscode", "user", "target-provider", str(missing_cwd)))

    def test_list_session_backups_finds_import_overwrite_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            target = write_session(
                home,
                session_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                user_message="Backup prompt",
            )
            backup = target.with_name(target.name + ".bak.1770000000")
            shutil.copy2(target, backup)

            summaries = list_session_backups(CodexPaths(home=home))

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].session_id, session_id)
            self.assertEqual(summaries[0].scope, "active")
            self.assertEqual(summaries[0].backup_kind, "import-overwrite")
            self.assertEqual(summaries[0].target_path, target)
            self.assertTrue(summaries[0].target_exists)
            self.assertIn("Backup prompt", summaries[0].preview)

    def test_restore_session_backup_replaces_target_and_saves_current_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            session_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            target = write_session(
                home,
                session_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                user_message="Backup version",
            )
            backup = target.with_name(target.name + ".bak.1770000001")
            shutil.copy2(target, backup)
            write_session(
                home,
                session_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                user_message="Current version",
            )

            result = restore_session_backup(CodexPaths(home=home), str(backup))

            self.assertTrue(result.restored)
            self.assertEqual(result.target_path, target)
            self.assertIsNotNone(result.current_backup_path)
            self.assertTrue(result.current_backup_path.is_file())
            self.assertIn("Backup version", target.read_text(encoding="utf-8"))
            self.assertIn("Current version", result.current_backup_path.read_text(encoding="utf-8"))

    def test_delete_session_backup_removes_only_backup_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            session_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
            target = write_session(
                home,
                session_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                user_message="Current version",
            )
            backup = target.with_name(target.name + ".bak.1770000003")
            shutil.copy2(target, backup)

            result = delete_session_backup(CodexPaths(home=home), str(backup))

            self.assertTrue(result.deleted)
            self.assertFalse(backup.exists())
            self.assertTrue(target.exists())

    def test_delete_session_backup_supports_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            session_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
            target = write_session(
                home,
                session_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
            )
            backup = target.with_name(target.name + ".bak.1770000004")
            shutil.copy2(target, backup)

            result = delete_session_backup(CodexPaths(home=home), str(backup), dry_run=True)

            self.assertFalse(result.deleted)
            self.assertTrue(backup.exists())
            self.assertTrue(target.exists())

    def test_restore_session_backup_rejects_paths_outside_session_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            outside = Path(tmpdir) / "rollout-2026-04-10T10-00-00-cccccccc-cccc-cccc-cccc-cccccccccccc.jsonl.bak.1770000002"
            outside.write_text("{}", encoding="utf-8")

            with self.assertRaises(ToolkitError):
                restore_session_backup(CodexPaths(home=home), str(outside))

    def test_export_session_uses_rollout_prompt_when_history_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()

            session_id = "16161616-1616-1616-1616-161616161616"
            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                user_message="Recovered export title from rollout prompt",
            )

            with pushd(workspace):
                export_result = export_session(CodexPaths(home=src_home), session_id)

            manifest = load_manifest(export_result.bundle_dir / "manifest.env")
            self.assertEqual(manifest["THREAD_NAME"], "Recovered export title from rollout prompt")

    def test_export_session_prefers_desktop_sqlite_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            db_path = create_threads_db(src_home)

            session_id = "18181818-1818-1818-1818-181818181818"
            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                user_message="Long first user prompt that should not become the Desktop title",
            )
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, title, first_user_message) values (?, ?, ?)",
                (
                    session_id,
                    "short desktop title",
                    "Long first user prompt that should not become the Desktop title",
                ),
            )
            conn.commit()
            conn.close()

            with pushd(workspace):
                export_result = export_session(CodexPaths(home=src_home), session_id)

            manifest = load_manifest(export_result.bundle_dir / "manifest.env")
            self.assertEqual(manifest["THREAD_NAME"], "short desktop title")
            self.assertEqual(
                manifest["FIRST_USER_MESSAGE"],
                "Long first user prompt that should not become the Desktop title",
            )

    def test_export_session_uses_thread_name_updated_event_when_sqlite_title_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")

            session_id = "21212121-2121-2121-2121-212121212121"
            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                user_message="Long prompt that should stay first_user_message",
                explicit_thread_name="renamed desktop title",
            )

            with pushd(workspace):
                export_result = export_session(CodexPaths(home=src_home), session_id)

            manifest = load_manifest(export_result.bundle_dir / "manifest.env")
            self.assertEqual(manifest["THREAD_NAME"], "renamed desktop title")
            self.assertEqual(manifest["FIRST_USER_MESSAGE"], "Long prompt that should stay first_user_message")

    def test_import_session_recovers_index_title_from_rollout_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            bundle_root = Path(tmpdir) / "codex_sessions" / "bundle"
            bundle_dir = bundle_root / "17171717-1717-1717-1717-171717171717"
            workspace.mkdir()
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "17171717-1717-1717-1717-171717171717"
            write_bundle_manifest(
                bundle_dir,
                session_id=session_id,
                thread_name="",
                session_cwd=str(workspace),
            )
            write_bundled_session_file(
                bundle_dir,
                session_id,
                cwd=workspace,
                user_message="Recovered import title from rollout prompt",
            )
            index_file = dst_home / ".codex" / "session_index.jsonl"
            index_file.write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": f"Imported {session_id}",
                        "updated_at": "2026-04-10T10:00:00Z",
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            with pushd(Path(tmpdir)):
                import_session(
                    CodexPaths(home=dst_home),
                    session_id,
                    bundle_root=bundle_root,
                    desktop_visible=True,
                )

            repaired_index = json.loads(index_file.read_text(encoding="utf-8"))
            self.assertEqual(repaired_index["thread_name"], "Recovered import title from rollout prompt")

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select title, first_user_message from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(
                row,
                (
                    "Recovered import title from rollout prompt",
                    "Recovered import title from rollout prompt",
                ),
            )

    def test_import_session_preserves_distinct_desktop_title_and_first_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            bundle_root = Path(tmpdir) / "codex_sessions" / "bundle"
            bundle_dir = bundle_root / "19191919-1919-1919-1919-191919191919"
            workspace.mkdir()
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "19191919-1919-1919-1919-191919191919"
            write_bundle_manifest(
                bundle_dir,
                session_id=session_id,
                thread_name="short desktop title",
                first_user_message="Long original prompt from Desktop",
                session_cwd=str(workspace),
            )
            write_bundled_session_file(
                bundle_dir,
                session_id,
                cwd=workspace,
                user_message="Long original prompt from Desktop",
            )

            with pushd(Path(tmpdir)):
                import_session(
                    CodexPaths(home=dst_home),
                    session_id,
                    bundle_root=bundle_root,
                    desktop_visible=True,
                )

            index_obj = json.loads((dst_home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(index_obj["thread_name"], "short desktop title")

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select title, first_user_message from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("short desktop title", "Long original prompt from Desktop"))

    def test_import_session_keeps_existing_desktop_title_when_bundle_title_is_prompt_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            bundle_root = Path(tmpdir) / "codex_sessions" / "bundle"
            bundle_dir = bundle_root / "20202020-2020-2020-2020-202020202020"
            workspace.mkdir()
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            db_path = create_threads_db(dst_home)

            session_id = "20202020-2020-2020-2020-202020202020"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, title, first_user_message) values (?, ?, ?)",
                (
                    session_id,
                    "existing short title",
                    "Long original prompt from Desktop",
                ),
            )
            conn.commit()
            conn.close()

            write_bundle_manifest(
                bundle_dir,
                session_id=session_id,
                thread_name="Long original prompt from Desktop",
                first_user_message="",
                session_cwd=str(workspace),
            )
            write_bundled_session_file(
                bundle_dir,
                session_id,
                cwd=workspace,
                user_message="Long original prompt from Desktop",
            )

            with pushd(Path(tmpdir)):
                import_session(
                    CodexPaths(home=dst_home),
                    session_id,
                    bundle_root=bundle_root,
                    desktop_visible=True,
                )

            index_obj = json.loads((dst_home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(index_obj["thread_name"], "existing short title")

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select title, first_user_message from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("existing short title", "Long original prompt from Desktop"))

    def test_import_session_prefers_thread_name_event_when_existing_title_is_prompt_with_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            bundle_root = Path(tmpdir) / "codex_sessions" / "bundle"
            bundle_dir = bundle_root / "24242424-2424-2424-2424-242424242424"
            workspace.mkdir()
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            db_path = create_threads_db(dst_home)

            session_id = "24242424-2424-2424-2424-242424242424"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, title, first_user_message) values (?, ?, ?)",
                (
                    session_id,
                    "https://example.test/repo.git \nclone it",
                    "https://example.test/repo.git clone it",
                ),
            )
            conn.commit()
            conn.close()

            write_bundle_manifest(
                bundle_dir,
                session_id=session_id,
                thread_name="https://example.test/repo.git clone it",
                first_user_message="https://example.test/repo.git clone it",
                session_cwd=str(workspace),
            )
            write_bundled_session_file(
                bundle_dir,
                session_id,
                cwd=workspace,
                user_message="https://example.test/repo.git clone it",
                explicit_thread_name="repo short title",
            )

            with pushd(Path(tmpdir)):
                import_session(
                    CodexPaths(home=dst_home),
                    session_id,
                    bundle_root=bundle_root,
                    desktop_visible=True,
                )

            conn = sqlite3.connect(db_path)
            row = conn.execute("select title, first_user_message from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("repo short title", "https://example.test/repo.git clone it"))

    def test_import_session_recovers_title_from_thread_name_updated_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            bundle_root = Path(tmpdir) / "codex_sessions" / "bundle"
            bundle_dir = bundle_root / "22222222-2222-2222-2222-222222222222"
            workspace.mkdir()
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "22222222-2222-2222-2222-222222222222"
            write_bundle_manifest(
                bundle_dir,
                session_id=session_id,
                thread_name="",
                session_cwd=str(workspace),
            )
            write_bundled_session_file(
                bundle_dir,
                session_id,
                cwd=workspace,
                user_message="Long original prompt from Desktop",
                explicit_thread_name="short renamed title",
            )

            with pushd(Path(tmpdir)):
                import_session(
                    CodexPaths(home=dst_home),
                    session_id,
                    bundle_root=bundle_root,
                    desktop_visible=True,
                )

            index_obj = json.loads((dst_home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(index_obj["thread_name"], "short renamed title")

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select title, first_user_message from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("short renamed title", "Long original prompt from Desktop"))

    def test_repair_desktop_prefers_thread_name_event_over_prompt_fallback_with_agents_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "target-provider")
            write_state_file(home)
            db_path = create_threads_db(home)

            session_id = "23232323-2323-2323-2323-232323232323"
            write_session(
                home,
                session_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                include_env_context=True,
                user_message="# AGENTS.md instructions for /tmp/project\n<INSTRUCTIONS>\nkeep behavior compatible\n</INSTRUCTIONS>",
                explicit_thread_name="short renamed title",
            )
            index_file = home / ".codex" / "session_index.jsonl"
            index_file.write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": "# AGENTS.md instructions for /tmp/project",
                        "updated_at": "2026-04-10T10:00:00Z",
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, title, first_user_message) values (?, ?, ?)",
                (
                    session_id,
                    "# AGENTS.md instructions for /tmp/project",
                    "# AGENTS.md instructions for /tmp/project",
                ),
            )
            conn.commit()
            conn.close()

            repair_desktop(CodexPaths(home=home))

            index_obj = json.loads(index_file.read_text(encoding="utf-8"))
            self.assertEqual(index_obj["thread_name"], "short renamed title")

            conn = sqlite3.connect(db_path)
            row = conn.execute("select title from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row[0], "short renamed title")

    def test_repair_desktop_prefers_thread_name_event_over_prompt_fallback_with_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "target-provider")
            write_state_file(home)
            db_path = create_threads_db(home)

            session_id = "25252525-2525-2525-2525-252525252525"
            write_session(
                home,
                session_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                user_message="https://example.test/repo.git clone it",
                explicit_thread_name="repo short title",
            )
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, title, first_user_message) values (?, ?, ?)",
                (
                    session_id,
                    "https://example.test/repo.git \nclone it",
                    "https://example.test/repo.git clone it",
                ),
            )
            conn.commit()
            conn.close()

            repair_desktop(CodexPaths(home=home))

            conn = sqlite3.connect(db_path)
            row = conn.execute("select title, first_user_message from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("repo short title", "https://example.test/repo.git clone it"))

    def test_import_session_uses_desktop_thread_provider_when_config_has_no_model_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_state_file(dst_home)
            db_path = create_threads_db(dst_home)
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, updated_at, model_provider) values (?, ?, ?)",
                ("account-thread", 200, "account-provider"),
            )
            conn.commit()
            conn.close()

            session_id = "15151515-1515-1515-1515-151515151515"
            write_session(
                src_home,
                session_id,
                provider="custom",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace / "project-a",
            )

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                export_result = export_session(src_paths, session_id)
                import_result = import_session(dst_paths, str(export_result.bundle_dir), desktop_visible=True)

            target_session = dst_home / ".codex" / import_result.relative_path
            payload = read_session_payload(target_session)
            self.assertEqual(import_result.target_desktop_model_provider, "account-provider")
            self.assertEqual(payload["model_provider"], "account-provider")

    def test_import_session_promotes_imported_thread_even_when_workspace_has_newer_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            db_path = create_threads_db(dst_home)

            workspace_dir = workspace / "project"
            workspace_dir.mkdir()
            imported_session_id = "21212121-2121-4121-8121-212121212121"
            newer_session_id = "22222222-2222-4222-8222-222222222222"
            write_session(
                src_home,
                imported_session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace_dir,
            )
            write_history(src_home, imported_session_id, "imported prompt")

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                insert into threads (
                    id, rollout_path, cwd, updated_at, updated_at_ms, source, archived
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    newer_session_id,
                    str(dst_home / ".codex" / "sessions" / "2026" / "04" / "10" / "rollout-newer.jsonl"),
                    str(workspace_dir),
                    1770000000,
                    1770000000000,
                    "vscode",
                    0,
                ),
            )
            conn.commit()
            conn.close()

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Source-Mac"):
                export_result = export_session(CodexPaths(home=src_home), imported_session_id)
                import_result = import_session(
                    CodexPaths(home=dst_home),
                    str(export_result.bundle_dir),
                    desktop_visible=True,
                )

            self.assertEqual(import_result.desktop_sidebar_promoted_count, 1)
            self.assertEqual(import_result.desktop_pinned_count, 0)
            state_data = json.loads((dst_home / ".codex" / ".codex-global-state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                state_data["thread-workspace-root-hints"][imported_session_id],
                str(workspace_dir),
            )
            self.assertEqual(
                state_data["sidebar-project-thread-orders"][str(workspace_dir)]["threadIds"][0],
                imported_session_id,
            )
            self.assertNotIn(imported_session_id, state_data.get("pinned-thread-ids", []))
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "select id, updated_at, updated_at_ms from threads where id in (?, ?) order by updated_at desc",
                (imported_session_id, newer_session_id),
            ).fetchall()
            conn.close()
            self.assertEqual(rows[0][0], imported_session_id)
            self.assertEqual(rows[1][0], newer_session_id)
            self.assertGreater(rows[0][1], 1770000000)
            self.assertEqual(rows[0][2], rows[0][1] * 1000)

    def test_repair_desktop_rebuilds_index_and_converts_cli_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "repaired-provider")
            write_state_file(home)
            create_threads_db(home)

            desktop_cwd = workspace / "desktop-project"
            cli_cwd = workspace / "cli-project"
            desktop_cwd.mkdir()
            cli_cwd.mkdir()

            desktop_id = "33333333-3333-3333-3333-333333333333"
            cli_id = "44444444-4444-4444-4444-444444444444"
            write_session(
                home,
                desktop_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=desktop_cwd,
            )
            write_session(
                home,
                cli_id,
                provider="old-provider",
                source="cli",
                originator="codex_cli_rs",
                cwd=cli_cwd,
            )
            write_history(home, desktop_id, "desktop message")
            write_history(home, cli_id, "cli message")

            paths = CodexPaths(home=home)
            result = repair_desktop(paths, include_cli=True)

            self.assertEqual(result.desktop_retagged, 2)
            self.assertEqual(result.cli_converted, 1)
            self.assertEqual(result.threads_updated, 2)

            desktop_payload = read_session_payload(
                home / ".codex" / "sessions" / "2026" / "04" / "10" / f"rollout-2026-04-10T10-00-00-{desktop_id}.jsonl"
            )
            cli_payload = read_session_payload(
                home / ".codex" / "sessions" / "2026" / "04" / "10" / f"rollout-2026-04-10T10-00-00-{cli_id}.jsonl"
            )
            self.assertEqual(desktop_payload["model_provider"], "repaired-provider")
            self.assertEqual(cli_payload["model_provider"], "repaired-provider")
            self.assertEqual(cli_payload["source"], "cli")
            self.assertEqual(cli_payload["originator"], "codex_cli_rs")

            index_lines = (home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(index_lines), 2)
            index_by_id = {json.loads(raw)["id"]: json.loads(raw) for raw in index_lines}
            self.assertEqual(index_by_id[desktop_id]["thread_name"], "desktop message")
            self.assertEqual(index_by_id[cli_id]["thread_name"], "cli message")

            state_data = json.loads((home / ".codex" / ".codex-global-state.json").read_text(encoding="utf-8"))
            self.assertIn(str(desktop_cwd), state_data["electron-saved-workspace-roots"])
            self.assertIn(str(cli_cwd), state_data["electron-saved-workspace-roots"])

            conn = sqlite3.connect(home / ".codex" / "state_0001.sqlite")
            rows = conn.execute(
                "select id, model_provider, source, thread_source from threads order by id"
            ).fetchall()
            conn.close()
            self.assertEqual(
                rows,
                [
                    (desktop_id, "repaired-provider", "vscode", "user"),
                    (cli_id, "repaired-provider", "cli", "user"),
                ],
            )

    def test_delete_archived_sessions_removes_rollouts_index_and_desktop_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "provider")
            write_state_file(home)
            db_path = create_threads_db(home)

            active_id = "11111111-2222-4333-8444-555555555555"
            archived_id = "22222222-3333-4444-8555-666666666666"
            other_archived_id = "33333333-4444-4555-8666-777777777777"
            active_file = write_session(
                home,
                active_id,
                provider="provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace / "active-project",
            )
            archived_file = write_session(
                home,
                archived_id,
                provider="provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace / "archived-project",
                archived=True,
            )
            other_archived_file = write_session(
                home,
                other_archived_id,
                provider="provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace / "other-archived-project",
                archived=True,
                timestamp="2026-04-10T10:10:00Z",
            )
            (home / ".codex" / "session_index.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": active_id,
                                "thread_name": "active title",
                                "updated_at": "2026-04-10T10:06:00Z",
                            },
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            {
                                "id": archived_id,
                                "thread_name": "archived title",
                                "updated_at": "2026-04-10T10:06:00Z",
                            },
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            {
                                "id": other_archived_id,
                                "thread_name": "other archived title",
                                "updated_at": "2026-04-10T10:10:00Z",
                            },
                            separators=(",", ":"),
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            conn = sqlite3.connect(db_path)
            for session_id, rollout_path, archived in [
                (active_id, active_file, 0),
                (archived_id, archived_file, 1),
                (other_archived_id, other_archived_file, 1),
            ]:
                conn.execute(
                    """
                    insert into threads (
                        id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                        sandbox_policy, approval_mode, tokens_used, has_user_event, archived, cli_version,
                        first_user_message, memory_mode
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        str(rollout_path),
                        0,
                        0,
                        "vscode",
                        "provider",
                        str(workspace),
                        session_id,
                        "{}",
                        "on-request",
                        0,
                        1,
                        archived,
                        "0.1.0",
                        session_id,
                        "enabled",
                    ),
                )
            conn.commit()
            conn.close()

            paths = CodexPaths(home=home)
            dry_run = delete_archived_sessions(paths, session_ids={archived_id}, dry_run=True)
            self.assertEqual(dry_run.files_to_delete, [archived_file])
            self.assertEqual(dry_run.session_ids, [archived_id])
            self.assertEqual(dry_run.thread_rows_removed, 1)
            self.assertEqual(dry_run.index_entries_removed, 1)
            self.assertTrue(archived_file.exists())
            self.assertTrue(other_archived_file.exists())

            result = delete_archived_sessions(paths, session_ids={archived_id})

            self.assertEqual(result.deleted_files, [archived_file])
            self.assertEqual(result.session_ids, [archived_id])
            self.assertEqual(result.thread_rows_removed, 1)
            self.assertEqual(result.index_entries_removed, 1)
            self.assertFalse(archived_file.exists())
            self.assertTrue(other_archived_file.exists())
            self.assertTrue(active_file.exists())

            index_lines = (home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(raw)["id"] for raw in index_lines], [active_id, other_archived_id])

            conn = sqlite3.connect(db_path)
            rows = conn.execute("select id from threads order by id").fetchall()
            conn.close()
            self.assertEqual(rows, [(active_id,), (other_archived_id,)])

            delete_all_result = delete_archived_sessions(paths)
            self.assertEqual(delete_all_result.deleted_files, [other_archived_file])
            self.assertFalse(other_archived_file.exists())

    def test_delete_archived_sessions_preserves_active_duplicate_session_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "provider")
            write_state_file(home)
            db_path = create_threads_db(home)

            session_id = "44444444-5555-4666-8777-888888888888"
            active_file = write_session(
                home,
                session_id,
                provider="provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace / "active-project",
                user_message="active session prompt",
            )
            archived_file = write_session(
                home,
                session_id,
                provider="provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace / "archived-project",
                archived=True,
                user_message="archived session prompt",
            )
            (home / ".codex" / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": "active title",
                        "updated_at": "2026-04-10T10:06:00Z",
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                insert into threads (
                    id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                    sandbox_policy, approval_mode, tokens_used, has_user_event, archived, archived_at,
                    cli_version, first_user_message, memory_mode
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(archived_file),
                    0,
                    0,
                    "vscode",
                    "provider",
                    str(workspace / "archived-project"),
                    "active title",
                    "{}",
                    "on-request",
                    0,
                    1,
                    1,
                    123,
                    "0.1.0",
                    "active session prompt",
                    "enabled",
                ),
            )
            conn.commit()
            conn.close()

            paths = CodexPaths(home=home)
            dry_run = delete_archived_sessions(paths, session_ids={session_id}, dry_run=True)
            self.assertEqual(dry_run.files_to_delete, [archived_file])
            self.assertEqual(dry_run.index_entries_removed, 0)
            self.assertEqual(dry_run.thread_rows_removed, 0)
            self.assertEqual(dry_run.thread_rows_restored, 1)

            result = delete_archived_sessions(paths, session_ids={session_id})

            self.assertEqual(result.deleted_files, [archived_file])
            self.assertEqual(result.index_entries_removed, 0)
            self.assertEqual(result.thread_rows_removed, 0)
            self.assertEqual(result.thread_rows_restored, 1)
            self.assertFalse(archived_file.exists())
            self.assertTrue(active_file.exists())

            index_lines = (home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(raw)["id"] for raw in index_lines], [session_id])

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "select rollout_path, archived, archived_at from threads where id = ?",
                (session_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(row, (str(active_file), 0, None))

    def test_repair_desktop_repairs_desktop_registered_cli_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "repaired-provider")
            write_state_file(home)
            db_path = create_threads_db(home)

            session_id = "56565656-5656-5656-5656-565656565656"
            session_cwd = workspace / "registered-cli-project"
            session_cwd.mkdir()
            session_file = write_session(
                home,
                session_id,
                provider="old-provider",
                source="cli",
                originator="codex-tui",
                cwd=session_cwd,
                user_message="Repair this desktop-visible thread",
            )
            (home / ".codex" / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": "Repair this desktop-visible thread",
                        "updated_at": "2026-04-10T10:05:00Z",
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                insert into threads (
                    id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                    sandbox_policy, approval_mode, tokens_used, has_user_event, archived, cli_version,
                    first_user_message, memory_mode
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(session_file),
                    0,
                    0,
                    "cli",
                    "old-provider",
                    str(session_cwd),
                    "sub2api",
                    "{}",
                    "on-request",
                    0,
                    1,
                    0,
                    "0.1.0",
                    "sub2api",
                    "enabled",
                ),
            )
            conn.commit()
            conn.close()

            result = repair_desktop(CodexPaths(home=home))
            self.assertEqual(result.desktop_retagged, 1)
            self.assertEqual(result.cli_converted, 0)
            self.assertEqual(result.threads_updated, 1)

            payload = read_session_payload(session_file)
            self.assertEqual(payload["model_provider"], "repaired-provider")
            self.assertEqual(payload["source"], "cli")
            self.assertEqual(payload["originator"], "codex-tui")

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "select source, model_provider, title, first_user_message from threads where id = ?",
                (session_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(
                row,
                (
                    "cli",
                    "repaired-provider",
                    "sub2api",
                    "Repair this desktop-visible thread",
                ),
            )

            repaired_index = json.loads((home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(repaired_index["thread_name"], "sub2api")

    def test_repair_desktop_repairs_orphan_blank_thread_sources_and_sidebar_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "repaired-provider")
            write_state_file(home)
            db_path = create_threads_db(home)

            project_dir = workspace / "visible-project"
            project_dir.mkdir()
            session_id = "78787878-7878-7878-7878-787878787878"
            write_session(
                home,
                session_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=project_dir,
                user_message="Visible project prompt",
            )
            orphan_id = "89898989-8989-8989-8989-898989898989"
            orphan_rollout = (
                home
                / ".codex"
                / "sessions"
                / "2026"
                / "04"
                / "10"
                / f"rollout-2026-04-10T10-00-00-{orphan_id}.jsonl"
            )
            orphan_rollout.parent.mkdir(parents=True, exist_ok=True)
            orphan_rollout.write_text("{invalid json\n", encoding="utf-8")

            state_file = home / ".codex" / ".codex-global-state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "electron-saved-workspace-roots": [str(workspace)],
                        "active-workspace-roots": [str(workspace)],
                        "project-order": [str(workspace)],
                        "electron-persisted-atom-state": {
                            "sidebar-collapsed-sections-v1": {
                                "chats": True,
                                "pinned": True,
                                "threads": True,
                            },
                            "sidebar-workspace-filter-v2": "current",
                            "sidebar-collapsed-groups": {
                                str(project_dir): True,
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )

            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into threads (id, rollout_path, source, thread_source) values (?, ?, ?, ?)",
                (orphan_id, str(orphan_rollout), "vscode", ""),
            )
            conn.commit()
            conn.close()

            result = repair_desktop(CodexPaths(home=home))

            self.assertEqual(result.thread_sources_repaired, 1)
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertIn(str(project_dir), state_data["electron-saved-workspace-roots"])
            self.assertIn(str(project_dir), state_data["active-workspace-roots"])
            self.assertIn(str(project_dir), state_data["project-order"])
            self.assertEqual(state_data["thread-workspace-root-hints"][session_id], str(project_dir))
            self.assertEqual(
                state_data["sidebar-project-thread-orders"][str(project_dir)]["threadIds"][0],
                session_id,
            )
            self.assertNotIn(session_id, state_data.get("pinned-thread-ids", []))
            persisted_state = state_data["electron-persisted-atom-state"]
            self.assertEqual(
                persisted_state["sidebar-collapsed-sections-v1"],
                {
                    "chats": False,
                    "pinned": False,
                    "threads": False,
                },
            )
            self.assertEqual(persisted_state["sidebar-workspace-filter-v2"], "all")
            self.assertEqual(persisted_state["sidebar-collapsed-groups"], {})

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "select thread_source from threads where id = ?",
                (orphan_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(row, ("user",))

    def test_repair_desktop_ignores_thread_rows_outside_codex_session_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "repaired-provider")
            write_state_file(home)
            db_path = create_threads_db(home)

            session_id = "67676767-6767-6767-6767-676767676767"
            session_cwd = workspace / "plain-cli-project"
            session_cwd.mkdir()
            session_file = write_session(
                home,
                session_id,
                provider="old-provider",
                source="cli",
                originator="codex-tui",
                cwd=session_cwd,
                user_message="Leave this as CLI",
            )

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                insert into threads (
                    id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                    sandbox_policy, approval_mode, tokens_used, has_user_event, archived, cli_version,
                    first_user_message, memory_mode
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    "/tmp/not-codex/rollout-2026-04-10T10-00-00-67676767-6767-6767-6767-676767676767.jsonl",
                    0,
                    0,
                    "cli",
                    "old-provider",
                    str(session_cwd),
                    "plain cli",
                    "{}",
                    "on-request",
                    0,
                    1,
                    0,
                    "0.1.0",
                    "plain cli",
                    "enabled",
                ),
            )
            conn.commit()
            conn.close()

            result = repair_desktop(CodexPaths(home=home))
            self.assertEqual(result.desktop_retagged, 0)
            self.assertEqual(result.cli_converted, 0)
            self.assertEqual(result.threads_updated, 0)

            payload = read_session_payload(session_file)
            self.assertEqual(payload["model_provider"], "old-provider")
            self.assertEqual(payload["source"], "cli")
            self.assertEqual(payload["originator"], "codex-tui")

    def test_repair_desktop_recovers_weak_thread_name_from_session_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "repaired-provider")
            write_state_file(home)
            create_threads_db(home)

            session_id = "25252525-2525-2525-2525-252525252525"
            session_cwd = workspace / "named-project"
            session_cwd.mkdir()
            write_session(
                home,
                session_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=session_cwd,
                user_message="Restore this real thread name",
            )
            (home / ".codex" / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": session_id,
                        "updated_at": "2026-04-10T10:00:00Z",
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            repair_desktop(CodexPaths(home=home))

            repaired_index = json.loads((home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(repaired_index["thread_name"], "Restore this real thread name")

            conn = sqlite3.connect(home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select title, first_user_message from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("Restore this real thread name", "Restore this real thread name"))

    def test_repair_desktop_prefers_rollout_prompt_over_stale_history_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "repaired-provider")
            write_state_file(home)
            create_threads_db(home)

            session_id = "26262626-2626-2626-2626-262626262626"
            session_cwd = workspace / "named-project"
            session_cwd.mkdir()
            write_session(
                home,
                session_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=session_cwd,
                user_message="Original rollout title",
            )
            write_history(home, session_id, "Later local resume prompt")
            (home / ".codex" / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": "Later local resume prompt",
                        "updated_at": "2026-04-10T10:00:00Z",
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            repair_desktop(CodexPaths(home=home))

            repaired_index = json.loads((home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(repaired_index["thread_name"], "Original rollout title")

            conn = sqlite3.connect(home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select title, first_user_message from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("Original rollout title", "Original rollout title"))

    def test_repair_desktop_skips_archived_sessions_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "repaired-provider")
            write_state_file(home)
            create_threads_db(home)

            active_id = "35353535-3535-3535-3535-353535353535"
            archived_id = "45454545-4545-4545-4545-454545454545"
            active_cwd = workspace / "active-project"
            archived_cwd = workspace / "archived-project"
            active_cwd.mkdir()
            archived_cwd.mkdir()
            write_session(
                home,
                active_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=active_cwd,
                user_message="active thread",
            )
            write_session(
                home,
                archived_id,
                provider="old-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=archived_cwd,
                archived=True,
                user_message="archived thread",
            )

            paths = CodexPaths(home=home)
            default_result = repair_desktop(paths)
            self.assertEqual(default_result.entries_scanned, 1)
            self.assertFalse(default_result.include_archived)

            index_lines = (home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(raw)["id"] for raw in index_lines], [active_id])
            conn = sqlite3.connect(home / ".codex" / "state_0001.sqlite")
            rows = conn.execute("select id from threads order by id").fetchall()
            conn.close()
            self.assertEqual(rows, [(active_id,)])

            archived_result = repair_desktop(paths, include_archived=True)
            self.assertEqual(archived_result.entries_scanned, 2)
            self.assertTrue(archived_result.include_archived)
            index_ids = {
                json.loads(raw)["id"]
                for raw in (home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            }
            self.assertEqual(index_ids, {active_id, archived_id})
            conn = sqlite3.connect(home / ".codex" / "state_0001.sqlite")
            archived_row = conn.execute("select archived from threads where id = ?", (archived_id,)).fetchone()
            conn.close()
            self.assertEqual(archived_row, (1,))

            cleanup_result = repair_desktop(paths)
            self.assertEqual(cleanup_result.entries_scanned, 1)
            self.assertEqual(cleanup_result.threads_pruned, 1)
            conn = sqlite3.connect(home / ".codex" / "state_0001.sqlite")
            rows = conn.execute("select id, archived from threads order by id").fetchall()
            conn.close()
            self.assertEqual(rows, [(active_id, 0)])

    def test_import_preserves_newer_local_session_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "55555555-5555-5555-5555-555555555555"
            src_cwd = workspace / "src-project"
            dst_cwd = workspace / "dst-project"
            src_cwd.mkdir()
            dst_cwd.mkdir()

            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=src_cwd,
                timestamp="2026-04-10T10:00:00Z",
            )
            write_history(src_home, session_id, "older imported history")

            write_session(
                dst_home,
                session_id,
                provider="target-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=dst_cwd,
                timestamp="2026-04-11T12:00:00Z",
            )
            write_history(dst_home, session_id, "newer local history")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Work-Laptop"):
                export_result = export_session(src_paths, session_id)
                import_result = import_session(dst_paths, str(export_result.bundle_dir), desktop_visible=True)

            self.assertEqual(import_result.rollout_action, "preserved_newer_local")

            target_session = dst_home / ".codex" / export_result.relative_path
            target_payload = read_session_payload(target_session)
            self.assertEqual(target_payload["model_provider"], "target-provider")
            self.assertEqual(target_payload["cwd"], str(dst_cwd))
            self.assertEqual(target_payload["timestamp"], "2026-04-11T12:00:00Z")

            history_lines = (dst_home / ".codex" / "history.jsonl").read_text(encoding="utf-8")
            self.assertIn("older imported history", history_lines)
            self.assertIn("newer local history", history_lines)

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select model_provider, cwd from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("target-provider", str(dst_cwd)))

    def test_repair_desktop_returns_structured_warnings_for_invalid_session_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_config(home, "repaired-provider")
            broken_session = home / ".codex" / "sessions" / "2026" / "04" / "10" / "rollout-2026-04-10T10-00-00-bad.jsonl"
            broken_session.parent.mkdir(parents=True, exist_ok=True)
            broken_session.write_text("NOT JSON\n", encoding="utf-8")

            paths = CodexPaths(home=home)
            result = repair_desktop(paths, dry_run=True)

            self.assertEqual(result.skipped_sessions, [str(broken_session)])
            self.assertTrue(any(warning.code == "skipped_invalid_session_file" for warning in result.warnings))

    def test_import_session_resolves_desktop_bundle_by_session_id_with_machine_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "66666666-6666-6666-6666-666666666666"
            project_dir = workspace / "desktop-project"
            project_dir.mkdir()
            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=project_dir,
            )
            write_history(src_home, session_id, "desktop bundle by session id")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Studio-Mac"):
                export_active_desktop_all(src_paths)
                result = import_session(
                    dst_paths,
                    session_id,
                    source_group="desktop",
                    machine_filter=machine_label_to_key("Studio-Mac"),
                    desktop_visible=True,
                )

            self.assertTrue(result.resolved_from_session_id)
            self.assertIn("active", result.bundle_dir.parts)
            self.assertIn(machine_label_to_key("Studio-Mac"), result.bundle_dir.parts)

    def test_export_session_normalizes_manifest_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "source-provider")

            session_id = "99999999-9999-9999-9999-999999999999"
            project_dir = workspace / "project"
            project_dir.mkdir()
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=project_dir,
            )
            write_history(home, session_id, "normalize manifest path")

            paths = CodexPaths(home=home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Win-Machine"):
                result = export_session(paths, session_id)

            manifest = load_manifest(result.bundle_dir / "manifest.env")
            self.assertEqual(
                manifest["RELATIVE_PATH"],
                f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl",
            )

    def test_export_session_uses_short_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "source-provider")

            session_id = "99999999-1111-4111-8111-999999999999"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
            )
            write_history(home, session_id, "short staging directory")

            mkdtemp_calls = []
            real_mkdtemp = tempfile.mkdtemp

            def capture_mkdtemp(*args, **kwargs):
                mkdtemp_calls.append(kwargs.copy())
                return real_mkdtemp(*args, **kwargs)

            paths = CodexPaths(home=home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Win-Machine"):
                with patch(
                    "codex_session_toolkit.services.exporting.tempfile.mkdtemp",
                    side_effect=capture_mkdtemp,
                ):
                    result = export_session(paths, session_id)

            self.assertEqual(mkdtemp_calls[0]["prefix"], ".tmp.")
            self.assertNotIn(session_id, mkdtemp_calls[0]["prefix"])
            self.assertTrue((result.bundle_dir / "manifest.env").is_file())
            self.assertFalse(any(path.name.startswith(".tmp.") for path in result.bundle_dir.parent.iterdir()))

    def test_export_session_falls_back_when_stage_rename_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "source-provider")

            session_id = "99999999-3333-4333-8333-999999999999"
            write_session(
                home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
            )
            write_history(home, session_id, "rename fallback")

            real_rename = Path.rename

            def rename_side_effect(self, target):
                if self.name.startswith(".tmp."):
                    raise PermissionError("simulated rename denial")
                return real_rename(self, target)

            paths = CodexPaths(home=home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Win-Machine"):
                with patch("pathlib.Path.rename", rename_side_effect):
                    result = export_session(paths, session_id)

            self.assertTrue((result.bundle_dir / "manifest.env").is_file())
            self.assertFalse(any(path.name.startswith(".tmp.") for path in result.bundle_dir.parent.iterdir()))

    def test_write_manifest_keeps_multiline_values_on_one_manifest_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_file = Path(tmpdir) / "manifest.env"

            write_manifest(
                manifest_file,
                {
                    "SESSION_ID": "99999999-2222-4222-8222-999999999999",
                    "RELATIVE_PATH": "sessions/2026/04/10/rollout-2026-04-10T10-00-00-99999999-2222-4222-8222-999999999999.jsonl",
                    "FIRST_USER_MESSAGE": "line one\n\nline two",
                },
            )

            lines = manifest_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum(1 for line in lines if line.startswith("FIRST_USER_MESSAGE=")), 1)
            manifest = load_manifest(manifest_file)
            self.assertEqual(manifest["FIRST_USER_MESSAGE"], "line one  line two")

    def test_infer_skill_source_root_accepts_windows_paths(self) -> None:
        self.assertEqual(
            infer_skill_source_root(r"C:\Users\me\.agents\skills\demo-skill\SKILL.md"),
            ("agents", "demo-skill"),
        )
        self.assertEqual(
            infer_skill_source_root(r"C:\Users\me\.codex\skills\slides\SKILL.md"),
            ("codex", "slides"),
        )

    def test_export_selected_sessions_supports_multiple_targets_and_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            write_config(home, "source-provider")

            first_id = "aaaaaaaa-1111-4111-8111-111111111111"
            second_id = "bbbbbbbb-2222-4222-8222-222222222222"
            third_id = "cccccccc-3333-4333-8333-333333333333"
            for session_id in (first_id, second_id, third_id):
                write_session(
                    home,
                    session_id,
                    provider="source-provider",
                    source="vscode",
                    originator="Codex Desktop",
                    cwd=workspace,
                )
                write_history(home, session_id, f"history {session_id}")

            paths = CodexPaths(home=home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Studio-Mac"):
                dry_run = export_selected_sessions(paths, [first_id, second_id], dry_run=True)
                self.assertEqual(dry_run.session_ids, [first_id, second_id])
                self.assertFalse(dry_run.export_root.exists())

                result = export_selected_sessions(paths, [first_id, second_id])
                self.assertEqual(result.success_ids, [first_id, second_id])
                self.assertTrue((result.export_root / first_id / "manifest.env").is_file())
                self.assertTrue((result.export_root / second_id / "manifest.env").is_file())
                self.assertTrue(result.manifest_file and result.manifest_file.is_file())

                export_all = export_selected_sessions(paths, all_sessions=True)
                self.assertEqual(set(export_all.success_ids), {first_id, second_id, third_id})

    def test_import_and_validate_accept_windows_manifest_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_id = "12121212-3434-5656-7878-909090909090"
            bundle_dir = (
                workspace
                / "codex_sessions"
                / "Windows-PC"
                / "single"
                / "20260411-100000-000001"
                / session_id
            )
            session_rel = Path("sessions/2026/03/19") / f"rollout-2026-03-19T22-00-41-{session_id}.jsonl"
            bundled_session = bundle_dir / "codex" / session_rel
            bundled_session.parent.mkdir(parents=True, exist_ok=True)
            bundled_session.write_text(
                "\n".join([
                    '{"timestamp":"2026-03-19T22:00:41Z","type":"session_meta","payload":{"id":"' + session_id + '","model_provider":"source-provider","source":"vscode","originator":"Codex Desktop","cwd":"' + str(workspace / "project") + '","timestamp":"2026-03-19T22:00:41Z","cli_version":"0.1.0"}}',
                    '{"timestamp":"2026-03-19T22:05:00Z","type":"message","payload":{"role":"assistant","text":"reply"}}',
                ]) + "\n",
                encoding="utf-8",
            )
            (bundle_dir / "history.jsonl").write_text(
                '{"session_id":"' + session_id + '","text":"windows bundle"}\n',
                encoding="utf-8",
            )
            write_bundle_manifest(
                bundle_dir,
                session_id=session_id,
                relative_path=f"sessions\\2026\\03\\19\\rollout-2026-03-19T22-00-41-{session_id}.jsonl",
                export_machine="Windows-PC",
                export_machine_key="Windows-PC",
                session_cwd=str(workspace / "project"),
            )

            paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                validation = validate_bundles(paths)
                self.assertEqual(len(validation.results), 1)
                self.assertTrue(validation.results[0].is_valid, validation.results[0].message)
                result = import_session(paths, str(bundle_dir), desktop_visible=True)

            self.assertEqual(
                result.relative_path,
                f"sessions/2026/03/19/rollout-2026-03-19T22-00-41-{session_id}.jsonl",
            )
            self.assertTrue(
                (
                    dst_home
                    / ".codex"
                    / "sessions"
                    / "2026"
                    / "03"
                    / "19"
                    / f"rollout-2026-03-19T22-00-41-{session_id}.jsonl"
                ).exists()
            )

    def test_import_desktop_all_filters_machine_and_latest_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            other_home = Path(tmpdir) / "other_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(other_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            target_session_id = "77777777-7777-7777-7777-777777777777"
            other_session_id = "88888888-8888-8888-8888-888888888888"
            target_project = workspace / "target-project"
            other_project = workspace / "other-project"
            target_project.mkdir()
            other_project.mkdir()

            write_session(
                src_home,
                target_session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=target_project,
                timestamp="2026-04-10T10:00:00Z",
            )
            write_history(src_home, target_session_id, "older desktop export")

            write_session(
                other_home,
                other_session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=other_project,
            )
            write_history(other_home, other_session_id, "other machine export")

            src_paths = CodexPaths(home=src_home)
            other_paths = CodexPaths(home=other_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Work-Laptop"):
                export_active_desktop_all(src_paths)
                write_session(
                    src_home,
                    target_session_id,
                    provider="source-provider",
                    source="vscode",
                    originator="Codex Desktop",
                    cwd=target_project,
                    timestamp="2026-04-11T12:00:00Z",
                )
                export_active_desktop_all(src_paths)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Office-iMac"):
                export_active_desktop_all(other_paths)

            with pushd(workspace):
                result = import_desktop_all(
                    dst_paths,
                    machine_filter=machine_label_to_key("Work-Laptop"),
                    latest_only=True,
                    desktop_visible=True,
                )

            self.assertEqual(len(result.bundle_dirs), 1)
            self.assertEqual(len(result.success_dirs), 1)
            self.assertEqual(result.machine_filter, machine_label_to_key("Work-Laptop"))
            self.assertEqual(result.machine_label, "Work-Laptop")
            self.assertTrue(result.latest_only)

            imported_payload = read_session_payload(dst_home / ".codex" / "sessions" / "2026" / "04" / "10" / f"rollout-2026-04-10T10-00-00-{target_session_id}.jsonl")
            self.assertEqual(imported_payload["timestamp"], "2026-04-11T12:00:00Z")

            self.assertFalse(
                (dst_home / ".codex" / "sessions" / "2026" / "04" / "10" / f"rollout-2026-04-10T10-00-00-{other_session_id}.jsonl").exists()
            )

    def test_import_desktop_all_makes_archived_bundle_visible_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "source-provider")
            write_config(dst_home, "target-provider")
            write_state_file(dst_home)
            db_path = create_threads_db(dst_home)

            session_id = "99999999-aaaa-4bbb-8ccc-dddddddddddd"
            project = workspace / "archived-project"
            project.mkdir()
            write_session(
                src_home,
                session_id,
                provider="source-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=project,
                archived=True,
                user_message="archived import should become visible",
            )
            write_history(src_home, session_id, "archived visible import")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "Archive-Mac"):
                export_desktop_all(src_paths)

            with pushd(workspace):
                result = import_desktop_all(
                    dst_paths,
                    machine_filter=machine_label_to_key("Archive-Mac"),
                    export_group_filter="desktop",
                    desktop_visible=True,
                )

            active_target = (
                dst_home
                / ".codex"
                / "sessions"
                / "2026"
                / "04"
                / "10"
                / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            )
            archived_target = (
                dst_home
                / ".codex"
                / "archived_sessions"
                / "2026"
                / "04"
                / "10"
                / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            )
            self.assertEqual(len(result.success_dirs), 1)
            self.assertTrue(active_target.is_file())
            self.assertFalse(archived_target.exists())

            index_lines = (dst_home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(raw)["id"] for raw in index_lines], [session_id])

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "select rollout_path, archived, title from threads where id = ?",
                (session_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(row[0], str(active_target))
            self.assertEqual(row[1], 0)
            self.assertTrue(row[2])

    def test_import_desktop_all_matches_browse_and_validate_visible_bundle_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "dst-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            session_cwd = workspace / "project"
            session_cwd.mkdir()

            primary_session_id = "aaa10000-0000-7000-8000-000000000001"
            legacy_cli_session_id = "aaa10000-0000-7000-8000-000000000002"
            legacy_desktop_session_id = "aaa10000-0000-7000-8000-000000000003"

            primary_bundle = (
                workspace
                / "codex_sessions"
                / "MachineA"
                / "active"
                / "20260411-100000-000001"
                / primary_session_id
            )
            legacy_cli_bundle = (
                workspace
                / "codex_sessions"
                / "bundles"
                / "cli_batches"
                / "20260411-100000-000001"
                / legacy_cli_session_id
            )
            legacy_desktop_bundle = (
                workspace
                / "codex_sessions"
                / "desktop_bundles"
                / "desktop_active_batches"
                / "20260411-100000-000001"
                / legacy_desktop_session_id
            )

            for bundle_dir, session_id, source, originator in [
                (primary_bundle, primary_session_id, "vscode", "Codex Desktop"),
                (legacy_cli_bundle, legacy_cli_session_id, "cli", "Codex CLI"),
                (legacy_desktop_bundle, legacy_desktop_session_id, "vscode", "Codex Desktop"),
            ]:
                write_bundle_manifest(
                    bundle_dir,
                    session_id=session_id,
                    export_machine="MachineA",
                    export_machine_key="MachineA",
                    session_cwd=str(session_cwd),
                    session_kind="desktop" if source == "vscode" else "cli",
                )
                write_bundled_session_file(
                    bundle_dir,
                    session_id,
                    cwd=session_cwd,
                    source=source,
                    originator=originator,
                )

            paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                bundle_summaries = get_bundle_summaries(paths, source_group="all", limit=None)
                validation = validate_bundles(paths, source_group="all")
                result = import_desktop_all(paths, desktop_visible=True)

            visible_ids = {summary.session_id for summary in bundle_summaries}
            validated_ids = {entry.session_id for entry in validation.valid_results}
            imported_ids = {path.name for path in result.success_dirs}

            self.assertEqual(
                visible_ids,
                {primary_session_id, legacy_cli_session_id, legacy_desktop_session_id},
            )
            self.assertEqual(validated_ids, visible_ids)
            self.assertEqual(imported_ids, visible_ids)

    def test_import_desktop_all_promotes_each_imported_thread_without_pinning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "dst-provider")
            write_state_file(dst_home)
            db_path = create_threads_db(dst_home)

            bulk_project = workspace / "bulk-project"
            hidden_project = workspace / "hidden-project"
            bulk_project.mkdir()
            hidden_project.mkdir()

            bundle_root = workspace / "codex_bundles" / "MachineA" / "sessions" / "active" / "20260411-100000-000001"
            bulk_session_ids = []
            for index in range(60):
                session_id = f"aaa20000-0000-7000-8000-{index:012d}"
                bulk_session_ids.append(session_id)
                bundle_dir = bundle_root / session_id
                write_bundle_manifest(
                    bundle_dir,
                    session_id=session_id,
                    export_machine="MachineA",
                    export_machine_key="MachineA",
                    updated_at="2026-04-10T10:00:00Z",
                    session_cwd=str(bulk_project),
                )
                write_bundled_session_file(
                    bundle_dir,
                    session_id,
                    cwd=bulk_project,
                    timestamp="2026-04-10T10:00:00Z",
                    user_message=f"bulk prompt {index}",
                )

            hidden_session_id = "aaa20000-0000-7000-8000-999999999999"
            hidden_bundle = bundle_root / hidden_session_id
            write_bundle_manifest(
                hidden_bundle,
                session_id=hidden_session_id,
                export_machine="MachineA",
                export_machine_key="MachineA",
                updated_at="2026-04-09T10:00:00Z",
                session_cwd=str(hidden_project),
            )
            write_bundled_session_file(
                hidden_bundle,
                hidden_session_id,
                cwd=hidden_project,
                timestamp="2026-04-09T10:00:00Z",
                user_message="hidden project prompt",
            )

            paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                result = import_desktop_all(paths, desktop_visible=True)

            self.assertEqual(len(result.success_dirs), 61)
            self.assertEqual(result.desktop_sidebar_promoted_count, 61)
            self.assertEqual(result.desktop_pinned_count, 0)

            state_data = json.loads((dst_home / ".codex" / ".codex-global-state.json").read_text(encoding="utf-8"))
            pinned_ids = state_data.get("pinned-thread-ids", [])
            self.assertEqual(pinned_ids, [])
            self.assertEqual(
                state_data["sidebar-project-thread-orders"][str(bulk_project)]["threadIds"],
                list(reversed(bulk_session_ids)),
            )
            self.assertEqual(
                state_data["sidebar-project-thread-orders"][str(hidden_project)]["threadIds"],
                [hidden_session_id],
            )
            self.assertEqual(state_data["thread-workspace-root-hints"][hidden_session_id], str(hidden_project))

            conn = sqlite3.connect(db_path)
            top_rows = conn.execute(
                "select id, cwd from threads order by updated_at desc, id desc limit 61"
            ).fetchall()
            hidden_row = conn.execute(
                "select updated_at, updated_at_ms from threads where id = ?",
                (hidden_session_id,),
            ).fetchone()
            conn.close()
            self.assertEqual({row[0] for row in top_rows}, set(bulk_session_ids + [hidden_session_id]))
            self.assertEqual({row[1] for row in top_rows}, {str(bulk_project), str(hidden_project)})
            self.assertEqual(top_rows[0][0], bulk_session_ids[-1])
            self.assertIn(hidden_session_id, {row[0] for row in top_rows[:2]})
            self.assertEqual(hidden_row[1], hidden_row[0] * 1000)

    def test_import_desktop_all_writes_skills_restore_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "batch-skill", "batched skill")

            session_id = "aaa00000-0000-7000-8000-000000000000"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                skill_entries=[
                    {"name": "batch-skill", "file": str(agents_skills / "batch-skill" / "SKILL.md")},
                ],
            )
            write_history(src_home, session_id, "desktop batch skills")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_active_desktop_all(src_paths)

            with pushd(workspace):
                result = import_desktop_all(dst_paths)

            self.assertIsNotNone(result.skills_restore_report_path)
            assert result.skills_restore_report_path is not None
            self.assertTrue(result.skills_restore_report_path.is_file())

            report_data = json.loads(result.skills_restore_report_path.read_text(encoding="utf-8"))
            self.assertEqual(report_data["total_sessions"], 1)
            self.assertEqual(report_data["sessions"][0]["session_id"], session_id)
            self.assertEqual(report_data["sessions"][0]["restored"], 1)
            self.assertEqual(report_data["sessions"][0]["already_present"], 0)
            self.assertEqual(report_data["sessions"][0]["conflict_skipped"], 0)
            self.assertEqual(report_data["sessions"][0]["missing"], 0)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(print_batch_import_result(result), 0)
            self.assertIn("Skills restore report:", stdout.getvalue())
            self.assertIn(str(result.skills_restore_report_path), stdout.getvalue())

    def test_import_desktop_all_separates_restored_and_already_present_skill_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "batch-skill", "batched skill")

            session_id = "aaa00000-0000-7000-8000-000000000020"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                skill_entries=[
                    {"name": "batch-skill", "file": str(agents_skills / "batch-skill" / "SKILL.md")},
                ],
            )
            write_history(src_home, session_id, "desktop batch skills already present")

            dst_agents_skills = dst_home / ".agents" / "skills"
            write_test_skill(dst_agents_skills, "batch-skill", "batched skill")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_active_desktop_all(src_paths)

            with pushd(workspace):
                result = import_desktop_all(dst_paths)

            self.assertEqual(result.total_skills_restored, 0)
            self.assertEqual(result.total_skills_already_present, 1)
            self.assertEqual(result.total_skills_conflict_skipped, 0)

            assert result.skills_restore_report_path is not None
            report_data = json.loads(result.skills_restore_report_path.read_text(encoding="utf-8"))
            self.assertEqual(report_data["sessions"][0]["restored"], 0)
            self.assertEqual(report_data["sessions"][0]["already_present"], 1)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(print_batch_import_result(result), 0)
            self.assertIn("Total skills restored:          0", stdout.getvalue())
            self.assertIn("Total skills already present:   1", stdout.getvalue())
            self.assertIn("Total skills missing:           0", stdout.getvalue())

    def test_import_desktop_all_aggregates_missing_skill_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            missing_skill_path = src_home / ".agents" / "skills" / "missing-skill" / "SKILL.md"
            session_id = "aaa00000-0000-7000-8000-000000000022"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                skill_entries=[
                    {"name": "missing-skill", "file": str(missing_skill_path)},
                ],
            )
            write_history(src_home, session_id, "desktop batch skills missing")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_active_desktop_all(src_paths)

            with pushd(workspace):
                result = import_desktop_all(dst_paths)

            self.assertEqual(result.total_skills_restored, 0)
            self.assertEqual(result.total_skills_already_present, 0)
            self.assertEqual(result.total_skills_conflict_skipped, 0)
            self.assertEqual(result.total_skills_missing, 1)
            self.assertTrue(any(warning.code == "missing_skill" and warning.session_id == session_id for warning in result.warnings))

            assert result.skills_restore_report_path is not None
            report_data = json.loads(result.skills_restore_report_path.read_text(encoding="utf-8"))
            self.assertEqual(report_data["sessions"][0]["missing"], 1)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(print_batch_import_result(result), 0)
            self.assertIn("Total skills missing:           1", stdout.getvalue())

    def test_import_desktop_all_counts_failed_skill_restores_in_report_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "good-skill", "good content")
            write_test_skill(agents_skills, "bad-skill", "bad content")

            session_id = "aaa00000-0000-7000-8000-000000000023"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                skill_entries=[
                    {"name": "good-skill", "file": str(agents_skills / "good-skill" / "SKILL.md")},
                    {"name": "bad-skill", "file": str(agents_skills / "bad-skill" / "SKILL.md")},
                ],
            )
            write_history(src_home, session_id, "desktop batch skills failed restore")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_active_desktop_all(src_paths)

            real_copytree = shutil.copytree

            def copytree_side_effect(src, dst, *args, **kwargs):
                if str(src).endswith("skills/agents/bad-skill"):
                    raise OSError("simulated restore failure")
                return real_copytree(src, dst, *args, **kwargs)

            with patch("codex_session_toolkit.stores.skills.shutil.copytree", side_effect=copytree_side_effect):
                with pushd(workspace):
                    result = import_desktop_all(dst_paths)

            self.assertEqual(result.total_skills_restored, 1)
            self.assertEqual(result.total_skills_failed, 1)
            self.assertTrue(any(warning.code == "restore_skill_failed" and warning.name == "bad-skill" for warning in result.warnings))

            assert result.skills_restore_report_path is not None
            report_data = json.loads(result.skills_restore_report_path.read_text(encoding="utf-8"))
            self.assertEqual(report_data["sessions"][0]["restored"], 1)
            self.assertEqual(report_data["sessions"][0]["failed"], 1)
            self.assertEqual(
                {skill["name"]: skill["status"] for skill in report_data["sessions"][0]["skills"]},
                {"good-skill": "restored", "bad-skill": "failed"},
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(print_batch_import_result(result), 0)
            self.assertIn("Total skills restored:          1", stdout.getvalue())
            self.assertIn("Total skills failed:            1", stdout.getvalue())

    def test_import_desktop_all_uses_distinct_skills_restore_report_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "batch-skill", "batched skill")

            session_id = "aaa00000-0000-7000-8000-000000000021"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                skill_entries=[
                    {"name": "batch-skill", "file": str(agents_skills / "batch-skill" / "SKILL.md")},
                ],
            )
            write_history(src_home, session_id, "desktop batch skills repeated imports")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_active_desktop_all(src_paths)

            with pushd(workspace), patch("codex_session_toolkit.services.importing.time.time", return_value=1_776_123_456):
                first_result = import_desktop_all(dst_paths)
                second_result = import_desktop_all(dst_paths)

            self.assertIsNotNone(first_result.skills_restore_report_path)
            self.assertIsNotNone(second_result.skills_restore_report_path)
            assert first_result.skills_restore_report_path is not None
            assert second_result.skills_restore_report_path is not None
            self.assertNotEqual(first_result.skills_restore_report_path, second_result.skills_restore_report_path)

            first_report = json.loads(first_result.skills_restore_report_path.read_text(encoding="utf-8"))
            second_report = json.loads(second_result.skills_restore_report_path.read_text(encoding="utf-8"))
            self.assertEqual(first_report["total_sessions"], 1)
            self.assertEqual(second_report["total_sessions"], 1)
            self.assertEqual(first_report["sessions"][0]["restored"], 1)
            self.assertEqual(first_report["sessions"][0]["already_present"], 0)
            self.assertEqual(first_report["sessions"][0]["failed"], 0)
            self.assertEqual(second_report["sessions"][0]["restored"], 0)
            self.assertEqual(second_report["sessions"][0]["already_present"], 1)
            self.assertEqual(second_report["sessions"][0]["failed"], 0)

    def test_batch_export_and_import_aggregate_skill_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            missing_skill_path = src_home / ".agents" / "skills" / "missing-skill" / "SKILL.md"
            session_id = "aaa10000-0000-7000-8000-000000000010"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="vscode",
                originator="Codex Desktop",
                cwd=workspace,
                skill_entries=[
                    {"name": "missing-skill", "file": str(missing_skill_path)},
                ],
            )
            write_history(src_home, session_id, "batch warning flow")

            src_paths = CodexPaths(home=src_home)
            dst_paths = CodexPaths(home=dst_home)

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_active_desktop_all(src_paths)

            self.assertTrue(
                any(
                    warning.code == "skill_not_bundled"
                    and warning.session_id == session_id
                    and warning.name == "missing-skill"
                    for warning in export_result.warnings
                )
            )

            with pushd(workspace):
                import_result = import_desktop_all(dst_paths)

            self.assertTrue(
                any(
                    warning.code == "missing_skill" and warning.session_id == session_id
                    for warning in import_result.warnings
                )
            )

    # --- Skill export/import tests ---

    def test_session_export_bundles_only_required_custom_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()
            write_config(src_home, "test-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "used-skill", "used content")
            write_test_skill(agents_skills, "available-skill", "available content")

            session_id = "aaa00025-0000-7000-8000-000000000025"
            write_session_with_skills(
                src_home,
                session_id,
                provider="test-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "used-skill", "file": str(agents_skills / "used-skill" / "SKILL.md")},
                    {"name": "available-skill", "file": str(agents_skills / "available-skill" / "SKILL.md")},
                ],
                used_skill_names=["used-skill"],
            )

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                result = export_session(CodexPaths(home=src_home), session_id)

            self.assertEqual(result.skills_available_count, 2)
            self.assertEqual(result.skills_bundled_count, 1)
            self.assertTrue((result.bundle_dir / "skills" / "agents" / "used-skill" / "SKILL.md").is_file())
            self.assertFalse((result.bundle_dir / "skills" / "agents" / "available-skill").exists())
            manifest = read_skills_manifest(result.bundle_dir)
            self.assertIsNotNone(manifest)
            assert manifest is not None
            levels = {skill.name: skill.dependency_level for skill in manifest.skills}
            self.assertEqual(levels["used-skill"], "required")
            self.assertEqual(levels["available-skill"], "available")

    def test_session_import_does_not_report_missing_for_available_only_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            missing_skill_path = src_home / ".agents" / "skills" / "available-only" / "SKILL.md"
            session_id = "aaa00026-0000-7000-8000-000000000026"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "available-only", "file": str(missing_skill_path)},
                ],
                used_skill_names=[],
            )

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(CodexPaths(home=src_home), session_id)
            with pushd(workspace):
                import_result = import_session(CodexPaths(home=dst_home), str(export_result.bundle_dir))

            self.assertEqual(import_result.skills_missing_count, 0)
            self.assertFalse(any(warning.code == "missing_skill" for warning in import_result.warnings))

    def test_session_export_does_not_treat_plain_skill_mentions_as_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()
            write_config(src_home, "test-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "plain-mentioned-skill", "plain content")

            session_id = "aaa00027-0000-7000-8000-000000000027"
            rollout = write_session_with_skills(
                src_home,
                session_id,
                provider="test-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "plain-mentioned-skill", "file": str(agents_skills / "plain-mentioned-skill" / "SKILL.md")},
                ],
                used_skill_names=[],
            )
            with rollout.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "timestamp": "2026-04-10T10:07:00Z",
                            "type": "message",
                            "payload": {
                                "role": "assistant",
                                "text": "This skill plain-mentioned-skill is only being described.",
                            },
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                result = export_session(CodexPaths(home=src_home), session_id)

            self.assertEqual(result.skills_available_count, 1)
            self.assertEqual(result.skills_bundled_count, 0)
            self.assertFalse((result.bundle_dir / "skills" / "agents" / "plain-mentioned-skill").exists())

    def test_standalone_skills_export_import_restores_custom_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()

            agents_skills = src_home / ".agents" / "skills"
            codex_skills = src_home / ".codex" / "skills"
            write_test_skill(agents_skills, "solo-skill", "solo content")
            write_test_skill(codex_skills, "solo-skill", "duplicate content")

            local_rows = list_local_skills(CodexPaths(home=src_home))
            self.assertEqual([row.relative_dir for row in local_rows], ["solo-skill"])
            self.assertEqual(local_rows[0].source_root, "agents")

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_skills(CodexPaths(home=src_home))
                bundles = list_skill_bundles(CodexPaths(home=dst_home))

            self.assertEqual(export_result.exported_count, 1)
            self.assertEqual(len(bundles), 1)
            self.assertIn("codex_bundles", str(export_result.bundle_dir))
            self.assertTrue((export_result.bundle_dir / "skills" / "agents" / "solo-skill" / "SKILL.md").is_file())

            with pushd(workspace):
                import_result = import_skill_bundle(CodexPaths(home=dst_home), str(export_result.bundle_dir))

            self.assertEqual(import_result.restored_count, 1)
            self.assertEqual(import_result.conflict_skipped_count, 0)
            self.assertEqual(
                (dst_home / ".agents" / "skills" / "solo-skill" / "SKILL.md").read_text(encoding="utf-8"),
                "solo content",
            )

    def test_standalone_skills_selected_export_and_import_multiple_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            first_home = Path(tmpdir) / "first_home"
            second_home = Path(tmpdir) / "second_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()

            first_skill = write_test_skill(first_home / ".agents" / "skills", "first-skill", "first content")
            second_skill = write_test_skill(second_home / ".codex" / "skills", "second-skill", "second content")

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                first_export = export_skills(CodexPaths(home=first_home), input_values=[str(first_skill)])
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineB"):
                second_export = export_skills(CodexPaths(home=second_home), input_values=[str(second_skill)])

            self.assertIn("/skills/single/", first_export.bundle_dir.as_posix())
            self.assertIn("/skills/single/", second_export.bundle_dir.as_posix())

            with pushd(workspace):
                import_result = import_skill_bundle(
                    CodexPaths(home=dst_home),
                    str(first_export.bundle_dir),
                    str(second_export.bundle_dir),
                )

            self.assertEqual(import_result.restored_count, 2)
            self.assertEqual(
                (dst_home / ".agents" / "skills" / "first-skill" / "SKILL.md").read_text(encoding="utf-8"),
                "first content",
            )
            self.assertEqual(
                (dst_home / ".codex" / "skills" / "second-skill" / "SKILL.md").read_text(encoding="utf-8"),
                "second content",
            )

    def test_standalone_skills_import_skips_conflicting_skill_across_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()

            src_codex_skills = src_home / ".codex" / "skills"
            dst_agents_skills = dst_home / ".agents" / "skills"
            write_test_skill(src_codex_skills, "cross-root-standalone", "source content")
            write_test_skill(dst_agents_skills, "cross-root-standalone", "local content")

            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_skills(CodexPaths(home=src_home))
            with pushd(workspace):
                import_result = import_skill_bundle(CodexPaths(home=dst_home), str(export_result.bundle_dir))

            self.assertEqual(import_result.restored_count, 0)
            self.assertEqual(import_result.conflict_skipped_count, 1)
            self.assertEqual(
                (dst_home / ".agents" / "skills" / "cross-root-standalone" / "SKILL.md").read_text(encoding="utf-8"),
                "local content",
            )
            self.assertFalse((dst_home / ".codex" / "skills" / "cross-root-standalone").exists())

    def test_delete_local_skill_supports_dry_run_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            agents_skills = home / ".agents" / "skills"
            skill_dir = write_test_skill(agents_skills, "delete-me", "delete content")
            paths = CodexPaths(home=home)

            dry_run = delete_local_skill(paths, "delete-me", source_root="agents", dry_run=True)
            self.assertFalse(dry_run.deleted)
            self.assertTrue(skill_dir.is_dir())

            deleted = delete_local_skill(paths, "delete-me", source_root="agents")
            self.assertTrue(deleted.deleted)
            self.assertFalse(skill_dir.exists())

    def test_delete_local_skills_supports_multiple_targets_and_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            agents_skills = home / ".agents" / "skills"
            codex_skills = home / ".codex" / "skills"
            first_dir = write_test_skill(agents_skills, "delete-first", "first")
            second_dir = write_test_skill(codex_skills, "delete-second", "second")
            keep_dir = write_test_skill(agents_skills, "keep-for-all", "keep")
            paths = CodexPaths(home=home)

            dry_run = delete_local_skills(
                paths,
                [str(first_dir), str(second_dir)],
                dry_run=True,
            )
            self.assertEqual([result.relative_dir for result in dry_run], ["delete-first", "delete-second"])
            self.assertTrue(first_dir.is_dir())
            self.assertTrue(second_dir.is_dir())

            deleted = delete_local_skills(paths, [str(first_dir), str(second_dir)])
            self.assertEqual(len(deleted), 2)
            self.assertFalse(first_dir.exists())
            self.assertFalse(second_dir.exists())
            self.assertTrue(keep_dir.is_dir())

            delete_all = delete_local_skills(paths, all_skills=True)
            self.assertEqual([result.relative_dir for result in delete_all], ["keep-for-all"])
            self.assertFalse(keep_dir.exists())

    def test_delete_local_skill_refuses_ambiguous_cross_root_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            write_test_skill(home / ".agents" / "skills", "duplicate-delete", "agents content")
            write_test_skill(home / ".codex" / "skills", "duplicate-delete", "codex content")

            from codex_session_toolkit.errors import ToolkitError
            with self.assertRaises(ToolkitError):
                delete_local_skill(CodexPaths(home=home), "duplicate-delete")

            result = delete_local_skill(CodexPaths(home=home), "duplicate-delete", source_root="codex")
            self.assertTrue(result.deleted)
            self.assertTrue((home / ".agents" / "skills" / "duplicate-delete").is_dir())
            self.assertFalse((home / ".codex" / "skills" / "duplicate-delete").exists())

    def test_export_session_bundles_custom_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()
            write_config(src_home, "test-provider")

            agents_skills = src_home / ".agents" / "skills"
            codex_skills = src_home / ".codex" / "skills"
            write_test_skill(agents_skills, "my-skill", "my skill content")
            write_test_skill(codex_skills, str(Path(".system") / "sys-skill"), "system skill")

            session_id = "aaa00001-0000-7000-8000-000000000001"
            write_session_with_skills(
                src_home,
                session_id,
                provider="test-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "my-skill", "file": str(agents_skills / "my-skill" / "SKILL.md")},
                    {"name": "sys-skill", "file": str(codex_skills / ".system" / "sys-skill" / "SKILL.md")},
                ],
            )
            write_history(src_home, session_id, "test prompt")

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "TestMachine"):
                result = export_session(paths, session_id)

            self.assertEqual(result.skills_available_count, 2)
            self.assertEqual(result.skills_bundled_count, 1)
            self.assertIsNotNone(result.skills_manifest_path)
            assert result.skills_manifest_path is not None
            self.assertTrue(result.skills_manifest_path.is_file())
            self.assertTrue((result.bundle_dir / "skills_manifest.json").is_file())
            self.assertTrue((result.bundle_dir / "skills" / "agents" / "my-skill" / "SKILL.md").is_file())
            self.assertFalse((result.bundle_dir / "skills" / "codex" / ".system" / "sys-skill").exists())

    def test_export_session_warns_when_custom_skill_cannot_be_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()
            write_config(src_home, "test-provider")

            missing_skill_path = src_home / ".agents" / "skills" / "missing-skill" / "SKILL.md"
            session_id = "aaa00011-0000-7000-8000-000000000011"
            write_session_with_skills(
                src_home,
                session_id,
                provider="test-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "missing-skill", "file": str(missing_skill_path)},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "TestMachine"):
                result = export_session(paths, session_id)

            self.assertEqual(result.skills_available_count, 1)
            self.assertEqual(result.skills_bundled_count, 0)
            self.assertIsNotNone(result.skills_manifest_path)
            assert result.skills_manifest_path is not None
            self.assertTrue(result.skills_manifest_path.is_file())
            self.assertTrue(
                any(
                    warning.code == "skill_not_bundled"
                    and warning.name == "missing-skill"
                    for warning in result.warnings
                )
            )

    def test_export_session_warns_when_custom_skill_location_is_unrestorable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            detached_skills = Path(tmpdir) / "detached_skills"
            workspace.mkdir()
            write_config(src_home, "test-provider")

            write_test_skill(detached_skills, "detached-skill", "detached content")

            session_id = "aaa00018-0000-7000-8000-000000000018"
            write_session_with_skills(
                src_home,
                session_id,
                provider="test-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "detached-skill", "file": str(detached_skills / "detached-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "TestMachine"):
                result = export_session(paths, session_id)

            self.assertEqual(result.skills_available_count, 1)
            self.assertEqual(result.skills_bundled_count, 0)
            self.assertFalse((result.bundle_dir / "skills" / "unknown").exists())
            self.assertTrue(
                any(
                    warning.code == "skill_not_bundled"
                    and warning.name == "detached-skill"
                    and warning.detail == "unsupported skill location"
                    for warning in result.warnings
                )
            )

    def test_export_session_warns_when_skill_copy_raises_filesystem_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()
            write_config(src_home, "test-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "copy-fail-skill", "copy me")

            session_id = "aaa00015-0000-7000-8000-000000000015"
            write_session_with_skills(
                src_home,
                session_id,
                provider="test-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "copy-fail-skill", "file": str(agents_skills / "copy-fail-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            real_copytree = shutil.copytree

            def copytree_side_effect(src, dst, *args, **kwargs):
                if str(src).endswith("copy-fail-skill"):
                    raise OSError("simulated copy failure")
                return real_copytree(src, dst, *args, **kwargs)

            with patch("codex_session_toolkit.stores.skills.shutil.copytree", side_effect=copytree_side_effect):
                with pushd(workspace), env_override("CST_MACHINE_LABEL", "TestMachine"):
                    result = export_session(paths, session_id)

            self.assertEqual(result.skills_available_count, 1)
            self.assertEqual(result.skills_bundled_count, 0)
            self.assertIsNotNone(result.skills_manifest_path)
            assert result.skills_manifest_path is not None
            self.assertTrue(result.skills_manifest_path.is_file())
            self.assertTrue(
                any(
                    warning.code == "bundle_skill_failed"
                    and warning.name == "copy-fail-skill"
                    for warning in result.warnings
                )
            )

    def test_export_session_keeps_partial_skill_results_when_hashing_one_skill_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()
            write_config(src_home, "test-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "good-skill", "good content")
            write_test_skill(agents_skills, "hash-fail-skill", "hash fail content")

            session_id = "aaa00020-0000-7000-8000-000000000020"
            write_session_with_skills(
                src_home,
                session_id,
                provider="test-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "good-skill", "file": str(agents_skills / "good-skill" / "SKILL.md")},
                    {"name": "hash-fail-skill", "file": str(agents_skills / "hash-fail-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            real_compute_hash = compute_skill_directory_hash

            def compute_hash_side_effect(skill_dir):
                if str(skill_dir).endswith("hash-fail-skill"):
                    raise OSError("simulated hash failure")
                return real_compute_hash(skill_dir)

            with patch(
                "codex_session_toolkit.stores.skills.compute_skill_directory_hash",
                side_effect=compute_hash_side_effect,
            ):
                with pushd(workspace), env_override("CST_MACHINE_LABEL", "TestMachine"):
                    result = export_session(paths, session_id)

            self.assertEqual(result.skills_available_count, 2)
            self.assertEqual(result.skills_bundled_count, 1)
            self.assertTrue((result.bundle_dir / "skills" / "agents" / "good-skill" / "SKILL.md").is_file())
            self.assertFalse((result.bundle_dir / "skills" / "agents" / "hash-fail-skill").exists())
            self.assertTrue(
                any(
                    warning.code == "bundle_skill_failed"
                    and warning.name == "hash-fail-skill"
                    for warning in result.warnings
                )
            )

    def test_export_session_strict_mode_raises_when_custom_skill_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            workspace.mkdir()
            write_config(src_home, "test-provider")

            missing_skill_path = src_home / ".agents" / "skills" / "missing-skill" / "SKILL.md"
            session_id = "aaa00012-0000-7000-8000-000000000012"
            write_session_with_skills(
                src_home,
                session_id,
                provider="test-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "missing-skill", "file": str(missing_skill_path)},
                ],
            )

            paths = CodexPaths(home=src_home)
            from codex_session_toolkit.errors import ToolkitError
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "TestMachine"), self.assertRaises(ToolkitError):
                export_session(paths, session_id, skills_mode="strict")

    def test_import_session_restores_bundled_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")
            write_state_file(dst_home)
            create_threads_db(dst_home)

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "imp-skill", "imported skill")

            session_id = "aaa00002-0000-7000-8000-000000000002"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "imp-skill", "file": str(agents_skills / "imp-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(paths, session_id)
                bundle_dir = export_result.bundle_dir

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_restored_count, 1)
            self.assertEqual(import_result.skills_already_present_count, 0)
            self.assertTrue((dst_home / ".agents" / "skills" / "imp-skill" / "SKILL.md").is_file())
            self.assertEqual(
                (dst_home / ".agents" / "skills" / "imp-skill" / "SKILL.md").read_text(encoding="utf-8"),
                "imported skill",
            )

    def test_import_session_skills_conflict_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "conflict-skill", "original content")

            session_id = "aaa00003-0000-7000-8000-000000000003"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "conflict-skill", "file": str(agents_skills / "conflict-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(paths, session_id)
                bundle_dir = export_result.bundle_dir

            dst_agents_skills = dst_home / ".agents" / "skills"
            write_test_skill(dst_agents_skills, "conflict-skill", "different content")

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_conflict_skipped_count, 1)
            self.assertEqual(
                (dst_home / ".agents" / "skills" / "conflict-skill" / "SKILL.md").read_text(encoding="utf-8"),
                "different content",
            )

    def test_import_session_skills_already_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "same-skill", "identical content")

            session_id = "aaa00004-0000-7000-8000-000000000004"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "same-skill", "file": str(agents_skills / "same-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(paths, session_id)
                bundle_dir = export_result.bundle_dir

            dst_agents_skills = dst_home / ".agents" / "skills"
            write_test_skill(dst_agents_skills, "same-skill", "identical content")

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_already_present_count, 1)

    def test_import_session_skips_skill_copy_when_same_skill_exists_in_other_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            src_codex_skills = src_home / ".codex" / "skills"
            write_test_skill(src_codex_skills, "cross-root-skill", "identical content")

            session_id = "aaa00024-0000-7000-8000-000000000024"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "cross-root-skill", "file": str(src_codex_skills / "cross-root-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(paths, session_id)
                bundle_dir = export_result.bundle_dir

            dst_agents_skills = dst_home / ".agents" / "skills"
            write_test_skill(dst_agents_skills, "cross-root-skill", "identical content")

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_restored_count, 0)
            self.assertEqual(import_result.skills_already_present_count, 1)
            self.assertTrue((dst_home / ".agents" / "skills" / "cross-root-skill" / "SKILL.md").is_file())
            self.assertFalse((dst_home / ".codex" / "skills" / "cross-root-skill").exists())

    def test_import_session_skips_conflicting_skill_in_other_root_without_creating_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            src_codex_skills = src_home / ".codex" / "skills"
            write_test_skill(src_codex_skills, "cross-root-conflict", "source content")

            session_id = "aaa00025-0000-7000-8000-000000000025"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "cross-root-conflict", "file": str(src_codex_skills / "cross-root-conflict" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(paths, session_id)
                bundle_dir = export_result.bundle_dir

            dst_agents_skills = dst_home / ".agents" / "skills"
            write_test_skill(dst_agents_skills, "cross-root-conflict", "local content")

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_restored_count, 0)
            self.assertEqual(import_result.skills_conflict_skipped_count, 1)
            self.assertEqual(
                (dst_home / ".agents" / "skills" / "cross-root-conflict" / "SKILL.md").read_text(encoding="utf-8"),
                "local content",
            )
            self.assertFalse((dst_home / ".codex" / "skills" / "cross-root-conflict").exists())

    def test_import_session_skills_missing_in_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "dst-provider")

            session_id = "aaa00005-0000-7000-8000-000000000005"
            bundle_dir = workspace / "codex_sessions" / "test-machine" / session_id
            bundle_dir.mkdir(parents=True)

            manifest = SkillsManifest(
                available_skill_count=1,
                used_skill_count=1,
                bundled_skill_count=0,
                skills=(
                    SkillDescriptor(
                        name="missing-skill",
                        skill_file="/home/user/.agents/skills/missing-skill/SKILL.md",
                        source_root="agents",
                        relative_dir="missing-skill",
                        location_kind="custom",
                        used=True,
                        usage_count=1,
                    ),
                ),
            )
            write_skills_manifest(manifest, bundle_dir)

            relative_path = f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            write_bundle_manifest(bundle_dir, session_id=session_id, relative_path=relative_path)

            codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
            codex_dir.mkdir(parents=True)
            session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            session_file.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-10T10:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "model_provider": "test",
                            "source": "cli",
                            "originator": "CLI",
                            "cwd": str(workspace),
                            "timestamp": "2026-04-10T10:00:00Z",
                            "cli_version": "0.1.0",
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_missing_count, 1)
            self.assertTrue(any(warning.code == "missing_skill" and warning.name == "missing-skill" for warning in import_result.warnings))

    def test_import_session_warns_when_manifest_points_to_missing_bundled_skill_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "dst-provider")

            session_id = "aaa00016-0000-7000-8000-000000000016"
            bundle_dir = workspace / "codex_sessions" / "test-machine" / session_id
            bundle_dir.mkdir(parents=True)

            manifest = SkillsManifest(
                available_skill_count=1,
                used_skill_count=1,
                bundled_skill_count=1,
                skills=(
                    SkillDescriptor(
                        name="missing-bundled-skill",
                        skill_file="/home/user/.agents/skills/missing-bundled-skill/SKILL.md",
                        source_root="agents",
                        relative_dir="missing-bundled-skill",
                        location_kind="custom",
                        used=True,
                        usage_count=1,
                        bundled=True,
                        bundle_path="skills/agents/missing-bundled-skill",
                        content_hash="abc123",
                    ),
                ),
            )
            write_skills_manifest(manifest, bundle_dir)

            relative_path = f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            write_bundle_manifest(bundle_dir, session_id=session_id, relative_path=relative_path)

            codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
            codex_dir.mkdir(parents=True)
            session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            session_file.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-10T10:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "model_provider": "test",
                            "source": "cli",
                            "originator": "CLI",
                            "cwd": str(workspace),
                            "timestamp": "2026-04-10T10:00:00Z",
                            "cli_version": "0.1.0",
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_missing_count, 0)
            self.assertEqual(import_result.skills_failed_count, 1)
            self.assertTrue(
                any(
                    warning.code == "invalid_bundled_skill"
                    and warning.name == "missing-bundled-skill"
                    for warning in import_result.warnings
                )
            )

    def test_import_session_warns_when_bundled_skill_directory_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "dst-provider")

            session_id = "aaa00021-0000-7000-8000-000000000021"
            bundle_dir = workspace / "codex_sessions" / "test-machine" / session_id
            bundle_dir.mkdir(parents=True)

            manifest = SkillsManifest(
                available_skill_count=1,
                used_skill_count=1,
                bundled_skill_count=1,
                skills=(
                    SkillDescriptor(
                        name="broken-bundled-skill",
                        skill_file="/home/user/.agents/skills/broken-bundled-skill/SKILL.md",
                        source_root="agents",
                        relative_dir="broken-bundled-skill",
                        location_kind="custom",
                        used=True,
                        usage_count=1,
                        bundled=True,
                        bundle_path="skills/agents/broken-bundled-skill",
                        content_hash="abc123",
                    ),
                ),
            )
            write_skills_manifest(manifest, bundle_dir)
            (bundle_dir / "skills" / "agents" / "broken-bundled-skill").mkdir(parents=True)
            (bundle_dir / "skills" / "agents" / "broken-bundled-skill" / "README.txt").write_text(
                "missing SKILL.md",
                encoding="utf-8",
            )

            relative_path = f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            write_bundle_manifest(bundle_dir, session_id=session_id, relative_path=relative_path)

            codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
            codex_dir.mkdir(parents=True)
            session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            session_file.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-10T10:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "model_provider": "test",
                            "source": "cli",
                            "originator": "CLI",
                            "cwd": str(workspace),
                            "timestamp": "2026-04-10T10:00:00Z",
                            "cli_version": "0.1.0",
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_restored_count, 0)
            self.assertEqual(import_result.skills_failed_count, 1)
            self.assertTrue(
                any(
                    warning.code == "invalid_bundled_skill"
                    and warning.name == "broken-bundled-skill"
                    and warning.detail == "missing SKILL.md"
                    for warning in import_result.warnings
                )
            )
            self.assertFalse((dst_home / ".agents" / "skills" / "broken-bundled-skill").exists())

    def test_import_session_warns_on_invalid_skills_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "dst-provider")

            session_id = "aaa00013-0000-7000-8000-000000000013"
            bundle_dir = workspace / "codex_sessions" / "test-machine" / session_id
            bundle_dir.mkdir(parents=True)

            relative_path = f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            write_bundle_manifest(bundle_dir, session_id=session_id, relative_path=relative_path)

            codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
            codex_dir.mkdir(parents=True)
            session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            session_file.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-10T10:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "model_provider": "test",
                            "source": "cli",
                            "originator": "CLI",
                            "cwd": str(workspace),
                            "timestamp": "2026-04-10T10:00:00Z",
                            "cli_version": "0.1.0",
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            (bundle_dir / "skills_manifest.json").write_text("NOT VALID JSON{{{", encoding="utf-8")

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertTrue(any(warning.code == "invalid_skills_manifest" for warning in import_result.warnings))

    def test_import_session_warns_on_structurally_invalid_skills_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "dst-provider")

            session_id = "aaa00019-0000-7000-8000-000000000019"
            bundle_dir = workspace / "codex_sessions" / "test-machine" / session_id
            bundle_dir.mkdir(parents=True)

            relative_path = f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            write_bundle_manifest(bundle_dir, session_id=session_id, relative_path=relative_path)

            codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
            codex_dir.mkdir(parents=True)
            session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            session_file.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-10T10:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "model_provider": "test",
                            "source": "cli",
                            "originator": "CLI",
                            "cwd": str(workspace),
                            "timestamp": "2026-04-10T10:00:00Z",
                            "cli_version": "0.1.0",
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            (bundle_dir / "skills_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "available_skill_count": 1,
                        "used_skill_count": 1,
                        "bundled_skill_count": 1,
                        "skills": [
                            {
                                "name": "bad-skill",
                                "skill_file": "/tmp/source/.agents/skills/bad-skill/SKILL.md",
                                "source_root": "agents",
                                "relative_dir": "bad-skill",
                                "location_kind": "custom",
                                "used": True,
                                "usage_count": 1,
                                "bundled": True,
                                "content_hash": "",
                            }
                        ],
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_restored_count, 0)
            self.assertTrue(any(warning.code == "invalid_skills_manifest" for warning in import_result.warnings))
            self.assertFalse((dst_home / ".agents" / "skills" / "bad-skill").exists())

    def test_import_session_strict_mode_raises_on_invalid_skills_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(dst_home, "dst-provider")

            session_id = "aaa00014-0000-7000-8000-000000000014"
            bundle_dir = workspace / "codex_sessions" / "test-machine" / session_id
            bundle_dir.mkdir(parents=True)

            relative_path = f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            write_bundle_manifest(bundle_dir, session_id=session_id, relative_path=relative_path)

            codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
            codex_dir.mkdir(parents=True)
            session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            session_file.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-10T10:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "model_provider": "test",
                            "source": "cli",
                            "originator": "CLI",
                            "cwd": str(workspace),
                            "timestamp": "2026-04-10T10:00:00Z",
                            "cli_version": "0.1.0",
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            (bundle_dir / "skills_manifest.json").write_text("NOT VALID JSON{{{", encoding="utf-8")

            dst_paths = CodexPaths(home=dst_home)
            from codex_session_toolkit.errors import ToolkitError
            with pushd(workspace), self.assertRaises(ToolkitError):
                import_session(dst_paths, str(bundle_dir), skills_mode="strict")

    def test_import_session_keeps_partial_skill_restore_results_when_one_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "good-skill", "good content")
            write_test_skill(agents_skills, "bad-skill", "bad content")

            session_id = "aaa00017-0000-7000-8000-000000000017"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "good-skill", "file": str(agents_skills / "good-skill" / "SKILL.md")},
                    {"name": "bad-skill", "file": str(agents_skills / "bad-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(paths, session_id)
                bundle_dir = export_result.bundle_dir

            real_copytree = shutil.copytree

            def copytree_side_effect(src, dst, *args, **kwargs):
                if str(src).endswith("skills/agents/bad-skill"):
                    raise OSError("simulated restore failure")
                return real_copytree(src, dst, *args, **kwargs)

            dst_paths = CodexPaths(home=dst_home)
            with patch("codex_session_toolkit.stores.skills.shutil.copytree", side_effect=copytree_side_effect):
                with pushd(workspace):
                    import_result = import_session(dst_paths, str(bundle_dir))

            self.assertEqual(import_result.skills_restored_count, 1)
            self.assertEqual(import_result.skills_missing_count, 0)
            self.assertEqual(import_result.skills_failed_count, 1)
            self.assertTrue((dst_home / ".agents" / "skills" / "good-skill" / "SKILL.md").is_file())
            self.assertFalse((dst_home / ".agents" / "skills" / "bad-skill" / "SKILL.md").exists())
            self.assertTrue(
                any(
                    warning.code == "restore_skill_failed"
                    and warning.name == "bad-skill"
                    for warning in import_result.warnings
                )
            )

    def test_import_session_keeps_restore_counts_when_report_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "report-fail-skill", "report content")

            session_id = "aaa00022-0000-7000-8000-000000000022"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "report-fail-skill", "file": str(agents_skills / "report-fail-skill" / "SKILL.md")},
                ],
            )

            src_paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(src_paths, session_id)
                bundle_dir = export_result.bundle_dir

            report_path = workspace / "restore-report.json"
            dst_paths = CodexPaths(home=dst_home)
            with patch(
                "codex_session_toolkit.services.skill_sidecars.write_batch_skills_restore_report",
                side_effect=OSError("simulated report write failure"),
            ):
                with pushd(workspace):
                    import_result = import_session(
                        dst_paths,
                        str(bundle_dir),
                        skills_restore_report_path=report_path,
                    )

            self.assertEqual(import_result.skills_restored_count, 1)
            self.assertEqual(import_result.skills_failed_count, 0)
            self.assertTrue((dst_home / ".agents" / "skills" / "report-fail-skill" / "SKILL.md").is_file())
            self.assertTrue(
                any(
                    warning.code == "skills_restore_report_failed"
                    and warning.path == str(report_path)
                    for warning in import_result.warnings
                )
            )

    def test_import_session_skills_strict_mode_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "strict-skill", "content A")

            session_id = "aaa00006-0000-7000-8000-000000000006"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "strict-skill", "file": str(agents_skills / "strict-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(paths, session_id)
                bundle_dir = export_result.bundle_dir

            dst_agents_skills = dst_home / ".agents" / "skills"
            write_test_skill(dst_agents_skills, "strict-skill", "content B - different")

            dst_paths = CodexPaths(home=dst_home)
            from codex_session_toolkit.errors import ToolkitError
            with pushd(workspace), self.assertRaises(ToolkitError):
                import_session(dst_paths, str(bundle_dir), skills_mode="strict")

    def test_import_session_skills_overwrite_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "ow-skill", "new content")

            session_id = "aaa00007-0000-7000-8000-000000000007"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "ow-skill", "file": str(agents_skills / "ow-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(paths, session_id)
                bundle_dir = export_result.bundle_dir

            dst_agents_skills = dst_home / ".agents" / "skills"
            write_test_skill(dst_agents_skills, "ow-skill", "old content")

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir), skills_mode="overwrite")

            self.assertEqual(import_result.skills_restored_count, 1)
            self.assertEqual(
                (dst_home / ".agents" / "skills" / "ow-skill" / "SKILL.md").read_text(encoding="utf-8"),
                "new content",
            )

    def test_import_session_skills_skip_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            src_home = Path(tmpdir) / "src_home"
            dst_home = Path(tmpdir) / "dst_home"
            workspace.mkdir()
            write_config(src_home, "src-provider")
            write_config(dst_home, "dst-provider")

            agents_skills = src_home / ".agents" / "skills"
            write_test_skill(agents_skills, "skip-skill", "content")

            session_id = "aaa00008-0000-7000-8000-000000000008"
            write_session_with_skills(
                src_home,
                session_id,
                provider="src-provider",
                source="cli",
                originator="Codex CLI",
                cwd=workspace,
                skill_entries=[
                    {"name": "skip-skill", "file": str(agents_skills / "skip-skill" / "SKILL.md")},
                ],
            )

            paths = CodexPaths(home=src_home)
            with pushd(workspace), env_override("CST_MACHINE_LABEL", "MachineA"):
                export_result = export_session(paths, session_id)
                bundle_dir = export_result.bundle_dir

            dst_paths = CodexPaths(home=dst_home)
            with pushd(workspace):
                import_result = import_session(dst_paths, str(bundle_dir), skills_mode="skip")

            self.assertEqual(import_result.skills_restored_count, 0)
            self.assertEqual(import_result.skills_missing_count, 0)
            self.assertFalse((dst_home / ".agents" / "skills" / "skip-skill").exists())

    def test_validate_bundle_with_skills_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            paths = CodexPaths()

            session_id = "aaa00009-0000-7000-8000-000000000009"
            bundle_dir = workspace / "codex_sessions" / session_id
            bundle_dir.mkdir(parents=True)

            relative_path = f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            write_bundle_manifest(bundle_dir, session_id=session_id, relative_path=relative_path)

            codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
            codex_dir.mkdir(parents=True)
            session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            session_file.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-10T10:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": session_id, "model_provider": "test", "source": "cli", "originator": "CLI", "cwd": "/tmp", "timestamp": "2026-04-10T10:00:00Z", "cli_version": "0.1.0"},
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            manifest = SkillsManifest(available_skill_count=1, used_skill_count=0, bundled_skill_count=0, skills=())
            write_skills_manifest(manifest, bundle_dir)

            with pushd(workspace):
                report = validate_bundles(paths)

            self.assertTrue(report.results[0].is_valid)

    def test_validate_bundle_with_bad_skills_sidecar_still_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            paths = CodexPaths()

            session_id = "aaa00010-0000-7000-8000-000000000010"
            bundle_dir = workspace / "codex_sessions" / session_id
            bundle_dir.mkdir(parents=True)

            relative_path = f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            write_bundle_manifest(bundle_dir, session_id=session_id, relative_path=relative_path)

            codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
            codex_dir.mkdir(parents=True)
            session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
            session_file.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-10T10:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": session_id, "model_provider": "test", "source": "cli", "originator": "CLI", "cwd": "/tmp", "timestamp": "2026-04-10T10:00:00Z", "cli_version": "0.1.0"},
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            (bundle_dir / "skills_manifest.json").write_text("NOT VALID JSON{{{", encoding="utf-8")

            with pushd(workspace):
                report = validate_bundles(paths)

            self.assertTrue(report.results[0].is_valid)

    def test_validate_bundles_ignores_standalone_skills_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            paths = CodexPaths()

            skills_bundle = workspace / "codex_bundles" / "MachineA" / "skills" / "all" / "20260518-100000-000001"
            write_manifest(
                skills_bundle / "manifest.env",
                {
                    "BUNDLE_TYPE": "skills",
                    "EXPORTED_AT": "2026-05-18T10:00:00Z",
                    "EXPORT_MACHINE": "MachineA",
                    "EXPORT_MACHINE_KEY": "MachineA",
                    "EXPORT_GROUP": "all",
                    "SKILL_COUNT": "1",
                    "BUNDLED_SKILL_COUNT": "1",
                },
            )

            with pushd(workspace):
                report = validate_bundles(paths)

            self.assertEqual(report.results, [])


if __name__ == "__main__":
    unittest.main()
