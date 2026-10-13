#!/bin/sh
# Run the desktop test suite (CPython). Exits non-zero on the first failing file.
cd "$(dirname "$0")/.." || exit 1
n=0
for t in tests/test_*.py; do
    PYTHONPATH=eu.axistem.micropymesh python3 "$t" > /dev/null 2>&1 || { echo "FAIL $t"; PYTHONPATH=eu.axistem.micropymesh python3 "$t" 2>&1 | tail -15; exit 1; }
    n=$((n + 1))
done
echo "$n test files passed"
