#!/usr/bin/env python3
"""Replay MWCC's register colouring from a dbg.sh dump and compare it with retail.

    tools/mwcc-stack/regalloc.py DUMPDIR UNIT SYMBOL [gpr|fpr] [options]

DUMPDIR is a dbg.sh output directory for SYMBOL (it needs the regalloc-*,
backend-*before-regalloc and last backend-*after-scheduling files).
UNIT is the objdiff unit (mario/Enemy/fireWanwan).
The model is in docs/catalog/register-model.md.

It prints every web whose register differs from retail, with its position in
MWCC's colouring order, and then (options):
  --show A:B        print colouring positions A..B with degree and both registers
  --search N        try moving each differing web to positions 0..N (and last)
  --move V:bW,...   move web V before (bW) / after (aW) web W, or to index N
  --drop V:U,U;...  delete interference edges V-U, re-run simplify and colour
  --why V           web V's remaining degree when the first simplify sweep
                    reaches it, and which lower-numbered neighbours were pushed
                    before it (a web deferred past that sweep is coloured
                    before every web pushed in it)
Retail registers are recovered per web by aligning the dump's pcode with
decomp-diff's target column, so a few volatile webs may be unmatched.
"""
import argparse
import copy
import glob
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

K = {"r": 29, "f": 32}  # allocatable registers: r0,r3-r12,r14-r31 / f0-f31
VOLATILE = {"r": [0] + list(range(3, 13)), "f": list(range(0, 14))}


def parse(fn):
    """Nodes of a regalloc-*-{all,assigned}.txt file, and their file order."""
    nodes, order, cur = {}, [], None
    for line in open(fn):
        m = re.match(r"^([rf])(\d+) -> ([rf]\d+|none)\s*(.*)", line)
        if m:
            cur = {"v": int(m.group(2)), "phys": m.group(3), "name": m.group(4).strip(),
                   "nb": [], "flags": ""}
            nodes[cur["v"]] = cur
            order.append(cur["v"])
            continue
        m = re.match(r"^\s+neighbors: \d+ \((.*)\)", line)
        if m:
            cur["nb"] = [int(x[1:]) for x in m.group(1).split()]
        m = re.match(r"^\s+cost: (-?\d+)", line)
        if m:
            cur["cost"] = int(m.group(1))
        m = re.match(r"^\s+flags:(.*)", line)
        if m:
            cur["flags"] = m.group(1).split()
    return nodes, order


def simplify(nodes, k):
    """MWCC's simplify: ascending-vreg sweeps push every web whose remaining
    degree is below k; a coalesced web never leaves its neighbours' degree.
    Returns the colouring order (last pushed first)."""
    deg = {v: len(n["nb"]) for v, n in nodes.items()}
    live = [v for v in sorted(nodes) if "fCoalesced" not in nodes[v]["flags"]]
    pushed, stack = set(), []

    def push(v):
        pushed.add(v)
        stack.append(v)
        for u in nodes[v]["nb"]:
            if u in deg:
                deg[u] -= 1

    while len(pushed) < len(live):
        progress = False
        for v in live:
            if v not in pushed and deg[v] < k:
                push(v)
                progress = True
        if not progress:  # blocked: push the web with the lowest cost per remaining degree
            rest = [v for v in live if v not in pushed]
            push(min(rest, key=lambda v: nodes[v].get("cost", 0) / max(deg[v], 1)))
    return stack[::-1]


def colour(nodes, order, c):
    """Lowest free volatile; else the lowest callee-saved already in use;
    else a new callee-saved register just below the lowest in use (31 down)."""
    col, used = {}, []
    for v in order:
        taken = set()
        for u in nodes[v]["nb"]:
            if u < 32:
                taken.add(u)
            elif u in col:
                taken.add(col[u])
        pick = next((r for r in VOLATILE[c] if r not in taken), None)
        if pick is None:
            pick = next((r for r in sorted(used) if r not in taken), None)
        if pick is None:
            pick = next(r for r in range(31, 13, -1) if r not in used and r not in taken)
            used.append(pick)
        col[v] = pick
    return col


def blocks(fn):
    out, cur = {}, None
    for line in open(fn):
        m = re.match(r"^B(\d+):", line)
        if m:
            cur = out.setdefault(int(m.group(1)), [])
            continue
        if cur is not None and line.startswith("        ") and line.split():
            p = line.split()
            cur.append((p[0], p[1] if len(p) > 1 else ""))
    return out


REG = re.compile(r"\b([rf])(\d+)\b")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("unit")
    ap.add_argument("symbol")
    ap.add_argument("cls", nargs="?", default="gpr", choices=["gpr", "fpr"])
    ap.add_argument("--show")
    ap.add_argument("--search", type=int)
    ap.add_argument("--move")
    ap.add_argument("--drop")
    ap.add_argument("--why", type=int)
    a = ap.parse_args()
    c = "r" if a.cls == "gpr" else "f"

    allg, _ = parse(glob.glob(f"{a.dump}/regalloc-{a.cls}-pass-1-all.txt")[0])
    _, order = parse(glob.glob(f"{a.dump}/regalloc-{a.cls}-pass-1-assigned.txt")[0])
    if simplify(allg, K[c]) != order:
        print("warning: simplify replay differs from the dumped colouring order", file=sys.stderr)
    phys = {v: int(n["phys"][1:]) for v, n in allg.items() if n["phys"] != "none"}
    files = sorted(glob.glob(f"{a.dump}/backend-*"))
    pre = blocks([f for f in files if "before-regalloc" in f][0])
    post = blocks([f for f in files if "after-scheduling" in f][-1])

    out = subprocess.run([sys.executable, "tools/decomp-diff.py", "-u", a.unit, "-d", a.symbol,
                          "--no-collapse"], capture_output=True, text=True).stdout
    rows = [(m.group(1), m.group(2)) for m in
            (re.match(r"^[~<>| ]\s*[0-9a-f]* \| (.*?)\s* \| (.*)$", l) for l in out.splitlines()) if m]
    flat = [(b, op, ops) for b in sorted(post) for op, ops in post[b]]
    if len(flat) != len(rows):
        print(f"warning: {len(flat)} pcode rows vs {len(rows)} diff rows", file=sys.stderr)

    def ph(rc, r):
        return r if rc != c or r < 32 else phys.get(r, -1)

    votes = defaultdict(Counter)
    for (b, op, ops), (tgt, _ours) in zip(flat, rows):
        if op.startswith("b"):
            continue
        want = [ph(rc, int(r)) for rc, r in REG.findall(ops)]
        tregs = REG.findall(tgt)
        for op0, ops0 in pre.get(b, []):
            if op0 != op or REG.sub("R", ops0) != REG.sub("R", ops):
                continue
            r0 = [(rc, int(r)) for rc, r in REG.findall(ops0)]
            if [ph(rc, r) for rc, r in r0] == want and len(r0) == len(tregs):
                for (rc, v), (tc, tr) in zip(r0, tregs):
                    if rc == c and v >= 32:
                        votes[v][int(tr)] += 1
                break
    retail = {v: cn.most_common(1)[0][0] for v, cn in votes.items()}

    def score(o, g=allg):
        col = colour(g, o, c)
        return sum(1 for v in retail if v in col and col[v] != retail[v])

    pos = {v: i for i, v in enumerate(order)}

    def row(i, v):
        n, r = allg[v], retail.get(v)
        mark = "*" if r is not None and r != phys[v] else ""
        return (f"{i:4} {c}{v:<5} {n['name'] or '-':12} deg {len(n['nb']):3} ours {c}{phys[v]:<3}"
                f" retail {'' if r is None else c + str(r)} {mark}")

    for v in order:
        if v in retail and retail[v] != phys[v]:
            print(row(pos[v], v))
    print(f"differing {sum(1 for v in order if v in retail and retail[v] != phys[v])} "
          f"of {len(retail)} mapped; replay of the current order misses {score(order)}")
    if a.show:
        lo, hi = map(int, a.show.split(":"))
        for i, v in enumerate(order[lo:hi], lo):
            print(row(i, v))
    if a.move:
        o = list(order)
        for mv in a.move.split(","):
            v, p = mv.split(":")
            v = int(v)
            o.remove(v)
            if p[0] in "ab":
                o.insert(o.index(int(p[1:])) + (p[0] == "a"), v)
            else:
                o.insert(int(p), v)
        print("move: replay misses", score(o))
    if a.search is not None:
        base, found = score(order), []
        for v in [x for x in order if x in retail and retail[x] != phys[x]]:
            for p in list(range(min(a.search, len(order)))) + [len(order) - 1]:
                o = [x for x in order if x != v]
                o.insert(p, v)
                m = score(o)
                if m < base:
                    found.append((m, v, pos[v], p))
        for m, v, s, p in sorted(found)[:15]:
            print(f"move {c}{v} {allg[v]['name']} from {s} to {p}: misses {m}")
    if a.why is not None:
        v, deg, pushed = a.why, {x: len(n["nb"]) for x, n in allg.items()}, []
        for x in sorted(allg):
            if x == v:
                break
            if "fCoalesced" not in allg[x]["flags"] and deg[x] < K[c]:
                pushed.append(x)
                for u in allg[x]["nb"]:
                    if u in deg:
                        deg[u] -= 1
        print(f"{c}{v} {allg[v]['name']}: degree {len(allg[v]['nb'])}, {deg[v]} left at its "
              f"first-sweep turn (K {K[c]}): {'deferred' if deg[v] >= K[c] else 'pushed'}")
        print("  lower neighbours pushed before it:",
              " ".join(f"{c}{x}({allg[x]['name'] or '-'})" for x in pushed if v in allg[x]["nb"]))
    if a.drop:
        g = copy.deepcopy(allg)
        for part in a.drop.split(";"):
            v, us = part.split(":")
            for u in map(int, us.split(",")):
                for x, y in ((int(v), u), (u, int(v))):
                    if x in g and y in g[x]["nb"]:
                        g[x]["nb"].remove(y)
        print("drop: replay misses", score(simplify(g, K[c]), g))


if __name__ == "__main__":
    main()
