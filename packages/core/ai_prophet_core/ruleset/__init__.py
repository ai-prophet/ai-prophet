"""Ruleset constants for Prophet Arena v1.

DO NOT MODIFY without bumping version.
"""

# Trading Constraints
MAX_OPEN_POSITIONS = 30
MAX_NOTIONAL_PER_MARKET = 1000.0
MAX_GROSS_EXPOSURE = 10000.0
MAX_TRADES_PER_TICK = 10
MAX_TRADES_PER_DAY = 100
INITIAL_CASH = 10000.0
FEE_RATE = 0.0

# Time and Cadence
TICK_INTERVAL_SECONDS = 900  # 15 minutes
EVALUATION_DAYS = 30

# Derived tick boundaries: (0, 15, 30, 45) for 15-min ticks
_tick_interval_minutes = TICK_INTERVAL_SECONDS // 60
VALID_TICK_MINUTES = tuple(range(0, 60, _tick_interval_minutes)) if _tick_interval_minutes <= 60 else (0,)

# Agent Pipeline Constraints
MAX_REVIEW_ITEMS = 10
MAX_QUERIES_PER_ITEM = 3
MAX_SEARCH_RESULTS_PER_QUERY = 10
SEARCH_TIMEOUT_SECONDS = 30

# Market Eligibility (Indexer Filters)
MIN_24H_VOLUME_USD = 100.0
MIN_HOURS_TO_RESOLUTION = 24
MAX_HOURS_TO_RESOLUTION = 720  # 30 days
MAX_QUOTE_AGE_SECONDS = 600  # 10 minutes
MAX_MARKETS_PER_SNAPSHOT = 256

# Snapshot and Fairness
INDEXER_RUN_INTERVAL_SECONDS = 300  # 5 minutes
TICK_SUBMISSION_DEADLINE_SECS = 900  # 15 minutes after tick_ts
MAX_INTENTS_PER_TICK_REQUEST = 50

# Forecast Constraints
MIN_P_YES = 0.0
MAX_P_YES = 1.0

