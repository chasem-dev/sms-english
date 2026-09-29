#!/usr/bin/env python3
"""Score the other tree's units whole, in their own header context.

For every unit with a non-exact function, compile the other tree's copy of
the unit with our unit's compiler flags (their include paths, no prefix
header, a region define of their choice) and score each of our non-exact
functions in that object against our retail target. This is the ceiling a
port of their body can reach without their headers: when it is not above
our score, porting that function is not worth a header change.

    python3 tools/transplant/context.py --other PATH --out ctx.tsv [-j 2]
        [--region GMSJ01] [--unit Enemy/seal]

Rows: unit fn ours ctx delta note
"""

import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import transplant as T  # noqa: E402

ROOT = T.ROOT


def their_command(obj: str, their_rel: str, outdir: str, region: str) -> str:
    cmd = T.ninja_db().compile_command(obj, their_rel, outdir)
    cmd = re.sub(r"(^|\s)build/(tools|compilers)/", lambda m: m.group(1) + os.path.join(ROOT, "build", m.group(2)) + "/", cmd)
    cmd = re.sub(r"\s-i \S+", "", cmd)
    cmd = cmd.replace(" -prefix SMS.mch", "")
    cmd = re.sub(r"-DVERSION_GMS\w\d\d", "-DVERSION_" + region, cmd)
    inc = " -i include -i include/PowerPC_EABI_Support/Msl/MSL_C/MSL_Common" \
          " -i include/PowerPC_EABI_Support/Msl/MSL_C++/MSL_Common"
    cmd = cmd.replace(" -DBUILD_VERSION", inc + " -DBUILD_VERSION", 1)
    cmd = cmd.replace("-maxerrors 1", "-maxerrors 3")
    return cmd


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--other", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--region", default="GMSJ01")
    ap.add_argument("-j", type=int, default=2)
    ap.add_argument("--unit", action="append", default=[])
    ap.add_argument("--report", default=os.path.join(ROOT, "build", "GMSE01", "report.json"))
    args = ap.parse_args()
    work = tempfile.mkdtemp(prefix="tpctx-")
    rep = json.load(open(args.report))
    od = {u["name"]: u for u in json.load(open(os.path.join(ROOT, "objdiff.json")))["units"]}
    lock = threading.Lock()
    with open(args.out, "w") as f:
        f.write("unit\tfn\tours\tctx\tdelta\tnote\n")

    def sink(rows):
        with lock:
            with open(args.out, "a") as f:
                for r in rows:
                    f.write("\t".join(r) + "\n")

    def job(u):
        name = u["name"]
        short = name.split("/", 1)[1]
        fns = [f for f in u.get("functions", []) if f.get("fuzzy_match_percent", 0) < 100]
        o = od[name]
        rel = u["metadata"]["source_path"]
        tp = T.their_path(args.other, rel)
        base = T.objdiff_scores(os.path.join(ROOT, o["target_path"]), os.path.join(ROOT, o["base_path"]), work)
        rows = []
        if not tp:
            for f in fns:
                rows.append([short, f["name"], "%.3f" % base.get(f["name"], 0.0), "missing", "", "no unit"])
            sink(rows)
            return
        outdir = tempfile.mkdtemp(dir=work)
        their_rel = os.path.relpath(tp, args.other)
        cmd = their_command(o["base_path"], their_rel, outdir, args.region)
        r = subprocess.run(cmd, shell=True, cwd=args.other, capture_output=True, text=True, errors="replace")
        objf = os.path.join(outdir, os.path.splitext(os.path.basename(tp))[0] + ".o")
        if r.returncode or not os.path.exists(objf):
            msg = [l.strip("# ").strip() for l in (r.stdout + r.stderr).splitlines() if l.strip()]
            err = " ".join(msg[-4:])[:200]
            for f in fns:
                rows.append([short, f["name"], "%.3f" % base.get(f["name"], 0.0), "unit-fail", "", err])
            sink(rows)
            return
        sc = T.objdiff_scores(os.path.join(ROOT, o["target_path"]), objf, work)
        for f in fns:
            b = base.get(f["name"], f.get("fuzzy_match_percent", 0.0))
            c = sc.get(f["name"])
            if c is None:
                rows.append([short, f["name"], "%.3f" % b, "nosym", "", ""])
            else:
                rows.append([short, f["name"], "%.3f" % b, "%.3f" % c, "%+.3f" % (c - b), ""])
        sink(rows)

    units = [u for u in rep["units"] if u["name"] in od and
             any(f.get("fuzzy_match_percent", 0) < 100 for f in u.get("functions", [])) and
             (not args.unit or u["name"].split("/", 1)[1] in args.unit)]
    with cf.ThreadPoolExecutor(args.j) as ex:
        for fu in cf.as_completed([ex.submit(job, u) for u in units]):
            try:
                fu.result()
            except Exception as e:  # keep going
                print("unit failed:", e, flush=True)


if __name__ == "__main__":
    main()
