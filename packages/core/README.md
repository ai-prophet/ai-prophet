# ai-prophet-core

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
