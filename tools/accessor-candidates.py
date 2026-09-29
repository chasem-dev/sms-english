#!/usr/bin/env python3
"""List header accessors used by frame-short / slot-only functions.

Usage (from the repo root or a worktree, after a census.py run):
    python3 tools/accessor-candidates.py CENSUS.tsv [--all]
    python3 tools/accessor-candidates.py CENSUS.tsv --callers NAME
      (units with exact callers of NAME, most first, then the candidates)

An accessor is an in-class, zero-argument member function in include/ whose
body is a single `return <expr>;`:
  - getter: the expression is a member path (`m`, `&m`, `m.n`, `m[0]`);
  - predicate: the declared return type is `bool`.

For every function census.py classes as `slots`, or `frame` with our frame
smaller than retail's, the script finds the function's body in its unit's
.cpp (by the demangled class and method name; all overloads) and counts the
accessor names it calls.  Columns: candidate functions, exact functions
and other/over-frame functions that call the name (a binder shape adds a
word to every caller, so exact callers predict drops).  Accessors are ranked by the number of such
functions.  A name defined in several classes is reported once per
definition, with the same count (the match is by name only).
"""
import os
import re
import sys
from collections import defaultdict

ROOT = os.getcwd()

ACC = re.compile(
    r"(?P<ret>(?:const\s+)?[A-Za-z_][\w:<>,\s]*?[\s\*&]+)"
    r"(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^()]*)\)\s*(?:const\s*)?"
    r"\{\s*return\s+(?P<expr>[^;{}]+);\s*\}")
MEMBER = re.compile(r"^&?\s*[A-Za-z_][\w\.\[\]]*(?:->\w+)?$")
CLASS = re.compile(r"\b(?:class|struct)\s+(\w+)[^;{]*\{")


def headers():
    for base, _, files in os.walk(os.path.join(ROOT, "include")):
        for f in files:
            if f.endswith((".hpp", ".h")):
                yield os.path.join(base, f)


def enclosing_class(text, pos):
    best = None
    for m in CLASS.finditer(text, 0, pos):
        # class is open at pos if braces from its '{' to pos don't close
        depth = 0
        for ch in text[m.end() - 1:pos]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
        if depth > 0:
            best = m.group(1)
    return best


def accessors():
    out = []
    for h in headers():
        text = open(h, errors="replace").read()
        for m in ACC.finditer(text):
            ret = " ".join(m.group("ret").split())
            if ret.split()[-1] in ("return", "else", "new", "delete"):
                continue
            expr = m.group("expr").strip()
            kind = None
            if re.match(r"^(inline\s+)?(static\s+)?bool\b", ret):
                kind = "pred"
            elif MEMBER.match(expr) and "static" not in ret:
                kind = "getter"
            if kind is None:
                continue
            line = text.count("\n", 0, m.start("name")) + 1
            if m.group("params").strip() not in ("", "void") and kind == "getter":
                kind = "getter(args)"
            cls = enclosing_class(text, m.start())
            out.append(dict(name=m.group("name"), ret=ret, expr=expr,
                            kind=kind, file=os.path.relpath(h, ROOT),
                            line=line, cls=cls))
    return out


def parse_sym(sym):
    """Return (class, method) for an MWCC mangled name."""
    f = re.match(r"^(\w+?)__F", sym)
    if f:
        return None, f.group(1)
    m = re.match(r"^(\w+?)__(?:(\d+)(\w+?)|Q(\d)(.*?))(?:C?F|$)", sym)
    if not m:
        return None, sym
    meth = m.group(1)
    if m.group(2):
        n = int(m.group(2))
        cls = (m.group(3) + sym[m.end(3):])[:n]
    else:
        rest = sym[sym.index("__Q") + 3:]
        k = int(rest[0])
        rest = rest[1:]
        cls = None
        for _ in range(k):
            mm = re.match(r"(\d+)", rest)
            n = int(mm.group(1))
            cls = rest[len(mm.group(1)):len(mm.group(1)) + n]
            rest = rest[len(mm.group(1)) + n:]
    if meth == "__ct":
        meth = cls
    elif meth == "__dt":
        meth = "~" + cls
    return cls, meth


def bodies(src, cls, meth):
    text = open(src, errors="replace").read()
    if cls and meth == "execute" and "DEFINE_NERVE(%s," % cls in text:
        pat = re.compile(r"DEFINE_NERVE\(%s,[^)]*\)" % re.escape(cls))
    elif cls:
        pat = re.compile(r"^[^\n/]*\b%s\s*::\s*%s\s*\(" % (re.escape(cls), re.escape(meth)), re.M)
    else:
        pat = re.compile(r"^[^\n;]*\b%s\s*\(" % re.escape(meth), re.M)
    out = []
    for m in pat.finditer(text):
        i = text.find("{", m.end())
        semi = text.find(";", m.end())
        if i < 0 or (0 <= semi < i and cls is None):
            continue
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append(text[i:j + 1])
                    break
    return out


def main():
    census = sys.argv[1]
    show_all = "--all" in sys.argv
    callers = sys.argv[sys.argv.index("--callers") + 1] if "--callers" in sys.argv else None
    exact_units = defaultdict(lambda: defaultdict(int))
    acc = accessors()
    names = defaultdict(list)
    for a in acc:
        names[a["name"]].append(a)
    uses = defaultdict(set)
    other = defaultdict(lambda: defaultdict(int))
    for line in open(census):
        u, s, fa, fb, st, n = line.rstrip("\n").split("\t")
        fa, fb = int(fa), int(fb)
        cand = st == "slots" or (st == "frame" and fb < fa)
        if st == "frame" and not cand:
            st = "over"
        src = os.path.join(ROOT, "src", u[:-2] + ".cpp")
        if not os.path.exists(src):
            continue
        cls, meth = parse_sym(s)
        for b in bodies(src, cls, meth):
            for call in set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", b)):
                if call not in names:
                    continue
                if cand:
                    uses[call].add("%s:%s" % (u[:-2], s))
                else:
                    other[call][st] += 1
                    if st == "exact":
                        exact_units[call][u[:-2]] += 1
    if callers:
        # units with exact callers of the name, most first: probe candidates
        for u, n in sorted(exact_units[callers].items(), key=lambda kv: -kv[1]):
            print("%d\t%s" % (n, u))
        for f in sorted(uses[callers]):
            print("cand\t" + f)
        return
    rows = sorted(uses.items(), key=lambda kv: -len(kv[1]))
    for name, fs in rows:
        for a in names[name]:
            o = other[name]
            print("%3d\t%4d\t%3d\t%s\t%s::%s\t%s\t%s:%d\t%s" % (
                len(fs), o["exact"], o["other"] + o["over"], a["kind"], a["cls"], name, a["expr"], a["file"],
                a["line"], a["ret"]))
        if show_all:
            for f in sorted(fs):
                print("\t\t" + f)


if __name__ == "__main__":
    main()
