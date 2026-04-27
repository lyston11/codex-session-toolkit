import os
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(SRC_DIR))

from codex_session_toolkit.models import BundleSummary  # noqa: E402
from codex_session_toolkit.tui.bundle_state import (  # noqa: E402
    build_bundle_filter_state,
    build_category_folder_options,
    build_machine_folder_options,
    build_project_folder_options,
)


def make_bundle(
    session_id: str,
    *,
    bundle_dir: str,
    machine_key: str = "",
    machine_label: str = "",
    export_group: str = "",
    export_group_label: str = "",
    project_key: str = "",
    project_label: str = "",
    project_path: str = "",
) -> BundleSummary:
    return BundleSummary(
        source_group="all",
        session_id=session_id,
        bundle_dir=Path(bundle_dir),
        relative_path="sessions/x.jsonl",
        updated_at="2026-04-11T10:00:00Z",
        exported_at="2026-04-11T10:00:00Z",
        thread_name=session_id,
        session_cwd="/tmp/project",
        session_kind="desktop",
        source_machine=machine_label,
        source_machine_key=machine_key,
        export_group=export_group,
        export_group_label=export_group_label or export_group,
        project_key=project_key,
        project_label=project_label,
        project_path=project_path,
    )


class TuiBundleStateTests(unittest.TestCase):
    def test_build_bundle_filter_state_normalizes_filters_by_visible_entries(self) -> None:
        entries = [
            make_bundle("session-a", bundle_dir="/tmp/a", machine_key="machine-a", machine_label="Machine A", export_group="active"),
            make_bundle("session-b", bundle_dir="/tmp/b", machine_key="machine-b", machine_label="Machine B", export_group="project"),
            make_bundle("session-c", bundle_dir="/tmp/c", machine_key="machine-a", machine_label="Machine A", export_group="single"),
        ]

        state = build_bundle_filter_state(
            entries,
            machine_filter="machine-a",
            export_group_filter="project",
        )

        self.assertEqual(state.normalized_machine_filter, "machine-a")
        self.assertEqual(state.normalized_export_group_filter, "")
        self.assertEqual(state.current_machine_label, "Machine A")
        self.assertEqual(state.current_export_group_label, "全部导出方式")
        self.assertEqual(state.machine_options[1:], [("machine-a", "Machine A"), ("machine-b", "Machine B")])
        self.assertEqual(state.export_group_options, [("", "全部导出方式"), ("active", "active"), ("single", "single")])

    def test_build_machine_and_category_folder_options_are_grouped_and_ordered(self) -> None:
        entries = [
            make_bundle("session-a", bundle_dir="/tmp/a", machine_key="machine-a", machine_label="Machine A", export_group="single"),
            make_bundle("session-b", bundle_dir="/tmp/b", machine_key="machine-a", machine_label="Machine A", export_group="active"),
            make_bundle("session-c", bundle_dir="/tmp/c", machine_key="machine-a", machine_label="Machine A", export_group="custom", export_group_label="自定义目录"),
        ]

        machine_options = build_machine_folder_options(entries)
        category_options = build_category_folder_options(entries)

        self.assertEqual(len(machine_options), 1)
        self.assertEqual(machine_options[0].machine_key, "machine-a")
        self.assertEqual(machine_options[0].bundle_count, 3)
        self.assertEqual(machine_options[0].export_groups, ("active", "single", "custom"))
        self.assertEqual([option.export_group for option in category_options], ["active", "single", "custom"])

    def test_build_project_folder_options_uses_local_target_resolver(self) -> None:
        entries = [
            make_bundle(
                "session-a",
                bundle_dir="/tmp/MachineA/project/project-a/20260411-100000-000001/session-a",
                export_group="project",
                project_label="Project A",
                project_path="/source/project-a",
            ),
            make_bundle(
                "session-b",
                bundle_dir="/tmp/MachineA/project/project-a/20260411-100000-000001/session-b",
                export_group="project",
                project_label="Project A",
                project_path="/source/project-a",
            ),
        ]

        project_options = build_project_folder_options(
            entries,
            local_target_resolver=lambda label, path: ("/local/project-a", "same_name"),
        )

        self.assertEqual(len(project_options), 1)
        self.assertEqual(project_options[0].project_label, "Project A")
        self.assertEqual(project_options[0].bundle_count, 2)
        self.assertEqual(project_options[0].local_status, "same_name")
        self.assertEqual(project_options[0].local_status_label, "同名项目可用")
        self.assertEqual(project_options[0].local_target_path, "/local/project-a")


if __name__ == "__main__":
    unittest.main()
