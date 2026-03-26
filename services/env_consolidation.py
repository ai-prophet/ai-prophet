#!/usr/bin/env python3
"""
Environment variable consolidation helper.

This module helps consolidate environment variables from multiple sources
into a single shared configuration that all services can use.
"""

import os
from typing import Optional


class SharedEnvConfig:
    """
    Centralized environment configuration that all services should use.

    Instead of each service having its own env vars, we use the shared ones
    from kalshi-trading-shared for EVERYTHING.
    """

    # Shared environment variable prefix
    SHARED_PREFIX = "KALSHI_TRADING_SHARED_"

    @staticmethod
    def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get environment variable, preferring shared version.

        Priority order:
        1. KALSHI_TRADING_SHARED_<KEY>
        2. <KEY> (fallback for backwards compatibility)
        3. default value
        """
        # Try shared version first
        shared_key = f"{SharedEnvConfig.SHARED_PREFIX}{key}"
        value = os.getenv(shared_key)
        if value:
            return value

        # Fallback to non-shared version
        value = os.getenv(key)
        if value:
            return value

        return default

    @staticmethod
    def get_kalshi_config():
        """Get Kalshi API configuration from shared env vars."""
        # Check instance-specific variables first
        instance_name = os.getenv("TRADING_INSTANCE_NAME", "Haifeng").upper()

        # Try instance-specific, then shared, then general
        api_key_id = (
            os.getenv(f"KALSHI_API_KEY_ID_{instance_name}") or
            SharedEnvConfig.get_env("KALSHI_API_KEY_ID") or
            ""
        )

        private_key = (
            os.getenv(f"KALSHI_PRIVATE_KEY_B64_{instance_name}") or
            SharedEnvConfig.get_env("KALSHI_PRIVATE_KEY_B64") or
            ""
        )

        base_url = (
            os.getenv(f"KALSHI_BASE_URL_{instance_name}") or
            SharedEnvConfig.get_env("KALSHI_BASE_URL", "https://api.elections.kalshi.com") or
            "https://api.elections.kalshi.com"
        )

        return {
            "api_key_id": api_key_id,
            "private_key_base64": private_key,
            "base_url": base_url,
        }

    @staticmethod
    def get_db_config():
        """Get database configuration from shared env vars."""
        return {
            "database_url": SharedEnvConfig.get_env("DATABASE_URL"),
            "pool_size": int(SharedEnvConfig.get_env("DB_POOL_SIZE", "10")),
            "max_overflow": int(SharedEnvConfig.get_env("DB_MAX_OVERFLOW", "20")),
        }

    @staticmethod
    def get_sync_config():
        """Get sync service configuration from shared env vars."""
        return {
            "sync_interval": int(SharedEnvConfig.get_env("SYNC_INTERVAL_SEC", "600")),  # 10 minutes
            "stale_order_threshold": int(SharedEnvConfig.get_env("STALE_ORDER_THRESHOLD_MINUTES", "120")),
            "instance_name": SharedEnvConfig.get_env("INSTANCE_NAME", "Haifeng"),
            "dry_run": SharedEnvConfig.get_env("BETTING_DRY_RUN", "false").lower() == "true",
        }

    @staticmethod
    def get_trading_config():
        """Get trading engine configuration from shared env vars."""
        return {
            "max_order_cost": float(SharedEnvConfig.get_env("MAX_ORDER_COST", "100")),
            "max_markets_per_tick": int(SharedEnvConfig.get_env("MAX_MARKETS_PER_TICK", "10")),
            "starting_cash": float(SharedEnvConfig.get_env("STARTING_CASH", "10000")),
            "emergency_stop_threshold": float(SharedEnvConfig.get_env("EMERGENCY_STOP_THRESHOLD", "10")),
        }

    @staticmethod
    def consolidate_and_export():
        """
        Export all shared variables to ensure consistency.
        This should be called at the start of each service.
        """
        # Get all configs
        kalshi = SharedEnvConfig.get_kalshi_config()
        db = SharedEnvConfig.get_db_config()
        sync = SharedEnvConfig.get_sync_config()
        trading = SharedEnvConfig.get_trading_config()

        # Export to environment for backwards compatibility
        if kalshi["api_key_id"]:
            os.environ["KALSHI_API_KEY_ID"] = kalshi["api_key_id"]
        if kalshi["private_key_base64"]:
            os.environ["KALSHI_PRIVATE_KEY_B64"] = kalshi["private_key_base64"]
        if db["database_url"]:
            os.environ["DATABASE_URL"] = db["database_url"]

        os.environ["INSTANCE_NAME"] = sync["instance_name"]
        os.environ["BETTING_DRY_RUN"] = str(sync["dry_run"])

        print(f"✓ Environment consolidated for instance: {sync['instance_name']}")
        print(f"  Dry run: {sync['dry_run']}")
        print(f"  Sync interval: {sync['sync_interval']}s")
        print(f"  Database: {'configured' if db['database_url'] else 'not configured'}")
        print(f"  Kalshi API: {'configured' if kalshi['api_key_id'] else 'not configured'}")

        return {
            "kalshi": kalshi,
            "db": db,
            "sync": sync,
            "trading": trading,
        }


# Helper function for services to use
def setup_shared_environment():
    """
    Call this at the start of any service to use shared environment variables.

    Example:
        from env_consolidation import setup_shared_environment
        config = setup_shared_environment()
    """
    return SharedEnvConfig.consolidate_and_export()


if __name__ == "__main__":
    # Test consolidation
    print("Testing environment consolidation...")
    config = setup_shared_environment()

    print("\nConfiguration loaded:")
    print(f"  Instance: {config['sync']['instance_name']}")
    print(f"  Sync interval: {config['sync']['sync_interval']}s")
    print(f"  Emergency stop threshold: ${config['trading']['emergency_stop_threshold']}")