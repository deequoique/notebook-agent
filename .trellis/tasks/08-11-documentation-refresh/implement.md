# Implementation plan

1. Inventory current documentation, documentation links, command surfaces, and
   configuration claims.
2. Create the documentation directory hierarchy and move runbooks to their
   canonical locations.
3. Add landing/index pages that prescribe the intended reading sequence.
4. Rewrite the English and Chinese READMEs around an overview, a minimal
   launcher-based quick start, supported paths, and the docs map.
5. Update moved links and configuration-template references.
6. Validate Markdown links and confirm command/profile statements against the
   actual launcher and CLI help output.

## Validation

- `rg` check for obsolete `docs/*.md` links and paths.
- A local Markdown-link checker script that verifies repository-relative links.
- `./scripts/notebook-agent --help`
- `.venv/bin/python -m app.cli --help`
