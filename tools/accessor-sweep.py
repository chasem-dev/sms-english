#!/usr/bin/env python3
"""Probe header accessor shapes one at a time on the units that call them.

Usage (from the repo root or a worktree, clean tree, after a full build):
    python3 tools/accessor-sweep.py [--max N] BASE.tsv OUTDIR SPEC [SPEC ...]

BASE.tsv is a tools/mwcc-stack/census.py run of the unedited tree.
Each SPEC is `FILE:LINE[:SHAPE]`, naming a one-line accessor as
tools/accessor-candidates.py lists it (header path and line number):

    SHAPE binder (default)  `T get() { return m; }` -> `{ T x = m; return x; }`
    SHAPE bool              `bool is() { return e; }` -> `BOOL is() ...`

For each spec the tool rewrites that line, builds the objects of every unit
whose functions call the accessor's name (`-j2 -k 0`; at most N units,
default 40, non-exact callers' units first), runs census.py and
compares it with BASE.tsv (tools/mwcc-stack/cmpcensus.py), then restores the
header with `git checkout` and rebuilds the same objects so the next probe
starts from the base.  One line per spec is appended to OUTDIR/sweep.tsv:

    label  up  down  exact-before  exact-after  units  fuzzy+U-D  first ups

(fuzzy+U-D counts functions whose objdiff fuzzy match moved against
build/GMSE01/baseline.json; it sees gains inside a census class.)

A spec with ups and no downs is worth a full tree-wide measurement with
tools/shape-measure.sh.  The probe only sees the calling units: it can miss
drops elsewhere (a name called through another inline) but never reports a
false drop.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

ROOT = os.getcwd()
NINJA = os.path.join(ROOT, "build", "venv", "bin", "ninja")
HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location(
    "accand", os.path.join(HERE, "accessor-candidates.py"))
accand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accand)

LINE = re.compile(
    r"^(?P<ind>\s*)(?P<ret>(?:(?:virtual|inline|static)\s+)*(?:const\s+)?[A-Za-z_][\w:<>,\s]*?[\s\*&]+)"
    r"(?P<name>[A-Za-z_]\w*)\s*\(\s*\)\s*(?P<cv>const\s*)?"
    r"\{\s*return\s+(?P<expr>[^;{}]+);\s*\}(?P<tail>.*)$")


def rewrite(line, shape):
    m = LINE.match(line)
    if not m:
        return None, None
    ind, ret, name = m.group("ind"), m.group("ret").rstrip(), m.group("name")
    cv = " const" if m.group("cv") else ""
    expr = m.group("expr").strip()
    if shape == "bool":
        if not re.search(r"\bbool$", ret):
            return None, None
        return name, "%s%s %s()%s { return %s; }%s" % (
            ind, re.sub(r"\bbool$", "BOOL", ret), name, cv, expr, m.group("tail"))
    local = re.sub(r"\b(virtual|inline|static)\s+", "", ret).strip()
    var = re.sub(r"^(get|is)", "", name) or "value"
    var = var[0].lower() + var[1:]
    if not var.isidentifier() or var == name:
        var = "value"
    return name, "%s%s %s()%s\n%s{\n%s\t%s %s = %s;\n%s\treturn %s;\n%s}%s" % (
        ind, ret, name, cv, ind, ind, local, var, expr, ind, var, ind, m.group("tail"))


_BODIES = None


def all_bodies(base):
    global _BODIES
    if _BODIES is None:
        _BODIES = []
        texts = {}
        for line in open(base):
            u, s, fa, fb, st, n = line.rstrip("\n").split("\t")
            src = os.path.join(ROOT, "src", u[:-2] + ".cpp")
            if not os.path.exists(src):
                continue
            cls, meth = accand.parse_sym(s)
            _BODIES.append((u[:-2], st, "\n".join(accand.bodies(src, cls, meth))))
    return _BODIES


def callers(base, name):
    pat = re.compile(r"\b%s\s*\(" % re.escape(name))
    units = defaultdict(lambda: [0, 0])
    for u, st, body in all_bodies(base):
        if pat.search(body):
            units[u][0 if st == "exact" else 1] += 1
    return sorted(units, key=lambda u: (-units[u][1], -units[u][0]))


def fuzzy(out, tag):
    """Function fuzzy-match changes against build/GMSE01/baseline.json."""
    cli = os.path.join(ROOT, "build", "tools", "objdiff-cli")
    rep = os.path.join(out, tag + ".report.json")
    chg = os.path.join(out, tag + ".changes.json")
    subprocess.run([cli, "report", "generate", "-o", rep], cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run([cli, "report", "changes", "--format", "json-pretty",
                    os.path.join(ROOT, "build", "GMSE01", "baseline.json"), rep,
                    "-o", chg], cwd=ROOT, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    up = down = 0
    lines = []
    try:
        d = json.load(open(chg))
    except Exception:
        return 0, 0, ["(no report)"]
    for u in d.get("units", []):
        for f in u.get("functions", []):
            a = f.get("from", {}).get("fuzzy_match_percent", 0.0)
            b = f.get("to", {}).get("fuzzy_match_percent", 0.0)
            if b > a:
                up += 1
            elif b < a:
                down += 1
            if a != b:
                lines.append("%s %.2f -> %.2f" % (f["name"], a, b))
    return up, down, lines


def build(units):
    targets = ["build/GMSE01/src/%s.o" % u for u in units]
    return subprocess.run([NINJA, "-j2", "-k", "0"] + targets,
                          cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)


def main():
    args = sys.argv[1:]
    cap = 40
    if "--max" in args:
        i = args.index("--max")
        cap = int(args[i + 1])
        del args[i:i + 2]
    base, out = args[0], args[1]
    os.makedirs(out, exist_ok=True)
    census = os.path.join(HERE, "mwcc-stack", "census.py")
    cmp = os.path.join(HERE, "mwcc-stack", "cmpcensus.py")
    for sp in args[2:]:
        parts = sp.split(":")
        path, lineno = parts[0], int(parts[1])
        shape = parts[2] if len(parts) > 2 else "binder"
        lines = open(path).read().split("\n")
        # an accessor may span a few lines: join them until one matches
        name = None
        for k in range(1, 7):
            chunk = lines[lineno - 1:lineno - 1 + k]
            joined = chunk[0] + " " + " ".join(l.strip() for l in chunk[1:])
            name, new = rewrite(joined.rstrip(), shape)
            if name:
                break
        if not name:
            print("%s: line %d is not a one-line accessor" % (path, lineno))
            continue
        label = "%s:%d:%s:%s" % (os.path.basename(path), lineno, name, shape)
        units = callers(base, name)[:cap]
        if not units:
            print("%s: no callers" % label)
            continue
        lines[lineno - 1:lineno - 1 + k] = [new]
        open(path, "w").write("\n".join(lines))
        try:
            r = build(units)
            tag = re.sub(r"[^\w]+", "_", label)
            tsv = os.path.join(out, tag + ".tsv")
            subprocess.run([sys.executable, census, tsv], cwd=ROOT,
                           stdout=subprocess.DEVNULL)
            c = subprocess.run([sys.executable, cmp, base, tsv, "40"], cwd=ROOT,
                               stdout=subprocess.PIPE, text=True).stdout
            fz = fuzzy(out, tag)
            open(os.path.join(out, tag + ".cmp.txt"), "w").write(
                c + "\nfuzzy up/down:\n" + "\n".join(fz[2]) +
                ("\nBUILD FAILED\n" + r.stdout[-3000:] if r.returncode else ""))
        finally:
            subprocess.run(["git", "checkout", "--", path], cwd=ROOT)
            build(units)
        ex = re.search(r"exact: (\d+) -> (\d+)", c)
        ud = re.search(r"up (\d+) down (\d+)", c)
        ups = [l.split()[1][:50] for l in c.split("\n") if l.startswith("UP")]
        row = [label, ud.group(1), ud.group(2), ex.group(1), ex.group(2),
               str(len(units)), "fuzzy+%d-%d" % (fz[0], fz[1]), ("FAILED " if r.returncode else "") + " ".join(ups[:6])]
        print("\t".join(row), flush=True)
        with open(os.path.join(out, "sweep.tsv"), "a") as f:
            f.write("\t".join(row) + "\n")


if __name__ == "__main__":
    main()
