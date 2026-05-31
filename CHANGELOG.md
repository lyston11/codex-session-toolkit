# Changelog

## 1.0.0 - 2026-05-10

### Desktop Visibility

- Import and batch import now update Desktop sidebar state, including workspace hints, project thread order, workspace roots, and expanded sidebar sections, without automatically pinning imported threads
- Imported Desktop-visible threads are promoted in Desktop SQLite so they appear inside Desktop's limited recent thread pool even when many old conversations exist
- `repair-desktop` now repairs blank managed `thread_source` values and prunes stale managed Desktop `threads` rows that point to missing or archived rollout files

### Thread Titles

- Export, import, and Desktop repair now recover real short titles from rollout `thread_name_updated` events when SQLite or Bundle titles are missing
- Prompt/title comparison now normalizes whitespace and ignores injected meta context such as `AGENTS.md` instructions, preventing first prompts from replacing Desktop thread names

### Skills

- TUI Skill deletion now supports multi-select deletion and select-all for local custom Skills, matching archived session cleanup
- `delete-skill` now accepts multiple Skill targets or `--all` for batch deletion from scripts
- TUI Skill export is now one selectable local Skills browser flow with current and multi-select actions; `a` selects all matching custom Skills before export
- TUI Skills Bundle import is now one selectable browser flow with current and multi-select actions; `a` selects all matching Skills Bundles before import
- `export-skills` and `import-skill-bundle` now accept multiple selected inputs, while legacy all-import remains available for scripts

### Session Export

- The recent-session browser now includes current and multi-select export actions, with `a` standardized as select-all for the current filter
- Project session browsing now supports exporting the current or selected sessions from the same list view; `a` selects all matching project sessions before export
- `export` now accepts multiple session ids, `--all`, and `--dry-run` for scriptable batch export

### Bundle Transfer

- TUI Bundle import is now one selectable browser flow with current and multi-select actions; `a` selects all matching Bundles before import
- Bundle export-all menu labels no longer use the extra “批量” prefix
- `import` now accepts multiple bundle inputs plus project remap options, while legacy `import-desktop-all` remains available for scripts
- TUI Bundle import browsing now reuses cached scan results while navigating and uses import-oriented source/category labels
- Bundle browsing is now separated from importing; the browse page manages local Bundle records and can delete selected filtered Bundles after confirmation

### TUI Selection Shortcuts

- The `a` key now has one standard meaning across multi-select feature pages: select every item matching the current filter
- Export, import, and delete actions remain explicit through `e`, `i`, or `x` after selection
- README and in-app help now document the unified select-all workflow

## 0.1.1 - 2026-04-30

### Highlights

- Added session-bound Skill export/import so custom Skills can travel with Bundles across devices
- Added project-path session browsing, project-scoped export, and project-folder guided import
- Improved batch import defaults with best-effort Skill restore, conflict skip, and missing/failure summaries
- Clarified stable API/TUI compatibility boundaries and kept legacy wrappers as forwarding-only shims
- Fixed Desktop repair so registered CLI threads keep their original source while syncing provider metadata
- Improved TUI session browsing performance and reduced redraw flicker

### Bundle / Transfer

- Export now records optional `skills_manifest.json` metadata and bundled custom Skill payloads
- Import now distinguishes restored, already present, conflict skipped, missing, and failed Skill states
- Batch import writes a per-run Skill restore report for post-import review
- Bundle browser surfaces packaged Skill metadata so imported history is easier to inspect
- Bundle browsing is now separated from importing; the browse page manages local Bundle records and can delete selected Bundles after confirmation

### TUI / CLI / Docs

- TUI project import/export flows were split into smaller stateful modules for easier maintenance
- CLI subcommands now accept explicit `--skills-mode` handling for export and import flows
- README now documents project-based migration, Skill transport semantics, and release workflow
- README and TUI prompts now clarify Desktop repair scope, archived handling, and provider rebinding behavior

### Desktop Repair

- `repair-desktop` now recognizes sessions already registered in Desktop `threads` even when their source is `cli`
- Registered CLI threads keep their original `source` and `originator` instead of being rewritten as Desktop-created sessions
- Desktop repair rebuilds `threads` rows with the target provider and prunes stale archived rows left by earlier repairs
- Thread titles can be recovered from meaningful session prompts when weak imported names are present

## 0.1.0

- Initial public release of Codex Session Toolkit
