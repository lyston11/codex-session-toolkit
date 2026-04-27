import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from codex_session_toolkit import APP_COMMAND, CodexPaths, ToolkitError, __version__, build_app_context, resolve_target_model_provider, run_cli  # noqa: E402
from codex_session_toolkit import core as core_api  # noqa: E402
import codex_session_toolkit.terminal_ui as terminal_ui_compat  # noqa: E402
import codex_session_toolkit.tui_app as tui_app_compat  # noqa: E402
from codex_session_toolkit.cli import DEFAULT_MODEL_PROVIDER, create_arg_parser  # noqa: E402
from codex_session_toolkit.tui_app import ToolkitAppContext  # noqa: E402
from codex_session_toolkit.tui.app import build_tui_menu_actions, build_tui_menu_sections  # noqa: E402
from codex_session_toolkit.tui.terminal import LOGO_FONT_BANNER  # noqa: E402


def _module_env() -> dict:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) if not existing else f"{SRC_DIR}{os.pathsep}{existing}"
    return env


class PackagingSmokeTests(unittest.TestCase):
    def test_package_root_exposes_stable_runtime_api(self) -> None:
        self.assertIs(CodexPaths, core_api.CodexPaths)
        self.assertIs(ToolkitError, core_api.ToolkitError)
        self.assertTrue(callable(build_app_context))
        self.assertTrue(callable(resolve_target_model_provider))
        self.assertTrue(callable(run_cli))

    def test_cli_parser_uses_packaged_command_name(self) -> None:
        parser = create_arg_parser()
        self.assertEqual(parser.prog, APP_COMMAND)

    def test_tui_context_uses_packaged_command_name(self) -> None:
        context = ToolkitAppContext(
            target_provider="demo-provider",
            active_sessions_dir="/tmp/demo-sessions",
            config_path="/tmp/demo-config.toml",
        )
        self.assertEqual(context.entry_command, APP_COMMAND)

    def test_tui_compat_wrappers_expose_explicit_lazy_exports(self) -> None:
        self.assertIn("ToolkitAppContext", tui_app_compat.__all__)
        self.assertIn("run_tui", tui_app_compat.__all__)
        self.assertIs(ToolkitAppContext, tui_app_compat.ToolkitAppContext)
        self.assertIn("render_box", terminal_ui_compat.__all__)
        self.assertIs(LOGO_FONT_BANNER, terminal_ui_compat.LOGO_FONT_BANNER)

    def test_tui_main_sections_are_grouped_by_domain(self) -> None:
        section_ids = [section.section_id for section in build_tui_menu_sections()]
        self.assertEqual(section_ids, ["session", "bundle", "repair"])

        actions_by_section = {}
        labels_by_action = {}
        for action in build_tui_menu_actions():
            actions_by_section.setdefault(action.section_id, set()).add(action.action_id)
            labels_by_action[action.action_id] = action.label

        self.assertEqual(actions_by_section["session"], {"list_sessions", "export_one", "project_sessions"})
        self.assertEqual(
            actions_by_section["bundle"],
            {
                "browse_bundles",
                "validate_bundles",
                "export_desktop_all",
                "export_desktop_active",
                "export_cli_all",
                "import_one",
                "import_desktop_all",
            },
        )
        self.assertEqual(
            actions_by_section["repair"],
            {
                "provider_migration",
                "desktop_repair",
                "clean_legacy",
            },
        )
        self.assertEqual(labels_by_action["provider_migration"], "迁移到当前 Provider")
        self.assertEqual(labels_by_action["desktop_repair"], "修复会话在 Desktop 中显示")
        self.assertEqual(labels_by_action["clean_legacy"], "清理旧版无标记副本")
        self.assertEqual(labels_by_action["project_sessions"], "按项目路径查看并导出会话")

    def test_logo_font_covers_toolkit_wordmark(self) -> None:
        missing = {ch for ch in "CODEX SESSION TOOLKIT" if ch != " " and ch not in LOGO_FONT_BANNER}
        self.assertEqual(missing, set())

    def test_build_app_context_reads_provider_at_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            code_dir = home / ".codex"
            code_dir.mkdir(parents=True, exist_ok=True)
            (code_dir / "config.toml").write_text('model_provider = "runtime-provider"\n', encoding="utf-8")

            context = build_app_context(CodexPaths(home=home))

        self.assertEqual(context.target_provider, "runtime-provider")
        self.assertEqual(context.active_sessions_dir, str(home / ".codex" / "sessions"))
        self.assertEqual(context.config_path, str(home / ".codex" / "config.toml"))

    def test_build_app_context_falls_back_when_config_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            context = build_app_context(CodexPaths(home=Path(tmpdir)))

        self.assertEqual(context.target_provider, DEFAULT_MODEL_PROVIDER)

    def test_core_exports_smaller_stable_api(self) -> None:
        self.assertIn("clone_to_provider", core_api.__all__)
        self.assertIn("repair_desktop", core_api.__all__)
        self.assertNotIn("parse_jsonl_records", core_api.__all__)
        self.assertNotIn("validate_jsonl_file", core_api.__all__)

    def test_core_keeps_lazy_legacy_compatibility(self) -> None:
        self.assertTrue(callable(core_api.parse_jsonl_records))
        self.assertTrue(callable(core_api.validate_jsonl_file))

    def test_package_source_avoids_internal_compatibility_imports(self) -> None:
        package_root = ROOT_DIR / "src" / "codex_session_toolkit"
        compat_paths = {
            package_root / "core.py",
            package_root / "tui_app.py",
            package_root / "terminal_ui.py",
            package_root / "stores" / "bundles.py",
        }
        blocked_imports = {
            "codex_session_toolkit.core",
            "codex_session_toolkit.tui_app",
            "codex_session_toolkit.terminal_ui",
            "codex_session_toolkit.stores.bundles",
            ".core",
            ".tui_app",
            ".terminal_ui",
            ".stores.bundles",
        }

        for path in package_root.rglob("*.py"):
            if path in compat_paths:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    module_name = "." * node.level + (node.module or "")
                    imported = {module_name}
                else:
                    continue
                self.assertTrue(
                    imported.isdisjoint(blocked_imports),
                    f"{path.relative_to(ROOT_DIR)} should import canonical modules, not compatibility facades: {sorted(imported & blocked_imports)}",
                )

    def test_module_help_mentions_packaged_command(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "codex_session_toolkit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn(f"usage: {APP_COMMAND}", result.stdout)
        self.assertIn("clone-provider", result.stdout)

    def test_module_version_matches_package_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "codex_session_toolkit", "--version"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), f"{APP_COMMAND} {__version__}")

    def test_repo_local_launcher_help_runs(self) -> None:
        result = subprocess.run(
            ["sh", "./codex-session-toolkit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn(f"usage: {APP_COMMAND}", result.stdout)
        self.assertIn("--version", result.stdout)

    def test_repo_local_launcher_prefers_source_mode_in_git_worktree(self) -> None:
        result = subprocess.run(
            ["sh", "./codex-session-toolkit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("Launcher (Source Mode)", result.stdout)

    def test_unix_install_script_help_runs(self) -> None:
        result = subprocess.run(
            ["sh", "./install.sh", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("Usage: ./install.sh", result.stdout)
        self.assertIn("--editable", result.stdout)
        self.assertIn("isolated local virtual environment", result.stdout)

    def test_installers_are_configured_for_isolated_local_venv(self) -> None:
        unix_installer = (ROOT_DIR / "scripts" / "install" / "install.unix.sh").read_text(encoding="utf-8")
        windows_installer = (ROOT_DIR / "scripts" / "install" / "install.windows.ps1").read_text(encoding="utf-8")
        unix_launcher = (ROOT_DIR / "codex-session-toolkit").read_text(encoding="utf-8")
        windows_launcher = (ROOT_DIR / "codex-session-toolkit.ps1").read_text(encoding="utf-8")
        makefile = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

        self.assertNotIn("--system-site-packages", unix_installer)
        self.assertNotIn("--system-site-packages", windows_installer)
        self.assertIn("isolated", unix_installer.lower())
        self.assertIn("isolated", windows_installer.lower())
        self.assertIn('VENV_PYTHON="$VENV_DIR/bin/python"', unix_launcher)
        self.assertIn('Join-Path $venvScriptsDir "python.exe"', windows_launcher)
        self.assertIn("install: bootstrap-editable", makefile)
        self.assertIn("DEV_PIP_PACKAGES := 'ruff>=0.6,<1.0'", makefile)
        self.assertIn("$(VENV_PYTHON) -m pip install $(DEV_PIP_PACKAGES)", makefile)

    def test_release_script_help_runs(self) -> None:
        result = subprocess.run(
            ["sh", "./release.sh", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("Usage: ./release.sh", result.stdout)
        self.assertIn("--output-dir", result.stdout)

    def test_release_folder_install_and_launcher_work_offline_for_end_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "releases"
            subprocess.run(
                ["sh", "./release.sh", "--output-dir", str(output_dir)],
                cwd=ROOT_DIR,
                env=_module_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
            release_dir = output_dir / f"{APP_COMMAND}-{__version__}"
            self.assertTrue((release_dir / "install.sh").exists())
            self.assertTrue((release_dir / "codex-session-toolkit").exists())

            install_result = subprocess.run(
                ["sh", "./install.sh", "--force"],
                cwd=release_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
            self.assertIn("Isolation: enabled", install_result.stdout)
            self.assertIn("Install complete.", install_result.stdout)

            version_result = subprocess.run(
                ["sh", "./codex-session-toolkit", "--version"],
                cwd=release_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
            self.assertIn("Launcher (Local Venv)", version_result.stdout)
            self.assertIn(f"{APP_COMMAND} {__version__}", version_result.stdout)


if __name__ == "__main__":
    unittest.main()
