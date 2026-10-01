#!/usr/bin/env python3
"""Compare the data sections of our objects with retail's, byte for byte.

objdiff matches data objects by symbol name and ignores their order, so a unit
can score 100% data and still change the linked DOL. This compares the raw
section contents of build/GMSE01/src/<unit>.o against the split retail object
build/GMSE01/obj/<unit>.o (trailing zero padding stripped) and classifies each
section:

  same   identical bytes
  order  the same bytes in a different order (sorted bytes agree): usually an
         include-order fix, since headers such as Map/MapCollisionEntry.hpp,
         System/DummyStrings.hpp or M3DUtil/InfectiousStrings.hpp emit their
         literals where they are first included
  diff   different content
  ours/retail  the section exists on one side only

.rodata and .sdata2 are compared as raw bytes (the offset after "@" is the first
differing byte). .data and .sdata are compared as sequences of symbol contents
with weak symbols left out, since retail keeps each weak vtable in one unit only,
and with our unreferenced local objects left out, since the linker strips them
(the number after "@" is then a symbol index); relocated words read as zero on
both sides, so a pointer to the wrong target is not caught.

  tools/rodata-order.py                      every unlinked unit, summary table
  tools/rodata-order.py -u Enemy/smallEnemy  one unit, with a per-symbol listing
  tools/rodata-order.py --all                linked units too
  tools/rodata-order.py -s .rodata -s .sdata2

Run it from the repository root (or a worktree) after building the objects.
"""

import argparse
import json
import signal
import struct
import sys
from pathlib import Path

SECTIONS = [".rodata", ".sdata2", ".data", ".sdata"]
# Weak objects (template vtables, ...) live in whichever unit the linker kept them
# from, so these sections are compared symbol by symbol with weak ones left out.
SYMBOL_SECTIONS = {".data", ".sdata"}
VERSION = "GMSE01"


class Elf:
    def __init__(self, path):
        self.raw = raw = Path(path).read_bytes()
        if raw[:4] != b"\x7fELF" or raw[5] != 2:
            raise ValueError(f"{path}: not a big-endian ELF")
        shoff, = struct.unpack_from(">I", raw, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from(">HHH", raw, 0x2E)
        self.shdrs = [struct.unpack_from(">IIIIIIIIII", raw, shoff + i * shentsize) for i in range(shnum)]
        strtab = self.shdrs[shstrndx]
        self.names = [self._str(strtab, sh[0]) for sh in self.shdrs]

    def _str(self, sh, off):
        start = sh[4] + off
        return self.raw[start:self.raw.index(b"\0", start)].decode("latin-1")

    def section(self, name):
        for i, n in enumerate(self.names):
            if n == name:
                sh = self.shdrs[i]
                if sh[1] == 8:  # SHT_NOBITS
                    return i, bytes(sh[5])
                return i, self.raw[sh[4]:sh[4] + sh[5]]
        return None, None

    def symbols(self, shndx):
        out = []
        for sh in self.shdrs:
            if sh[1] != 2:  # SHT_SYMTAB
                continue
            strsh = self.shdrs[sh[6]]
            for k in range(sh[5] // 16):
                name, value, size, info, other, ndx = struct.unpack_from(">IIIBBH", self.raw, sh[4] + k * 16)
                if ndx == shndx and (info & 0xF) == 1:  # STT_OBJECT
                    out.append((value, size, self._str(strsh, name), info >> 4, k))
        return sorted(out)

    def referenced(self):
        """Indices of the symbols some relocation points at."""
        out = set()
        for sh in self.shdrs:
            if sh[1] == 4:  # SHT_RELA
                for k in range(sh[5] // 12):
                    out.add(struct.unpack_from(">I", self.raw, sh[4] + k * 12 + 4)[0] >> 8)
        return out


def strip(data):
    return data.rstrip(b"\0") if data is not None else None


def blobs(elf, name, skip, live=None):
    """The section as a list of its symbols' bytes, leaving out weak symbols,
    dtk gap fillers and (given `live`) local symbols nothing refers to."""
    shndx, data = elf.section(name)
    if shndx is None or not data:
        return None
    return [data[off:off + size] for off, size, nm, bind, idx in elf.symbols(shndx)
            if bind != 2 and nm not in skip and not nm.startswith("gap_")
            and (live is None or bind != 0 or idx in live)]


def classify_symbols(oe, re_, name):
    oi = oe.section(name)[0]
    weak = {sym[2] for sym in oe.symbols(oi) if sym[3] == 2} if oi is not None else set()
    # The linker drops unreferenced local objects (the pointer statics of the
    # rogue string headers, unused vector literals), so retail never has them.
    a, b = blobs(oe, name, weak, oe.referenced()), blobs(re_, name, weak)
    if not a and not b:
        return "-", None
    if not a:
        return "retail", None
    if not b:
        return "ours", None
    if a == b:
        return "same", None
    first = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
    if sorted(a) == sorted(b):
        return "order", first
    return "diff", first


def classify(ours, retail):
    ours = ours or None
    retail = retail or None
    if ours is None and retail is None:
        return "-", None
    if ours is None:
        return "retail", None
    if retail is None:
        return "ours", None
    a, b = strip(ours), strip(retail)
    if a == b:
        return "same", None
    first = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
    if sorted(a) == sorted(b):
        return "order", first
    return "diff", first


def compare(oe, re_, name):
    if name in SYMBOL_SECTIONS:
        return classify_symbols(oe, re_, name)
    return classify(oe.section(name)[1], re_.section(name)[1])


def units(root, everything):
    linked = set()
    objects_json = root / "config" / VERSION / "objects.json"
    if objects_json.exists():
        linked = {str(Path(p).with_suffix("")) for p in json.loads(objects_json.read_text())}
    retail_dir = root / "build" / VERSION / "obj"
    for path in sorted(retail_dir.rglob("*.o")):
        unit = str(path.relative_to(retail_dir).with_suffix(""))
        if everything or unit not in linked:
            yield unit


def show(unit, oe, re_, name):
    oi, od = oe.section(name)
    ri, rd = re_.section(name)
    print(f"{name}: ours {len(strip(od)) if od is not None else '-'} bytes, retail {len(strip(rd)) if rd is not None else '-'} bytes")
    osyms = oe.symbols(oi) if oi is not None else []
    rsyms = re_.symbols(ri) if ri is not None else []

    def fmt(sym, data):
        if sym is None:
            return "-"
        off, size, nm = sym[:3]
        blob = data[off:off + size]
        text = blob.rstrip(b"\0")
        if text and all(0x20 <= c < 0x7F or c >= 0x80 for c in text) and len(text) >= 2:
            body = repr(text.decode("shift_jis", "replace")[:28])
        else:
            body = blob[:12].hex()
        return f"{off:5x} {size:4x} {nm[:22]:22s} {body}"

    for i in range(max(len(osyms), len(rsyms))):
        x = osyms[i] if i < len(osyms) else None
        y = rsyms[i] if i < len(rsyms) else None
        same = x and y and od[x[0]:x[0] + x[1]] == rd[y[0]:y[0] + y[1]]
        print(("  " if same else "* ") + fmt(x, od).ljust(72) + " | " + fmt(y, rd))


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-u", "--unit", action="append", help="unit path without extension, e.g. Enemy/smallEnemy (repeatable)")
    ap.add_argument("-s", "--section", action="append", help=f"section to compare (repeatable; default {' '.join(SECTIONS)})")
    ap.add_argument("--all", action="store_true", help="include units already linked (in objects.json)")
    ap.add_argument("-q", "--quiet", action="store_true", help="list only units with an order or diff section")
    ap.add_argument("--root", default=".", help="repository root or worktree (default .)")
    args = ap.parse_args()
    root = Path(args.root)
    sections = args.section or SECTIONS
    build = root / "build" / VERSION
    todo = [u.removeprefix("mario/") for u in args.unit] if args.unit else list(units(root, args.all))

    counts = {s: {} for s in sections}
    print(f"{'unit':44s} " + " ".join(f"{s:>14s}" for s in sections))
    for unit in todo:
        ours_path, retail_path = build / "src" / f"{unit}.o", build / "obj" / f"{unit}.o"
        if not retail_path.exists():
            print(f"{unit}: no retail object at {retail_path}", file=sys.stderr)
            continue
        if not ours_path.exists():
            print(f"{unit:44s} (not built)")
            continue
        oe, re_ = Elf(ours_path), Elf(retail_path)
        cells, interesting = [], False
        for s in sections:
            kind, first = compare(oe, re_, s)
            counts[s][kind] = counts[s].get(kind, 0) + 1
            interesting |= kind not in ("same", "-")
            cells.append(kind if first is None else f"{kind}@{first:x}")
        if interesting or not args.quiet:
            print(f"{unit:44s} " + " ".join(f"{c:>14s}" for c in cells))
        if args.unit:
            for s in sections:
                if compare(oe, re_, s)[0] not in ("same", "-"):
                    show(unit, oe, re_, s)
    if not args.unit:
        print()
        for s in sections:
            print(f"{s:8s} " + ", ".join(f"{k} {v}" for k, v in sorted(counts[s].items())))


if __name__ == "__main__":
    main()
