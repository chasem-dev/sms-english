#!/usr/bin/env python3
"""Search raw-member respellings of an accessor's call sites in one function.

Usage (from the repo root or a worktree):
    python3 tools/accessor-sitefix.py UNIT SYMBOL NAME MEMBER [--max-sites N]

UNIT is e.g. `System/CardManager`, SYMBOL the mangled function, NAME the
accessor (`getState`) and MEMBER the member it returns (`mState`).  Every
call `recv.NAME()`, `recv->NAME()` or bare `NAME()` in the function's body is
a site; each subset of sites is respelled as the raw member (`recv.MEMBER`,
`recv->MEMBER`, `MEMBER`), the unit object rebuilt and classified the way
tools/mwcc-stack/census.py does.  Prints, per subset, the function's status
and frame and whether any other function of the unit moved; restores the
source at the end.

Used after a header accessor changes shape (research c-r30): a function that
dropped because it read the member raw in retail is closed by the subset
that is exact with nothing else in the unit moving.
"""
import importlib.util
import itertools
import os
import re
import subprocess
import sys

ROOT = os.getcwd()
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "accand", os.path.join(HERE, "accessor-candidates.py"))
accand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accand)

OD = os.path.join(ROOT, "build", "binutils", "powerpc-eabi-objdump")


def funcs(path):
    out = subprocess.run([OD, "-d", "--no-show-raw-insn", path],
                         capture_output=True, text=True).stdout
    res, cur = {}, None
    for l in out.split("\n"):
        m = re.match(r"^[0-9a-f]+ <(.*)>:$", l)
        if m:
            cur = m.group(1)
            res[cur] = []
            continue
        if cur and "\t" in l:
            ins = l.split("\t", 1)[1].strip()
            ins = re.sub(r"\s+[0-9a-f]+ <.*>$", "", ins)
            ins = re.sub(r"<.*>", "", ins)
            res[cur].append(ins)
    return res


def norm(ins):
    return re.sub(r"-?\d+\(r1\)", "X(r1)", re.sub(r"(r1,)-?\d+$", r"\1X", ins))


def frame(lst):
    for i in lst[:8]:
        m = re.match(r"stwu\s+r1,-(\d+)\(r1\)", i)
        if m:
            return int(m.group(1))
    return 0


def classify(unit):
    a = funcs(os.path.join(ROOT, "build", "GMSE01", "obj", unit + ".o"))
    b = funcs(os.path.join(ROOT, "build", "GMSE01", "src", unit + ".o"))
    rows = {}
    for s, ai in a.items():
        bi = b.get(s)
        if bi is None:
            continue
        if ai == bi:
            st = "exact"
        elif len(ai) == len(bi) and all(norm(x) == norm(y) for x, y in zip(ai, bi)):
            st = "frame" if frame(ai) != frame(bi) else "slots"
        else:
            st = "other"
        rows[s] = (st, frame(ai), frame(bi), bi)
    return rows


def main():
    args = sys.argv[1:]
    cap = 6
    if "--max-sites" in args:
        i = args.index("--max-sites")
        cap = int(args[i + 1])
        del args[i:i + 2]
    unit, sym, name, member = args
    src = os.path.join(ROOT, "src", unit + ".cpp")
    orig = open(src).read()
    cls, meth = accand.parse_sym(sym)
    if cls:
        head = re.compile(r"\b%s\s*::\s*%s\s*\(" % (re.escape(cls), re.escape(meth)))
    else:
        head = re.compile(r"^[^\n;]*\b%s\s*\(" % re.escape(meth), re.M)
    m = head.search(orig)
    start = orig.index("{", m.end())
    depth = 0
    for end in range(start, len(orig)):
        depth += {"{": 1, "}": -1}.get(orig[end], 0)
        if depth == 0:
            break
    body = orig[start:end + 1]
    site = re.compile(r"(\.|->)?\s*\b%s\s*\(\s*\)" % re.escape(name))
    sites = list(site.finditer(body))
    print("%d sites" % len(sites))
    if len(sites) > cap:
        sys.exit("too many sites (--max-sites)")
    ninja = os.path.join(ROOT, "build", "venv", "bin", "ninja")
    obj = "build/GMSE01/src/%s.o" % unit
    base = None
    try:
        for bits in itertools.product((0, 1), repeat=len(sites)):
            nb, last = [], 0
            for b, s in zip(bits, sites):
                nb.append(body[last:s.start()])
                nb.append((s.group(1) or "") + member if b else s.group(0))
                last = s.end()
            nb.append(body[last:])
            open(src, "w").write(orig[:start] + "".join(nb) + orig[end + 1:])
            r = subprocess.run([ninja, obj], cwd=ROOT, capture_output=True, text=True)
            if r.returncode:
                print("".join(map(str, bits)), "BUILD FAILED")
                continue
            rows = classify(unit)
            if base is None:
                base = rows
            moved = sorted(k for k in rows if k != sym and rows[k][3] != base[k][3])
            st, fa, fb, _ = rows.get(sym, ("?", 0, 0, None))
            print("%s\t%s\t%d/%d\tothers moved: %s" % (
                "".join(map(str, bits)), st, fb, fa, " ".join(moved) or "-"))
    finally:
        open(src, "w").write(orig)
        subprocess.run([ninja, obj], cwd=ROOT, capture_output=True)


if __name__ == "__main__":
    main()
