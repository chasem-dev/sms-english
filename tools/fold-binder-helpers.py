#!/usr/bin/env python3
"""Fold TU-local binder helpers into a header accessor.

Usage (from the repo root or a worktree):
    python3 tools/fold-binder-helpers.py NAME CPP:HELPER [CPP:HELPER ...]

Each HELPER is a TU-local `static inline T* Helper(P* p) { T* x = p->m;
return x; }` in CPP (a path under src/).  The helper's definition is removed
and every call `Helper(arg)` becomes `arg->NAME()` (`(arg)->NAME()` when the
argument is not a plain name).  Use it after giving the accessor NAME the
binder shape (`T* NAME() { T* x = m; return x; }`), which is what these
helpers emulate (research c-r30); then measure tree-wide.
"""
import re
import sys


def fold(path, helper, name):
    s = open(path).read()
    d = re.compile(
        r"(?:\n//[^\n]*)*\nstatic inline [^\n;{(]*?\b%s\(([^\n;{)]*)\)\s*\{[^{}]*\}\n"
        % re.escape(helper))
    m = d.search(s)
    if not m:
        sys.exit("%s: no helper %s" % (path, helper))
    head, tail = s[:m.start()], s[m.end():]
    s = head + ("" if head.endswith("\n") and tail.startswith("\n") else "\n") + tail
    out, i = [], 0
    call = re.compile(r"\b%s\(" % re.escape(helper))
    n = 0
    while True:
        c = call.search(s, i)
        if not c:
            out.append(s[i:])
            break
        depth, j = 1, c.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(s[j], 0)
            j += 1
        arg = s[c.end():j - 1].strip()
        if not re.match(r"^[A-Za-z_]\w*$", arg):
            arg = "(%s)" % arg
        out.append(s[i:c.start()])
        out.append("%s->%s()" % (arg, name) if arg != "this" else "%s()" % name)
        i = j
        n += 1
    open(path, "w").write("".join(out))
    return n


def main():
    name = sys.argv[1]
    for spec in sys.argv[2:]:
        path, helper = spec.split(":")
        print("%s %s: %d calls" % (path, helper, fold(path, helper, name)))


if __name__ == "__main__":
    main()
