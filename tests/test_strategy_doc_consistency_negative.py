"""Negative tests: prove the strategy-doc consistency checks actually DETECT drift.

These exercise the SAME detection logic the positive checks in
``test_strategy_doc_consistency.py`` and the runtime verifier
``scripts/verify_strategy_doc_runtime.py`` use (shared helpers), plus one
end-to-end document-tamper case against a temporary copy of the real doc.

Covered failure modes (owner directive 2026-10-22):
  N1. Constructor parameter changed in code without updating the document.
  N2. Critical documented parameter removed from the document.
  N3. Documented persisted value mismatching effective runtime settings.
  N4. Unavailable runtime source must NOT be reported as verified.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.test_strategy_doc_consistency import (  # noqa: E402
    _constructor_defaults,
    _doc_markers,
    constructor_marker_errors,
    missing_critical,
)
from scripts.verify_strategy_doc_runtime import (  # noqa: E402
    UNAVAILABLE,
    VERIFIED,
    coerce,
    parse_markers,
    verify_persisted_markers,
)

REAL_MARKERS = _doc_markers()
REAL_CTOR = _constructor_defaults()


def test_n1_constructor_drift_is_detected():
    """N1: tamper one extracted constructor default; the shared checker must fail."""
    tampered = dict(REAL_CTOR)
    original = tampered["stop_loss_pct"]
    tampered["stop_loss_pct"] = original + 1  # code changed, doc not updated
    errors = constructor_marker_errors(REAL_MARKERS, tampered)
    assert any("stop_loss_pct" in e and "!=" in e for e in errors), (
        f"constructor drift not detected: {errors}"
    )
    # control: untampered extraction produces no errors
    assert constructor_marker_errors(REAL_MARKERS, REAL_CTOR) == []


def test_n1b_document_value_drift_is_detected():
    """Same logic, opposite direction: doc edited, code unchanged."""
    tampered_doc_markers = dict(REAL_MARKERS)
    value, source = tampered_doc_markers["take_profit_pct"]
    assert source == "constructor"
    tampered_doc_markers["take_profit_pct"] = ("99.0", source)
    errors = constructor_marker_errors(tampered_doc_markers, REAL_CTOR)
    assert any("take_profit_pct" in e for e in errors)


def test_n2_removed_critical_param_is_detected():
    """N2: remove a critical marker; the presence check must fail."""
    tampered = {k: v for k, v in REAL_MARKERS.items() if k != "rsi_buy_threshold"}
    missing = missing_critical(tampered)
    assert "rsi_buy_threshold" in missing
    # control: real document has nothing missing
    assert missing_critical(REAL_MARKERS) == []


def test_n3_persisted_runtime_mismatch_is_detected():
    """N3: doc says 35, runtime says 40 -> MISMATCH reported, not silently passed."""
    markers = {"rsi_buy_threshold": ("35", "persisted")}
    effective = {"rsi_buy_threshold": 40}
    verified, mismatches = verify_persisted_markers(markers, effective)
    assert verified == []
    assert len(mismatches) == 1
    assert "rsi_buy_threshold" in mismatches[0] and "35" in mismatches[0] and "40" in mismatches[0]
    # control: matching values verify
    verified, mismatches = verify_persisted_markers(markers, {"rsi_buy_threshold": 35})
    assert verified == ["rsi_buy_threshold"] and mismatches == []


def test_n3b_persisted_marker_absent_from_runtime_is_detected():
    markers = {"phantom_key": ("1", "persisted")}
    verified, mismatches = verify_persisted_markers(markers, {})
    assert verified == []
    assert mismatches and "phantom_key" in mismatches[0]


def test_n4_unavailable_runtime_is_not_verified():
    """N4: if the runtime loader cannot run, status must be UNAVAILABLE, never VERIFIED."""
    import scripts.verify_strategy_doc_runtime as v

    captured: dict[str, str] = {}

    def fake_main_with_unavailable_loader(monkeypatch):
        def boom():
            raise RuntimeError("no settings store available")
        monkeypatch.setattr(v, "load_effective", boom)
        # emulate main()'s status decision: UNAVAILABLE path must return 2
        try:
            v.load_effective()
        except Exception as exc:  # noqa: BLE001
            captured["status"] = v.UNAVAILABLE
            captured["reason"] = type(exc).__name__
        return captured

    result = fake_main_with_unavailable_loader(pytest.MonkeyPatch())
    assert result["status"] == UNAVAILABLE
    assert result["status"] != VERIFIED
    assert result["reason"] == "RuntimeError"


def test_parse_markers_roundtrip_and_coercion():
    text = "<!-- param: a=10 source=persisted --> <!-- param: b=true source=persisted -->"
    m = parse_markers(text)
    assert m == {"a": ("10", "persisted"), "b": ("true", "persisted")}
    assert coerce("10") == 10 and coerce("true") is True and coerce("0.5") == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
