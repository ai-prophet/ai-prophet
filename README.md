# ai-prophet

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI: ai-prophet-core](https://img.shields.io/badge/PyPI-ai--prophet--core-blue.svg)](https://pypi.org/project/ai-prophet-core/)
[![PyPI: ai-prophet](https://img.shields.io/badge/PyPI-ai--prophet-blue.svg)](https://pypi.org/project/ai-prophet/)

LLM benchmark client and SDK for Prophet Arena prediction-market evaluation.

## Packages

- `packages/core` - typed SDK (`ai-prophet-core`) for API models and client calls
- `packages/cli` - benchmark runner and CLI (`ai-prophet`)

## Local Setup

```bash
python -m pip install -e packages/core
python -m pip install -e "packages/cli[dev]"
```

## Test

```bash
pytest packages/core/tests
pytest packages/cli/tests
```

## License

MIT. See `LICENSE`.
