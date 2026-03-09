from datetime import UTC, datetime

from ai_prophet.trade.memory import LocalReasoningStore


def test_local_memory_store_append_and_read_recent(tmp_path):
    store = LocalReasoningStore(base_dir=tmp_path, experiment_slug="exp_a")
    p_idx = 2

    store.append_reasoning(
        participant_idx=p_idx,
        tick_id=datetime(2026, 2, 20, 6, 0, tzinfo=UTC),
        reasoning={"forecasts": {"m1": {"p_yes": 0.42}}},
    )
    store.append_reasoning(
        participant_idx=p_idx,
        tick_id=datetime(2026, 2, 20, 7, 0, tzinfo=UTC),
        reasoning={"forecasts": {"m1": {"p_yes": 0.45}}},
    )

    entries = store.read_recent_reasoning(participant_idx=p_idx, limit=1)
    assert len(entries) == 1
    assert entries[0].tick_ts == datetime(2026, 2, 20, 7, 0, tzinfo=UTC)
    assert entries[0].reasoning["forecasts"]["m1"]["p_yes"] == 0.45


def test_local_memory_store_skips_malformed_lines(tmp_path):
    store = LocalReasoningStore(base_dir=tmp_path, experiment_slug="exp_b")
    p_idx = 0
    path = tmp_path / "exp_b" / "participant_0.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "{not-json",
                '{"tick_id":"2026-02-20T06:00:00+00:00","reasoning":{"review":[]}}',
                '{"tick_id":null,"reasoning":{"review":[]}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    entries = store.read_recent_reasoning(participant_idx=p_idx, limit=10)
    assert len(entries) == 1
    assert entries[0].tick_ts == datetime(2026, 2, 20, 6, 0, tzinfo=UTC)


def test_local_memory_store_dedupes_by_tick_and_prunes(tmp_path):
    store = LocalReasoningStore(base_dir=tmp_path, experiment_slug="exp_c", max_rows=2)
    p_idx = 1

    store.append_reasoning(
        participant_idx=p_idx,
        tick_id="2026-02-20T06:00:00+00:00",
        reasoning={"forecasts": {"m1": {"p_yes": 0.10}}},
    )
    # Duplicate tick should be ignored.
    store.append_reasoning(
        participant_idx=p_idx,
        tick_id="2026-02-20T06:00:00+00:00",
        reasoning={"forecasts": {"m1": {"p_yes": 0.99}}},
    )
    store.append_reasoning(
        participant_idx=p_idx,
        tick_id="2026-02-20T07:00:00+00:00",
        reasoning={"forecasts": {"m1": {"p_yes": 0.20}}},
    )
    store.append_reasoning(
        participant_idx=p_idx,
        tick_id="2026-02-20T08:00:00+00:00",
        reasoning={"forecasts": {"m1": {"p_yes": 0.30}}},
    )

    entries = store.read_recent_reasoning(participant_idx=p_idx, limit=10)
    # Max rows=2 should keep only the most recent two ticks.
    assert [e.tick_ts for e in entries] == [
        datetime(2026, 2, 20, 7, 0, tzinfo=UTC),
        datetime(2026, 2, 20, 8, 0, tzinfo=UTC),
    ]

