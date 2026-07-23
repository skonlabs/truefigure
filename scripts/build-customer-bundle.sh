#!/usr/bin/env bash
# =============================================================================
# build-customer-bundle.sh — assemble the ONLY artifacts a customer receives.
#
# Produces dist/truefigure-customer-bundle-<version>/ (and a .tar.gz):
#   * the truefigure-sdk wheel (built from sdk/)
#   * the public API contract (openapi.yaml, events.schema.json, error-codes.json)
#   * the SDK README + INTEGRATION.md
#
# It NEVER includes server code (src/truefigure_server), the ops CLI, the schema,
# or tests. A guard fails the build if anything server-side sneaks in.
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

red() { printf '\033[31m%s\033[0m\n' "$1"; }
grn() { printf '\033[32m%s\033[0m\n' "$1"; }

VERSION="$(grep -E '^version' sdk/pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
OUT="dist/truefigure-customer-bundle-${VERSION}"
rm -rf dist/wheel "$OUT" "${OUT}.tar.gz"
mkdir -p dist/wheel "$OUT/contract"

echo "== building the SDK wheel =="
( cd sdk && python3 -m pip wheel . --no-deps -w "$ROOT/dist/wheel" >/dev/null )
cp dist/wheel/truefigure_sdk-*.whl "$OUT/"

echo "== copying the public contract + docs =="
cp contract/openapi.yaml contract/events.schema.json contract/error-codes.json "$OUT/contract/"
cp sdk/README.md sdk/INTEGRATION.md "$OUT/"

# ---- guard: nothing server-side may appear in the bundle -------------------
echo "== guard: no server code in the bundle =="
LEAK=0
# no server package / engine / ops references in any bundle file
if grep -RIlE 'truefigure_server|src/truefigure_server|opsctl|_write_figure|compute_deployment' "$OUT" 2>/dev/null; then
  red "server references found in the bundle (see above)"; LEAK=1
fi
# the wheel must contain ONLY the client package
if python3 - "$OUT" <<'PY'
import glob, sys, zipfile
out = sys.argv[1]
whl = glob.glob(f"{out}/truefigure_sdk-*.whl")[0]
names = zipfile.ZipFile(whl).namelist()
bad = [n for n in names if n.startswith(("truefigure_server", "src", "ops", "tests", "supabase"))]
sys.exit(1 if bad else 0)
PY
then :; else red "wheel contains non-client files"; LEAK=1; fi
[ "$LEAK" = 0 ] || { red "BUNDLE GUARD FAILED"; exit 1; }
grn "guard clean — client SDK + public contract + docs only"

echo "== packaging =="
tar -czf "${OUT}.tar.gz" -C dist "truefigure-customer-bundle-${VERSION}"
echo
grn "customer bundle ready:"
echo "  ${OUT}.tar.gz"
echo "  contents:"; ( cd "$OUT" && find . -type f | sed 's#^#    #' )
