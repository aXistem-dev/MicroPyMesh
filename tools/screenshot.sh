#!/bin/sh
# Save what the device shows as a PNG on this machine:  tools/screenshot.sh out.png
# Takes a raw RGB565 snapshot through the asyncio REPL, stops the TaskManager so mpremote
# can copy it, and converts it here (needs Pillow). Restart the device afterwards
# (tools/deploy.sh does) to get the OS back.
set -e
cd "$(dirname "$0")/.."
PORT=${PORT:-/dev/ttyUSB0}
MP=${MPREMOTE:-mpremote}
PY=${PYTHON:-python3}
out=${1:-shot.png}
$PY tools/repl_type.py "$PORT" "from mpos.ui.testing import capture_screenshot; capture_screenshot('/shot.raw', 480, 480)" 6 >/dev/null
$PY tools/repl_type.py "$PORT" --stop
# A running radio manager prints to the console, which breaks mpremote's transfers.
$PY tools/repl_type.py "$PORT" "import sys; m = sys.modules.get('meshcore_manager'); m and m.MeshCoreManager.get_instance().stop()" 3 >/dev/null
raw=$(mktemp)
trap 'rm -f "$raw"' EXIT
$MP connect "$PORT" resume fs cp :/shot.raw "$raw"
python3 - "$raw" "$out" <<'PYEOF'
import sys
from PIL import Image
data = open(sys.argv[1], "rb").read()
img = Image.frombytes("RGB", (480, 480), data, "raw", "BGR;16")
img.save(sys.argv[2])
print("saved", sys.argv[2])
PYEOF
