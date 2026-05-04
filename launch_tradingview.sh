#!/bin/bash
# Launches TradingView Desktop with Chrome DevTools Protocol enabled.
# Required for push_pine() to inject Pine Script into the editor.

echo "Stopping TradingView..."
pkill -x TradingView 2>/dev/null
sleep 3

echo "Launching TradingView with CDP on port 9222..."
/Applications/TradingView.app/Contents/MacOS/TradingView \
  --remote-debugging-port=9222 \
  '--remote-allow-origins=*' \
  > /tmp/tradingview_cdp.log 2>&1 &

TV_PID=$!
echo "TradingView PID: $TV_PID"

echo "Waiting for CDP to become available..."
for i in $(seq 1 20); do
  sleep 1
  if curl -s http://localhost:9222/json > /dev/null 2>&1; then
    echo "CDP ready on port 9222 ✓"
    echo ""
    echo "Open a XAUUSD chart in TradingView, then verify:"
    echo "  python3 -c \"from tradingview_client import TradingViewClient; tv = TradingViewClient(); print('quote:', tv.get_quote())\""
    exit 0
  fi
  echo "  waiting... ($i/20)"
done

echo "CDP did not start. Check /tmp/tradingview_cdp.log for errors."
exit 1
