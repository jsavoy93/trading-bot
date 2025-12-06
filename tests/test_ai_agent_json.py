#!/usr/bin/env python3
"""Unit tests for AI JSON response extraction."""

import sys
from pathlib import Path

import pytest

# Ensure src is on path for local tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analysis.ai_agent import AITradingAgent


def _agent():
    # Bypass __init__ to avoid hitting external services
    return AITradingAgent.__new__(AITradingAgent)


def test_extracts_json_from_code_fence():
    agent = _agent()
    response = """
    Sure, here is the selection:
    ```json
    {
      "recommended_tickers": ["AAPL", "MSFT"],
      "notes": "Stable mega-caps"
    }
    ```
    Extra commentary after the block.
    """

    parsed, error = agent._extract_json_from_response(response)

    assert error is None
    assert parsed == {
        "recommended_tickers": ["AAPL", "MSFT"],
        "notes": "Stable mega-caps",
    }


def test_trailing_commas_are_cleaned():
    agent = _agent()
    response = (
        '{"recommended_tickers": ["AAPL", "GOOGL",], '
        '"meta": {"source": "ai",}, }'
    )

    parsed, error = agent._extract_json_from_response(response)

    assert error is None
    assert parsed["recommended_tickers"] == ["AAPL", "GOOGL"]
    assert parsed["meta"] == {"source": "ai"}


def test_unbalanced_braces_return_error():
    agent = _agent()
    response = '{"recommended_tickers": ["AAPL"]'

    parsed, error = agent._extract_json_from_response(response)

    assert parsed is None
    assert error is not None
    assert "closing brace" in error.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
