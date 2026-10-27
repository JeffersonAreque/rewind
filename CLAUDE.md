# Notes for Claude Code

## Editing files
- Create and change files only with the Edit and Write tools.
- Never write files from PowerShell (`Set-Content`, `Add-Content`, `Out-File`, `>`): Windows PowerShell 5.1 adds a UTF-8 BOM and CRLF line endings.
- Write commit messages to a file in the scratchpad and commit with `git commit -F <file>`; PowerShell breaks `-m` messages that contain double quotes.

## Project rules
- No secrets in the code. `.env` and `runs/` are never committed.
- Small, frequent commits in English (Conventional Commits).
- Models: Muse Spark Open via Baseten (OpenAI-compatible); the provider is chosen in `.env`.
- Models never write code: they fill JSON specs that pydantic and checkers validate.
- Run `uv run pytest` before each commit.
