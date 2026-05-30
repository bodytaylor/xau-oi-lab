# OI Data Warehouse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `signal_features` Supabase table, feature extraction, ML scoring pipeline, and manual training script so every fired signal is scored with a win-probability and that history can be used to retrain the model.

**Architecture:** At signal fire time `model_scorer.score()` is injected between `compute_signal()` and `db_sync.insert_signal()` in `server.py`. Features are stored in a new `signal_features` Supabase table. A manually run `train.py` reads labeled history (JOIN with `signals.outcome`) and saves a sklearn Pipeline to `model/model.pkl`. On cold start (no model file) signals still fire normally, features are still saved.

**Tech Stack:** Python 3.11, scikit-learn>=1.4, xgboost>=2.0, pandas>=2.0, Supabase (existing), pytest (existing)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `migrations/002_signal_features.sql` | Supabase table DDL |
| Create | `feature_builder.py` | Shared feature extraction + column constants |
| Create | `model_scorer.py` | Lazy-load model, score one signal |
| Create | `train.py` | Fetch labeled data, train pipeline, save pkl |
| Create | `model/` | Directory (gitignored) for pkl + metadata |
| Create | `tests/test_feature_builder.py` | Unit tests for feature extraction |
| Create | `tests/test_model_scorer.py` | Unit tests for scorer (cold start + mock model) |
| Create | `tests/test_train.py` | Unit tests for build_features, _next_version |
| Modify | `db_sync.py` | `insert_signal` returns UUID; add `insert_signal_features` |
| Modify | `tests/test_db_sync.py` | Update for new return type + new function |
| Modify | `server.py` | Inject scorer + feature write at signal fire site |
| Modify | `alerts.py` | Add model score field to `zone_alert` |
| Modify | `requirements.txt` | Add scikit-learn, xgboost, pandas |
| Modify | `.gitignore` | Add model/ entries + .superpowers/ |

---

## Task 1: Infrastructure — Migration, Dependencies, Gitignore

**Files:**
- Create: `migrations/002_signal_features.sql`
- Modify: `requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Create the migration file**

```sql
-- migrations/002_signal_features.sql
-- Run in the Supabase SQL editor after 001_initial_schema.sql

create table if not exists signal_features (
  -- Identity
  id                   uuid primary key default gen_random_uuid(),
  signal_id            uuid references signals(id),
  session_date         date references sessions(date),

  -- Signal context
  zone                 text,
  direction            text,
  confidence           text,
  recovery             boolean,

  -- Price features
  price                float,
  price_vs_open        float,
  price_vs_open_pct    float,
  sd1_pts              float,
  sd2_pts              float,
  sd3_pts              float,
  dist_to_nearest_sd   float,

  -- IV features
  iv_pct               float,
  vol_skew_left        float,
  vol_skew_right       float,
  vol_skew_ratio       float,
  vol_skew_verdict     text,

  -- OI summary features
  call_pct             float,
  put_pct              float,
  skew_verdict         text,
  magnet_count         int,
  gamma_level_count    int,
  nearest_magnet_dist  float,

  -- OI raw snapshot
  oi_snapshot          jsonb,

  -- Model output (written at fire time)
  win_prob             float,
  confidence_label     text,
  model_version        text,

  created_at           timestamptz default now()
);
```

- [ ] **Step 2: Add new dependencies to requirements.txt**

Replace the existing `requirements.txt` content with:

```
playwright>=1.44.0
apscheduler>=3.10.0
fastapi>=0.111.0
uvicorn>=0.29.0
python-dotenv>=1.0.0
supabase>=2.4.0
websocket-client>=1.8.0
requests>=2.31.0
httpx>=0.27.0
websockets>=12.0
scikit-learn>=1.4
xgboost>=2.0
pandas>=2.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 3: Add model/ and .superpowers/ to .gitignore**

Append to `.gitignore`:

```
# ML model artefacts
model/model.pkl
model/metadata.json

# Superpowers brainstorming session files
.superpowers/
```

- [ ] **Step 4: Install new dependencies**

Run:
```bash
pip install scikit-learn>=1.4 xgboost>=2.0 pandas>=2.0
```

Expected: packages install without error.

- [ ] **Step 5: Commit**

```bash
git add migrations/002_signal_features.sql requirements.txt .gitignore
git commit -m "feat: add signal_features migration + ML dependencies"
```

---

## Task 2: feature_builder.py (TDD)

**Files:**
- Create: `feature_builder.py`
- Create: `tests/test_feature_builder.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_feature_builder.py`:

```python
# tests/test_feature_builder.py
import copy
import pytest
from tests.conftest import SAMPLE_SESSION


SAMPLE_SIGNAL = {
    "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
    "entry": 4750.0, "tp1": 4725.0, "tp2": 4700.0, "sl": 4775.0,
    "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
}


def test_extract_returns_all_required_fields():
    from feature_builder import extract
    features = extract(SAMPLE_SIGNAL.copy(), 4740.0, copy.deepcopy(SAMPLE_SESSION))
    required = [
        "zone", "direction", "confidence", "recovery",
        "price", "price_vs_open", "price_vs_open_pct",
        "sd1_pts", "sd2_pts", "sd3_pts", "dist_to_nearest_sd",
        "iv_pct", "vol_skew_left", "vol_skew_right", "vol_skew_ratio", "vol_skew_verdict",
        "call_pct", "put_pct", "skew_verdict", "magnet_count", "gamma_level_count",
        "nearest_magnet_dist", "oi_snapshot",
    ]
    for field in required:
        assert field in features, f"Missing field: {field}"


def test_extract_price_features():
    from feature_builder import extract
    features = extract(SAMPLE_SIGNAL.copy(), 4740.0, copy.deepcopy(SAMPLE_SESSION))
    # open_price = 4621.78, price = 4740.0
    assert features["price"] == 4740.0
    assert abs(features["price_vs_open"] - 118.22) < 0.01
    assert features["sd1_pts"] == 82.04
    assert features["sd2_pts"] == 164.07
    assert features["sd3_pts"] == 246.11


def test_extract_dist_to_nearest_sd():
    from feature_builder import extract
    # open=4621.78, price=4740.0 → diff=118.22
    # |118.22-82.04|=36.18, |118.22-164.07|=45.85, |118.22-246.11|=127.89 → nearest=36.18
    features = extract(SAMPLE_SIGNAL.copy(), 4740.0, copy.deepcopy(SAMPLE_SESSION))
    assert abs(features["dist_to_nearest_sd"] - 36.18) < 0.1


def test_extract_oi_summary_features():
    from feature_builder import extract
    features = extract(SAMPLE_SIGNAL.copy(), 4740.0, copy.deepcopy(SAMPLE_SESSION))
    assert features["call_pct"] == 65.0
    assert features["put_pct"] == 35.0
    assert features["magnet_count"] == 2   # magnets=[4700.0, 4500.0]
    assert features["gamma_level_count"] == 1


def test_extract_nearest_magnet_distance():
    from feature_builder import extract
    # entry=4750.0, magnets=[4700.0, 4500.0] → distances 50, 250 → nearest=50
    features = extract(SAMPLE_SIGNAL.copy(), 4740.0, copy.deepcopy(SAMPLE_SESSION))
    assert features["nearest_magnet_dist"] == 50.0


def test_extract_nearest_magnet_none_when_no_magnets():
    from feature_builder import extract
    session = copy.deepcopy(SAMPLE_SESSION)
    session["oi_analysis"]["magnets"] = []
    features = extract(SAMPLE_SIGNAL.copy(), 4740.0, session)
    assert features["nearest_magnet_dist"] is None


def test_extract_oi_snapshot_is_session_oi_data():
    from feature_builder import extract
    session = copy.deepcopy(SAMPLE_SESSION)
    features = extract(SAMPLE_SIGNAL.copy(), 4740.0, session)
    assert features["oi_snapshot"] == session["oi_data"]


def test_extract_handles_missing_vol_skew():
    from feature_builder import extract
    session = copy.deepcopy(SAMPLE_SESSION)
    del session["vol_skew_analysis"]
    features = extract(SAMPLE_SIGNAL.copy(), 4740.0, session)
    assert features["vol_skew_left"] is None
    assert features["vol_skew_right"] is None
    assert features["vol_skew_ratio"] is None


def test_extract_uses_price_as_entry_fallback_for_wait_signal():
    from feature_builder import extract
    # WAIT signal has entry=None
    wait_signal = {
        "zone": "INSIDE_1SD", "direction": "WAIT", "confidence": "LOW",
        "entry": None, "tp1": None, "tp2": None, "sl": None,
        "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
    }
    session = copy.deepcopy(SAMPLE_SESSION)
    features = extract(wait_signal, 4640.0, session)
    # magnet distances should be calculated from price=4640.0
    # magnets=[4700.0, 4500.0] → distances 60, 140 → nearest=60
    assert features["nearest_magnet_dist"] == 60.0


def test_feature_cols_constant_contains_expected_columns():
    from feature_builder import FEATURE_COLS, CATEGORICAL_COLS, NUMERIC_COLS
    assert "zone" in CATEGORICAL_COLS
    assert "direction" in CATEGORICAL_COLS
    assert "call_pct" in NUMERIC_COLS
    assert "recovery" in NUMERIC_COLS
    assert set(FEATURE_COLS) == set(CATEGORICAL_COLS + NUMERIC_COLS)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_feature_builder.py -v
```

Expected: `ModuleNotFoundError: No module named 'feature_builder'`

- [ ] **Step 3: Implement feature_builder.py**

Create `feature_builder.py`:

```python
# feature_builder.py
"""
Shared feature extraction for model training and inference.

Both model_scorer.py (inference) and train.py (training) call
feature_builder.extract() to get the same flat feature representation.

CATEGORICAL_COLS, NUMERIC_COLS, and FEATURE_COLS are imported by both
callers to ensure column ordering stays in sync with the trained pipeline.
"""

CATEGORICAL_COLS = ["zone", "direction", "confidence", "skew_verdict", "vol_skew_verdict"]
NUMERIC_COLS = [
    "price_vs_open", "price_vs_open_pct",
    "sd1_pts", "sd2_pts", "sd3_pts", "dist_to_nearest_sd",
    "iv_pct", "vol_skew_left", "vol_skew_right", "vol_skew_ratio",
    "call_pct", "put_pct",
    "magnet_count", "gamma_level_count", "nearest_magnet_dist",
    "recovery",
]
FEATURE_COLS = CATEGORICAL_COLS + NUMERIC_COLS


def extract(signal: dict, price: float, session: dict) -> dict:
    """
    Extract a flat feature dict + oi_snapshot from a (signal, price, session) tuple.

    Returns all signal_features table columns except: id, signal_id,
    session_date, win_prob, confidence_label, model_version, created_at.
    Those are added by db_sync.insert_signal_features().
    """
    sd     = session.get("sd_zones") or {}
    oi     = session.get("oi_analysis") or {}
    vs     = session.get("vol_skew_analysis") or {}

    open_p = session.get("open_price") or 0.0
    sd1    = sd.get("sd1_pts") or 0.0
    sd2    = sd.get("sd2_pts") or 0.0
    sd3    = sd.get("sd3_pts") or 0.0

    diff               = abs(price - open_p)
    dist_to_nearest_sd = min(abs(diff - sd1), abs(diff - sd2), abs(diff - sd3))

    magnets = oi.get("magnets") or []
    entry   = signal.get("entry") or price   # WAIT signals have entry=None — fall back to price
    nearest_magnet_dist = (
        min(abs(m - entry) for m in magnets) if magnets else None
    )

    price_vs_open     = round(price - open_p, 4)
    price_vs_open_pct = round((price - open_p) / open_p * 100, 4) if open_p else None

    return {
        # Signal context
        "zone":       signal.get("zone"),
        "direction":  signal.get("direction"),
        "confidence": signal.get("confidence"),
        "recovery":   bool(signal.get("recovery", False)),

        # Price features
        "price":               price,
        "price_vs_open":       price_vs_open,
        "price_vs_open_pct":   price_vs_open_pct,
        "sd1_pts":             sd1,
        "sd2_pts":             sd2,
        "sd3_pts":             sd3,
        "dist_to_nearest_sd":  round(dist_to_nearest_sd, 4),

        # IV features
        "iv_pct":           session.get("iv_pct"),
        "vol_skew_left":    vs.get("left_slope"),
        "vol_skew_right":   vs.get("right_slope"),
        "vol_skew_ratio":   vs.get("slope_ratio"),
        "vol_skew_verdict": vs.get("verdict"),

        # OI summary features
        "call_pct":            oi.get("call_pct"),
        "put_pct":             oi.get("put_pct"),
        "skew_verdict":        oi.get("skew_verdict"),
        "magnet_count":        len(magnets),
        "gamma_level_count":   len(oi.get("gamma_levels") or []),
        "nearest_magnet_dist": (
            round(nearest_magnet_dist, 4) if nearest_magnet_dist is not None else None
        ),

        # OI raw snapshot (stored as JSONB; excluded from model input)
        "oi_snapshot": session.get("oi_data") or [],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_feature_builder.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add feature_builder.py tests/test_feature_builder.py
git commit -m "feat: add feature_builder with shared OI feature extraction"
```

---

## Task 3: model_scorer.py (TDD)

**Files:**
- Create: `model_scorer.py`
- Create: `tests/test_model_scorer.py`
- Create: `model/` directory

- [ ] **Step 1: Write the failing tests**

Create `tests/test_model_scorer.py`:

```python
# tests/test_model_scorer.py
import copy
import numpy as np
import pytest
from unittest.mock import MagicMock
from tests.conftest import SAMPLE_SESSION

SAMPLE_SIGNAL = {
    "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
    "entry": 4750.0, "tp1": 4725.0, "tp2": 4700.0, "sl": 4775.0,
    "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
}


def test_score_returns_unscored_when_no_model_file(tmp_path, monkeypatch):
    """Cold start: no model.pkl → returns UNSCORED, does not crash."""
    import model_scorer
    monkeypatch.setattr(model_scorer, "MODEL_PATH", tmp_path / "model.pkl")
    monkeypatch.setattr(model_scorer, "META_PATH", tmp_path / "metadata.json")
    monkeypatch.setattr(model_scorer, "_pipeline", None)

    result = model_scorer.score(
        SAMPLE_SIGNAL.copy(), 4740.0, copy.deepcopy(SAMPLE_SESSION)
    )
    assert result["win_prob"] is None
    assert result["confidence_label"] == "UNSCORED"
    assert result["model_version"] is None


def test_score_preserves_original_signal_fields(tmp_path, monkeypatch):
    """All original signal fields are present in the returned dict."""
    import model_scorer
    monkeypatch.setattr(model_scorer, "MODEL_PATH", tmp_path / "model.pkl")
    monkeypatch.setattr(model_scorer, "_pipeline", None)

    result = model_scorer.score(
        SAMPLE_SIGNAL.copy(), 4740.0, copy.deepcopy(SAMPLE_SESSION)
    )
    assert result["zone"] == "+2SD"
    assert result["direction"] == "SHORT"
    assert result["entry"] == 4750.0
    assert result["tp1"] == 4725.0


def test_label_high():
    import model_scorer
    assert model_scorer._label(0.65) == "HIGH"
    assert model_scorer._label(0.90) == "HIGH"


def test_label_medium():
    import model_scorer
    assert model_scorer._label(0.64) == "MEDIUM"
    assert model_scorer._label(0.50) == "MEDIUM"


def test_label_low():
    import model_scorer
    assert model_scorer._label(0.49) == "LOW"
    assert model_scorer._label(0.0) == "LOW"


def test_score_with_mock_pipeline(monkeypatch):
    """When a pipeline is loaded, score() returns win_prob and label."""
    import model_scorer

    mock_pipeline = MagicMock()
    mock_pipeline.predict_proba.return_value = np.array([[0.32, 0.68]])
    monkeypatch.setattr(model_scorer, "_pipeline", mock_pipeline)
    monkeypatch.setattr(model_scorer, "_model_version", "v2")

    result = model_scorer.score(
        SAMPLE_SIGNAL.copy(), 4740.0, copy.deepcopy(SAMPLE_SESSION)
    )
    assert result["win_prob"] == 0.68
    assert result["confidence_label"] == "HIGH"
    assert result["model_version"] == "v2"
    assert result["zone"] == "+2SD"   # original fields preserved


def test_score_returns_unscored_on_pipeline_exception(monkeypatch):
    """If predict_proba raises, score() returns UNSCORED gracefully."""
    import model_scorer

    mock_pipeline = MagicMock()
    mock_pipeline.predict_proba.side_effect = ValueError("bad input")
    monkeypatch.setattr(model_scorer, "_pipeline", mock_pipeline)
    monkeypatch.setattr(model_scorer, "_model_version", "v1")

    result = model_scorer.score(
        SAMPLE_SIGNAL.copy(), 4740.0, copy.deepcopy(SAMPLE_SESSION)
    )
    assert result["win_prob"] is None
    assert result["confidence_label"] == "UNSCORED"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_model_scorer.py -v
```

Expected: `ModuleNotFoundError: No module named 'model_scorer'`

- [ ] **Step 3: Create model/ directory**

```bash
mkdir -p model
touch model/.gitkeep
```

- [ ] **Step 4: Implement model_scorer.py**

Create `model_scorer.py`:

```python
# model_scorer.py
"""
ML model inference wrapper.

Loads model/model.pkl lazily on first call (cached in module-level variable).
Returns win_prob and confidence_label added to the signal dict.

Cold start (no model file): returns signal unchanged with
  win_prob=None, confidence_label="UNSCORED", model_version=None.
Signals still fire and features are still saved during cold start.
"""
import json
import logging
import pickle
from pathlib import Path

from feature_builder import FEATURE_COLS, extract

log        = logging.getLogger("model_scorer")
BASE       = Path(__file__).parent
MODEL_PATH = BASE / "model" / "model.pkl"
META_PATH  = BASE / "model" / "metadata.json"

_pipeline      = None
_model_version = None


def _load() -> bool:
    """Load pipeline from disk. Returns True on success."""
    global _pipeline, _model_version
    if not MODEL_PATH.exists():
        return False
    try:
        with open(MODEL_PATH, "rb") as f:
            _pipeline = pickle.load(f)
        if META_PATH.exists():
            meta = json.loads(META_PATH.read_text())
            _model_version = meta.get("version")
        log.info(f"Model loaded: {_model_version}")
        return True
    except Exception as e:
        log.error(f"Failed to load model: {e}")
        _pipeline = None
        return False


def _label(prob: float) -> str:
    if prob >= 0.65:
        return "HIGH"
    if prob >= 0.50:
        return "MEDIUM"
    return "LOW"


def score(signal: dict, price: float, session: dict) -> dict:
    """
    Score a signal and return it with win_prob, confidence_label,
    and model_version added.

    If no model is available (cold start or load failure), returns
    the signal unchanged with win_prob=None and confidence_label="UNSCORED".
    Does not raise.
    """
    global _pipeline
    if _pipeline is None:
        _load()

    if _pipeline is None:
        return {**signal, "win_prob": None, "confidence_label": "UNSCORED",
                "model_version": None}

    try:
        import pandas as pd
        features = extract(signal, price, session)
        features.pop("oi_snapshot", None)   # JSONB — not a model input
        X = pd.DataFrame([features]).reindex(columns=FEATURE_COLS)
        prob = float(_pipeline.predict_proba(X)[0, 1])
        return {
            **signal,
            "win_prob":        round(prob, 4),
            "confidence_label": _label(prob),
            "model_version":   _model_version,
        }
    except Exception as e:
        log.error(f"Scoring failed: {e}")
        return {**signal, "win_prob": None, "confidence_label": "UNSCORED",
                "model_version": None}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_model_scorer.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add model_scorer.py model/.gitkeep tests/test_model_scorer.py
git commit -m "feat: add model_scorer with cold-start handling"
```

---

## Task 4: Update db_sync.py

**Files:**
- Modify: `db_sync.py`
- Modify: `tests/test_db_sync.py`

`insert_signal` must return `str | None` (the new row's UUID) instead of `bool`. A new `insert_signal_features` function writes features to the `signal_features` table.

- [ ] **Step 1: Update test_db_sync.py**

Replace the content of `tests/test_db_sync.py` with:

```python
# tests/test_db_sync.py
from unittest.mock import MagicMock, patch
import pytest


INSERTED_UUID = "abc123-def456-ghi789"


def _mock_client(return_uuid: str = INSERTED_UUID):
    """Build a mock Supabase client that chains .table().insert().execute() etc."""
    client = MagicMock()
    table  = MagicMock()
    client.table.return_value = table
    table.upsert.return_value = table
    table.insert.return_value = table
    table.select.return_value = table
    table.order.return_value  = table
    table.limit.return_value  = table
    table.execute.return_value = MagicMock(
        data=[{"id": return_uuid, "date": "2026-05-02"}]
    )
    return client


SAMPLE_SIGNAL = {
    "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
    "entry": 4750.0, "tp1": 4725.0, "tp2": 4700.0, "sl": 4775.0,
    "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
    "win_prob": 0.68, "confidence_label": "HIGH", "model_version": "v1",
}


@patch("db_sync._client")
def test_upsert_session_calls_table(mock_client_fn, sample_session):
    mock_client_fn.return_value = _mock_client()
    import db_sync
    result = db_sync.upsert_session(sample_session)
    assert result is True
    mock_client_fn.return_value.table.assert_called_with("sessions")


@patch("db_sync._client")
def test_upsert_session_returns_false_when_not_configured(mock_client_fn):
    mock_client_fn.return_value = None
    import db_sync
    assert db_sync.upsert_session({"date": "2026-05-02"}) is False


@patch("db_sync._client")
def test_insert_signal_returns_uuid(mock_client_fn):
    mock_client_fn.return_value = _mock_client(INSERTED_UUID)
    import db_sync
    result = db_sync.insert_signal("2026-05-02", SAMPLE_SIGNAL, 4740.0)
    assert result == INSERTED_UUID


@patch("db_sync._client")
def test_insert_signal_returns_none_when_not_configured(mock_client_fn):
    mock_client_fn.return_value = None
    import db_sync
    assert db_sync.insert_signal("2026-05-02", SAMPLE_SIGNAL, 4740.0) is None


@patch("db_sync._client")
def test_insert_signal_features_calls_table(mock_client_fn, sample_session):
    mock_client_fn.return_value = _mock_client()
    import db_sync
    result = db_sync.insert_signal_features(
        INSERTED_UUID, "2026-05-02", SAMPLE_SIGNAL, 4740.0, sample_session
    )
    assert result is True
    mock_client_fn.return_value.table.assert_called_with("signal_features")


@patch("db_sync._client")
def test_insert_signal_features_returns_false_when_not_configured(mock_client_fn, sample_session):
    mock_client_fn.return_value = None
    import db_sync
    result = db_sync.insert_signal_features(
        INSERTED_UUID, "2026-05-02", SAMPLE_SIGNAL, 4740.0, sample_session
    )
    assert result is False


@patch("db_sync._client")
def test_get_history_returns_list(mock_client_fn):
    mock_client_fn.return_value = _mock_client()
    import db_sync
    assert isinstance(db_sync.get_history(n=5), list)


@patch("db_sync._client")
def test_get_history_returns_empty_when_not_configured(mock_client_fn):
    mock_client_fn.return_value = None
    import db_sync
    assert db_sync.get_history() == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_db_sync.py -v
```

Expected: `test_insert_signal_returns_uuid` and feature tests FAIL (wrong return type + missing function).

- [ ] **Step 3: Update db_sync.py**

Replace the full content of `db_sync.py` with:

```python
# db_sync.py
"""
Supabase persistence layer.
"""
import logging
from config import settings

log = logging.getLogger("db_sync")


def _client():
    """Return Supabase client, or None if not configured."""
    if not settings.supabase_url or not settings.supabase_service_key:
        return None
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_key)


def upsert_session(session: dict) -> bool:
    """Upsert a session row (keyed on date). Returns True on success."""
    client = _client()
    if client is None:
        log.warning("Supabase not configured — skipping session upsert")
        return False
    try:
        row = {
            "date":        session["date"],
            "open_price":  session["open_price"],
            "iv_pct":      session["iv_pct"],
            "sd_zones":    session["sd_zones"],
            "oi_analysis": session.get("oi_analysis"),
            "phase1_at":   session.get("locked_at"),
            "phase2_at":   session.get("phase2_at"),
        }
        client.table("sessions").upsert(row, on_conflict="date").execute()
        log.info(f"Session {session['date']} upserted")
        return True
    except Exception as e:
        log.error(f"upsert_session: {e}")
        return False


def insert_signal(session_date: str, signal: dict, price: float) -> str | None:
    """
    Insert a fired signal row. Returns the new row's UUID on success, None on failure.
    """
    client = _client()
    if client is None:
        return None
    try:
        row = {
            "session_date": session_date,
            "fired_at":     signal["signal_at"],
            "price":        price,
            "zone":         signal["zone"],
            "direction":    signal["direction"],
            "entry":        signal.get("entry"),
            "tp1":          signal.get("tp1"),
            "tp2":          signal.get("tp2"),
            "sl":           signal.get("sl"),
            "confidence":   signal["confidence"],
            "recovery":     signal.get("recovery", False),
        }
        res = client.table("signals").insert(row).execute()
        signal_id = res.data[0]["id"]
        log.info(f"Signal {signal['zone']} {signal['direction']} stored → {signal_id}")
        return signal_id
    except Exception as e:
        log.error(f"insert_signal: {e}")
        return None


def insert_signal_features(
    signal_id: str,
    session_date: str,
    signal: dict,
    price: float,
    session: dict,
) -> bool:
    """
    Insert a signal_features row for the given signal_id.
    Uses feature_builder.extract() to build the flat feature dict.
    Returns True on success.
    """
    client = _client()
    if client is None:
        return False
    try:
        from feature_builder import extract
        features = extract(signal, price, session)
        row = {
            "signal_id":        signal_id,
            "session_date":     session_date,
            "win_prob":         signal.get("win_prob"),
            "confidence_label": signal.get("confidence_label"),
            "model_version":    signal.get("model_version"),
            **features,
        }
        client.table("signal_features").insert(row).execute()
        log.info(f"Signal features stored for {signal_id}")
        return True
    except Exception as e:
        log.error(f"insert_signal_features: {e}")
        return False


def get_history(n: int = 30) -> list[dict]:
    """Fetch the last N session rows from Supabase, newest first."""
    client = _client()
    if client is None:
        return []
    try:
        res = (
            client.table("sessions")
            .select("*")
            .order("date", desc=True)
            .limit(n)
            .execute()
        )
        return res.data
    except Exception as e:
        log.error(f"get_history: {e}")
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_db_sync.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add db_sync.py tests/test_db_sync.py
git commit -m "feat: insert_signal returns UUID + add insert_signal_features"
```

---

## Task 5: Wire scoring + feature write into server.py

**Files:**
- Modify: `server.py`

The signal fire site is in `_poll_once()` at the `zone != _last_zone` branch (around line 133). Three changes:
1. Import `model_scorer` at the top
2. Call `model_scorer.score()` before `insert_signal`
3. Call `insert_signal_features` with the returned UUID

- [ ] **Step 1: Add model_scorer import to server.py**

In `server.py`, find the imports block (around line 30–34):

```python
from config import settings
from pine_exporter import run as export_pine
from signal_engine import compute_signal
from tradingview_client import TradingViewClient
import alerts
import db_sync
```

Replace with:

```python
from config import settings
from pine_exporter import run as export_pine
from signal_engine import compute_signal
from tradingview_client import TradingViewClient
import alerts
import db_sync
import model_scorer
```

- [ ] **Step 2: Inject scoring + feature write at signal fire site**

In `server.py`, find the signal fire block (around line 128–134):

```python
    # Zone entry alert (fires once per zone per session via alerts deduplication)
    if zone not in ("INSIDE_1SD",) and zone != _last_zone:
        alerts.zone_alert(price, signal)
        if signal.get("recovery"):
            alerts.recovery_alert(price, signal)
        if signal["confidence"] in ("HIGH", "MEDIUM"):
            db_sync.insert_signal(session["date"], signal, price)
```

Replace with:

```python
    # Zone entry alert (fires once per zone per session via alerts deduplication)
    if zone not in ("INSIDE_1SD",) and zone != _last_zone:
        signal = model_scorer.score(signal, price, session)
        alerts.zone_alert(price, signal)
        if signal.get("recovery"):
            alerts.recovery_alert(price, signal)
        if signal["confidence"] in ("HIGH", "MEDIUM"):
            signal_id = db_sync.insert_signal(session["date"], signal, price)
            if signal_id:
                db_sync.insert_signal_features(signal_id, session["date"], signal, price, session)
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest -v
```

Expected: all existing tests pass. No new failures introduced.

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "feat: inject model scoring + feature storage at signal fire site"
```

---

## Task 6: Add model score to Discord zone_alert

**Files:**
- Modify: `alerts.py`
- Modify: `tests/test_alerts.py`

- [ ] **Step 1: Read the existing zone_alert test**

Open `tests/test_alerts.py` and find the `zone_alert` test to understand the existing pattern.

Run:
```bash
grep -n "zone_alert\|def test_" tests/test_alerts.py
```

- [ ] **Step 2: Add a test for score field in zone_alert**

In `tests/test_alerts.py`, add after the existing zone_alert tests:

```python
@patch("alerts._post")
def test_zone_alert_includes_model_score_when_present(mock_post):
    from alerts import zone_alert
    signal = {
        "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
        "entry": 4750.0, "tp1": 4725.0, "tp2": 4700.0, "sl": 4775.0,
        "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
        "win_prob": 0.68, "confidence_label": "HIGH", "model_version": "v2",
    }
    zone_alert(4740.0, signal)
    args = mock_post.call_args[0][0]
    field_names = [f["name"] for f in args["fields"]]
    assert "Model Score" in field_names


@patch("alerts._post")
def test_zone_alert_omits_model_score_when_unscored(mock_post):
    from alerts import zone_alert
    signal = {
        "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
        "entry": 4750.0, "tp1": 4725.0, "tp2": 4700.0, "sl": 4775.0,
        "recovery": False, "signal_at": "2026-05-02T09:00:00+07:00",
        "win_prob": None, "confidence_label": "UNSCORED", "model_version": None,
    }
    zone_alert(4740.0, signal)
    args = mock_post.call_args[0][0]
    field_names = [f["name"] for f in args["fields"]]
    assert "Model Score" not in field_names
```

- [ ] **Step 3: Run new tests to verify they fail**

```bash
pytest tests/test_alerts.py -v -k "model_score"
```

Expected: FAIL — `Model Score` field not found.

- [ ] **Step 4: Update zone_alert in alerts.py**

In `alerts.py`, find `zone_alert` (around line 90). Replace its `fields` assembly and `_post` call:

```python
def zone_alert(price: float, signal: dict) -> None:
    zone  = signal.get("zone", "")
    key   = f"zone_{zone}_{datetime.now(UTC7).strftime('%Y-%m-%d')}"
    if not _dedup(key):
        return
    is_3sd  = "3SD" in zone
    color   = 0xFF0000 if is_3sd else 0xFF9900
    emoji   = "🔴" if is_3sd else "⚠️"
    entry   = signal.get("entry")
    fields  = [
        {"name": "Zone",       "value": zone,                          "inline": True},
        {"name": "Direction",  "value": signal.get("direction", ""),  "inline": True},
        {"name": "Confidence", "value": signal.get("confidence", ""), "inline": True},
    ]
    if entry is not None:
        fields += [
            {"name": "Entry", "value": str(entry),                                    "inline": True},
            {"name": "TP1",   "value": str(signal.get("tp1")),                        "inline": True},
            {"name": "TP2/SL","value": f"{signal.get('tp2')} / {signal.get('sl')}",  "inline": True},
        ]
    win_prob = signal.get("win_prob")
    if win_prob is not None:
        label   = signal.get("confidence_label", "")
        version = signal.get("model_version") or "N/A"
        fields.append({
            "name":   "Model Score",
            "value":  f"{win_prob:.0%} win probability [{label}]  (model {version})",
            "inline": False,
        })
    _post({
        "title":     f"{emoji} XAUUSD at {zone} — {price:.2f}",
        "color":     color,
        "fields":    fields,
        "timestamp": datetime.now(UTC7).isoformat(),
    })
```

- [ ] **Step 5: Run all alert tests**

```bash
pytest tests/test_alerts.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add alerts.py tests/test_alerts.py
git commit -m "feat: add model score field to Discord zone alert"
```

---

## Task 7: train.py (TDD)

**Files:**
- Create: `train.py`
- Create: `tests/test_train.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_train.py`:

```python
# tests/test_train.py
import copy
import json
import pandas as pd
import pytest


SAMPLE_ROWS = [
    {
        "zone": "+2SD", "direction": "SHORT", "confidence": "MEDIUM",
        "skew_verdict": "CALL heavy — bullish", "vol_skew_verdict": "LEFT heavy — bearish vol skew",
        "recovery": False,
        "price_vs_open": 118.22, "price_vs_open_pct": 2.56,
        "sd1_pts": 82.04, "sd2_pts": 164.07, "sd3_pts": 246.11, "dist_to_nearest_sd": 36.18,
        "iv_pct": 28.4, "vol_skew_left": 0.0012, "vol_skew_right": 0.0008, "vol_skew_ratio": 1.5,
        "call_pct": 65.0, "put_pct": 35.0,
        "magnet_count": 2, "gamma_level_count": 1, "nearest_magnet_dist": 50.0,
        "outcome": "WIN",
    },
    {
        "zone": "-3SD", "direction": "LONG", "confidence": "HIGH",
        "skew_verdict": "PUT heavy — bearish", "vol_skew_verdict": "LEFT heavy — bearish vol skew",
        "recovery": False,
        "price_vs_open": -238.5, "price_vs_open_pct": -5.16,
        "sd1_pts": 82.04, "sd2_pts": 164.07, "sd3_pts": 246.11, "dist_to_nearest_sd": 7.6,
        "iv_pct": 30.1, "vol_skew_left": 0.0014, "vol_skew_right": 0.0007, "vol_skew_ratio": 2.0,
        "call_pct": 40.0, "put_pct": 60.0,
        "magnet_count": 1, "gamma_level_count": 0, "nearest_magnet_dist": 25.0,
        "outcome": "LOSS",
    },
    {
        "zone": "+3SD", "direction": "SHORT", "confidence": "HIGH",
        "skew_verdict": "PUT heavy — bearish", "vol_skew_verdict": "RIGHT heavy — bullish vol skew",
        "recovery": True,
        "price_vs_open": 248.0, "price_vs_open_pct": 5.37,
        "sd1_pts": 82.04, "sd2_pts": 164.07, "sd3_pts": 246.11, "dist_to_nearest_sd": 1.89,
        "iv_pct": 31.5, "vol_skew_left": 0.0010, "vol_skew_right": 0.0011, "vol_skew_ratio": 0.9,
        "call_pct": 45.0, "put_pct": 55.0,
        "magnet_count": 0, "gamma_level_count": 2, "nearest_magnet_dist": None,
        "outcome": "WIN",
    },
]


def test_build_features_returns_correct_shape():
    from train import build_features
    X, y = build_features(pd.DataFrame(SAMPLE_ROWS))
    assert len(X) == 3
    assert len(y) == 3


def test_build_features_labels_win_as_1_loss_as_0():
    from train import build_features
    X, y = build_features(pd.DataFrame(SAMPLE_ROWS))
    assert y.iloc[0] == 1   # WIN
    assert y.iloc[1] == 0   # LOSS
    assert y.iloc[2] == 1   # WIN


def test_build_features_drops_rows_with_high_missing():
    from train import build_features
    rows = copy.deepcopy(SAMPLE_ROWS)
    # Row with all feature values as None (100% missing → exceeds 20% threshold)
    bad = {k: None for k in rows[0]}
    bad["outcome"] = "WIN"
    rows.append(bad)
    X, y = build_features(pd.DataFrame(rows))
    assert len(X) == 3   # bad row dropped, original 3 kept


def test_build_features_converts_recovery_to_int():
    from train import build_features
    X, y = build_features(pd.DataFrame(SAMPLE_ROWS))
    assert X["recovery"].dtype in (int, "int64", "int32")


def test_next_version_returns_v1_when_no_metadata(tmp_path, monkeypatch):
    import train
    monkeypatch.setattr(train, "BASE", tmp_path)
    (tmp_path / "model").mkdir()
    assert train._next_version() == "v1"


def test_next_version_increments_from_existing_metadata(tmp_path, monkeypatch):
    import train
    monkeypatch.setattr(train, "BASE", tmp_path)
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "metadata.json").write_text(json.dumps({"version": "v3"}))
    assert train._next_version() == "v4"


def test_build_pipeline_selects_logreg_for_small_dataset():
    from train import build_pipeline
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    X = pd.DataFrame(SAMPLE_ROWS)[["call_pct", "put_pct"]]
    pipeline, model_type = build_pipeline(X, force_logreg=False)
    assert model_type == "LogisticRegression"
    assert isinstance(pipeline, Pipeline)


def test_build_pipeline_force_logreg_overrides_count(tmp_path):
    from train import build_pipeline
    # Even if we pretend we have 200 rows, force_logreg should override
    import pandas as pd
    rows = SAMPLE_ROWS * 70   # 210 rows
    X = pd.DataFrame(rows)[["call_pct", "put_pct"]]
    _, model_type = build_pipeline(X, force_logreg=True)
    assert model_type == "LogisticRegression"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_train.py -v
```

Expected: `ModuleNotFoundError: No module named 'train'`

- [ ] **Step 3: Implement train.py**

Create `train.py`:

```python
# train.py
"""
OI Data Warehouse — Model Training Script

Usage:
  python train.py                 # auto-select LogReg (<100) or XGBoost (>=100)
  python train.py --force-logreg  # always use LogisticRegression

Fetches labeled signal_features from Supabase (JOIN signals.outcome),
trains a sklearn Pipeline, saves model/model.pkl + model/metadata.json.

Run this manually whenever you want to update the model.
"""
import argparse
import json
import logging
import pickle
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_validate
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from feature_builder import CATEGORICAL_COLS, FEATURE_COLS, NUMERIC_COLS

BASE      = Path(__file__).parent
MODEL_DIR = BASE / "model"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("train")


def fetch_labeled_data() -> pd.DataFrame:
    """
    Query Supabase: signal_features JOIN signals WHERE outcome IN ('WIN','LOSS').
    Returns a DataFrame with all feature columns + 'outcome'.
    """
    from config import settings
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_service_key)
    res = (
        client.table("signal_features")
        .select("*, signals!inner(outcome)")
        .execute()
    )
    rows = []
    for r in res.data:
        outcome = (r.pop("signals") or {}).get("outcome")
        if outcome in ("WIN", "LOSS"):
            r["outcome"] = outcome
            rows.append(r)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def build_features(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """
    Clean and encode a raw labeled DataFrame into (X, y).

    Drops rows with >20% missing flat features.
    Converts 'recovery' boolean to int.
    X has exactly FEATURE_COLS columns (NaN for any missing).
    y is 1=WIN, 0=LOSS.
    """
    # Ensure all expected columns present (NaN if not in df)
    all_cols = FEATURE_COLS + ["outcome"]
    df = df.reindex(columns=all_cols)

    missing_pct = df[FEATURE_COLS].isnull().mean(axis=1)
    df = df[missing_pct <= 0.2].copy()

    df["recovery"] = df["recovery"].fillna(False).astype(int)

    X = df[FEATURE_COLS].copy()
    y = (df["outcome"] == "WIN").astype(int)
    return X, y


def build_pipeline(X: pd.DataFrame, force_logreg: bool = False) -> tuple[Pipeline, str]:
    """
    Build a sklearn Pipeline with ColumnTransformer preprocessing + classifier.

    Selects LogisticRegression when n_samples < 100 OR force_logreg=True.
    Selects XGBClassifier otherwise.
    """
    num_cols = [c for c in NUMERIC_COLS if c in X.columns]
    cat_cols = [c for c in CATEGORICAL_COLS if c in X.columns]

    preprocessor = ColumnTransformer([
        ("num", StandardScaler(), num_cols),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cat_cols),
    ])

    if force_logreg or len(X) < 100:
        clf        = LogisticRegression(max_iter=1000, class_weight="balanced")
        model_type = "LogisticRegression"
    else:
        from xgboost import XGBClassifier
        clf        = XGBClassifier(
            n_estimators=100, max_depth=4, eval_metric="logloss", verbosity=0
        )
        model_type = "XGBClassifier"

    return Pipeline([("prep", preprocessor), ("clf", clf)]), model_type


def _next_version() -> str:
    """Return the next model version string (e.g. 'v4')."""
    meta_path = BASE / "model" / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            n = int(meta.get("version", "v0").lstrip("v")) + 1
            return f"v{n}"
        except Exception:
            pass
    return "v1"


def main(force_logreg: bool = False) -> dict | None:
    MODEL_DIR.mkdir(exist_ok=True)

    log.info("Fetching labeled data from Supabase…")
    df = fetch_labeled_data()
    if df.empty:
        log.error("No labeled data returned from Supabase.")
        return None

    n_total  = len(df)
    win_rate = (df["outcome"] == "WIN").mean()
    log.info(f"Dataset: {n_total} labeled rows, WIN rate: {win_rate:.1%}")

    X, y = build_features(df)
    n_samples = len(X)
    log.info(f"After quality filter: {n_samples} training rows")

    if n_samples < 10:
        log.error(f"Need at least 10 rows to train. Have {n_samples}.")
        return None

    pipeline, model_type = build_pipeline(X, force_logreg)
    log.info(f"Model type: {model_type}")

    n_splits = min(5, n_samples)
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    cv_results = cross_validate(
        pipeline, X, y, cv=cv, scoring=["accuracy", "roc_auc"]
    )
    cv_acc = float(cv_results["test_accuracy"].mean())
    cv_auc = float(cv_results["test_roc_auc"].mean())
    log.info(f"CV ({n_splits}-fold) accuracy={cv_acc:.3f}  ROC-AUC={cv_auc:.3f}")

    pipeline.fit(X, y)

    version = _next_version()

    with open(MODEL_DIR / "model.pkl", "wb") as f:
        pickle.dump(pipeline, f)

    meta = {
        "version":     version,
        "trained_at":  datetime.now(timezone.utc).isoformat(),
        "n_samples":   n_samples,
        "win_rate":    round(win_rate, 4),
        "model_type":  model_type,
        "cv_accuracy": round(cv_acc, 4),
        "cv_roc_auc":  round(cv_auc, 4),
        "features":    list(X.columns),
    }
    (MODEL_DIR / "metadata.json").write_text(json.dumps(meta, indent=2))

    log.info(f"Saved: model/{version}  ({model_type}, {n_samples} samples)")
    log.info(f"       accuracy={cv_acc:.1%}  ROC-AUC={cv_auc:.3f}")
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train OI signal scoring model")
    parser.add_argument("--force-logreg", action="store_true",
                        help="Force LogisticRegression regardless of sample count")
    args = parser.parse_args()
    main(force_logreg=args.force_logreg)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_train.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Run the full suite**

```bash
pytest -v
```

Expected: all tests PASS. Zero failures.

- [ ] **Step 6: Commit**

```bash
git add train.py tests/test_train.py
git commit -m "feat: add train.py with LogReg/XGBoost auto-selection and CV"
```

---

## Task 8: Dashboard score badge

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`

The WebSocket already broadcasts `win_prob` and `confidence_label` inside the signal object (added by `model_scorer.score()` in Task 5). The dashboard only needs to render them.

- [ ] **Step 1: Add score badge element to index.html**

In `dashboard/index.html`, find the signal card block containing `zone-badge`, `direction-badge`, `confidence`:

```html
    <div id="zone-badge">—</div>
    <div id="direction-badge">WAIT</div>
    <div id="confidence">—</div>
```

Add a score badge element immediately after `confidence`:

```html
    <div id="zone-badge">—</div>
    <div id="direction-badge">WAIT</div>
    <div id="confidence">—</div>
    <div id="model-score-badge" style="display:none"></div>
```

- [ ] **Step 2: Update renderSignal in app.js to populate the badge**

In `dashboard/app.js`, find the `renderSignal` function (line 521). Replace it with:

```javascript
function renderSignal(sig) {
  if (!sig) return;
  const zone  = sig.zone || '—';
  const dir   = sig.direction || 'WAIT';
  const conf  = sig.confidence || '—';

  document.getElementById('zone-badge').textContent      = zone;
  document.getElementById('direction-badge').textContent  = dir;
  document.getElementById('direction-badge').style.color  =
    dir === 'LONG' ? 'var(--green)' : dir === 'SHORT' ? 'var(--red)' : 'var(--muted)';
  document.getElementById('confidence').textContent       = conf;

  const fields = ['entry', 'tp1', 'tp2', 'sl'];
  fields.forEach(f => {
    const el = document.getElementById(`val-${f}`);
    if (el) el.textContent = sig[f] != null ? sig[f].toFixed(2) : '—';
  });

  const banner = document.getElementById('recovery-banner');
  banner.style.display = sig.recovery ? 'block' : 'none';

  // Model score badge
  const scoreBadge = document.getElementById('model-score-badge');
  if (scoreBadge) {
    const prob  = sig.win_prob;
    const label = sig.confidence_label || 'UNSCORED';
    if (prob != null) {
      const colorMap = { HIGH: 'var(--green)', MEDIUM: 'var(--amber)', LOW: 'var(--red)' };
      scoreBadge.textContent  = `Model: ${Math.round(prob * 100)}% [${label}]`;
      scoreBadge.style.color  = colorMap[label] || 'var(--muted)';
      scoreBadge.style.display = 'block';
    } else {
      scoreBadge.textContent  = 'Model: UNSCORED';
      scoreBadge.style.color  = 'var(--muted)';
      scoreBadge.style.display = 'block';
    }
  }
}
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest -v
```

Expected: all tests PASS (no server/dashboard unit tests cover this rendering, so no failures expected).

- [ ] **Step 4: Commit**

```bash
git add dashboard/index.html dashboard/app.js
git commit -m "feat: add model score badge to dashboard signal card"
```

---

## Task 9: Final verification

- [ ] **Step 1: Run the complete test suite one final time**

```bash
pytest -v --tb=short
```

Expected output: all tests PASS, zero failures, zero errors.

- [ ] **Step 2: Verify no uncommitted changes**

```bash
git status
```

Expected: `nothing to commit, working tree clean`

- [ ] **Step 3: Verify model/ directory is gitignored**

```bash
echo "test" > model/test.txt && git status model/ && rm model/test.txt
```

Expected: `model/test.txt` does NOT appear in git status (it is ignored).

- [ ] **Step 4: Smoke-check train.py help**

```bash
python train.py --help
```

Expected: prints usage without error.

- [ ] **Step 5: Final commit (if any loose ends)**

```bash
git log --oneline -8
```

Verify commits exist for all 8 tasks above.
