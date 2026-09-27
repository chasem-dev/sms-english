#!/bin/bash
# tools/unused-sizes.sh Dir/Unit
# Compare the sizes of a unit's UNUSED map functions (inlined everywhere, then
# stripped) with the bodies in our object. Prints `symbol map=SIZE ours=SIZE` for
# every mismatch; ours=4 (or empty) is a stub whose real body is still missing.
# Run from the repo root after a build.
set -e
u=$1; b=$(basename "$u")
nm=$(mktemp)
build/binutils/powerpc-eabi-nm -S "build/GMSE01/src/$u.o" > "$nm"
awk -v f="$b.cpp" '/\.text section layout/{t=1} /\.ctors section layout/{t=0} t && $0 ~ (" "f) && /UNUSED/{print $2, $4}' orig/GMSE01/files/marioUS.MAP |
while read -r sz sym; do
	o=$(awk -v s="$sym" '$4==s{print $2}' "$nm" | head -1)
	[ "$((16#$sz))" != "$((16#${o:-0}))" ] && echo "$sym map=$sz ours=$o"
done
rm -f "$nm"
