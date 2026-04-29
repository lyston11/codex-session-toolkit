# Changelog

## Unreleased

### Highlights

- Added session-bound Skill export/import so custom Skills can travel with Bundles across devices
- Added project-path session browsing, project-scoped export, and project-folder guided import
- Improved batch import defaults with best-effort Skill restore, conflict skip, and missing/failure summaries
- Clarified stable API/TUI compatibility boundaries and kept legacy wrappers as forwarding-only shims

### Bundle / Transfer

- Export now records optional `skills_manifest.json` metadata and bundled custom Skill payloads
- Import now distinguishes restored, already present, conflict skipped, missing, and failed Skill states
- Batch import writes a per-run Skill restore report for post-import review
- Bundle browser surfaces packaged Skill metadata so imported history is easier to inspect

### TUI / CLI / Docs

- TUI project import/export flows were split into smaller stateful modules for easier maintenance
- CLI subcommands now accept explicit `--skills-mode` handling for export and import flows
- README now documents project-based migration, Skill transport semantics, and release workflow

## 0.1.0

- Initial public release of Codex Session Toolkit
