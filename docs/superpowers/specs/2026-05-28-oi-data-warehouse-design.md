# OI Data Warehouse — Design Spec
**Date:** 2026-05-28
**Project:** XAUUSD Automation Framework
**Status:** Approved

---

## 1. Goal

Add a data warehouse layer to the existing XAUUSD automation framework that:
- Captures a full feature snapshot (OI, price, IV) every time a signal fires
- Scores each signal with a win-probability using a trained ML model
- Displays the score in Discord alerts and the dashboard
- Enables manual retraining from accumulated labeled history (WIN/LOSS outcomes)

The model does **not** replace the rule-based signal engine. It annotates and ranks signals that the engine already fires, with a HIGH / MEDIUM / LOW confidence label.

---

## 2. Architecture

### Data Flow

```
── COLLECTION (Phase 1 + 2, runs at market open) ──────────────────
collector.py      →  open_price, iv_pct, sd_zones, vol_curve_points
oi_collector.py   →  oi_data, oi_interest_data, oi_analysis, vol_skew_analysis
                  ↓
          session_data.json  (local cache)
          Supabase: sessions  (upserted by db_sync.py)

── SIGNAL FIRE (intraday, on price tick) ───────────────────────────
signal_engine.py  →  zone, direction, entry, tp1, tp2, sl, confidence
                  ↓
          [NEW] model_scorer.py  →  win_prob, confidence_label
                  ↓
          db_sync.py
            Supabase: signals           (existing)
            [NEW] Supabase: signal_features  (feature snapshot per signal)
            Discord webhook             (includes win_prob + label)
            Dashboard (WebSocket)       (shows score prominently)

── OUTCOME ENTRY (manual, after trade closes) ──────────────────────
Update signals.outcome = WIN / LOSS / SCRATCH  (already happening)

── TRAINING (manual trigger) ───────────────────────────────────────
python train.py
  1. Query signal_features JOIN signals WHERE outcome IN ('WIN','LOSS')
  2. Build feature matrix
  3. LogReg (<100 samples) or XGBoost (≥100 samples)
  4. Save model/model.pkl + model/metadata.json
  5. Next signal automatically uses new model
```

### New Components

| Component | Purpose |
|-----------|---------|
| `feature_builder.py` | Shared feature extraction — used by both `model_scorer` and `train.py` |
| `model_scorer.py` | Loads `model/model.pkl`, scores a signal at fire time |
| `train.py` | Manually-triggered training script |
| `model/model.pkl` | Serialized trained model (gitignored) |
| `model/metadata.json` | Training run metadata (version, accuracy, n_samples) |
| `migrations/002_signal_features.sql` | New Supabase table |

### Modified Components

| File | Change |
|------|--------|
| `server.py` | Call `model_scorer.score(signal, price, session)` at signal fire site; broadcast score via WS; display score badge on signal card |
| `scheduler.py` | Call `model_scorer.score(signal, price, session)` at signal fire site |
| `db_sync.py` | `insert_signal()` returns new signal UUID; new `insert_signal_features()` function |
| Discord alert | Append score line to existing message format |

---

## 3. Database Schema

### New table: `signal_features`

```sql
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

**Label join at training time (not stored in this table):**
```sql
SELECT sf.*, s.outcome
FROM signal_features sf
JOIN signals s ON sf.signal_id = s.id
WHERE s.outcome IN ('WIN', 'LOSS')
```

SCRATCH outcomes are excluded from training (ambiguous label) but their feature rows are retained for analysis.

---

## 4. Feature Builder (`feature_builder.py`)

Extracts a flat feature dict from a `(signal, session)` pair. Used identically by both `model_scorer.py` at inference time and `train.py` at training time.

**Input:** signal dict (from `signal_engine.compute_signal`) + session dict (from `session_data.json`)

**Output flat fields:**
- `zone`, `direction`, `confidence`, `recovery`
- `price`, `price_vs_open`, `price_vs_open_pct`
- `sd1_pts`, `sd2_pts`, `sd3_pts`, `dist_to_nearest_sd`
- `iv_pct`, `vol_skew_left`, `vol_skew_right`, `vol_skew_ratio`, `vol_skew_verdict`
- `call_pct`, `put_pct`, `skew_verdict`
- `magnet_count`, `gamma_level_count`, `nearest_magnet_dist`
- `oi_snapshot` (full `oi_data` list, stored as-is for JSONB)

**Categorical encoding at training time** (inside `train.py`, not in feature_builder):
- `zone` → ordinal (INSIDE_1SD=0, 2SD=1, 3SD=2, BEYOND_3SD=3)
- `direction` → binary (LONG=1, SHORT=0)
- `skew_verdict` → one-hot
- `vol_skew_verdict` → one-hot
- `confidence` → ordinal (LOW=0, MED=1, HIGH=2)

---

## 5. Model Scorer (`model_scorer.py`)

Loads `model/model.pkl` on first call (lazy load, cached in memory).

**At signal fire time:**
1. Call `feature_builder.extract(signal, price, session)` → flat feature dict
2. Drop `oi_snapshot` (not used for inference — JSONB, not numeric)
3. Apply same encoding as training
4. Call `model.predict_proba(X)[:, 1]` → `win_prob` (float 0–1)
5. Map to label: `>= 0.65` → HIGH, `0.50–0.64` → MEDIUM, `< 0.50` → LOW

**Cold start (no model file):** return `win_prob=None`, `confidence_label="UNSCORED"`. Signals still fire normally. Features are still saved.

**Model version:** read from `model/metadata.json`, written to `signal_features.model_version`.

---

## 6. Training Script (`train.py`)

```
python train.py [--min-samples N] [--force-logreg]
```

**Steps:**
1. Query Supabase: `signal_features JOIN signals WHERE outcome IN ('WIN','LOSS')`
2. Print dataset summary (n samples, WIN rate, feature completeness)
3. Drop rows with >20% missing flat features
4. Encode categoricals
5. Model selection:
   - `< 100` samples → `sklearn.linear_model.LogisticRegression`
   - `>= 100` samples → `xgboost.XGBClassifier`
6. 5-fold stratified cross-validation → log accuracy + ROC-AUC
7. Fit final model on full dataset
8. Save `model/model.pkl` (pickle)
9. Save `model/metadata.json`:
   ```json
   {
     "version": "v<N>",
     "trained_at": "<ISO timestamp>",
     "n_samples": 87,
     "win_rate": 0.54,
     "model_type": "LogisticRegression",
     "cv_accuracy": 0.64,
     "cv_roc_auc": 0.71,
     "features": ["zone_enc", "call_pct", ...]
   }
   ```
10. Print summary to stdout

Version `N` is auto-incremented from the previous `metadata.json`, or starts at `v1`.

---

## 7. Integration Details

### `signal_engine.py`

After `compute_signal()` returns, the call site (`server.py` and `scheduler.py`) calls:
```python
from model_scorer import score
signal = score(signal, price, session)  # adds win_prob, confidence_label
```

`signal_engine.py` itself is not modified — scoring is injected at the two call sites.

### `db_sync.py`

`insert_signal()` returns the new signal UUID. A second call writes features:
```python
signal_id = insert_signal(session_date, signal, price)
if signal_id:
    insert_signal_features(signal_id, session_date, signal, session)
```

### Discord Alert

Append to existing message:
```
Model score: 67% win probability [HIGH]  (model v3, 87 samples)
```
If `UNSCORED`, omit the line entirely.

### Dashboard

Add a score badge to the signal card. HIGH = green, MEDIUM = yellow, LOW = red, UNSCORED = gray.

---

## 8. File & Directory Layout

```
xauusd_automation/
├── feature_builder.py          # NEW — shared feature extraction
├── model_scorer.py             # NEW — inference wrapper
├── train.py                    # NEW — training script
├── model/
│   ├── model.pkl               # NEW — gitignored
│   └── metadata.json           # NEW — gitignored
├── migrations/
│   ├── 001_initial_schema.sql  # existing
│   └── 002_signal_features.sql # NEW
├── signal_engine.py            # modified (call site only)
├── db_sync.py                  # modified (add insert_signal_features)
├── server.py                   # modified (broadcast score)
└── dashboard/                  # modified (display score badge)
```

Add to `.gitignore`:
```
model/model.pkl
model/metadata.json
.superpowers/
```

---

## 9. Dependencies

Add to `requirements.txt`:
```
scikit-learn>=1.4
xgboost>=2.0
pandas>=2.0
```

---

## 10. Open Questions / Future Work

- **SCRATCH handling:** Currently excluded from training. Could later be treated as a third class or as partial credit (0.5 label).
- **Intraday OI updates:** Current collection runs once at market open. If intraday OI shifts significantly, the snapshot may be stale by the time a signal fires later in the session. A future phase could re-collect OI mid-session.
- **Feature importance reporting:** `train.py` could optionally print top-N features after training to guide future feature engineering.
- **Backtesting:** Once `signal_features` has enough rows, a separate `backtest.py` script can replay historical signals through different model versions.
