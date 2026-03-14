"""YAML-based configuration for PA Client."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


@dataclass
class SearchConfig:
    """Search-related configuration."""
    max_queries_per_market: int = 1
    max_results_per_query: int = 3
    mock: bool = False
    connect_timeout: int = 10
    total_timeout: int = 30
    fetch_timeout: int = 15
    max_concurrent: int = 3
    max_html_bytes: int = 512 * 1024   # Cap per-page HTML download (bytes)
    max_extract_chars: int = 5_000     # Truncate extracted article text (chars)


@dataclass
class PipelineConfig:
    """Pipeline-related configuration."""
    max_markets: int = 5
    min_size_usd: float = 1.0


@dataclass
class LLMConfig:
    """LLM-related configuration."""
    temperature: float = 0.7
    max_tokens: int = 4096
    max_retries: int = 3
    retry_delay: float = 1.0
    http_timeout: float = 600.0
    verbose_truncate_chars: int = 3000


@dataclass
class ServerConfig:
    """Server/API configuration."""
    timeout: int = 30
    max_retries: int = 3
    retry_backoff: float = 1.0
    poll_interval: float = 60.0
    max_polls: int = 60   # Max polling iterations (60 * 60s = 1 hour)


@dataclass
class BenchmarkConfig:
    """Benchmark execution configuration."""
    initial_cash: float = 10000.0
    deadline_buffer_seconds: int = 180  # Skip tick if deadline < this
    result_timeout_seconds: int = 300  # Wait for agent results
    process_join_timeout: int = 10  # Wait for process cleanup


@dataclass
class MemoryConfig:
    """Memory/context configuration."""
    recent_ticks_limit: int = 5
    market_history_limit: int = 3


@dataclass
class MiniProphetConfig:
    """Mini-prophet forecasting agent configuration."""
    enabled: bool = False
    step_limit: int = 20
    search_limit: int = 3
    cost_limit: float = 1.0
    context_window: int = 6
    show_current_time: bool = True
    batch_workers: int = 5       # Parallel workers for batch_forecast (0 = sequential)
    batch_timeout: float = 120.0  # Per-market timeout in seconds
    search_class: str = "exa"    # Search backend: exa, perplexity, brave, tavily
    concise_sources: bool = True  # Strip snippet; False = include full content


@dataclass
class ClientConfig:
    """Full client configuration."""
    search: SearchConfig = field(default_factory=SearchConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    benchmark: BenchmarkConfig = field(default_factory=BenchmarkConfig)
    mini_prophet: MiniProphetConfig = field(default_factory=MiniProphetConfig)

    # Singleton instance (class-level, not per-instance)
    _instance: ClassVar[ClientConfig | None] = None

    @classmethod
    def from_mapping(cls, config_data: dict[str, Any] | None = None) -> ClientConfig:
        """Build a config object from raw nested mapping data."""
        config_data = config_data or {}
        return cls(
            search=SearchConfig(**config_data.get("search", {})),
            pipeline=PipelineConfig(**config_data.get("pipeline", {})),
            llm=LLMConfig(**config_data.get("llm", {})),
            server=ServerConfig(**config_data.get("server", {})),
            memory=MemoryConfig(**config_data.get("memory", {})),
            benchmark=BenchmarkConfig(**config_data.get("benchmark", {})),
            mini_prophet=MiniProphetConfig(**config_data.get("mini_prophet", {})),
        )

    @classmethod
    def defaults(cls) -> ClientConfig:
        """Return bundled package defaults without cwd overrides."""
        return cls.from_mapping(_load_bundled_config())

    @classmethod
    def load(
        cls,
        config_path: Path | str | None = None,
        *,
        local_override_path: Path | str | None = None,
        cache: bool = True,
    ) -> ClientConfig:
        """Load runtime configuration from bundled defaults plus overrides.

        The caller chooses whether to apply local overrides. Library code should
        pass explicit config objects rather than relying on cwd-based loading.
        """
        if (
            cache
            and cls._instance is not None
            and config_path is None
            and local_override_path is None
        ):
            return cls._instance

        config_data = _load_bundled_config()

        if config_path is not None:
            config_data = _deep_merge(
                config_data,
                _load_yaml_file(Path(config_path), missing_log_level=logging.INFO),
            )

        if local_override_path is not None:
            config_data = _deep_merge(
                config_data,
                _load_yaml_file(Path(local_override_path), missing_log_level=logging.DEBUG),
            )

        instance = cls.from_mapping(config_data)

        if cache:
            cls._instance = instance

        return instance

    @classmethod
    def load_runtime(cls, config_path: Path | str | None = None) -> ClientConfig:
        """Load CLI/runtime config, including optional local overrides."""
        return cls.load(
            config_path=config_path,
            local_override_path=Path("config.local.yaml"),
            cache=True,
        )

    @classmethod
    def get(cls) -> ClientConfig:
        """Get the current config instance, falling back to bundled defaults."""
        if cls._instance is None:
            cls._instance = cls.defaults()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None

    def __repr__(self) -> str:
        return (
            f"ClientConfig(\n"
            f"  search: queries={self.search.max_queries_per_market}, results={self.search.max_results_per_query}\n"
            f"  pipeline: markets={self.pipeline.max_markets}, min_size=${self.pipeline.min_size_usd}\n"
            f"  llm: temp={self.llm.temperature}, max_tokens={self.llm.max_tokens}\n"
            f"  server: timeout={self.server.timeout}s, retries={self.server.max_retries}\n"
            f"  benchmark: cash=${self.benchmark.initial_cash}\n"
            f")"
        )


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts, with override taking precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_bundled_config() -> dict[str, Any]:
    try:
        pkg_files = resources.files("ai_prophet")
        config_text = (pkg_files / "config.yaml").read_text(encoding="utf-8")
        logger.info("Loaded bundled config.yaml defaults")
        return yaml.safe_load(config_text) or {}
    except (FileNotFoundError, TypeError):
        logger.info("No bundled config.yaml found, using defaults")
        return {}


def _load_yaml_file(path: Path, *, missing_log_level: int) -> dict[str, Any]:
    if not path.exists():
        logger.log(missing_log_level, "No config file found at %s, using defaults", path)
        return {}

    logger.info("Loading config from %s", path)
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
