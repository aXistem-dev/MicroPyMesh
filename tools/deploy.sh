#!/bin/sh
# Install the app on a MicroPythonOS device over Wi-Fi and (with --start) open it.
#   HOST_IP=<this machine's LAN address> tools/deploy.sh [--start]
# Builds the .mpk, serves it from this machine, and types the install command into the
# device's REPL over USB serial (PORT, default /dev/ttyUSB0; PYTHON is a python3 with
# pyserial). Bulk copies over the serial REPL are unreliable on the SenseCAP Indicator, so
# only short commands go that way; the package itself travels over Wi-Fi. The install is
# checked on the device (file count and bytes) before the app is started.
set -e
cd "$(dirname "$0")/.."
: "${HOST_IP:?set HOST_IP to this machine's address on the device's LAN}"
APP=eu.axistem.micropymesh
PORT=${PORT:-/dev/ttyUSB0}
PY=${PYTHON:-python3}
HTTP_PORT=${HTTP_PORT:-8765}

dist=$(mktemp -d)
trap 'rm -rf "$dist"' EXIT
python3 build_mpk.py >/dev/null
mv ${APP}_*.mpk "$dist/app.mpk"

python3 tools/serve_once.py "$dist/app.mpk" "$HOST_IP" "$HTTP_PORT" 180 >"$dist/serve.log" &
server=$!
sleep 1
$PY tools/repl_type.py "$PORT" "from mpos import TaskManager as T, AppManager as A; T.create_task(A.download_and_install_package('http://$HOST_IP:$HTTP_PORT/app.mpk', '$APP'))" 2 >/dev/null
if ! wait $server; then
    echo "the device did not fetch the package"
    exit 1
fi

# What a complete install holds: the regular files and their total size, per folder.
check=$(python3 - "$dist/app.mpk" "$APP" <<'PY'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1]); app = sys.argv[2]
files = [i for i in z.infolist() if not i.filename.endswith("/")]
dirs = sorted(set(i.filename.rsplit("/", 1)[0] for i in files))
print("%d %d %s" % (len(files), sum(i.file_size for i in files),
                    ",".join(repr("apps/" + d) for d in dirs)))
PY
)
want=$(echo "$check" | cut -d' ' -f1,2)
dirs=$(echo "$check" | cut -d' ' -f3)
line="import os;f=[(p+'/'+e[0]) for p in [$dirs] for e in os.ilistdir(p) if e[1]==0x8000];print('MPK',len(f),sum(os.stat(x)[6] for x in f))"
i=0
while :; do
    sleep 3
    got=$($PY tools/repl_type.py "$PORT" "$line" 8 2>/dev/null | grep -a "^MPK " | tail -1 | cut -c5- | tr -d '\r')
    [ "$got" = "$want" ] && break
    i=$((i + 1))
    [ $i -ge 10 ] && { echo "install incomplete on the device: got '$got', want '$want'"; exit 1; }
done
echo "installed ($want)"
if [ "$1" = "--start" ]; then
    $PY tools/repl_type.py "$PORT" "from mpos import AppManager; AppManager.start_app('$APP')" 2 >/dev/null
    echo "started"
fi
