"""UI panel rendering and detail screens extracted from the TUI app shell."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, List, Optional, Tuple

from .terminal import (
    Ansi,
    align_line,
    app_logo_lines,
    ellipsize_middle,
    glyphs,
    read_key,
    render_box,
    style_text,
)

if TYPE_CHECKING:
    from ..models import BundleSummary, SessionSummary
    from .app import ToolkitTuiApp


def show_detail_panel(
    app: "ToolkitTuiApp",
    title: str,
    lines: List[str],
    *,
    border_codes: Optional[Tuple[str, ...]] = None,
) -> None:
    box_width = app._print_branded_header(title)
    for line in render_box(lines, width=box_width, border_codes=border_codes or (Ansi.DIM, Ansi.BLUE)):
        print(line)
    print("")
    input(style_text("按 Enter 返回...", Ansi.DIM))


def session_action_center(app: "ToolkitTuiApp", summary: "SessionSummary") -> None:
    pointer = glyphs().get("pointer", ">")
    actions = [
        {"key": "e", "label": "导出该会话为 Bundle", "color": Ansi.MAGENTA},
        {"key": "q", "label": "返回", "color": Ansi.DIM},
    ]
    selected_index = 0

    while True:
        box_width = app._print_branded_header("会话详情 / 导出")
        for line in render_box(app._session_detail_lines(summary), width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")

        action_lines: List[str] = []
        for idx, action in enumerate(actions):
            label = f"[{action['key']}] {action['label']}"
            if idx == selected_index:
                action_lines.append(style_text(f"{pointer} {label}", Ansi.BOLD, Ansi.UNDERLINE, action["color"]))
            else:
                action_lines.append("  " + style_text(f"[{action['key']}]", Ansi.DIM, action["color"]) + f" {action['label']}")
        for line in render_box(action_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
            print(line)
        print("")
        print(style_text("按键：↑/↓ 选择 · Enter 执行 · e 快捷 · q 返回", Ansi.DIM))

        key = read_key()
        if key is None:
            raw = input("命令 [Enter/e/q]：").strip()
            key = raw if raw else "ENTER"

        if key in ("UP", "k", "K"):
            selected_index = (selected_index - 1) % len(actions)
            continue
        if key in ("DOWN", "j", "J"):
            selected_index = (selected_index + 1) % len(actions)
            continue

        action_key = actions[selected_index]["key"] if key == "ENTER" else str(key).strip().lower()
        if action_key in {"q", "esc", "0"} or key == "ESC":
            return
        if action_key == "e":
            app._run_action(
                f"导出会话 {summary.session_id} 为 Bundle",
                ["export", summary.session_id],
                dry_run=False,
                runner=lambda: app._run_toolkit(["export", summary.session_id]),
                danger=False,
            )


def bundle_action_center(app: "ToolkitTuiApp", bundle: "BundleSummary") -> None:
    pointer = glyphs().get("pointer", ">")
    actions = [
        {"key": "i", "label": "导入该 Bundle 为会话", "color": Ansi.GREEN},
        {"key": "v", "label": "导入该 Bundle 为会话并自动创建工作目录", "color": Ansi.CYAN},
        {"key": "q", "label": "返回", "color": Ansi.DIM},
    ]
    selected_index = 0

    while True:
        box_width = app._print_branded_header("Bundle 详情 / 导入")
        for line in render_box(app._bundle_detail_lines(bundle), width=box_width, border_codes=(Ansi.DIM, Ansi.GREEN)):
            print(line)
        print("")

        action_lines: List[str] = []
        for idx, action in enumerate(actions):
            label = f"[{action['key']}] {action['label']}"
            if idx == selected_index:
                action_lines.append(style_text(f"{pointer} {label}", Ansi.BOLD, Ansi.UNDERLINE, action["color"]))
            else:
                action_lines.append("  " + style_text(f"[{action['key']}]", Ansi.DIM, action["color"]) + f" {action['label']}")
        for line in render_box(action_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
            print(line)
        print("")
        print(style_text("按键：↑/↓ 选择 · Enter 执行 · i/v 快捷 · q 返回", Ansi.DIM))

        key = read_key()
        if key is None:
            raw = input("命令 [Enter/i/v/q]：").strip()
            key = raw if raw else "ENTER"

        if key in ("UP", "k", "K"):
            selected_index = (selected_index - 1) % len(actions)
            continue
        if key in ("DOWN", "j", "J"):
            selected_index = (selected_index + 1) % len(actions)
            continue

        action_key = actions[selected_index]["key"] if key == "ENTER" else str(key).strip().lower()
        if action_key in {"q", "esc", "0"} or key == "ESC":
            return
        if action_key == "i":
            app._run_action(
                f"导入 Bundle {bundle.session_id} 为会话",
                ["import", str(bundle.bundle_dir)],
                dry_run=False,
                runner=lambda: app._run_toolkit(["import", str(bundle.bundle_dir)]),
                danger=False,
            )
            continue
        if action_key == "v":
            app._run_action(
                f"导入 Bundle {bundle.session_id} 为会话（自动创建目录）",
                ["import", "--desktop-visible", str(bundle.bundle_dir)],
                dry_run=False,
                runner=lambda: app._run_toolkit(["import", "--desktop-visible", str(bundle.bundle_dir)]),
                danger=False,
            )


def tui_help_text(app: "ToolkitTuiApp") -> None:
    box_width = app._print_branded_header("帮助 / 使用说明")
    lines = [
        style_text("菜单分组：", Ansi.BOLD),
        "  Session / Browse   : 浏览本机会话、按项目路径筛会话、导出单个会话或整项目会话",
        "  Bundle / Transfer  : 浏览 Bundle、校验 Bundle、批量导出与批量导入（project 分类支持按项目文件夹导入）",
        "  Repair / Maintenance : 先按目标选择 Provider 迁移、Desktop 显示修复或旧副本清理",
        "",
        style_text("常用 CLI（更完整的工具链能力）：", Ansi.BOLD),
        "  clone-provider                克隆活动会话到当前 provider",
        "  clean-clones                  清理旧版无标记副本",
        "  list [pattern]                列出本机会话",
        "  list-project-sessions <path>  列出某个项目路径下的全部会话",
        "  list-bundles [pattern]        列出 Bundle 导出记录",
        "  validate-bundles              校验 Bundle 导出目录健康度",
        "  export <session_id>           导出单个会话为 Bundle",
        "  export-project <path>         批量导出某个项目路径下的全部会话",
        "  export-desktop-all            批量导出全部 Desktop 会话为 Bundle（含 archived）",
        "  export-active-desktop-all     批量导出全部 Active Desktop 会话为 Bundle",
        "  export-cli-all                批量导出全部 CLI 会话为 Bundle",
        "  import <session_id|bundle_dir> 导入单个 Bundle 为会话",
        "  import-desktop-all            先选设备文件夹，再选分类文件夹；project 分类会继续选项目文件夹",
        "  repair-desktop                修复会话在 Desktop 中的显示与登记",
        "",
        style_text("兼容入口参数：", Ansi.BOLD),
        "  --dry-run          模拟运行（不写入/不删除）",
        "  --clean            清理旧版无标记副本（删除）",
        "  --no-tui           即使无参数也不进菜单（直接执行克隆）",
        "",
        style_text("示例：", Ansi.BOLD),
        f"  {app._cli_preview(('clone-provider', '--dry-run'))}",
        f"  {app._cli_preview(('list-project-sessions', '/Users/example/project-a'))}",
        f"  {app._cli_preview(('list-bundles', '--source', 'desktop'))}",
        f"  {app._cli_preview(('validate-bundles', '--source', 'desktop'))}",
        f"  {app._cli_preview(('export-project', '/Users/example/project-a'))}",
        f"  {app._cli_preview(('export-cli-all', '--dry-run'))}",
        f"  {app._cli_preview(('export', '019d582f-e8f4-7ce3-9948-c0406b4faaf2'))}",
        f"  {app._cli_preview(('import-desktop-all',))}",
        f"  {app._cli_preview(('import-desktop-all', '--machine', 'Work-Laptop', '--project', 'project-a', '--target-project-path', '/Users/example/project-a'))}",
        f"  {app._cli_preview(('import-desktop-all', '--machine', 'Work-Laptop', '--export-group', 'active'))}",
        f"  {app._cli_preview(('repair-desktop', '--dry-run'))}",
        "",
        style_text("终端兼容：", Ansi.BOLD),
        "  NO_COLOR=1         关闭颜色输出",
        "  CST_ASCII_UI=1     强制使用 ASCII 边框（不支持 Unicode 时可用）",
        "  CST_TUI_MAX_WIDTH= 限制 TUI 最大宽度（用于超宽终端）",
        "  CST_MACHINE_LABEL= 覆盖导出 Bundle 所使用的机器标识",
        "",
        style_text("TUI 结构：", Ansi.BOLD),
        "  首页先选择功能域，再回车进入该功能页。",
        "  功能页内部再选择必要范围和是否 Dry-run；二级菜单同样支持 ↑/↓/Enter。",
        "",
        style_text("TUI 快捷键：", Ansi.BOLD),
        "  首页：↑/↓ 选择功能域，Enter 进入，q 退出",
        "  功能页：↑/↓ 选择动作，Enter 执行，q / ← 返回首页",
        "  功能页：←/→ 或 PgUp/PgDn 切换上一个 / 下一个功能页",
        "  二级菜单：↑/↓ 选择执行方式或范围，Enter 确认，q / ← 返回",
        "  h                  打开帮助",
        "  0                  直接退出",
        "",
        style_text("浏览器说明：", Ansi.BOLD),
        "  /                  在会话列表 / Bundle 列表中搜索",
        "  Enter              在浏览模式下进入单条操作面板，在选择模式下直接确认",
        "  d                  只打开详情面板，不执行导入/导出",
        "  e                  在会话列表直接导出为 Bundle",
        "  x                  在项目会话列表直接导出这个项目下的全部会话",
        "  p                  在项目会话列表重新输入项目路径",
        "  s                  在 Bundle 列表切换导出方式",
        "  m                  在 Bundle 列表按导出机器切换",
        "  l                  在 Bundle 列表切换“全部历史 / 仅最新”",
        "  i / v              在 Bundle 列表直接导入为会话 / 导入为会话并自动建目录",
    ]
    for line in render_box(lines, width=box_width, border_codes=(Ansi.DIM,)):
        print(line)
    print("")
    input("按 Enter 返回菜单...")


def render_home(app: "ToolkitTuiApp", selected_section_index: int) -> None:
    box_width, center = app._screen_layout()
    pointer = glyphs().get("pointer", ">")
    output_lines: List[str] = []
    selected_section = app.menu_sections[selected_section_index]

    for line in app_logo_lines(max_width=100):
        output_lines.append(align_line(line, box_width, center=center))
    output_lines.append(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
    output_lines.append(align_line(style_text("选择一个功能域，回车进入对应功能页", Ansi.DIM), box_width, center=center))
    output_lines.append(align_line(app._section_tabs_line(selected_section_index, box_width), box_width, center=center))
    output_lines.append("")

    info_lines = [
        f"{style_text('Provider', Ansi.DIM)} : {style_text(app.context.target_provider, Ansi.BOLD, Ansi.CYAN)}"
        f"  {style_text('Sessions', Ansi.DIM)} : {ellipsize_middle(app.context.active_sessions_dir, max(16, box_width - 40))}",
        f"{style_text('Config', Ansi.DIM)} : {ellipsize_middle(app.context.config_path, max(16, box_width - 18))}",
    ]
    for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
        output_lines.append(line)
    output_lines.append("")

    section_nav_lines = [style_text("功能域导航", Ansi.BOLD)]
    for pos, menu_section in enumerate(app.menu_sections):
        section_color = app._section_color(menu_section)
        header = f"[{pos + 1}] {menu_section.title}"
        if pos == selected_section_index:
            section_nav_lines.append(style_text(f"{pointer} {header}", Ansi.BOLD, Ansi.UNDERLINE, section_color))
        else:
            section_nav_lines.append("  " + style_text(header, Ansi.DIM, section_color))
    for line in render_box(section_nav_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
        output_lines.append(line)
    output_lines.append("")

    selected_actions = app._actions_for_section(selected_section.section_id)
    preview_labels = " / ".join(action.label for _, action in selected_actions[:3])
    if len(selected_actions) > 3:
        preview_labels += " / ..."
    summary_lines = [
        style_text(selected_section.title, Ansi.BOLD, app._section_color(selected_section)),
        f"{style_text('动作数', Ansi.DIM)} : {len(selected_actions)}",
    ]
    for note in app._section_notes(selected_section)[:1]:
        summary_lines.append(f"{style_text('说明', Ansi.DIM)} : {note}")
    summary_lines.append(f"{style_text('包含动作', Ansi.DIM)} : {preview_labels}")
    for line in render_box(summary_lines, width=box_width, border_codes=selected_section.border_codes):
        output_lines.append(line)
    output_lines.append("")

    output_lines.append(style_text("Enter 进入功能页  |  ↑/↓ 选择功能域  |  h 帮助  |  q 退出", Ansi.DIM))
    if os.name == "nt":
        output_lines.append(style_text(f"提示：先运行 .\\install.ps1，再用 .\\{app.context.entry_command}.cmd 启动", Ansi.DIM))
    else:
        output_lines.append(style_text(f"提示：先运行 ./install.sh，再用 ./{app.context.entry_command} 启动", Ansi.DIM))

    hide_cursor = "\033[?25l"
    show_cursor = "\033[?25h"
    home_cursor = "\033[H"
    clear_to_eol = "\033[K"
    clear_to_eos = "\033[J"
    visible_lines = app._fit_lines_to_screen(output_lines)
    full_output = "\n".join(line + clear_to_eol for line in visible_lines) + "\n"
    sys.stdout.write(hide_cursor + home_cursor + full_output + clear_to_eos + show_cursor)
    sys.stdout.flush()


def render_section_page(app: "ToolkitTuiApp", section_index: int, action_offset: int) -> None:
    box_width, center = app._screen_layout()
    screen_height = app._screen_height()
    pointer = glyphs().get("pointer", ">")
    output_lines: List[str] = []

    menu_section = app.menu_sections[section_index]
    section_actions = app._actions_for_section(menu_section.section_id)
    if not section_actions:
        return

    action_offset = max(0, min(action_offset, len(section_actions) - 1))
    _, selected_action = section_actions[action_offset]
    for line in app_logo_lines(max_width=100):
        output_lines.append(align_line(line, box_width, center=center))
    output_lines.append(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
    output_lines.append(align_line(style_text(f"{menu_section.title} / 功能页", Ansi.DIM), box_width, center=center))
    output_lines.append(align_line(app._section_tabs_line(section_index, box_width), box_width, center=center))
    output_lines.append("")

    info_lines = [
        f"{style_text('当前动作', Ansi.DIM)} : {style_text(selected_action.label, Ansi.BOLD, app._action_color(selected_action))}",
        f"{style_text('执行方式', Ansi.DIM)} : 直接在 TUI 中执行",
        f"{style_text('目标 Provider', Ansi.DIM)} : {style_text(app.context.target_provider, Ansi.BOLD, Ansi.CYAN)}",
    ]
    for note in app._action_notes(selected_action)[:1]:
        info_lines.append(f"{style_text('说明', Ansi.DIM)} : {note}")
    for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
        output_lines.append(line)
    output_lines.append("")

    section_lines = [style_text(menu_section.title, Ansi.BOLD)]
    reserved_rows = len(output_lines) + 2
    max_visible_actions = max(3, screen_height - reserved_rows - 4)
    start, end = app._action_window(len(section_actions), action_offset, max_visible_actions)
    if start > 0:
        section_lines.append(style_text("... 上方还有更多动作 ...", Ansi.DIM))
    for offset in range(start, end):
        _, menu_action = section_actions[offset]
        hotkey = f"[{menu_action.hotkey}]"
        label = f"{hotkey} {menu_action.label}"
        if offset == action_offset:
            prefix = style_text(pointer, Ansi.BOLD, Ansi.BRIGHT_CYAN) + " "
            section_lines.append(prefix + style_text(label, Ansi.BOLD, Ansi.UNDERLINE, app._action_color(menu_action)))
        else:
            section_lines.append("  " + style_text(hotkey, Ansi.DIM, app._action_color(menu_action)) + " " + menu_action.label)
    if end < len(section_actions):
        section_lines.append(style_text("... 下方还有更多动作 ...", Ansi.DIM))
    for line in render_box(section_lines, width=box_width, border_codes=menu_section.border_codes):
        output_lines.append(line)
    output_lines.append("")

    output_lines.append(style_text("↑/↓ 选择动作  |  Enter 执行  |  ←/q 返回首页  |  →/PgDn 下一功能页  |  PgUp 上一功能页", Ansi.DIM))

    hide_cursor = "\033[?25l"
    show_cursor = "\033[?25h"
    home_cursor = "\033[H"
    clear_to_eol = "\033[K"
    clear_to_eos = "\033[J"
    visible_lines = app._fit_lines_to_screen(output_lines)
    full_output = "\n".join(line + clear_to_eol for line in visible_lines) + "\n"
    sys.stdout.write(hide_cursor + home_cursor + full_output + clear_to_eos + show_cursor)
    sys.stdout.flush()
