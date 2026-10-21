# Agent skills


## Testing
- Tests - only `pytest`, functions. No `unittest.TestCase`.
- Mocks - only Fake classes. No `MagicMock`, `patch`, `unittest.mock`.
- Unit tests must be fast and isolated.
- Do not use shared state between tests.
- If a method should return no entity - check for `is None`.
- For errors use `pytest.raises(..., match=...)`.
- Test names should follow the `test_...` style.

### Unit tests
- Happy path.
- Edge cases: empty string, `None`, whitespace.
- Error cases: invalid input, missing entity, network errors.
- A regression test for a discovered bug.
- Each test creates its own service instance via the factory.

### Integration tests
- Integration tests verify the bot with the fakes.
- The test environment state must be reset before tests.
- For this, it is convenient to use `pytest.fixture(autouse=True)`.

## Code Style
- Write clear, short code without unnecessary complexity.
- If logic repeats - extract it into helper functions.
- Follow PEP 8 for formatting (99 characters per line, snake_case for functions/variables, PascalCase for classes, UPPER_SNAKE for constants).
- Use descriptive, unambiguous names for variables. Use descriptive names that start with a verb for functions.
- Type annotations - required everywhere, including `-> None`.
- Write Google-style docstrings for every public function and method.
- Do not add unused imports.
- All exception raises must be logged.
- Add logger.info at the main places, add logger.debug at the critical places with payload in logs with `%` formatting. Create a module-level logger with `__name__`.
- Apply KISS, DRY and SOLID principles to the code.

## Definition of done (quality gates)
Declare a task done only after docstrings are updated, README.md is updated, and all of these pass green:
  - `uv run ruff format .`
  - `uv run ruff check --fix .`
  - `uv run pytest -q` passes, with a test added for every new code

## Constraints
- Never edit: lock files, `.env`.
- Never commit `.env` files.
- Ask before adding dependencies.
- Before starting coding work, create a new git branch from `main` according to the project's branch naming convention.
- If a mismatch causes you to think for too long, ask the user this question and suggest possible next steps.
- Do not look at the `__pycache__` folder unless the user has explicitly asked you to do so.
- Do not look at git branches other than `main` unless the user has explicitly asked you to do so.

## Git branch naming convention
Branches should be named according to the type of task.

Git branch name format: `<type>-<short-description>`, where:
- `type` - task type: `feat`, `fix`, `test`, `refactor`, `docs`, `ci`, `chore`, `style`, according to the Conventional Commits
- `short-description` - a short description in the style of `kebab-case`

## Commit tag rules
Each commit must be marked with a tag in square brackets at the beginning of the message:
- `[agent]` - the diff from the agent was accepted without significant edits
- `[assisted]` - the agent did the bulk of the work, but the developer rewrote a significant portion (logic, structure, >20% of lines)
- `[manual]` - the code was written manually without the agent's assistance

## Issue tracker

Issues live in GitHub Issues for minaton-ru/cv-job-match, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

## Triage labels

Default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

## Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Confidentiality

Docs, ADRs, the glossary, issues, commit messages and code comments must not contain company names, website addresses, private or personal data, or the location of the hosting server. Refer to these generically ("a Company", "a Source", "the hosting region").

The real list of Sources lives only in `sources.yaml`, and the Candidate Profile lives only in `profile/`. Both are excluded from Git by `.gitignore`. Never commit them, and never copy their contents into tracked files.

Site adapters are named after a technique, never after a Company. Site-specific values go in `sources.yaml`, not in code (see `docs/adr/0003-adapters-by-technique-config-by-source.md`). Test fixtures for adapters must be synthetic.
