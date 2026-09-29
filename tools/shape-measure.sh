#!/bin/bash
# Measure a header-shape edit tree-wide against a census baseline.
#
# usage: tools/shape-measure.sh BASE.tsv OUTDIR LABEL [PROBE_UNIT ...]
#
# Run from the repo root (or a worktree) with the edit already applied.
# BASE.tsv is a tools/mwcc-stack/census.py run of the unedited tree.
#
# 1. Probe (optional): build only PROBE_UNIT objects (e.g. "Enemy/Gesso"),
#    census, and stop with REJECT if any function moved down.  With
#    PROBE_ONLY=1 in the environment, stop after the probe either way.  Use the units
#    of exact callers of the edited accessor for a cheap early rejection.
# 2. Full: `ninja -j2 -k 0`, DOL SHA-1, `ninja changes_all`, census and
#    cmpcensus against BASE.tsv, validate-symbol-order for every unit whose
#    census rows changed.
#
# Writes OUTDIR/LABEL.{build,changes,cmp,vso}.txt and OUTDIR/LABEL.tsv, and
# appends one summary line to OUTDIR/summary.txt:
#   LABEL  exact A -> B  up U down D  dol ok|BAD  build rc  regressions R  vso ok|FAIL
# Exit status: 0 = zero drops and DOL ok, 3 = rejected.
set -u
BASE=$1; OUT=$2; LABEL=$3; shift 3
NINJA=build/venv/bin/ninja
SHA=a6782903ef79d4196c8489ecb1b57decb5b3728f
mkdir -p "$OUT"
P="$OUT/$LABEL"

if [ $# -gt 0 ]; then
    targets=()
    for u in "$@"; do targets+=("build/GMSE01/src/$u.o"); done
    $NINJA -j2 -k 0 "${targets[@]}" > "$P.probe-build.txt" 2>&1
    python3 tools/mwcc-stack/census.py "$P.probe.tsv" > /dev/null
    python3 tools/mwcc-stack/cmpcensus.py "$BASE" "$P.probe.tsv" 40 > "$P.probe-cmp.txt"
    down=$(sed -n 's/^up [0-9]* down \([0-9]*\)$/\1/p' "$P.probe-cmp.txt")
    if [ "${down:-1}" != 0 ]; then
        echo -e "$LABEL\tPROBE REJECT\t$(sed -n 2p "$P.probe-cmp.txt")" | tee -a "$OUT/summary.txt"
        exit 3
    fi
    if [ "${PROBE_ONLY:-0}" = 1 ]; then
        echo -e "$LABEL\tPROBE\t$(sed -n 2p "$P.probe-cmp.txt")" | tee -a "$OUT/summary.txt"
        exit 0
    fi
fi

$NINJA -j2 -k 0 > "$P.build.txt" 2>&1
brc=$?
dol=BAD
[ "$(sha1sum build/GMSE01/mario.dol | cut -d' ' -f1)" = "$SHA" ] && dol=ok
$NINJA changes_all > "$P.changes.txt" 2>&1
python3 tools/mwcc-stack/census.py "$P.tsv" > /dev/null
python3 tools/mwcc-stack/cmpcensus.py "$BASE" "$P.tsv" 60 > "$P.cmp.txt"
nreg=$(python3 - build/GMSE01/report_changes.json "$P.regress.txt" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
out = []
for u in d.get("units", []):
    for f in u.get("functions", []):
        a = f.get("from", {}).get("fuzzy_match_percent", 0.0)
        b = f.get("to", {}).get("fuzzy_match_percent", 0.0)
        if b < a:
            out.append("%s\t%s\t%.2f -> %.2f" % (u["name"], f["name"], a, b))
open(sys.argv[2], "w").write("\n".join(out) + "\n")
print(len(out))
EOF
)
vso=ok
: > "$P.vso.txt"
for u in $(python3 - "$BASE" "$P.tsv" <<'EOF'
import sys
def load(p):
    return {tuple(l.split('\t')[:2]): l for l in open(p)}
a, b = load(sys.argv[1]), load(sys.argv[2])
print('\n'.join(sorted({k[0][:-2] for k in set(a) | set(b) if a.get(k) != b.get(k)})))
EOF
); do
    r=$(NM=build/binutils/powerpc-eabi-nm build/venv/bin/python3 tools/validate-symbol-order.py \
        -u "mario/$u" --map orig/GMSE01/files/marioUS.MAP 2>&1 | grep "^RESULT")
    echo -e "$u\t$r" >> "$P.vso.txt"
    case "$r" in *FAIL*) vso=FAIL ;; esac
done
ex=$(sed -n 1p "$P.cmp.txt")
ud=$(sed -n 2p "$P.cmp.txt")
echo -e "$LABEL\t$ex\t$ud\tdol $dol\tbuild $brc\tchanges-regress $nreg\tvso $vso" | tee -a "$OUT/summary.txt"
down=$(echo "$ud" | sed -n 's/^up [0-9]* down \([0-9]*\)$/\1/p')
if [ "$dol" = ok ] && [ "$brc" = 0 ] && [ "${down:-1}" = 0 ] && [ "$nreg" = 0 ]; then
    exit 0
fi
exit 3
