import io
import json
import os
import shlex
import shutil
import sqlite3
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
from codex_session_toolkit.models import BundleSummary  # noqa: E402
from codex_session_toolkit.presenters.reports import print_batch_import_result  # noqa: E402
from codex_session_toolkit.services.browse import get_bundle_summaries, get_project_session_summaries, get_session_summaries, validate_bundles  # noqa: E402
from codex_session_toolkit.services.clone import clone_to_provider  # noqa: E402
from codex_session_toolkit.services.exporting import export_active_desktop_all, export_project_sessions, export_session  # noqa: E402
from codex_session_toolkit.services.importing import import_desktop_all, import_session  # noqa: E402
from codex_session_toolkit.services.provider import detect_provider  # noqa: E402
from codex_session_toolkit.services.repair import repair_desktop  # noqa: E402
from codex_session_toolkit.support import default_local_project_target, machine_label_to_key  # noqa: E402
from codex_session_toolkit.stores import bundles as legacy_bundles  # noqa: E402
from codex_session_toolkit.stores.bundle_scanner import collect_known_bundle_summaries, latest_distinct_bundle_summaries  # noqa: E402
from codex_session_toolkit.stores.session_files import iter_session_files, read_session_payload  # noqa: E402
from codex_session_toolkit.stores.skills import SkillDescriptor, SkillsManifest, compute_skill_directory_hash, write_skills_manifest  # noqa: E402
from codex_session_toolkit.validation import load_manifest  # noqa: E402


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
    db_path = home / ".codex" / "state_0001.sqlite"
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
            reasoning_effort text
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
) -> Path:
    codex_dir = bundle_dir / "codex" / "sessions" / "2026" / "04" / "10"
    codex_dir.mkdir(parents=True, exist_ok=True)
    session_file = codex_dir / f"rollout-2026-04-10T10-00-00-{session_id}.jsonl"
    session_file.write_text(
        json.dumps(
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
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
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
        {
            "timestamp": "2026-04-10T10:06:00Z",
            "type": "message",
            "payload": {"role": "assistant", "text": "reply"},
        },
    ]
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

    def test_collect_known_bundle_summaries_infers_export_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            home = Path(tmpdir) / "home"
            workspace.mkdir()
            paths = CodexPaths(home=home)

            new_single = (
                workspace
                / "codex_sessions"
                / "MacBook-Pro-A"
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
                / "codex_sessions"
                / "Studio-Mac"
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
                / "codex_sessions"
                / "MacBook-Pro-A"
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

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            rows = conn.execute("select id, cwd from threads order by id").fetchall()
            conn.close()
            self.assertEqual(
                rows,
                [
                    (source_root_session, str(target_project)),
                    (source_nested_session, str(target_project / "packages" / "ui")),
                ],
            )

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

            conn = sqlite3.connect(dst_home / ".codex" / "state_0001.sqlite")
            row = conn.execute("select source, model_provider, cwd from threads where id = ?", (session_id,)).fetchone()
            conn.close()
            self.assertEqual(row, ("vscode", "target-provider", str(missing_cwd)))

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

            self.assertEqual(result.desktop_retagged, 1)
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
            self.assertEqual(cli_payload["source"], "vscode")
            self.assertEqual(cli_payload["originator"], "Codex Desktop")

            index_lines = (home / ".codex" / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(index_lines), 2)
            index_by_id = {json.loads(raw)["id"]: json.loads(raw) for raw in index_lines}
            self.assertEqual(index_by_id[desktop_id]["thread_name"], "desktop message")
            self.assertEqual(index_by_id[cli_id]["thread_name"], "cli message")

            state_data = json.loads((home / ".codex" / ".codex-global-state.json").read_text(encoding="utf-8"))
            self.assertIn(str(desktop_cwd), state_data["electron-saved-workspace-roots"])
            self.assertIn(str(cli_cwd), state_data["electron-saved-workspace-roots"])

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
                "codex_session_toolkit.services.importing.write_batch_skills_restore_report",
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


if __name__ == "__main__":
    unittest.main()
