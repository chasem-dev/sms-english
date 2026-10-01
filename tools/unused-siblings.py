#!/usr/bin/env python3
"""Find verified sibling bodies for UNUSED map functions that are stubs or mis-sized.

For every UNUSED `.text` entry in the map whose body in our object is missing or
has the wrong size, list the other map functions with the same method name, the
same signature (the mangled part after the class) and the same size whose body
in our tree is already verified: scored exact in report.json, or itself UNUSED
and already at its map size. Such a sibling (TKoopaJr::setAnimationIndex for
TLimitKoopa's, TGuide::changePattern for TCardLoad's) is strong evidence for the
stub's body; the class layouts still have to justify adapting it.

  python3 tools/unused-siblings.py [-u Dir/unit ...] [--loose] [--fuzzy] [--all]

--loose also lists same-name same-size siblings with another signature, marked `~`;
--fuzzy also lists same-size siblings whose method name shares the first and last
camel-case words (startKoopaJrMessage ~ startTinKoopaMessage), marked `%`;
--all also lists siblings that are not verified, marked `?`.
Run from the repository root after a full build (reads build/GMSE01/report.json
and the unit objects). Constructors, destructors and nerve `execute`s match
across unrelated classes by size alone, so read those hits with suspicion; an
UNUSED sibling only counts as verified when its own size matches the map.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

MAP = "orig/GMSE01/files/marioUS.MAP"
NM = "build/binutils/powerpc-eabi-nm"
REPORT = "build/GMSE01/report.json"


def parse_map():
    out = []
    inside = False
    with open(MAP, errors="replace") as f:
        for line in f:
            if ".text section layout" in line:
                inside = True
                continue
            if ".ctors section layout" in line:
                break
            if not inside:
                continue
            p = line.split()
            if len(p) >= 5 and p[0] == "UNUSED":
                out.append(dict(unused=True, size=int(p[1], 16), sym=p[3], lib=p[4],
                                file=p[5] if len(p) > 5 else p[4]))
            elif len(p) >= 6 and re.fullmatch(r"[0-9a-f]{8}", p[0]) and p[3] != "1":
                out.append(dict(unused=False, size=int(p[1], 16), sym=p[4], lib=p[5],
                                file=p[6] if len(p) > 6 else p[5]))
    return out


def split_mangled(sym):
    """Return (method, signature) for `name__<class>F...`, or None."""
    i = sym.find("__", 2 if sym.startswith("__") else 1)
    if i < 0:
        return None
    name, rest = sym[:i], sym[i + 2:]
    # skip the class qualifier
    j = 0
    if rest.startswith("Q") and len(rest) > 1 and rest[1].isdigit():
        n = int(rest[1]); j = 2
        for _ in range(n):
            m = re.match(r"\d+", rest[j:])
            if not m:
                return None
            j += len(m.group()) + int(m.group())
    else:
        m = re.match(r"\d+", rest)
        if not m:
            return None
        j = len(m.group()) + int(m.group())
    return name, rest[j:]


def words(name):
    return re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-u", "--unit", action="append", default=[])
    ap.add_argument("--loose", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--fuzzy", action="store_true")
    a = ap.parse_args()

    rep = json.load(open(REPORT))
    by_base = defaultdict(list)
    exact = set()
    for u in rep["units"]:
        sp = u.get("metadata", {}).get("source_path")
        if not sp:
            continue
        by_base[os.path.basename(sp)].append((u["name"], sp))
        for fn in u.get("functions", []):
            if fn.get("fuzzy_match_percent") == 100.0:
                exact.add((u["name"], fn["name"]))

    entries = parse_map()

    def unit_of(e):
        c = by_base.get(e["file"], [])
        if len(c) > 1:
            lib = e["lib"].replace(".a", "")
            c = [x for x in c if "/" + lib + "/" in "/" + x[1]] or c
        return c[0] if c else None

    sizes_cache = {}

    def our_sizes(unit):
        if unit in sizes_cache:
            return sizes_cache[unit]
        obj = "build/GMSE01/" + os.path.splitext(unit[1])[0] + ".o"
        d = {}
        if os.path.exists(obj):
            r = subprocess.run([NM, "-S", obj], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                p = line.split()
                if len(p) == 4 and p[2] in "TtWw":
                    d[p[3]] = int(p[1], 16)
        sizes_cache[unit] = d
        return d

    want = {("mario/" + x if not x.startswith("mario/") else x) for x in a.unit}

    by_key = defaultdict(list)
    by_name = defaultdict(list)
    by_size = defaultdict(list)
    for e in entries:
        s = split_mangled(e["sym"])
        if not s:
            continue
        e["key"] = s
        by_key[(s, e["size"])].append(e)
        by_name[(s[0], e["size"])].append(e)
        by_size[e["size"]].append(e)

    def verified(e):
        u = unit_of(e)
        if not u:
            return False
        if e["unused"]:
            return our_sizes(u).get(e["sym"]) == e["size"]
        return (u[0], e["sym"]) in exact

    for e in entries:
        if not e["unused"] or "key" not in e:
            continue
        u = unit_of(e)
        if not u or (want and u[0] not in want):
            continue
        ours = our_sizes(u).get(e["sym"])
        if ours == e["size"]:
            continue
        hits = []
        for s in by_key[(e["key"], e["size"])]:
            if s is e:
                continue
            v = verified(s)
            if v or a.all:
                hits.append(("=" if v else "?", s))
        if a.loose:
            for s in by_name[(e["key"][0], e["size"])]:
                if s is e or s["key"] == e["key"]:
                    continue
                v = verified(s)
                if v or a.all:
                    hits.append(("~", s))
        if a.fuzzy:
            w = words(e["key"][0])
            for s in by_size[e["size"]]:
                if s is e or s["key"][0] == e["key"][0]:
                    continue
                w2 = words(s["key"][0])
                if len(w) > 1 and len(w2) > 1 and w[0] == w2[0] and w[-1] == w2[-1] and s["key"][1] == e["key"][1]:
                    v = verified(s)
                    if v or a.all:
                        hits.append(("%", s))
        if hits:
            print(f"{u[0]} {e['sym']} map=0x{e['size']:x} ours={'0x%x' % ours if ours else '-'}")
            for m, s in hits:
                su = unit_of(s)
                tag = "UNUSED" if s["unused"] else "scored"
                print(f"  {m} {s['sym']} [{su[0] if su else s['file']}] {tag}")


if __name__ == "__main__":
    sys.exit(main())
