#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/native"

gcc -O2 -Wall -Wextra -shared -fPIC -pthread protonida_uffd.c -o libprotonida_uffd.so
gcc -O2 -Wall -Wextra test_uffd_bridge.c -L. -Wl,-rpath,'$ORIGIN' -lprotonida_uffd -o test_uffd_bridge
./test_uffd_bridge
rm -f test_uffd_bridge
echo "[OK] Built $ROOT/native/libprotonida_uffd.so"
