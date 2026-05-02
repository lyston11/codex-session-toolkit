"""Bundle layout metadata and path inference helpers."""

from __future__ import annotations

from pathlib import Path

from ..support import normalize_project_path, project_label_from_path


LEGACY_MACHINE_KEY = "_legacy"
LEGACY_MACHINE_LABEL = "旧布局"
LEGACY_EXPORT_GROUP = "legacy"
CUSTOM_EXPORT_GROUP = "custom"
LEGACY_ROOT_DIRS = {"bundles", "desktop_bundles"}
CANONICAL_EXPORT_GROUPS = {
    "single",
    "cli",
    "desktop",
    "active",
    "project",
}
LEGACY_EXPORT_GROUP_ALIASES = {
    "single_exports": "single",
    "cli_batches": "cli",
    "desktop_all_batches": "desktop",
    "desktop_active_batches": "active",
}
KNOWN_BUNDLE_GROUPS = CANONICAL_EXPORT_GROUPS | set(LEGACY_EXPORT_GROUP_ALIASES)
EXPORT_GROUP_LABELS = {
    "single": "single",
    "cli": "cli",
    "desktop": "desktop",
    "active": "active",
    "project": "project",
    LEGACY_EXPORT_GROUP: "旧布局",
    CUSTOM_EXPORT_GROUP: "自定义目录",
}
EXPORT_GROUP_ORDER = (
    "desktop",
    "active",
    "cli",
    "project",
    "single",
    LEGACY_EXPORT_GROUP,
    CUSTOM_EXPORT_GROUP,
)


def bundle_export_group_label(export_group: str) -> str:
    return EXPORT_GROUP_LABELS.get(export_group, export_group or "未知导出方式")


def canonical_export_group_name(export_group: str) -> str:
    if not export_group:
        return ""
    return LEGACY_EXPORT_GROUP_ALIASES.get(export_group, export_group)


def source_group_allows_export_group(source_group: str, export_group: str) -> bool:
    canonical = canonical_export_group_name(export_group)
    if canonical in {LEGACY_EXPORT_GROUP, CUSTOM_EXPORT_GROUP}:
        return True
    if source_group in {"", "all"}:
        return True
    if source_group == "bundle":
        return canonical in {"single", "cli", "project"}
    if source_group == "desktop":
        return canonical in {"desktop", "active"}
    return True


def infer_bundle_machine(bundle_root: Path, bundle_dir: Path, manifest: dict) -> tuple[str, str]:
    manifest_key = manifest.get("EXPORT_MACHINE_KEY", "")
    manifest_label = manifest.get("EXPORT_MACHINE", "")
    if manifest_key:
        return manifest_key, manifest_label or manifest_key

    try:
        relative_parts = bundle_dir.relative_to(bundle_root).parts
    except ValueError:
        relative_parts = ()

    if len(relative_parts) >= 5 and relative_parts[1] == "sessions" and relative_parts[2] in KNOWN_BUNDLE_GROUPS:
        machine_key = relative_parts[0]
        return machine_key, manifest_label or machine_key

    if len(relative_parts) >= 4 and relative_parts[1] in KNOWN_BUNDLE_GROUPS:
        machine_key = relative_parts[0]
        return machine_key, manifest_label or machine_key

    return LEGACY_MACHINE_KEY, manifest_label or LEGACY_MACHINE_LABEL


def infer_bundle_export_group(bundle_root: Path, bundle_dir: Path) -> tuple[str, str]:
    try:
        relative_parts = bundle_dir.relative_to(bundle_root).parts
    except ValueError:
        relative_parts = ()

    if len(relative_parts) >= 5 and relative_parts[1] == "sessions" and relative_parts[2] in KNOWN_BUNDLE_GROUPS:
        export_group = canonical_export_group_name(relative_parts[2])
    elif len(relative_parts) >= 4 and relative_parts[1] in KNOWN_BUNDLE_GROUPS:
        export_group = canonical_export_group_name(relative_parts[1])
    elif len(relative_parts) >= 3 and relative_parts[0] in KNOWN_BUNDLE_GROUPS:
        export_group = canonical_export_group_name(relative_parts[0])
    else:
        export_group = CUSTOM_EXPORT_GROUP

    return export_group, bundle_export_group_label(export_group)


def load_project_export_metadata(batch_dir: Path) -> tuple[str, str]:
    manifest_file = batch_dir / "_project_export_manifest.txt"
    if not manifest_file.is_file():
        return "", ""

    project_label = ""
    project_path = ""
    try:
        with manifest_file.open("r", encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped.startswith("#"):
                    continue
                payload = stripped[1:].strip()
                if "=" not in payload:
                    continue
                key, value = payload.split("=", 1)
                if key == "project_label":
                    project_label = value.strip()
                elif key == "project_path":
                    project_path = normalize_project_path(value.strip())
    except OSError:
        return "", ""
    return project_label, project_path


def infer_bundle_project_metadata(
    bundle_root: Path,
    bundle_dir: Path,
    export_group: str,
    project_batch_cache: dict[Path, tuple[str, str]],
) -> tuple[str, str, str]:
    if export_group != "project":
        return "", "", ""

    try:
        relative_parts = bundle_dir.relative_to(bundle_root).parts
    except ValueError:
        relative_parts = ()

    project_key = ""
    if len(relative_parts) >= 6 and relative_parts[1] == "sessions" and relative_parts[2] == "project":
        project_key = relative_parts[3]
    elif len(relative_parts) >= 5 and relative_parts[1] == "project":
        project_key = relative_parts[2]
    elif len(relative_parts) >= 4 and relative_parts[0] == "project":
        project_key = relative_parts[1]
    if not project_key:
        return "", "", ""

    batch_dir = bundle_dir.parent
    if batch_dir not in project_batch_cache:
        project_batch_cache[batch_dir] = load_project_export_metadata(batch_dir)
    project_label, project_path = project_batch_cache[batch_dir]
    if not project_label:
        project_label = project_label_from_path(project_path) or project_key
    return project_key, project_label, project_path
