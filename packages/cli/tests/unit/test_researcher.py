"""Tests for the web research module — focused on category-aware query generation."""

from __future__ import annotations

import pytest
from ai_prophet.forecast import researcher
from ai_prophet.forecast.researcher import (
    CATEGORY_HINTS,
    DEFAULT_CATEGORY_HINT,
    _build_query_user_prompt,
    generate_search_queries,
    hint_for_category,
)

# ---------------------------------------------------------------------------
# hint_for_category()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("known", list(CATEGORY_HINTS.keys()))
def test_hint_for_known_category_returns_specific_hint(known: str) -> None:
    assert hint_for_category(known) == CATEGORY_HINTS[known]


@pytest.mark.parametrize("case", ["sports", "SPORTS", "Sports", "sPoRtS"])
def test_hint_for_category_is_case_insensitive(case: str) -> None:
    assert hint_for_category(case) == CATEGORY_HINTS["Sports"]


def test_hint_for_category_strips_whitespace() -> None:
    assert hint_for_category("  Crypto  ") == CATEGORY_HINTS["Crypto"]


@pytest.mark.parametrize("unknown", [None, "", "Entertainment", "Elections", "Music"])
def test_hint_for_unknown_or_missing_category_uses_default(unknown) -> None:
    assert hint_for_category(unknown) == DEFAULT_CATEGORY_HINT


# ---------------------------------------------------------------------------
# _build_query_user_prompt
# ---------------------------------------------------------------------------


def test_user_prompt_includes_category_hint_for_known_category() -> None:
    prompt = _build_query_user_prompt(
        title="Will Cleveland beat Detroit?",
        description=None,
        category="Sports",
    )
    assert "CATEGORY HINT:" in prompt
    # The Sports-specific guidance lands in the prompt verbatim.
    assert CATEGORY_HINTS["Sports"] in prompt
    # Generic hint is NOT in the prompt when a specific one exists.
    assert DEFAULT_CATEGORY_HINT not in prompt


def test_user_prompt_uses_default_hint_for_unknown_category() -> None:
    prompt = _build_query_user_prompt(
        title="Will Foo happen?",
        description="x",
        category="Entertainment",  # not in CATEGORY_HINTS
    )
    assert "CATEGORY HINT:" in prompt
    assert DEFAULT_CATEGORY_HINT in prompt


def test_user_prompt_uses_default_hint_for_no_category() -> None:
    prompt = _build_query_user_prompt(
        title="Will X resolve YES?",
        description=None,
        category=None,
    )
    assert "CATEGORY HINT:" in prompt
    assert DEFAULT_CATEGORY_HINT in prompt


def test_user_prompt_includes_event_title_and_description() -> None:
    prompt = _build_query_user_prompt(
        title="My event title",
        description="A description",
        category="Crypto",
    )
    assert "My event title" in prompt
    assert "A description" in prompt
    assert "Category: Crypto" in prompt


# ---------------------------------------------------------------------------
# generate_search_queries — verify the call_llm_json prompt actually sees the hint
# ---------------------------------------------------------------------------


def test_generate_search_queries_passes_category_hint_to_llm(monkeypatch) -> None:
    """The user prompt sent to call_llm_json carries the category-specific hint."""
    captured: list[dict] = []

    def fake_call(system, user, *, tier, temperature, max_tokens):
        captured.append({"system": system, "user": user, "tier": tier})
        return {"queries": ["q1", "q2", "q3"]}

    monkeypatch.setattr(researcher, "call_llm_json", fake_call)

    queries = generate_search_queries(
        title="Fed June meeting", description=None, category="Economics"
    )
    assert queries == ["q1", "q2", "q3"]
    assert len(captured) == 1
    user_prompt = captured[0]["user"]
    assert CATEGORY_HINTS["Economics"] in user_prompt
    # Reasoning tier choice is "research" for query generation.
    assert captured[0]["tier"] == "research"


def test_generate_search_queries_falls_back_to_default_hint_for_unknown(
    monkeypatch,
) -> None:
    captured: list[dict] = []

    def fake_call(system, user, *, tier, temperature, max_tokens):
        captured.append({"user": user})
        return {"queries": ["a", "b", "c"]}

    monkeypatch.setattr(researcher, "call_llm_json", fake_call)

    generate_search_queries(
        title="Will Y happen?", description=None, category="Entertainment"
    )
    assert DEFAULT_CATEGORY_HINT in captured[0]["user"]


def test_generate_search_queries_falls_back_to_defaults_on_llm_failure(
    monkeypatch,
) -> None:
    """If the LLM call raises, _fallback_queries is used and still returns N items."""
    from ai_prophet.forecast.researcher import MAX_SEARCH_QUERIES

    def boom(*_a, **_kw):
        raise RuntimeError("simulated outage")

    monkeypatch.setattr(researcher, "call_llm_json", boom)

    queries = generate_search_queries(
        title="Will X win?", category="Sports"
    )
    # Fallback is purely string-derived from the title; doesn't crash.
    assert len(queries) <= MAX_SEARCH_QUERIES
    assert all(isinstance(q, str) and q for q in queries)
