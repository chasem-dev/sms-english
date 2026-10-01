#!/usr/bin/env python3
"""Sweep the named-reference lever over every non-exact function.

Lever (frame-model.md, "Constants, conditions and named references", c-t5):
an inline accessor returning a reference (`getPoint()`, `getPosition()`,
`SMS_GetMarioPos()`, a params getter) used as a by-value initialiser, an
assignment source, an argument or a receiver leaves its result pointer as a
dead word below the named block; naming the reference first

    const JGeometry::TVec3<f32>& goal = node.getPoint();
    JGeometry::TVec3<f32> toGoal = goal;

moves that word into the named block at identical code.

For every function below 100% in build/GMSE01/report.json the tool finds calls
whose header return type is a reference to a class (not a pointer or scalar)
and `*gpMarioPos`, and makes one variant per site (the call named in a
`const T&` declared just before its statement) plus one per repeated call text
(every identical site of the function named once, when the first site's block
encloses the others and nothing between them writes the receiver).  Only
`get*` names count (a `negate()` returns *this after writing it); a name the
header index cannot resolve (TPathNode::getPoint returns a reference,
TGraphNode::getPoint a value) is tried with its one reference type and flagged
`?` in the description, so check the receiver's class by hand.  An existing
`const T& x = SMS_GetMarioPos();` / `= *gpMarioPos;` gets the other spelling
(`swap`), and an initialiser of a declared reference is never named again.
Each
variant compiles in a shadow root (hsearch's Evaluator) and the whole unit is
profiled with hsearch's score key (exact, fuzzy, instructions, frame delta,
registers, r1 slots); a variant is a GAIN when some function's key improves and
none gets worse.  Nothing is written to the tree; winners are printed as
unified diffs under --out.

Usage (worktree root, after a full build; the whole tree takes about 7 min):
  python3 tools/ref-name-sweep.py --list [--units Enemy/amiNoko,...]
  python3 tools/ref-name-sweep.py --out DIR [-j 2] [--units ...] [--skip ...] [--match REGEX]

The log (DIR/sweep.log) is resumable: a unit with a `unit done` line is not
run again.  A GAIN still needs review: a single site named among identical
ones, a frame filled by a word retail keeps elsewhere (check `hsearch dbg`),
or a reference named only to read one component are refused.
"""

import argparse
import concurrent.futures as cf
import difflib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..")))
from tools.hsearch import search as HS  # noqa: E402
from tools.hsearch.elfscore import regressions  # noqa: E402

LS = HS.LS
ROOT = HS.ROOT

SCALARS = set(LS.SCALAR_TYPES) | {"void", "bool", "BOOL"}
NAMES = {"getPoint": "goal", "SMS_GetMarioPos": "marioPos", "getPosition": "pos", "getTrans": "trans",
         "getRotation": "rot", "getScaling": "scale", "getScale": "scale", "getVelocity": "vel",
         "getMarioPos": "marioPos"}


def ref_class_type(rt):
    """`const X&` / `X&` of a class type -> X, else None."""
    if not rt or not rt.endswith("&") or rt.endswith("&&"):
        return None
    base = rt[:-1].strip()
    base = re.sub(r"^const\s+", "", base).strip()
    if base.endswith("*") or base.split("<")[0].split("::")[-1].strip() in SCALARS:
        return None
    if base in SCALARS:
        return None
    return base


class RefGen(LS.Gen):
    def sites(self):
        """[(a, k, type, name-base, context)] for reference-returning calls."""
        t = self.toks
        out = []
        for k in range(self.bo + 1, self.bc):
            # *gpMarioPos
            if t[k].text == "gpMarioPos" and t[k - 1].text == "*" and t[k - 2].text in ("=", "(", ",", "return"):
                out.append((k - 1, k, "JGeometry::TVec3<f32>", "marioPos", self.context(k - 1, k)))
                continue
            if t[k].text != ")" or k not in self.bm:
                continue
            o = self.bm[k]
            if o + 1 != k:  # accessors take no arguments
                continue
            if t[o - 1].kind != "id" or t[o - 1].text in LS.KEYWORDS:
                continue
            a = self.postfix_start(k)
            if t[a - 1].text in (".", "->", "::", "new", "&"):
                continue
            try:
                rt = self.call_type(a, k)
            except Exception:
                rt = None
            fname = t[o - 1].text
            if not re.match(r"(SMS_?)?[Gg]et[A-Z0-9]", fname):
                continue  # only accessors: negate() and friends return *this after writing it
            ty = ref_class_type(rt)
            amb = ""
            if ty is None and rt is None:
                # the name is ambiguous in the index (TPathNode::getPoint returns a
                # reference, TGraphNode::getPoint a value): try the one reference
                # type it has, and flag the site for a by-hand type check
                refs = set(filter(None, (ref_class_type(x) for x in self.idx.rettypes.get(fname, ()))))
                if len(refs) == 1:
                    ty, amb = refs.pop(), "?"
            if ty is None:
                continue
            base = NAMES.get(fname) or re.sub(r"^(SMS_?|MS)?(get|Get)(?=[A-Z])", "", fname)
            base = base[:1].lower() + base[1:] if base else "ref"
            out.append((a, k, ty, base, self.context(a, k) + amb))
        return out

    def context(self, a, k):
        t = self.toks
        p, n = t[a - 1].text, t[k + 1].text
        if n in (".", "->"):
            return "receiver"
        if p == "=" and n == ";":
            # declaration or assignment
            q = a - 2
            if t[q].kind == "id" and t[q - 1].text == "&":
                return "refinit"  # already a named reference: naming it again is a chain
            if q > self.bo and t[q].kind == "id" and t[q - 1].kind == "id" or t[q - 1].text in (">", "*", "&"):
                return "init"
            return "assign"
        if p == "(" and n == ")" and t[a - 2].kind == "id" and t[a - 3].kind == "id":
            return "init"  # T v(call())
        if p in ("(", ",") and n in (")", ","):
            return "arg"
        return "other"

    def writes_between(self, idents, lo, hi):
        t = self.toks
        for q in range(lo, hi):
            if t[q].text in idents and t[q + 1].text in LS.ASSIGN_OPS | {"++", "--"}:
                return True
        return False

    def variants(self):
        sites = self.sites()
        out = []  # (desc, edits)
        groups = {}
        for (a, k, ty, base, ctx) in sites:
            if ctx.startswith("refinit"):
                continue
            s = self.stmt_start(a)
            if s is None:
                continue
            if any(self.toks[q].text in ("&&", "||", "?") for q in range(s, a)):
                continue
            key = re.sub(r"\s+", "", self.span(a, k))
            groups.setdefault(key, []).append((a, k, ty, base, ctx, s))
        # the c-t5 knob on an existing Mario position reference:
        # `= SMS_GetMarioPos();` against `= *gpMarioPos;`
        t = self.toks
        for k in range(self.bo + 1, self.bc - 4):
            if t[k].text == "&" and t[k + 1].kind == "id" and t[k + 2].text == "=":
                line = self.text.count("\n", 0, t[k].s) + 1
                if t[k + 3].text == "SMS_GetMarioPos" and t[k + 4].text == "(" and t[k + 5].text == ")":
                    out.append(("L%d swap SMS_GetMarioPos() -> *gpMarioPos" % line,
                                [(t[k + 3].s, t[k + 5].e, "*gpMarioPos")]))
                elif t[k + 3].text == "*" and t[k + 4].text == "gpMarioPos":
                    out.append(("L%d swap *gpMarioPos -> SMS_GetMarioPos()" % line,
                                [(t[k + 3].s, t[k + 4].e, "SMS_GetMarioPos()")]))
        for key, g in groups.items():
            for (a, k, ty, base, ctx, s) in g:
                v = self.fresh(base, self.block_of(s), s)
                decl = "const %s& %s = %s;" % (ty, v, self.span(a, k))
                line = self.text.count("\n", 0, self.toks[a].s) + 1
                out.append(("L%d %s %s" % (line, ctx, key), [self.insert_before_stmt(s, decl),
                                                            (self.toks[a].s, self.toks[k].e, v)]))
            if len(g) > 1:
                a0, k0, ty, base, ctx, s0 = g[0]
                blk = self.block_of(s0)
                close = self.bm.get(blk)
                if close is None or any(not (s0 <= x[0] <= close) for x in g):
                    continue
                idents = set(self.toks[q].text for q in range(a0, k0) if self.toks[q].kind == "id")
                if self.writes_between(idents, s0, g[-1][1]):
                    continue
                v = self.fresh(base, blk, s0)
                decl = "const %s& %s = %s;" % (ty, v, self.span(a0, k0))
                eds = [self.insert_before_stmt(s0, decl)]
                eds += [(self.toks[x[0]].s, self.toks[x[1]].e, v) for x in g]
                line = self.text.count("\n", 0, self.toks[a0].s) + 1
                out.append(("L%d all%d %s" % (line, len(g), key), eds))
        return out


def nonexact():
    rep = json.load(open(os.path.join(ROOT, "build/GMSE01/report.json")))
    want = {}
    for u in rep["units"]:
        md = u.get("metadata", {})
        if not md.get("source_path") or md.get("auto_generated"):
            continue
        bad = [f["name"] for f in u.get("functions", []) if float(f.get("fuzzy_match_percent", 100)) < 100.0]
        if bad:
            want[u["name"]] = bad
    return want


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units")
    ap.add_argument("--skip", default="")
    ap.add_argument("-j", "--jobs", type=int, default=2)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--match", default=None, help="only variants whose description matches this regex")
    args = ap.parse_args()
    want = nonexact()
    if args.units:
        sel = set(x if x.startswith("mario/") else "mario/" + x for x in args.units.split(","))
        want = {k: v for k, v in want.items() if k in sel}
    skip = set(x if x.startswith("mario/") else "mario/" + x for x in args.skip.split(",") if x)
    out = args.out or os.path.join(ROOT, "build", "ref-name-sweep")
    os.makedirs(out, exist_ok=True)
    logf = open(os.path.join(out, "sweep.log"), "a")

    def log(*a):
        s = " ".join(str(x) for x in a)
        print(s, flush=True)
        logf.write(s + "\n")
        logf.flush()

    done = set()
    if not args.list and os.path.exists(os.path.join(out, "sweep.log")):
        cur = None
        for line in open(os.path.join(out, "sweep.log")):
            if line.startswith("UNIT "):
                cur = line.split()[1]
            elif line.startswith("  unit done") and cur:
                done.add(cur)  # resumable: a finished unit is not run again
    total = 0
    for uname, bad in sorted(want.items()):
        if uname in skip or uname in done:
            continue
        try:
            u = HS.find_unit(uname)
        except SystemExit:
            continue
        path = os.path.join(ROOT, u.rel_src)
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8", newline="").read()
        toks = LS.lex(text)
        bm = LS.bracket_map(toks)
        idx = LS.merged_index(text)
        cands = []
        for fn in bad:
            try:
                info = LS.parse_demangled(HS.demangle(fn))
                loc = LS.locate_function(toks, bm, info)
            except Exception:
                loc = None
            if loc is None:
                continue
            try:
                g = RefGen(text, loc, toks, bm, idx)
                for desc, eds in g.variants():
                    vt = LS.apply_edits(text, eds)
                    if vt is not None and (not args.match or re.search(args.match, desc)):
                        cands.append((fn, desc, vt))
            except Exception as e:  # a lexer corner case: skip the function
                log("  skip", fn, type(e).__name__, e)
        if not cands:
            continue
        total += len(cands)
        log("UNIT", uname, "variants", len(cands))
        if args.list:
            for fn, desc, _ in cands:
                log("   ", fn, desc)
            continue
        ev = HS.Evaluator(u, cands[0][0], args.jobs, out, HS.TrialDB(os.path.join(out, "trials.sqlite")))
        try:
            t0 = time.time()
            base = ev.profile(text)
            if base is None:
                log("  base compile failed")
                continue

            def trial(c):
                return c, ev.profile(c[2])

            with cf.ThreadPoolExecutor(args.jobs) as ex:
                for (fn, desc, vt), prof in ex.map(trial, cands):
                    if prof is None:
                        log("  FAIL", fn, desc)
                        continue
                    regs = regressions(base, prof, "")
                    gains = [k for k in base if not k.startswith("[") and prof.get(k) is not None and prof[k] < base[k]]
                    tag = "GAIN" if gains and not regs else ("mixed" if gains else ("drop" if regs else "same"))
                    log("  %s %s %s | %s" % (tag, fn, desc, "; ".join(
                        ["+%s %s->%s" % (g, base[g], prof[g]) for g in gains][:3] + ["-" + r for r in regs[:3]])))
                    if gains:
                        name = re.sub(r"\W+", "_", "%s_%s" % (fn, desc))[:150]
                        d = difflib.unified_diff(text.splitlines(True), vt.splitlines(True),
                                                 "a/" + u.rel_src, "b/" + u.rel_src)
                        with open(os.path.join(out, tag + "_" + name + ".patch"), "w") as f:
                            f.writelines(d)
            log("  unit done in %.0fs" % (time.time() - t0))
        finally:
            ev.close()
    log("TOTAL variants", total)


if __name__ == "__main__":
    main()
