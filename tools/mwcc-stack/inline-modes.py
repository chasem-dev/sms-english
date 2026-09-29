#!/usr/bin/env python3
"""Measure MWCC's per-site inline level for a battery of call contexts.

Research c-r24 (docs/catalog/codegen-tells.md, "Research batch c-r24").
Each probe compiles a scratch TU with the game's flags, in which a plain
callee P() with k counted statements sits in one call context.  The smallest
k at which P becomes a `bl` gives the allowance at that site, and the
published allowance table (14 / 9 / 6 / 2 / never at levels 1..5) turns it
into the inline level P is judged at.

    python3 tools/mwcc-stack/inline-modes.py            # GC/1.2.5, all probes
    python3 tools/mwcc-stack/inline-modes.py -v 1.1     # the debugger's compiler
    python3 tools/mwcc-stack/inline-modes.py -k root    # only probes whose name contains "root"
    python3 tools/mwcc-stack/inline-modes.py --snippet my.cpp
        # my.cpp must define the call site in a function `C` and call a
        # callee spelled `P(...)`; the tool supplies `P` (an int(int)) with
        # `K` statements and reports P's level at your site.

Run from the repository root (it uses build/tools/wibo, build/compilers and
build/binutils).  Each probe takes about a second per k.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

FLAGS = (
    '-nodefaults -align powerpc -enum int -fp hardware -Cpp_exceptions off '
    '-pragma "cats off" -maxerrors 1 -nosyspath -RTTI off -str reuse '
    '-proc gekko -O4,p -inline auto -fp_contract on -str reuse,readonly '
    '-opt all,nostrength -inline deferred -lang=c++'
)

# allowance (largest statement count that still expands) -> level
ALLOWANCE_LEVEL = {14: 1, 9: 2, 6: 3, 2: 4, -1: 5}

PRELUDE = """
extern int g[64]; extern void ext(int); extern float gf;
struct V { float x, y, z; V() {} V(int) {} V(const V& o) { x = o.x; y = o.y; z = o.z; }
  V& operator=(const V& o) { x = o.x; y = o.y; z = o.z; return *this; } };
extern V gv;
int P(int x) { PBODY return g[1]; }
inline void setI(int v) { g[60] = v; }
inline int id(int v) { return v; }
inline void W1(int x) { setI(P(x)); }          // a root call inside a body
inline void Wv(int x) { g[59] = P(x); }         // a value call inside a body
inline int Ci(int x) { V l; l = V(P(x)); return (int)l.x; }  // a root statement in a body
"""

# name -> body of C(int x)
PROBES = {
    # statement mode: the call is the whole statement, condition or return
    "root: setI(P(x));": "setI(P(x));",
    "root: id(P(x));": "id(P(x));",
    "root: return id(P(x));": "return id(P(x));",
    "root: if (id(P(x)))": "if (id(P(x))) ext(1);",
    "root: while (id(P(x)))": "while (id(P(x))) ext(1);",
    "root: switch (id(P(x)))": "switch (id(P(x))) { case 1: ext(1); }",
    "root: setI(1), setI(P(x));": "setI(1), setI(P(x));",
    "root: gv = V(P(x)); (class operator=)": "gv = V(P(x));",
    # expression mode: the call is an operand
    "value: g[1] = id(P(x));": "g[1] = id(P(x));",
    "value: int t = id(P(x));": "int t = id(P(x)); ext(t);",
    "value: ext(id(P(x)));": "ext(id(P(x)));",
    "value: if (id(P(x)) < 3)": "if (id(P(x)) < 3) ext(1);",
    "value: !id(P(x));": "!id(P(x));",
    "value: (void)(id(P(x)));": "(void)(id(P(x)));",
    # plain depth, for calibration
    "plain: P(x); (level 1)": "P(x);",
    "plain: W1(x); -> setI(P) root in body": "W1(x);",
    "plain: Wv(x); -> g = P value in body": "Wv(x);",
    # a callee inlined in value mode loses its statements' root status
    "body: Ci(x); (root)": "Ci(x);",
    "body: g[2] = Ci(x); (value)": "g[2] = Ci(x);",
}


def compile_count(root, version, src_text, callee="P__Fi", func="C__Fi"):
    with tempfile.TemporaryDirectory(dir=os.path.join(root, "build")) as td:
        src = os.path.join(td, "p.cpp")
        obj = os.path.join(td, "p.o")
        with open(src, "w") as f:
            f.write(src_text)
        cmd = (f'build/tools/wibo build/compilers/GC/{version}/mwcceppc.exe {FLAGS} '
               f'-c "{src}" -o "{obj}"')
        r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(obj):
            raise RuntimeError(r.stdout + r.stderr)
        d = subprocess.run(["build/binutils/powerpc-eabi-objdump", "-dr", obj],
                           cwd=root, capture_output=True, text=True).stdout
    n, cur = 0, None
    for line in d.splitlines():
        m = re.match(r"^[0-9a-f]+ <(.*)>:", line)
        if m:
            cur = m.group(1)
        elif cur == func and "R_PPC_REL24" in line and line.split()[-1] == callee:
            n += 1
    return n


def level_of(root, version, make_src, kmax=15):
    """Smallest k (statements in P) at which C calls P; returns (k, level)."""
    for k in range(0, kmax + 1):
        if compile_count(root, version, make_src(k)) > 0:
            allowance = k - 1
            return k, ALLOWANCE_LEVEL.get(allowance, f"?(allowance {allowance})")
    return None, "<=1 (never refused)"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-v", "--version", default="1.2.5")
    ap.add_argument("-k", "--filter", default="")
    ap.add_argument("--snippet", help="a C++ file with your own C(int x) and P(...) call")
    args = ap.parse_args()
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def body(k):
        return "".join(f"g[{i}] = x;" for i in range(k))

    if args.snippet:
        text = open(args.snippet).read()
        k, lvl = level_of(root, args.version,
                          lambda k: PRELUDE.replace("PBODY", body(k)) + text)
        print(f"{args.snippet}: P refused from {k} statements -> level {lvl}")
        return
    for name, stmt in PROBES.items():
        if args.filter and args.filter not in name:
            continue
        k, lvl = level_of(root, args.version,
                          lambda k: PRELUDE.replace("PBODY", body(k))
                          + "int C(int x) { " + stmt + " return 0; }\n")
        print(f"level {lvl!s:<24} (P refused from {k} statements)  {name}")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
