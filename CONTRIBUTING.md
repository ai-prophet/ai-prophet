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

## Releases

Package publishing is manual through GitHub Actions using `workflow_dispatch`.

1. Bump the version in `packages/core/pyproject.toml` and/or `packages/cli/pyproject.toml`.
2. Merge the release commit to `main`.
3. In GitHub Actions, run `Publish ai-prophet-core` and/or `Publish ai-prophet`.
4. Choose `testpypi` for a dry run or `pypi` for a live release.
5. Publish `ai-prophet-core` before `ai-prophet` when both packages change.

### First-Time PyPI Setup

Configure Trusted Publishing on both PyPI and TestPyPI for `ai-prophet/ai-prophet`.

- `ai-prophet-core` on TestPyPI: workflow `Publish ai-prophet-core`, environment `testpypi`
- `ai-prophet-core` on PyPI: workflow `Publish ai-prophet-core`, environment `pypi`
- `ai-prophet` on TestPyPI: workflow `Publish ai-prophet`, environment `testpypi`
- `ai-prophet` on PyPI: workflow `Publish ai-prophet`, environment `pypi`

If you want an extra approval step before upload, add required reviewers to the `testpypi` and `pypi` GitHub environments.

## Pull Requests

- Keep changes focused and small.
- Add tests for behavior changes.
- Update docs when behavior or interfaces change.
- Keep comments concise and implementation-aligned.

## Commit Style

Use clear, imperative commit messages that describe intent.
