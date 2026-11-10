#!/bin/bash
# scripts/install_scheduler.sh — Install the daily pipeline as a macOS launchd service.
#
# Run once: bash scripts/install_scheduler.sh
# To uninstall: bash scripts/install_scheduler.sh --uninstall

set -e
PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/com.faceless.daily-pipeline.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.faceless.daily-pipeline.plist"
LABEL="com.faceless.daily-pipeline"
LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)/logs"

# ── Uninstall ──────────────────────────────────────────────────────────────
if [[ "$1" == "--uninstall" ]]; then
  echo "🗑  Unloading and removing launchd job..."
  launchctl unload "$PLIST_DST" 2>/dev/null || true
  rm -f "$PLIST_DST"
  echo "✅ Uninstalled."
  exit 0
fi

# ── Install ────────────────────────────────────────────────────────────────
echo ""
echo "🤖 Faceless AI Channel — Daily Scheduler Installer"
echo "──────────────────────────────────────────────────"
echo ""

# 1. Ensure log dir exists
mkdir -p "$LOG_DIR"

# 2. Unload old version if present
launchctl unload "$PLIST_DST" 2>/dev/null || true

# 3. Copy plist to LaunchAgents
cp "$PLIST_SRC" "$PLIST_DST"
echo "✅ Plist copied → $PLIST_DST"

# 4. Load the job
launchctl load "$PLIST_DST"
echo "✅ Launchd job loaded"

# 5. Show next run
echo ""
echo "──────────────────────────────────────────────────"
echo "✅ Scheduler installed! Pipeline runs daily at 6:30 PM (auto-retries 8:00 and 9:30 PM)."
echo ""
echo "Useful commands:"
echo "  Run immediately:  launchctl start $LABEL"
echo "  Stop:             launchctl stop  $LABEL"
echo "  Uninstall:        bash scripts/install_scheduler.sh --uninstall"
echo "  View live logs:   tail -f $LOG_DIR/launchd_stdout.log"
echo "  View errors:      tail -f $LOG_DIR/launchd_stderr.log"
echo ""
echo "To change the run time — edit com.faceless.daily-pipeline.plist"
echo "then re-run this script."
echo ""

# ── Remind about YouTube setup ──────────────────────────────────────────────
if [ ! -f "$(cd "$(dirname "$0")/.." && pwd)/credentials/youtube_token.json" ]; then
  echo "⚠️  YouTube not authorized yet!"
  echo "   Run: python scripts/setup_youtube_auth.py"
  echo "   Then set YOUTUBE_PRODUCTION_MODE=true in .env"
  echo ""
fi
