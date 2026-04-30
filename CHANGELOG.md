# Changelog

## Unreleased

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
