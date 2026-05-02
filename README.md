# XAUUSD OI Trading Framework — Automation Scripts

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Playwright browser
python -m playwright install chromium

# 3. Run Phase 1 manually (open price + IV → SD zones)
python collector.py

# 4. Run Phase 2 manually (OI sentiment at 08:30)
python oi_collector.py

# 5. Or run the scheduler (auto-fires at correct times)
pip install apscheduler
python scheduler.py
```

---

## Files

| File | Purpose |
|---|---|
| `collector.py` | Phase 1 — scrapes investing.com open price + CME IV, calculates SD zones |
| `oi_collector.py` | Phase 2 — scrapes CME intraday OI, skew, magnets, gamma levels |
| `scheduler.py` | Runs both on a cron-like schedule (01:30 + 08:30 UTC+7) |
| `session_data.json` | Output — consumed by the trading dashboard HTML |
| `screenshots/` | Auto-saved screenshots for debugging |

---

## Output: session_data.json

```json
{
  "locked_at": "2025-01-15T01:30:00+07:00",
  "open_price": 3250.00,
  "iv_pct": 16.0,
  "sd_zones": {
    "daily_pct": 1.0000,
    "sd1_pts": 32.50,
    "sd2_pts": 65.00,
    "sd3_pts": 97.50,
    "zones": {
      "+3SD": 3347.50,
      "+2SD": 3315.00,
      "+1SD": 3282.50,
      "OPEN": 3250.00,
      "-1SD": 3217.50,
      "-2SD": 3185.00,
      "-3SD": 3152.50
    }
  },
  "oi_data": [...],
  "oi_analysis": {
    "call_pct": 52.0,
    "put_pct": 48.0,
    "skew_verdict": "Neutral",
    "magnets": [3200, 3300],
    "gamma_levels": [{"strike": 3150, "type": "put", "delta": 0.02}]
  }
}
```

---

## Schedule

| Phase | Time (UTC+7) | Time (UTC) | Script |
|---|---|---|---|
| Phase 1 | 01:30 Mon–Fri | 18:30 Sun–Thu | `collector.py` |
| Phase 2 | 08:30 Mon–Fri | 01:30 Mon–Fri | `oi_collector.py` |

---

## Troubleshooting

**Bot detection / 403 errors**
- Set `HEADLESS = False` in the script (already default)
- The visible browser helps bypass fingerprinting
- If still blocked: manually navigate to the site, solve any CAPTCHA, then re-run

**CME auto-extraction fails**
- The CME Vol2Vol tool is JavaScript-heavy; selectors may shift
- The script falls back to manual CLI input automatically
- Check `screenshots/` to see exactly what the browser is seeing

**investing.com layout changes**
- Edit the `strategies` list in `fetch_open_price()` — add new selectors at the top
- Use browser DevTools (F12) to find the current element's class or data-test attribute

---

## Cron Setup (Linux/macOS)

```bash
crontab -e
```

Add these lines:
```
# XAUUSD Phase 1 — 01:30 UTC+7 = 18:30 UTC (prev day)
30 18 * * 0-4  cd /path/to/xauusd_automation && python3 collector.py >> logs/phase1.log 2>&1

# XAUUSD Phase 2 — 08:30 UTC+7 = 01:30 UTC
30  1 * * 1-5  cd /path/to/xauusd_automation && python3 oi_collector.py >> logs/phase2.log 2>&1
```

## Windows Task Scheduler

- Action: `python C:\path\to\collector.py`
- Trigger: Daily at 18:30 UTC (adjust for your timezone offset)
- Repeat for `oi_collector.py` at 01:30 UTC
