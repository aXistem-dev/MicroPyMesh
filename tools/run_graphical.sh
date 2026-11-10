#!/bin/sh
# Run the graphical tests (tests/graphical/test_graphical_*.py) on the MicroPythonOS desktop
# build at the Indicator's 480x480, or only the ones named on the command line.
#   MPOS_DIR=/path/to/MicroPythonOS tools/run_graphical.sh [test_graphical_shell.py ...]
# MPOS_DIR must hold a unix build (lvgl_micropython/build). The app is symlinked into its
# apps/ and the tests are copied into its tests/ for the run; both stay out of its git status.
set -e
: "${MPOS_DIR:?set MPOS_DIR to a MicroPythonOS checkout with a unix build}"
here=$(cd "$(dirname "$0")/.." && pwd)
APP=eu.axistem.micropymesh

ln -sfn "$here/$APP" "$MPOS_DIR/internal_filesystem/apps/$APP"
exclude="$MPOS_DIR/.git/info/exclude"
if [ -f "$exclude" ]; then
    grep -qx "internal_filesystem/apps/$APP" "$exclude" || echo "internal_filesystem/apps/$APP" >> "$exclude"
    grep -qx "tests/_mc_*.py" "$exclude" || echo "tests/_mc_*.py" >> "$exclude"
fi

if [ $# -gt 0 ]; then
    names="$*"
else
    names=$(cd "$here/tests/graphical" && ls test_graphical_*.py)
fi
copies=""
for n in $names; do
    cp "$here/tests/graphical/$n" "$MPOS_DIR/tests/_mc_$n"
    copies="$copies tests/_mc_$n"
done
cp "$here/tests/graphical/mc_fixtures.py" "$MPOS_DIR/internal_filesystem/lib/mc_fixtures.py"
trap 'cd "$MPOS_DIR" && rm -f $copies internal_filesystem/lib/mc_fixtures.py' EXIT

cd "$MPOS_DIR"
MPOS_DISPLAY=480x480 python3 scripts/test_runner.py --timeout 180 $copies
