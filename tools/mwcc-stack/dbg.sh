#!/bin/bash
# tools/mwcc-stack/dbg.sh SRC MANGLED_SYM OUTDIR [compiler version, default 1.1]
# Dump MWCC's own stack objects for one function (see README.md). Run from the repo root.
# Needs MWCC_DEBUGGER (path to mwcc_debugger.py) and RETROWIN32 (the gdb-stub build).
# OVL=<dir> puts a header overlay directory ahead of include/.
set -e
: "${MWCC_DEBUGGER:?set MWCC_DEBUGGER to mwcc_debugger.py}" "${RETROWIN32:?set RETROWIN32 to target/lto/retrowin32}"
V=${4:-1.1}
FL='-nodefaults -align powerpc -enum int -fp hardware -Cpp_exceptions off -pragma "cats off" -pragma "warn_notinlined off" -maxerrors 1 -nosyspath -RTTI off -str reuse -multibyte -cwd source XOVL -i include -i include/PowerPC_EABI_Support/Msl/MSL_C/MSL_Common -i include/PowerPC_EABI_Support/Msl/MSL_C++/MSL_Common -i build/GMSE01/include -DBUILD_VERSION=2 -DVERSION_GMSE01 -proc gekko -DGEKKO -DNDEBUG=1 -O4,p -inline auto -fp_contract on -str reuse,readonly -opt all,nostrength -inline deferred -lang=c++'
FL="${FL/XOVL/${OVL:+-i $OVL}}"
OBJ=$(mktemp -d)
mkdir -p "$3"
timeout 900 python3 "$MWCC_DEBUGGER" -e "$RETROWIN32" -a "build/compilers/GC/$V/mwcceppc.exe $FL -c $1 -o $OBJ/" "$2" "$3" > "$3.log" 2>&1 || true
rm -rf "$OBJ"
tail -3 "$3.log" | grep -v '^$' || true
