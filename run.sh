#!/usr/bin/env bash
# PixelDeck on Linux and macOS: the same as  python3 pixeldeck.py  (see it for options).
#   ./run.sh           start (if needed) and open
#   ./run.sh --stop    stop the server
#   ./run.sh --status  print whether it runs
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$DIR/pixeldeck.py" "$@"
