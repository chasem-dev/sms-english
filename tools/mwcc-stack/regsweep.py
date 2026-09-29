#!/usr/bin/env python3
"""Batch register-colouring triage: dump each function with the MWCC debugger
and run regalloc.py --search on its GPR and FPR webs.

    tools/mwcc-stack/regsweep.py LIST OUTDIR [--search N] [--resume]
    tools/mwcc-stack/regsweep.py LIST OUTDIR --report

LIST is a TSV whose first two columns are the unit (Enemy/igaiga) and the
mangled symbol; other columns are ignored.  Run from the repo root after a
full build, with MWCC_DEBUGGER set. Use --native for Linux GDB/Wibo,
or set RETROWIN32 for the remote emulator path.

Each function's compile flags are the unit's own (taken from `ninja -t
commands`), run through GC/1.1 as dbg.sh does.  A `-prefix X.mch` unit gets
`#include <X.pch>` prepended, and the source is converted to Shift-JIS (the
real build does that through sjiswrap).  Functions are dumped one at a time.

Per function it writes OUTDIR/<n>/ (the dump), OUTDIR/<n>.{gpr,fpr}.txt and a
row of OUTDIR/summary.tsv:
  n unit symbol frame dlines swaps gpr fpr fix warn
frame is `=` when the stwu frame equals retail (else ours/retail), dlines the
differing diff rows, swaps how many of them are commutative operand swaps
(same registers, other order: not colouring).  gpr/fpr are `differing/mapped`
webs.  fix names the single web moves (`r40 name 12->0`) that make the replay
miss nothing, for every class with differing webs (`-` if none does), and
`[left/degree]` of the first one: its remaining degree when the first simplify
sweep reaches it (regalloc.py --why).  A web at or near K there is a deferral
case: it moves by gaining or losing a lower-numbered neighbour.
--report re-reads the outputs and prints the rows ranked: a single-move fix
with the frame equal first.
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REGALLOC = os.path.join(HERE, "regalloc.py")
NINJA = os.environ.get("NINJA", "build/venv/bin/ninja")


def unit_flags(unit):
    """(source path, flags, prefix header) from the unit's ninja command."""
    cmd = subprocess.run([NINJA, "-t", "commands", f"build/GMSE01/src/{unit}.o"],
                         capture_output=True, text=True).stdout.strip().splitlines()[-1]
    args = shlex.split(cmd.split(" && ")[0])
    i = next(k for k, a in enumerate(args) if a.endswith("mwcceppc.exe"))
    flags, src, prefix, k = [], None, None, i + 1
    while k < len(args):
        a = args[k]
        if a in ("-o",):
            k += 2
            continue
        if a == "-prefix":
            prefix = args[k + 1]
            k += 2
            continue
        if a == "-c":
            src = args[k + 1]
            k += 2
            continue
        if a in ("-MMD", "-MD"):
            k += 1
            continue
        flags.append(a)
        k += 1
    return src, flags, prefix


def dump(unit, sym, out, work, version):
    src, flags, prefix = unit_flags(unit)
    text = open(src, encoding="utf-8", errors="replace").read()
    if prefix:
        text = f"#include <{re.sub(r'.mch$', '.pch', prefix)}>\n" + text
    tmp = os.path.join(work, os.path.basename(src))
    with open(tmp, "wb") as f:
        f.write(text.encode("shift_jis", errors="ignore"))
    objdir = tempfile.mkdtemp(dir=work)
    compiler = f"build/compilers/GC/{version}/mwcceppc.exe"
    args = f"{compiler} {shlex.join(flags)} -c {tmp} -o {objdir}/"
    for attempt in range(3):  # the gdb stub's port is shared with other debugger runs
        with open(out + ".log", "w") as log:
            try:
                subprocess.run([sys.executable, os.environ["MWCC_DEBUGGER"], "-e",
                                os.environ["RETROWIN32"], "-a", args, sym, out],
                               stdout=log, stderr=subprocess.STDOUT, timeout=900)
            except subprocess.TimeoutExpired:
                log.write("regsweep: timeout\n")
        if os.path.exists(os.path.join(out, "variables.txt")):
            return True
        if not re.search(r"could not connect|Remote connection closed", open(out + ".log").read()):
            return False
        time.sleep(60)
    return False


def dump_native(unit, sym, out):
    src, _, _ = unit_flags(unit)
    if os.path.isdir(out) and os.listdir(out):
        # Preserve evidence from an incomplete earlier attempt before retrying.
        os.rename(out, out + ".failed-" + str(time.time_ns()))
    with open(out + ".log", "w") as log:
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(HERE, "native.py"), src, sym, out],
                stdout=log, stderr=subprocess.STDOUT, timeout=65)
        except subprocess.TimeoutExpired:
            log.write("regsweep: native timeout\n")
            return False
    return result.returncode == 0 and os.path.isfile(os.path.join(out, "variables.txt"))


ROW = re.compile(r"^([~<>| ])\s*[0-9a-f]* \| (.*?)\s* \| (.*)$")
REG = re.compile(r"\b[rf]\d+\b")


def diff_stats(unit, sym):
    out = subprocess.run([sys.executable, "tools/decomp-diff.py", "-u", f"mario/{unit}", "-d",
                          sym, "--no-collapse"], capture_output=True, text=True).stdout
    frame, dl, swaps = "?", 0, 0
    for line in out.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        mark, left, right = m.group(1), m.group(2).replace("{", "").replace("}", ""), \
            m.group(3).replace("{", "").replace("}", "")
        if left.startswith("stwu r1") and frame == "?":
            fl, fr = left.split("-")[-1].split("(")[0], right.split("-")[-1].split("(")[0]
            frame = "=" if fl == fr else f"{fr}/{fl}"
        if mark == " ":
            continue
        dl += 1
        ol, orr = left.split(None, 1), right.split(None, 1)
        if (len(ol) == 2 and len(orr) == 2 and ol[0] == orr[0] and left != right
                and sorted(REG.findall(left)) == sorted(REG.findall(right))
                and REG.sub("R", left) == REG.sub("R", right)):
            swaps += 1
    return frame, dl, swaps


def search(dumpdir, unit, sym, cls, n, out):
    p = subprocess.run([sys.executable, REGALLOC, dumpdir, f"mario/{unit}", sym, cls,
                        "--search", str(n)], capture_output=True, text=True, timeout=600)
    with open(out, "w") as f:
        f.write(p.stdout + p.stderr)
    return parse_search(p.stdout + p.stderr)


def parse_search(text):
    m = re.search(r"differing (\d+) of (\d+) mapped; replay of the current order misses (\d+)", text)
    if not m:
        return None
    fixes = [f"{mv.group(1)} {mv.group(2)} {mv.group(3)}->{mv.group(4)}" for mv in
             re.finditer(r"move (\S+) (\S*) from (\d+) to (\d+): misses 0", text)]
    warn = [w for w in ("pcode rows", "simplify replay differs") if w in text]
    return {"diff": int(m.group(1)), "mapped": int(m.group(2)), "fixes": fixes, "warn": warn}


def why(dumpdir, unit, sym, cls, fix):
    """`left/degree` of the first fixing web at its first-sweep turn."""
    v = re.match(r"[rf](\d+)", fix).group(1)
    p = subprocess.run([sys.executable, REGALLOC, dumpdir, f"mario/{unit}", sym, cls, "--why", v],
                       capture_output=True, text=True, timeout=600).stdout
    m = re.search(r"degree (\d+), (\d+) left", p)
    return f" [{m.group(2)}/{m.group(1)}]" if m else ""


def summarise(n, unit, sym, frame, dl, swaps, res, base=None):
    cells, fix, warn = [], [], []
    for cls in ("gpr", "fpr"):
        r = res.get(cls)
        if r is None:
            cells.append("err")
            continue
        cells.append(f"{r['diff']}/{r['mapped']}")
        warn += r["warn"]
        if r["diff"]:
            w = why(base, unit, sym, cls, r["fixes"][0]) if r["fixes"] and base else ""
            fix.append(", ".join(r["fixes"][:3]) + w if r["fixes"] else "-")
    return "\t".join([str(n), unit, sym, frame, str(dl), str(swaps), *cells,
                      "; ".join(fix) or "none", ",".join(sorted(set(warn)))])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("list")
    ap.add_argument("outdir")
    ap.add_argument("--search", type=int, default=60)
    ap.add_argument("--version", default="1.1")
    ap.add_argument("--native", action="store_true",
                    help="dump through native Linux GDB/Wibo instead of a remote emulator")
    ap.add_argument("--resume", action="store_true",
                    help="skip functions already dumped (re-runs failed dumps)")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.native and a.version != "1.1":
        ap.error("native object decoding supports GC/1.1 only")
    os.makedirs(a.outdir, exist_ok=True)
    work = os.path.join(a.outdir, "work")
    os.makedirs(work, exist_ok=True)
    todo = [l.rstrip("\n").split("\t")[:2] for l in open(a.list) if l.strip()]
    rows = []
    for n, (unit, sym) in enumerate(todo):
        base = os.path.join(a.outdir, str(n))
        if a.report:
            res = {}
            for cls in ("gpr", "fpr"):
                if os.path.exists(f"{base}.{cls}.txt"):
                    res[cls] = parse_search(open(f"{base}.{cls}.txt").read())
            if not res:
                continue
            frame, dl, swaps = diff_stats(unit, sym)
            rows.append(summarise(n, unit, sym, frame, dl, swaps, res, base))
            continue
        if a.resume and os.path.exists(f"{base}/variables.txt"):
            continue
        ok = dump_native(unit, sym, base) if a.native else dump(unit, sym, base, work, a.version)
        frame, dl, swaps = diff_stats(unit, sym)
        res = {cls: search(base, unit, sym, cls, a.search, f"{base}.{cls}.txt") if ok else None
               for cls in ("gpr", "fpr")}
        line = summarise(n, unit, sym, frame, dl, swaps, res, base)
        with open(os.path.join(a.outdir, "summary.tsv"), "a") as f:
            f.write(line + "\n")
        print(line, flush=True)
    if a.report:
        def key(r):
            c = r.split("\t")
            fixed = c[8] not in ("none",) and "-" not in c[8].split("; ") and c[6] != "err"
            return (not fixed, c[3] != "=", int(c[4]) - int(c[5]))
        for r in sorted(rows, key=key):
            print(r)


if __name__ == "__main__":
    main()
