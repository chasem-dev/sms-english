#!/usr/bin/env python3
"""Compare the data sections of our objects with retail's, before linking.

objdiff matches data objects by symbol name and ignores their order, so a unit
can score 100% data and still change the linked DOL. This compares
build/GMSE01/src/<unit>.o against the split retail object build/GMSE01/obj/<unit>.o
section by section and classifies each one:

  same    identical
  order   the same contents in a different order: in .rodata usually an
          include-order fix, since Map/MapCollisionEntry.hpp (setUpTrans's
          zero and one vectors), System/DummyStrings.hpp,
          M3DUtil/InfectiousStrings.hpp and Player/MarioDirtyStrings.hpp emit
          their literals where they are first included; in .sdata2 the order
          in which code first requests each float constant
  diff    different contents
  ours / retail   the section has live contents on one side only

The number after "@" is the first differing byte offset (.rodata) or the index
of the first differing object (the other sections).

The linker strips each unreferenced data object on its own (the map's UNUSED
rows), so sections are compared as sequences of live objects. The exception is
a .rodata block that live code or data addresses through its `...rodata.0`
label (the map's `...rodata.0 (entry of .rodata)` row): the linker keeps it whole, and it is
compared as raw bytes with trailing zero padding stripped. Our
side keeps only the objects retail could keep: those the map places in this
unit, and those reachable through relocations from them. So a float that only
an UNUSED helper or a discarded weak copy (JGeometry::TUtil<f>::sqrt's 0.5f
and 3.0f, say) refers to does not count, and a weak vtable the map gives to
another unit does not either. Relocated words read as zero on both sides, so a
pointer to the wrong target is not caught.

  tools/rodata-order.py                      every unlinked unit, summary table
  tools/rodata-order.py -u Enemy/smallEnemy  one unit, with a per-object listing
  tools/rodata-order.py --all                linked units too
  tools/rodata-order.py -s .rodata -s .sdata2

Run it from the repository root (or a worktree) after building the objects.
"""

import argparse
import json
import re
import signal
import struct
import sys
from collections import defaultdict
from pathlib import Path

SECTIONS = [".rodata", ".sdata2", ".data", ".sdata"]
VERSION = "GMSE01"
MAP = Path("orig") / VERSION / "files" / "marioUS.MAP"


class Elf:
    def __init__(self, path):
        self.raw = raw = Path(path).read_bytes()
        if raw[:4] != b"\x7fELF" or raw[5] != 2:
            raise ValueError(f"{path}: not a big-endian ELF")
        shoff, = struct.unpack_from(">I", raw, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from(">HHH", raw, 0x2E)
        self.shdrs = [struct.unpack_from(">IIIIIIIIII", raw, shoff + i * shentsize) for i in range(shnum)]
        self.names = [self._str(self.shdrs[shstrndx], sh[0]) for sh in self.shdrs]
        # (index, name, value, size, type, bind, shndx) of every symbol
        self.syms = []
        for sh in self.shdrs:
            if sh[1] == 2:  # SHT_SYMTAB
                strsh = self.shdrs[sh[6]]
                for k in range(sh[5] // 16):
                    name, value, size, info, _, ndx = struct.unpack_from(">IIIBBH", raw, sh[4] + k * 16)
                    self.syms.append((k, self._str(strsh, name), value, size, info & 0xF, info >> 4, ndx))

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

    def objects(self, shndx):
        """(offset, size, name, bind, index) of the data objects in a section."""
        return sorted((v, s, n, b, k) for k, n, v, s, t, b, x in self.syms if x == shndx and t == 1)

    def live(self, placed):
        """Symbol indices the linker could keep: those whose names the map
        places in this unit, and everything relocations reach from them
        without passing through a weak symbol the map places elsewhere."""
        by_section = defaultdict(list)
        for k, n, v, s, t, b, x in self.syms:
            if t in (1, 2) and s:  # objects and functions
                by_section[x].append((v, s, k))
        edges = defaultdict(set)
        for sh in self.shdrs:
            if sh[1] != 4:  # SHT_RELA
                continue
            spans = by_section.get(sh[7], [])
            for r in range(sh[5] // 12):
                off, info = struct.unpack_from(">II", self.raw, sh[4] + r * 12)
                owner = next((k for v, s, k in spans if v <= off < v + s), None)
                edges[owner].add(info >> 8)
        # A weak symbol the map gives to another unit is that unit's copy; ours
        # is discarded along with whatever only it refers to.
        foreign = {k for k, n, v, s, t, b, x in self.syms if b == 2 and n not in placed}
        todo = [k for k, n, v, s, t, b, x in self.syms if n in placed] + list(edges[None])
        seen = set()
        while todo:
            k = todo.pop()
            if k not in seen and k not in foreign:
                seen.add(k)
                todo.extend(edges[k])
        return seen, foreign


def read_map(path):
    """{source file stem: set of names the map places (not UNUSED) for it}."""
    placed = defaultdict(set)
    if not path.exists():
        return placed
    row = re.compile(r"^\s+[0-9a-f]{8} [0-9a-f]{6} [0-9a-f]{8}\s+\d+ (\S+)\s+(?:\S+\.a )?(\S+)\.(?:cpp|cp|c|s)\s*$")
    in_layout = False
    for line in path.read_text(encoding="latin-1").splitlines():
        if line.endswith("section layout"):
            in_layout = True
            continue
        if in_layout:
            m = row.match(line)
            if m:
                placed[m.group(2)].add(m.group(1))
    return placed


def strip(data):
    return data.rstrip(b"\0") if data is not None else None


def first_difference(a, b):
    return next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))


def object_list(elf, name, keep):
    shndx, data = elf.section(name)
    if shndx is None or not data:
        return []
    return [(nm, data[off:off + size]) for off, size, nm, bind, idx in elf.objects(shndx)
            if not nm.startswith("gap_") and keep(idx)]


def pooled(elf, name, live):
    """Whether the linker keeps the section whole: live code or data addresses
    it through its `...rodata.0`/`...data.0` label or the section symbol."""
    shndx = elf.section(name)[0]
    return any(t in (0, 3) and x == shndx and k in live[0] for k, n, v, s, t, b, x in elf.syms)


def keeper(elf, name, live):
    """Which of our objects in the section survive the link, or None to
    compare the section as raw bytes."""
    if live is None:
        return None if name == ".rodata" else (lambda idx: True)
    seen, foreign = live
    if pooled(elf, name, live):
        return None if name == ".rodata" else (lambda idx: idx not in foreign)
    return lambda idx: idx in seen


def compare(oe, re_, name, live):
    keep = keeper(oe, name, live)
    if keep is None:
        a, b = strip(oe.section(name)[1]) or None, strip(re_.section(name)[1]) or None
    else:
        a = [blob for _, blob in object_list(oe, name, keep)] or None
        b = [blob for _, blob in object_list(re_, name, lambda idx: True)] or None
    if a is None and b is None:
        return "-", None
    if a is None or b is None:
        return ("retail" if a is None else "ours"), None
    if a == b:
        return "same", None
    kind = "order" if sorted(a) == sorted(b) else "diff"
    return kind, first_difference(a, b)


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


def describe(name, size, blob):
    text = blob.rstrip(b"\0")
    if size not in (4, 8) and len(text) >= 2 and all(0x20 <= c < 0x7F or c >= 0x80 for c in text):
        return repr(text.decode("shift_jis", "replace")[:28])
    if size == 4:
        return f"{blob.hex()} {struct.unpack('>f', blob)[0]:.7g}f"
    if size == 8 and blob[:4] != b"\x43\x30\x00\x00":
        return f"{blob.hex()} {struct.unpack('>d', blob)[0]:.10g}"
    return blob[:12].hex()


def show(oe, re_, name, live):
    keep = keeper(oe, name, live)
    if keep is not None:
        a = object_list(oe, name, keep)
        b = object_list(re_, name, lambda idx: True)
        print(f"{name}: {len(a)} live objects ours, {len(b)} retail")
    else:
        oi, od = oe.section(name)
        ri, rd = re_.section(name)
        a = [(nm, od[off:off + size]) for off, size, nm, _, _ in oe.objects(oi)] if oi is not None else []
        b = [(nm, rd[off:off + size]) for off, size, nm, _, _ in re_.objects(ri)] if ri is not None else []
        print(f"{name}: ours {len(strip(od)) if od is not None else '-'} bytes, "
              f"retail {len(strip(rd)) if rd is not None else '-'} bytes")

    def fmt(entry):
        if entry is None:
            return "-"
        nm, blob = entry
        return f"{len(blob):4x} {nm[:24]:24s} {describe(nm, len(blob), blob)}"

    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        same = x and y and x[1] == y[1]
        print(("  " if same else "* ") + fmt(x).ljust(66) + " | " + fmt(y))


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-u", "--unit", action="append", help="unit path without extension, e.g. Enemy/smallEnemy (repeatable)")
    ap.add_argument("-s", "--section", action="append", help=f"section to compare (repeatable; default {' '.join(SECTIONS)})")
    ap.add_argument("--all", action="store_true", help="include units already linked (in objects.json)")
    ap.add_argument("-q", "--quiet", action="store_true", help="list only units with a section that is not same")
    ap.add_argument("--root", default=".", help="repository root or worktree (default .)")
    args = ap.parse_args()
    root = Path(args.root)
    sections = args.section or SECTIONS
    build = root / "build" / VERSION
    placed = read_map(root / MAP)
    todo = [u.removeprefix("mario/") for u in args.unit] if args.unit else list(units(root, args.all))

    counts = {s: defaultdict(int) for s in sections}
    print(f"{'unit':44s} " + " ".join(f"{s:>12s}" for s in sections))
    for unit in todo:
        ours_path, retail_path = build / "src" / f"{unit}.o", build / "obj" / f"{unit}.o"
        if not retail_path.exists():
            print(f"{unit}: no retail object at {retail_path}", file=sys.stderr)
            continue
        if not ours_path.exists():
            print(f"{unit:44s} (not built)")
            continue
        oe, re_ = Elf(ours_path), Elf(retail_path)
        names = placed.get(Path(unit).name)
        live = oe.live(names) if names else None
        results = [compare(oe, re_, s, live) for s in sections]
        for s, (kind, _) in zip(sections, results):
            counts[s][kind] += 1
        if not args.quiet or any(kind not in ("same", "-") for kind, _ in results):
            cells = [kind if first is None else f"{kind}@{first:x}" for kind, first in results]
            print(f"{unit:44s} " + " ".join(f"{c:>12s}" for c in cells))
        if args.unit:
            for s, (kind, _) in zip(sections, results):
                if kind not in ("same", "-"):
                    show(oe, re_, s, live)
    if not args.unit:
        print()
        for s in sections:
            print(f"{s:8s} " + ", ".join(f"{k} {v}" for k, v in sorted(counts[s].items())))


if __name__ == "__main__":
    main()
