import ast
import unittest
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT_DIR / "src" / "codex_session_toolkit"
PACKAGE_NAME = "codex_session_toolkit"

ENTRYPOINT_MODULES = {
    "codex_session_toolkit.__main__",
    "codex_session_toolkit.cli",
    "codex_session_toolkit.command_parser",
    "codex_session_toolkit.commands",
}
APPLICATION_MODULES = {
    "codex_session_toolkit.application",
}
PUBLIC_FACADE_MODULES = {
    "codex_session_toolkit",
    "codex_session_toolkit.api",
}
COMPATIBILITY_FACADE_MODULES = {
    "codex_session_toolkit.core",
    "codex_session_toolkit.stores.bundles",
    "codex_session_toolkit.terminal_ui",
    "codex_session_toolkit.tui_app",
}
SHARED_MODULES = {
    "codex_session_toolkit.errors",
    "codex_session_toolkit.command_catalog",
    "codex_session_toolkit.models",
    "codex_session_toolkit.paths",
    "codex_session_toolkit.support",
    "codex_session_toolkit.validation",
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([PACKAGE_NAME] + parts)


def _layer_for_module(module_name: str) -> str:
    if module_name == "codex_session_toolkit.presenters":
        return "presentation"
    if module_name == "codex_session_toolkit.services":
        return "service"
    if module_name == "codex_session_toolkit.stores":
        return "store"
    if module_name == "codex_session_toolkit.tui":
        return "tui"
    if module_name in ENTRYPOINT_MODULES:
        return "entrypoint"
    if module_name in APPLICATION_MODULES:
        return "application"
    if module_name.startswith("codex_session_toolkit.application."):
        return "application"
    if module_name in PUBLIC_FACADE_MODULES:
        return "public_facade"
    if module_name in COMPATIBILITY_FACADE_MODULES:
        return "compatibility_facade"
    if module_name in SHARED_MODULES:
        return "shared"
    if module_name.startswith("codex_session_toolkit.presenters."):
        return "presentation"
    if module_name.startswith("codex_session_toolkit.services."):
        return "service"
    if module_name.startswith("codex_session_toolkit.stores."):
        return "store"
    if module_name.startswith("codex_session_toolkit.tui."):
        return "tui"
    return "unclassified"


def _internal_imports(path: Path) -> Iterable[str]:
    module_name = _module_name(path)
    package_parts = module_name.split(".") if path.name == "__init__.py" else module_name.split(".")[:-1]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE_NAME or alias.name.startswith(PACKAGE_NAME + "."):
                    yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix_parts = package_parts[: len(package_parts) - node.level + 1]
                imported = ".".join(prefix_parts + ([node.module] if node.module else []))
            else:
                imported = node.module or ""
            if imported == PACKAGE_NAME or imported.startswith(PACKAGE_NAME + "."):
                yield imported


def _is_type_checking_guard(test: ast.AST) -> bool:
    return isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"


def _runtime_internal_imports(path: Path) -> Iterable[str]:
    module_name = _module_name(path)
    package_parts = module_name.split(".") if path.name == "__init__.py" else module_name.split(".")[:-1]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def visit(node: ast.AST, *, in_type_checking: bool = False) -> Iterable[str]:
        next_in_type_checking = in_type_checking
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            next_in_type_checking = True
        if not next_in_type_checking:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == PACKAGE_NAME or alias.name.startswith(PACKAGE_NAME + "."):
                        yield alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    prefix_parts = package_parts[: len(package_parts) - node.level + 1]
                    imported = ".".join(prefix_parts + ([node.module] if node.module else []))
                else:
                    imported = node.module or ""
                if imported == PACKAGE_NAME or imported.startswith(PACKAGE_NAME + "."):
                    yield imported
        for child in ast.iter_child_nodes(node):
            yield from visit(child, in_type_checking=next_in_type_checking)

    yield from visit(tree)


def _source_modules() -> dict[str, Path]:
    modules = {}
    for path in PACKAGE_ROOT.rglob("*.py"):
        modules[_module_name(path)] = path
    return modules


def _resolve_imported_module(imported: str, modules: dict[str, Path]) -> str:
    if imported in modules:
        return imported
    candidates = [module_name for module_name in modules if module_name.startswith(imported + ".")]
    return min(candidates) if candidates else ""


def _assert_no_blocked_imports(
    test_case: unittest.TestCase,
    *,
    layer: str,
    blocked_prefixes: tuple[str, ...],
) -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        module_name = _module_name(path)
        if _layer_for_module(module_name) != layer:
            continue
        for imported in _internal_imports(path):
            test_case.assertFalse(
                imported.startswith(blocked_prefixes),
                f"{module_name} must not import {imported}",
            )


class ArchitectureContractsTests(unittest.TestCase):
    def test_every_source_module_has_an_engineering_layer(self) -> None:
        unclassified = []
        for path in PACKAGE_ROOT.rglob("*.py"):
            module_name = _module_name(path)
            if _layer_for_module(module_name) == "unclassified":
                unclassified.append(str(path.relative_to(ROOT_DIR)))
        self.assertEqual(unclassified, [])

    def test_store_layer_does_not_depend_on_workflows_or_ui(self) -> None:
        _assert_no_blocked_imports(
            self,
            layer="store",
            blocked_prefixes=(
                "codex_session_toolkit.api",
                "codex_session_toolkit.cli",
                "codex_session_toolkit.application",
                "codex_session_toolkit.command_parser",
                "codex_session_toolkit.commands",
                "codex_session_toolkit.core",
                "codex_session_toolkit.presenters",
                "codex_session_toolkit.services",
                "codex_session_toolkit.tui",
            ),
        )

    def test_service_layer_does_not_depend_on_entrypoints_or_ui(self) -> None:
        _assert_no_blocked_imports(
            self,
            layer="service",
            blocked_prefixes=(
                "codex_session_toolkit.api",
                "codex_session_toolkit.cli",
                "codex_session_toolkit.application",
                "codex_session_toolkit.command_parser",
                "codex_session_toolkit.commands",
                "codex_session_toolkit.core",
                "codex_session_toolkit.presenters",
                "codex_session_toolkit.tui",
            ),
        )

    def test_application_layer_does_not_depend_on_entrypoints_or_ui(self) -> None:
        _assert_no_blocked_imports(
            self,
            layer="application",
            blocked_prefixes=(
                "codex_session_toolkit.api",
                "codex_session_toolkit.cli",
                "codex_session_toolkit.command_parser",
                "codex_session_toolkit.commands",
                "codex_session_toolkit.core",
                "codex_session_toolkit.tui",
            ),
        )

    def test_tui_flow_modules_do_not_runtime_import_app_shell(self) -> None:
        flow_paths = [
            path for path in (PACKAGE_ROOT / "tui").glob("*.py")
            if path.name not in {"__init__.py", "app.py"}
        ]
        offenders = []
        for path in flow_paths:
            for imported in _runtime_internal_imports(path):
                if imported == "codex_session_toolkit.tui.app":
                    offenders.append(str(path.relative_to(ROOT_DIR)))
        self.assertEqual(offenders, [])

    def test_runtime_internal_import_graph_has_no_cycles(self) -> None:
        modules = _source_modules()
        edges = {module_name: set() for module_name in modules}
        for module_name, path in modules.items():
            for imported in _runtime_internal_imports(path):
                target = _resolve_imported_module(imported, modules)
                if target and target != module_name:
                    edges[module_name].add(target)

        visiting = set()
        visited = set()
        stack: list[str] = []
        cycles: list[list[str]] = []

        def visit(module_name: str) -> None:
            visiting.add(module_name)
            stack.append(module_name)
            for target in edges[module_name]:
                if target in visiting:
                    cycles.append(stack[stack.index(target):] + [target])
                elif target not in visited:
                    visit(target)
            stack.pop()
            visiting.remove(module_name)
            visited.add(module_name)

        for module_name in modules:
            if module_name not in visited:
                visit(module_name)

        self.assertEqual([" -> ".join(cycle) for cycle in cycles], [])

    def test_presentation_layer_does_not_depend_on_workflows_or_storage(self) -> None:
        _assert_no_blocked_imports(
            self,
            layer="presentation",
            blocked_prefixes=(
                "codex_session_toolkit.api",
                "codex_session_toolkit.cli",
                "codex_session_toolkit.application",
                "codex_session_toolkit.command_parser",
                "codex_session_toolkit.commands",
                "codex_session_toolkit.core",
                "codex_session_toolkit.services",
                "codex_session_toolkit.stores",
                "codex_session_toolkit.tui",
            ),
        )
