"""Debugger-guided layout objective.

A variant's instructions can all match retail while its stack objects sit in
the wrong places.  The in-process score only counts the instructions whose
`r1` offset differs; this module names the objects.  It dumps MWCC's own stack
objects for one variant with the MWCC debugger (tools/mwcc-stack, GC/1.1 with
the unit's flags, whose layout equals our GC/1.2.5 for the functions measured
so far), maps every `r1` access of our compiled function to the object that
holds it, and reads retail's offset for the same access from the aligned
retail instruction.  That gives, per object, where retail keeps it, and so the
order and the gaps retail implies.

The distance (lower is better, all zero when every mapped object is where
retail has it):
  1. out of order: objects that must move for our top-down order to become
     retail's (the count outside a longest common order);
  2. gap words: words missing or extra between consecutive mapped objects,
     plus above the first and below the last (a uniform shift below one
     object is one region);
  3. misplaced: mapped objects whose offset differs.

Dumps take 7-20 s each and one debugger runs at a time (the gdb stub's port is
shared), so the search asks for them only for its best candidates.
"""

import bisect
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .elfscore import Func, MEM_D, PSQ, _frame

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "mwcc-stack"))
import regsweep  # noqa: E402
import iro  # noqa: E402

_ONE = threading.Lock()  # one debugger at a time in this process


@dataclass
class Obj:
    name: str
    lo: int
    hi: int
    kind: str = "named"


@dataclass
class Layout:
    """Where our objects are and where retail keeps them."""
    objs: List[Obj]
    retail: Dict[str, int]  # object name -> retail base offset (mapped objects only)
    coverage: float  # fraction of our r1 accesses that fell inside a dumped object
    frame_t: int
    frame_o: int
    key: Tuple[int, ...] = (0, 0, 0, 0)
    regions: List[str] = field(default_factory=list)
    webs: int = 0  # register webs coloured differently from retail (regalloc.py, GPR + FPR)

    def short(self) -> str:
        return "order %d gap %d misplaced %d webs %d" % self.key


def available() -> bool:
    return bool(os.environ.get("MWCC_DEBUGGER") and os.environ.get("RETROWIN32"))


def parse_variables(path: str) -> List[Obj]:
    out, sec = [], None
    for line in open(path, encoding="latin-1"):
        s = line.rstrip()
        if s.strip().endswith(":"):
            sec = s.strip()
            continue
        m = re.search(r"r1\+0x([0-9a-f]+)-0x([0-9a-f]+)\s+(\S+)\s*$", s)
        if m and "->" not in s.split("r1+")[0]:
            out.append(Obj(m.group(3), int(m.group(1), 16), int(m.group(2), 16),
                           "arg" if sec == "arguments:" else "named"))
        elif sec == "arguments:":
            m = re.search(r"r1\+0x([0-9a-f]+)-0x([0-9a-f]+)\s+\S+\s+->\s+\S+\s+(\S+)\s*$", s)
            if m:
                out.append(Obj(m.group(3), int(m.group(1), 16), int(m.group(2), 16), "arg"))
    return out


def object_kinds(dumpdir: str) -> Dict[str, str]:
    """named / inline / IRO kind (F, P, S, ...) per object, from a patched dump's names.txt."""
    kinds = {}
    p = os.path.join(dumpdir, "names.txt")
    if not os.path.exists(p):
        return kinds
    for line in open(p):
        f = line.split()
        if len(f) < 3:
            continue
        chain = [int(x, 16) for x in f[2:]]
        if f[1] == "iro":
            k = iro.kind_of(chain)
            if k:
                kinds[f[0]] = k
        elif chain and chain[0] == iro.TEMP_OBJECT_RET:
            kinds[f[0]] = "inline"
    return kinds


class Dumper:
    """Runs the debugger on variant texts of one unit."""

    def __init__(self, unit: str, work: str, timeout: int = 300):
        short = unit.split("/", 1)[1] if unit.startswith("mario/") else unit
        self.src, self.flags, self.prefix = regsweep.unit_flags(short)
        self.unit = "mario/" + short
        self.last_webs = -1
        self.work = tempfile.mkdtemp(prefix="hsdbg-", dir=work)
        self.dumps = 0
        self.secs = 0.0
        self.extra = ""  # includes GC/1.1 needs ahead of the unit (docs/catalog/register-model.md, c-f1)
        self.last_error = ""
        self.timeout = timeout  # seconds per debugger run (setupObjects needs about 7 minutes)
        self.pre_lines = 0  # lines prepended to the variant text in the last dump

    def close(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def webs(self, dumpdir: str, sym: str) -> int:
        """Webs whose register differs from retail, by regalloc.py's replay (-1 if unknown)."""
        n = 0
        for cls in ("gpr", "fpr"):
            if not os.path.exists(os.path.join(dumpdir, "regalloc-%s-pass-1-all.txt" % cls)):
                continue
            try:
                p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "mwcc-stack", "regalloc.py"),
                                    dumpdir, self.unit, sym, cls], capture_output=True, text=True,
                                   timeout=120, cwd=ROOT)
            except subprocess.TimeoutExpired:
                return -1
            m = re.search(r"differing (\d+) of (\d+) mapped", p.stdout + p.stderr)
            if not m:
                return -1
            n += int(m.group(1))
        return n

    def dump(self, text: str, sym: str) -> Optional[List[Obj]]:
        import time
        body = text
        tmp = os.path.join(self.work, os.path.basename(self.src))
        out = os.path.join(self.work, "dump")
        objdir = os.path.join(self.work, "obj")
        compiler = "build/compilers/GC/1.1/mwcceppc.exe"
        args = "%s %s -c %s -o %s/" % (compiler, " ".join(_q(a) for a in self.flags), tmp, objdir)
        t0 = time.time()
        with _ONE:
            for attempt in range(3):
                text = self.extra + body
                if self.prefix:
                    text = "#include <%s>\n" % re.sub(r".mch$", ".pch", self.prefix) + text
                self.pre_lines = text[:len(text) - len(body)].count("\n")
                with open(tmp, "wb") as f:
                    f.write(text.encode("shift_jis", errors="ignore"))
                shutil.rmtree(out, ignore_errors=True)
                shutil.rmtree(objdir, ignore_errors=True)
                os.makedirs(objdir)
                with open(out + ".log", "w") as log:
                    try:
                        subprocess.run([sys.executable, os.environ["MWCC_DEBUGGER"], "-e",
                                        os.environ["RETROWIN32"], "-a", args, sym, out],
                                       stdout=log, stderr=subprocess.STDOUT, timeout=self.timeout, cwd=ROOT)
                    except subprocess.TimeoutExpired:
                        pass
                objs = load(out)
                if objs is not None:
                    self.last_webs = self.webs(out, sym)
                    self.dumps += 1
                    self.secs += time.time() - t0
                    return objs
                log = open(out + ".log", encoding="latin-1").read()
                err = [l for l in log.splitlines() if "Error" in l or "illegal" in l or "undefined" in l]
                self.last_error = " | ".join(err[:3])
                if "incomplete struct/union/class" in log and not self.extra:
                    self.extra = ("#include <JSystem/J3D/J3DGraphAnimator/J3DModel.hpp>\n"
                                  "#include <JSystem/J3D/J3DGraphAnimator/J3DJoint.hpp>\n")
                    continue
                if not re.search(r"could not connect|Remote connection closed", log):
                    break
                time.sleep(20)
        self.secs += time.time() - t0
        return None


def load(dumpdir: str) -> Optional[List[Obj]]:
    """The objects of a kept dump (as Dumper.dump returns them)."""
    vp = os.path.join(dumpdir, "variables.txt")
    if not os.path.exists(vp):
        return None
    objs = parse_variables(vp)
    kinds = object_kinds(dumpdir)
    for o in objs:
        if o.kind != "arg":
            o.kind = kinds.get(o.name, "inline" if o.name.startswith("@") else "named")
    return objs


def statement_lines(dumpdir: str, pre_lines: int = 0) -> Dict[str, Tuple[int, str]]:
    """Per object, the source line of the first statement that references it, and its type.

    Read from the front end's initial code (frontend-00), whose statements carry
    the line of the variant text the debugger compiled; `pre_lines` (the lines
    Dumper prepended) is subtracted so the line is the unit source's.  With a
    patched debugger (tools/mwcc-stack/patch-debugger.py) the IR optimiser's F
    and P temporaries get iro.py's line too ("~" marks an approximate pairing).
    Objects an
    inlined body creates carry the line of the caller's statement that expanded
    it, so this names the statement whose depth a stray word belongs to.
    """
    out: Dict[str, Tuple[int, str]] = {}
    p = os.path.join(dumpdir, "frontend-00-ast-initial-code.txt")
    if not os.path.exists(p):
        return out
    line = 0
    for s in open(p, encoding="latin-1"):
        m = re.match(r"^(\d+) ST_", s)
        if m:
            line = int(m.group(1)) - pre_lines
        for name, typ in re.findall(r"EOBJREF \[([^\]\s]+)\] (.*)", s):
            if name not in out:
                out[name] = (line, typ.strip())
    # the IR optimiser's forced-load and comma temporaries (a patched debugger's names.txt)
    if os.path.exists(os.path.join(dumpdir, "names.txt")):
        try:
            fp, exact = iro.fp_lines(dumpdir)
        except (OSError, IndexError, ValueError):
            fp, exact = {}, True
        for name, (st, t) in fp.items():
            if name not in out and st and st.isdigit():
                out[name] = (int(st) - pre_lines, ("" if exact else "~") + t)
    # objects the back end made (int/float conversion temporaries, spills) have no statement
    vp = os.path.join(dumpdir, "variables.txt")
    sec = ""
    for s in open(vp, encoding="latin-1") if os.path.exists(vp) else ():
        if s.strip().endswith(":"):
            sec = s.strip()[:-1]
            continue
        m = re.search(r"r1\+0x[0-9a-f]+-0x[0-9a-f]+\s+(\S+)\s*$", s)
        if m and m.group(1) not in out and sec in ("temps", "spills"):
            out[m.group(1)] = (0, "(back-end %s)" % sec[:-1])
    return out


def describe_lines(lay: Layout, lines: Dict[str, Tuple[int, str]]) -> List[str]:
    """describe() with, per object, retail's displacement in words and the statement line."""
    rows = ["frame retail %#x ours %#x; %d%% of r1 accesses mapped; %s" % (
        lay.frame_t, lay.frame_o, round(100 * lay.coverage), lay.short()),
        "  %-10s %-6s %-6s %-6s %5s  %-6s %s" % ("object", "kind", "ours", "retail", "words", "line", "type")]
    for o in sorted(lay.objs, key=lambda o: -o.lo):
        if o.hi <= o.lo:
            continue
        t = lay.retail.get(o.name)
        ln, typ = lines.get(o.name, (0, ""))
        rows.append("  %-10s %-6s %#06x %-6s %5s  %-6s %s" % (
            o.name, o.kind, o.lo, "%#06x" % t if t is not None else "-",
            "%+d" % ((t - o.lo) // 4) if t is not None and t != o.lo else "",
            "L%d" % ln if ln > 0 else "?", typ[:70]))
    rows += ["  " + r for r in lay.regions]
    return rows


def _q(a: str) -> str:
    return a if re.fullmatch(r"[\w./,=+:-]+", a) else "'" + a.replace("'", "'\\''") + "'"


def _disp(w: int) -> Optional[int]:
    """The r1 displacement of a D-form access (or addi rD, r1, d), else None."""
    p = w >> 26
    if p not in MEM_D or (w >> 16) & 0x1F != 1:
        return None
    if p in PSQ:
        d = w & 0xFFF
        return d - 0x1000 if d & 0x800 else d
    d = w & 0xFFFF
    return d - 0x10000 if d & 0x8000 else d


def _lds(seq: List[int]) -> int:
    """Length of the longest strictly decreasing subsequence."""
    tails: List[int] = []
    for x in seq:
        y = -x
        i = bisect.bisect_left(tails, y)
        if i == len(tails):
            tails.append(y)
        else:
            tails[i] = y
    return len(tails)


def layout(objs: List[Obj], ours: Func, retail: Func) -> Optional[Layout]:
    """Map our objects to retail offsets through the aligned r1 accesses."""
    if len(ours.words) != len(retail.words):
        return None
    ft, fo = _frame(retail.words), _frame(ours.words)
    stack = sorted((o for o in objs if o.hi > o.lo), key=lambda o: o.lo)
    los = [o.lo for o in stack]
    votes: Dict[str, Counter] = {}
    n_acc = n_in = 0
    for a, b in zip(retail.words, ours.words):
        t, o = _disp(a), _disp(b)
        if t is None or o is None or a >> 26 != b >> 26:
            continue
        if o >= fo or t >= ft:  # the caller's frame (the saved LR)
            continue
        if stack and o >= max(x.hi for x in stack):
            continue  # the register save area above the locals
        n_acc += 1
        i = bisect.bisect_right(los, o) - 1
        if i < 0 or not (stack[i].lo <= o < stack[i].hi):
            continue  # the register save area, or a word no dumped object covers
        n_in += 1
        ob = stack[i]
        votes.setdefault(ob.name, Counter())[t - (o - ob.lo)] += 1
    retail_at = {n: c.most_common(1)[0][0] for n, c in votes.items()}
    lay = Layout(objs, retail_at, n_in / n_acc if n_acc else 1.0, ft, fo)
    mapped = sorted((o for o in stack if o.name in retail_at), key=lambda o: -o.lo)
    if not mapped:
        return lay
    T = [retail_at[o.name] for o in mapped]
    out_of_order = len(T) - _lds(T)
    misplaced = sum(1 for o in mapped if retail_at[o.name] != o.lo)
    gap = 0
    regions = []
    top = abs((ft - T[0]) - (fo - mapped[0].lo))
    if top:
        regions.append("%+d words above %s" % (((fo - mapped[0].lo) - (ft - T[0])) // 4, mapped[0].name))
    gap += top
    for x, y, tx, ty in zip(mapped, mapped[1:], T, T[1:]):
        d = (tx - ty) - (x.lo - y.lo)
        if d:
            regions.append("%+d words between %s and %s (%s)" % (-d // 4, x.name, y.name, y.kind))
        gap += abs(d)
    bottom = T[-1] - mapped[-1].lo
    if bottom:
        regions.append("%+d words below %s" % (-bottom // 4, mapped[-1].name))
    gap += abs(bottom)
    lay.key = (out_of_order, gap // 4, misplaced, 0)
    lay.regions = regions
    return lay


def describe(lay: Layout) -> List[str]:
    """Our objects top-down with retail's offset for each mapped one."""
    rows = ["frame retail %#x ours %#x; %d%% of r1 accesses mapped; %s" % (
        lay.frame_t, lay.frame_o, round(100 * lay.coverage), lay.short())]
    for o in sorted(lay.objs, key=lambda o: -o.lo):
        if o.hi <= o.lo:
            continue
        t = lay.retail.get(o.name)
        rows.append("  %-10s %-6s ours %#06x-%#06x  retail %s" % (
            o.name, o.kind, o.lo, o.hi, "%#06x" % t + ("" if t == o.lo else "  *") if t is not None else "-"))
    rows += ["  " + r for r in lay.regions]
    return rows
