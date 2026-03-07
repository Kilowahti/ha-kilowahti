## Git conventions
- Always start new changes on top of main branch unless instructed otherwise
- Branch naming: use format `feature/description` or `fix/description`
- Never use `claude/` prefix in branch names
- No PRs — internal code review only
- Push to `origin` (Gitea) by default; only push to `github` when explicitly asked

## Code quality
- Code must pass ruff linting and formatting (checked by CI)
- Import statements must be alphabetically sorted
- Ensure linting passes before committing: `ruff check custom_components/kilowahti/ && ruff format --check custom_components/kilowahti/`

## Release process
- Create and push the tag first, then create the GitHub release from it
- GitHub Actions will update `manifest.json` version and build + upload the ZIP artifact automatically
- Test release format: `YYYY.M.MINOR-test.N` (e.g. `2026.3.0-test.0`, N starts from zero)
- Stable release format: `YYYY.M.MINOR` (e.g. `2026.3.0`)
