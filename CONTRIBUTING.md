# Contributing

Thanks for contributing to `ai-prophet`.

## Development Setup

```bash
python -m pip install -e packages/core
python -m pip install -e "packages/cli[dev]"
```

## Local Checks

Run before opening a PR:

```bash
pytest packages/core/tests
pytest packages/cli/tests
```

## Pull Requests

- Keep changes focused and small.
- Add tests for behavior changes.
- Update docs when behavior or interfaces change.
- Keep comments concise and implementation-aligned.

## Commit Style

Use clear, imperative commit messages that describe intent.

## Code of Conduct

By participating, you agree to follow `CODE_OF_CONDUCT.md`.
