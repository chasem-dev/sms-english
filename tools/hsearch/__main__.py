"""hsearch: honest source search for near-exact functions.

  python3 -m tools.hsearch run -u Enemy/rocket -f control__7TRocketFv
  python3 -m tools.hsearch batch --list targets.tsv --out DIR
  python3 -m tools.hsearch score -u Enemy/rocket -f control__7TRocketFv
  python3 -m tools.hsearch moves -u Enemy/rocket -f control__7TRocketFv
  python3 -m tools.hsearch apply DIR/control__7TRocketFv.patch

Run from the repo or worktree root.  See tools/hsearch/README.md.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

from . import moves as M
from .search import ROOT, Search, TrialDB, find_unit, demangle, _count
from .elfscore import Target, Elf

DEFAULT_DB = os.environ.get("HSEARCH_DB") or os.path.join(tempfile.gettempdir(), "hsearch", "trials.sqlite")
TSV_HEAD = "unit\tfunction\tstatus\tbefore\tafter\tbuilds\tcached\tsecs\tbest_at\tpatch\tmoves\n"


def safe_name(fn: str) -> str:
    return re.sub(r"[^\w.-]", "_", fn)[:120]


def run_one(args, db, unit, fn):
    s = Search(unit, fn, args.jobs, args.work, db, seed=args.seed, dbg_k=args.dbg)
    try:
        r = s.run(args.budget)
        patch = ""
        if r.get("text") and r["status"] in ("exact", "improved", "improved-layout"):
            os.makedirs(args.out, exist_ok=True)
            patch = os.path.join(args.out, safe_name(fn) + (".patch" if r["status"] == "exact" else ".improved.patch"))
            with open(patch, "w", encoding="utf-8") as f:
                f.write("# hsearch %s %s\n# unit: %s\n# status: %s  %s -> %s\n# moves: %s\n" % (
                    s.u.name, fn, s.u.name, r["status"], r["base"].short(), r["best"].short(),
                    " + ".join(r["moves"])))
                f.write(s.patch(r["text"]))
        r["patch"] = patch
        db.put_run(fn, s.u.name, r["status"], r["base"].short(), r["best"].short(), r.get("builds", 0),
                   r.get("cached", 0), r.get("secs", 0), " + ".join(r["moves"]), patch)
        os.makedirs(args.out, exist_ok=True)
        tsv = os.path.join(args.out, "results.tsv")
        new = not os.path.exists(tsv)
        with open(tsv, "a", encoding="utf-8") as f:
            if new:
                f.write(TSV_HEAD)
            f.write("\t".join(str(x) for x in (
                s.u.name, fn, r["status"], r["base"].short(), r["best"].short(), r.get("builds", 0),
                r.get("cached", 0), "%.0f" % r.get("secs", 0), "%.0f" % r.get("best_at", (0, 0))[0], patch,
                re.sub(r"\s+", " ", " + ".join(r["moves"])))) + "\n")
        return r
    finally:
        s.close()


def cmd_run(args):
    db = TrialDB(args.db)
    r = run_one(args, db, args.unit, args.function)
    return 0 if r["status"] == "exact" else 1


def cmd_batch(args):
    import signal
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))  # unwind so shadow roots are removed
    db = TrialDB(args.db)
    rows = []
    for line in open(args.list, encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) < 2 or p[0].startswith("#") or p[0] == "unit":
            continue
        rows.append((p[0], p[1]))
    t0 = time.time()
    for unit, fn in rows:
        if not args.rerun and db.run_done(fn):
            continue
        if args.stop_file and os.path.exists(args.stop_file):
            print("stop file present; stopping")
            break
        try:
            run_one(args, db, unit, fn)
        except SystemExit as ex:
            if ex.code == 143:
                raise
            print("  error: %s" % ex)
        except Exception as ex:  # keep the batch going
            import traceback
            traceback.print_exc()
            db.put_run(fn, unit, "error: %r" % ex, "", "", 0, 0, 0, "", "")
    print("batch done in %.0fs; results in %s" % (time.time() - t0, os.path.join(args.out, "results.tsv")))
    return 0


def cmd_stats(args):
    """Summarise the runs of a trial database: yield, throughput and the winning move kinds."""
    import sqlite3
    db = sqlite3.connect(args.db)
    rows = db.execute("SELECT fn, unit, status, builds, secs, moves FROM runs ORDER BY at").fetchall()
    last = {}
    for r in rows:
        last[r[0]] = r  # the latest run of each function
    by, kinds = {}, {}
    builds = secs = 0
    for fn, unit, status, b, sec, mv in last.values():
        by[status] = by.get(status, 0) + 1
        builds += b or 0
        secs += sec or 0
        if status in ("exact", "improved", "improved-layout"):
            for m in (mv or "").split(" + "):
                if m:
                    k = m.split(" ")[0]
                    kinds.setdefault(k, [0, 0])[0 if status == "exact" else 1] += 1
    n = len(last)
    print("functions %d: %s" % (n, ", ".join("%s %d" % kv for kv in sorted(by.items()))))
    print("builds %d in %.2f h of search (%.0f variants/min)" % (builds, secs / 3600, 60 * builds / max(secs, 1)))
    hrs = secs / 3600
    print("exact per search-hour %.2f" % (by.get("exact", 0) / max(hrs, 1e-9)))
    print("trials recorded: %d" % db.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
    print("move kinds in winners (exact, improved):")
    for k, (a, b) in sorted(kinds.items(), key=lambda kv: -sum(kv[1])):
        print("  %-14s %d %d" % (k, a, b))
    return 0


def cmd_score(args):
    u = find_unit(args.unit)
    T = Target(u.target)
    sc = T.score_elf(Elf(os.path.join(ROOT, u.obj)), args.function, detail=True)
    print(sc.short())
    for d in sc.detail[:60]:
        print("  " + d)
    return 0


def cmd_try(args):
    """Score hand-written variant files of the unit's source (the tree is never written)."""
    from .search import Evaluator
    u = find_unit(args.unit)
    db = TrialDB(args.db)
    ev = Evaluator(u, args.function, 1, args.work, db)
    try:
        for p in args.files:
            text = open(p, encoding="utf-8", newline="").read()
            sc = ev.score(text, "try " + os.path.basename(p))
            print("%s: %s" % (os.path.basename(p), sc.short()))
            if args.detail and sc.ok:
                obj = os.path.join(ev.work, "try.o")
                if ev._compile(text, keep=obj)[0]:
                    for d in Target(u.target).score_elf(Elf(obj), args.function, detail=True).detail[:60]:
                        print("  " + d)
    finally:
        ev.close()
    return 0


def cmd_dbg(args):
    """Show which of our stack objects retail keeps elsewhere (needs the MWCC debugger)."""
    from . import dbgobj
    from .search import Evaluator
    from .elfscore import extract
    if not dbgobj.available():
        raise SystemExit("set MWCC_DEBUGGER and RETROWIN32 (see tools/mwcc-stack/README.md)")
    u = find_unit(args.unit)
    text = open(args.file or os.path.join(ROOT, u.rel_src), encoding="utf-8", newline="").read()
    ev = Evaluator(u, args.function, 1, args.work, TrialDB(args.db))
    d = dbgobj.Dumper(u.name, args.work)
    try:
        obj = os.path.join(ev.work, "dbg.o")
        p, err = ev._compile(text, keep=obj)
        if p is None:
            raise SystemExit("compile failed: " + err)
        objs = d.dump(text, args.function)
        if objs is None:
            raise SystemExit("debugger dump failed; see %s/dump.log" % d.work)
        lay = dbgobj.layout(objs, extract(Elf(obj), args.function), Target(u.target).funcs[args.function])
        if lay is None:
            raise SystemExit("instruction counts differ; the layout map needs equal lengths")
        lay.webs = d.last_webs
        lay.key = lay.key[:3] + (lay.webs,)
        print("\n".join(dbgobj.describe(lay)))
        print("dump %.1fs" % d.secs)
    finally:
        d.close()
        ev.close()
    return 0


def cmd_moves(args):
    u = find_unit(args.unit)
    text = open(os.path.join(ROOT, u.rel_src), encoding="utf-8", newline="").read()
    info = M.parse_demangled(demangle(args.function))
    mv = M.generate(text, info)
    print("%d moves: %s" % (len(mv), ", ".join("%s %d" % kv for kv in sorted(_count(m.kind for m in mv).items()))))
    for m in mv:
        if args.kind and m.kind not in args.kind.split(","):
            continue
        print("  [%s] %s" % (m.kind, m.desc))
        if args.verbose:
            for s_, e_, r in m.edits:
                print("      %r -> %r" % (text[s_:e_][:160], r[:240]))
    return 0


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, errors="replace", **kw)


def cmd_apply(args):
    """Apply a patch in this worktree and run the full verification."""
    head = open(args.patch, encoding="utf-8").read()
    m = re.search(r"^# unit: (\S+)", head, re.M)
    unit = args.unit or (m.group(1) if m else None)
    if not unit:
        raise SystemExit("patch has no '# unit:' line; pass --unit")
    u = find_unit(unit)
    short = u.name.split("/", 1)[1] if u.name.startswith("mario/") else u.name
    before_order = sh("NM=build/binutils/powerpc-eabi-nm build/venv/bin/python3 tools/validate-symbol-order.py "
                      "-u %s --map orig/GMSE01/files/marioUS.MAP" % u.name).stdout
    r = sh("git apply --check %s && git apply %s" % (args.patch, args.patch))
    if r.returncode:
        print("git apply failed:\n" + r.stderr)
        return 1
    ok = True
    print("== applied %s to %s" % (os.path.basename(args.patch), u.rel_src))
    r = sh("build/venv/bin/ninja -k 0", timeout=7200)
    print("== ninja -k 0: exit %d" % r.returncode)
    if r.returncode:
        ok = False
        print("\n".join(l for l in (r.stdout + r.stderr).splitlines() if "rror" in l or "FAILED" in l)[:2000])
    sha = sh("sha1sum build/GMSE01/mario.dol").stdout.split()[0] if os.path.exists(
        os.path.join(ROOT, "build/GMSE01/mario.dol")) else "?"
    want = open(os.path.join(ROOT, "config/GMSE01/build.sha1")).read().split()[0]
    print("== DOL sha1 %s" % ("OK" if sha == want else "MISMATCH " + sha))
    ok &= sha == want
    r = sh("build/venv/bin/ninja changes_all", timeout=3600)
    regs, n = [], 0
    for line in r.stdout.splitlines():
        if "->" in line and "|" in line:
            n += 1
            parts = line.split("|")
            if len(parts) >= 3:
                a, _, b = parts[2].partition("->")
                try:
                    if float(b.strip().rstrip("%")) < float(a.strip().rstrip("%")):
                        regs.append(line.strip())
                except ValueError:
                    pass
    print("== changes_all: %d changed lines, %d regressions" % (n, len(regs)))
    for l in [l for l in r.stdout.splitlines() if "->" in l][:20]:
        print("   " + l.strip())
    ok &= not regs
    after_order = sh("NM=build/binutils/powerpc-eabi-nm build/venv/bin/python3 tools/validate-symbol-order.py "
                     "-u %s --map orig/GMSE01/files/marioUS.MAP" % u.name).stdout

    def order_bad(out):
        return len([l for l in out.splitlines() if re.match(r"\s*(-|MISSING|!)", l)]), \
            "\n".join(l for l in out.splitlines() if "RESULT" in l)
    b0, rb = order_bad(before_order)
    b1, ra = order_bad(after_order)
    print("== symbol order: before %d issue lines (%s), after %d (%s)" % (b0, rb.strip(), b1, ra.strip()))
    ok &= b1 <= b0
    print("== %s" % ("VERIFIED" if ok else "FAILED"))
    if not ok and args.revert:
        sh("git apply -R %s" % args.patch)
        sh("build/venv/bin/ninja build/GMSE01/%s" % u.obj.split("build/GMSE01/", 1)[-1])
        print("== reverted")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="python3 -m tools.hsearch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--budget", type=float, default=900.0, help="seconds per function (default 900)")
        p.add_argument("-j", "--jobs", type=int, default=2)
        p.add_argument("--dbg", type=int, default=0, metavar="K",
                       help="debugger-guided layout objective: dump the K best candidates per level "
                            "(needs MWCC_DEBUGGER and RETROWIN32; 0 = off)")
        p.add_argument("--db", default=DEFAULT_DB, help="trial database (default %(default)s)")
        p.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "hsearch"),
                       help="patches and results.tsv")
        p.add_argument("--work", default=tempfile.gettempdir(), help="parent for shadow roots")
        p.add_argument("--seed", type=int, default=0)

    p = sub.add_parser("run", help="search one function")
    p.add_argument("-u", "--unit", required=True)
    p.add_argument("-f", "--function", required=True)
    common(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("batch", help="search every (unit, function) row of a TSV; resumable")
    p.add_argument("--list", required=True)
    p.add_argument("--rerun", action="store_true", help="search functions that already have a run")
    p.add_argument("--stop-file", help="stop before the next function when this file exists")
    common(p)
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("stats", help="summarise the runs in a trial database")
    p.add_argument("--db", default=DEFAULT_DB)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("score", help="score the current build of one function")
    p.add_argument("-u", "--unit", required=True)
    p.add_argument("-f", "--function", required=True)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("try", help="score hand-written variants of the unit's source file")
    p.add_argument("-u", "--unit", required=True)
    p.add_argument("-f", "--function", required=True)
    p.add_argument("files", nargs="+")
    p.add_argument("--detail", action="store_true", help="list the differing instructions")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--work", default=tempfile.gettempdir())
    p.set_defaults(func=cmd_try)

    p = sub.add_parser("dbg", help="map our stack objects to retail's offsets with the MWCC debugger")
    p.add_argument("-u", "--unit", required=True)
    p.add_argument("-f", "--function", required=True)
    p.add_argument("--file", help="a variant of the unit's source (default: the source itself)")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--work", default=tempfile.gettempdir())
    p.set_defaults(func=cmd_dbg)

    p = sub.add_parser("moves", help="list the honest moves for one function")
    p.add_argument("-u", "--unit", required=True)
    p.add_argument("-f", "--function", required=True)
    p.add_argument("-k", "--kind")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_moves)

    p = sub.add_parser("apply", help="apply a patch here and run the full verification")
    p.add_argument("patch")
    p.add_argument("--unit")
    p.add_argument("--revert", action="store_true", help="undo the patch when verification fails")
    p.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
