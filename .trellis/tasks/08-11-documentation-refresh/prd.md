# Reorganize and refresh project documentation

## Goal

Make the repository documentation accurate for the current runtime and easy to
navigate from a short first-run path into progressively more specialised
reference and operational material.

## Requirements

- Refresh both root README files so they describe the currently supported
  runtime: read-only and full MCP profiles, the same-origin Web application,
  optional LangBot channels, and YouTube as the supported ingestion source.
- Keep the root README readable: use it as a product overview, a verified
  quick start, and a map to deeper documentation instead of duplicating
  deployment runbooks.
- Replace the flat `docs/` layout with a clear hierarchy that begins with
  getting started, then configuration and interfaces, followed by deployment
  and specialised production runbooks.
- Move existing documentation into the new hierarchy without losing its
  operational guidance. Update all in-repository Markdown links and the
  environment-template pointer to their new locations.
- Add concise index pages where a directory contains several documents, with
  an intentional reading order and audience guidance.
- Verify commands and feature claims against `scripts/notebook-agent`,
  `app/cli.py`, `.env.example`, and deployment configuration. Do not claim
  unsupported connectors as available.
- Preserve unrelated existing worktree changes.

## Acceptance Criteria

- [x] `README.md` and `README.zh-CN.md` provide a current, scannable project
      overview, a minimal start path, and links to the reorganised docs.
- [x] `docs/` is organised by reader journey and has index pages for its
      top-level and multi-document sections.
- [x] Every Markdown documentation link resolves to an existing local file.
- [x] The quick-start commands and profile descriptions match the launcher
      and application CLI surfaces.
- [x] No documentation is removed without being represented by a moved or
      consolidated replacement.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
