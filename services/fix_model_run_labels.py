"""
Retroactively correct model_run decision labels using the stored metadata.

Old labels:        New labels:
  HOLD           →  HOLD_SPREAD or HOLD_MATCH (based on p_yes/yes_ask/no_ask)
  BUY_NO (wrong) →  HOLD_SPREAD (if p_yes was inside spread band)

Run from services/:
  python fix_model_run_labels.py
"""

import json
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)


def recompute_decision(p_yes: float, yes_ask: float, no_ask: float) -> str:
    _no_ask = no_ask if no_ask > 0 else (1.0 - yes_ask)
    if p_yes > yes_ask:
        return "BUY_YES"
    elif p_yes < (1.0 - _no_ask):
        return "BUY_NO"
    elif abs(p_yes - yes_ask) < 0.001:
        return "HOLD_MATCH"
    else:
        return "HOLD_SPREAD"


def main():
    updated = 0
    skipped = 0

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, decision, metadata_json FROM model_runs WHERE metadata_json IS NOT NULL")
        ).fetchall()

        print(f"Processing {len(rows)} model_run records...")

        for row in rows:
            row_id, old_decision, meta_raw = row
            try:
                meta = json.loads(meta_raw)
            except (json.JSONDecodeError, TypeError):
                skipped += 1
                continue

            p_yes = meta.get("p_yes")
            yes_ask = meta.get("yes_ask")
            no_ask = meta.get("no_ask")

            if p_yes is None or yes_ask is None:
                skipped += 1
                continue

            if no_ask is None:
                no_ask = 1.0 - yes_ask

            new_decision = recompute_decision(float(p_yes), float(yes_ask), float(no_ask))

            if new_decision != old_decision:
                conn.execute(
                    text("UPDATE model_runs SET decision = :d WHERE id = :id"),
                    {"d": new_decision, "id": row_id},
                )
                updated += 1

        conn.commit()

    print(f"Done. Updated: {updated}, Skipped (no metadata): {skipped}, Unchanged: {len(rows) - updated - skipped}")


if __name__ == "__main__":
    main()
