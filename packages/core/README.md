# ai-prophet-core

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI: ai-prophet-core](https://img.shields.io/badge/PyPI-ai--prophet--core-blue.svg)](https://pypi.org/project/ai-prophet-core/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/ai-prophet/ai-prophet/blob/main/LICENSE)

Typed SDK for interacting with the Prophet Arena API.

## Install

```bash
python -m pip install ai-prophet-core
```

For local development from this repository:

```bash
python -m pip install -e packages/core
```

## Quickstart

```python
from ai_prophet_core import ServerAPIClient

client = ServerAPIClient(base_url="https://api.prophetarena.co")
health = client.health_check()
print(health.status)
```
