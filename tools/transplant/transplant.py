#!/usr/bin/env python3
"""Score function bodies from another Sunshine decomp tree against our build.

For every non-exact function of our report (or the functions given), find
its definition in the other tree's copy of the same unit, splice that
definition over ours in a shadow copy of the unit, compile the unit with its
own ninja command, and score the function against retail with objdiff-cli.
The source tree is never written.

    python3 tools/transplant/transplant.py --other PATH --out results.tsv [-j 2]
        [--unit Enemy/seal] [--fn SUBSTR] [--rerun]

Bodies that fail to compile get a few automatic fixes: a member missing from
our headers is renamed to our member declared at the same /* 0xNN */ offset
of the same class (or a class holding a field of that name in the other
tree), and a parameterless accessor is replaced by ours that returns the
same offset. Region selections written for the other regions are tried in
each spelling (as written, JP branch, PAL branch); the best one is kept.

Rows (TSV, appended as each function finishes, so a rerun resumes):
    unit fn ours theirs delta status others variant fixes note
status: better / same / worse / needs-port / missing / header / ident
(ident: the bodies are textually identical once comments and spacing go).
"""

import argparse
import concurrent.futures as cf
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import cxxsrc as C  # noqa: E402

OBJDIFF = os.path.join(ROOT, "build", "tools", "objdiff-cli")


def _load_ls():
    spec = importlib.util.spec_from_file_location("lever_search", os.path.join(ROOT, "tools", "lever-search.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LS = _load_ls()


# ---------------------------------------------------------------------------
# class index: fields by /* 0xNN */ offset, bases, simple accessors
# ---------------------------------------------------------------------------

FIELD_RX = re.compile(r"/\*\s*0x([0-9A-Fa-f]+)\s*\*/\s*([^;{}()]*?)\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])*\s*(?::\s*\d+\s*)?;")
ACC_RX = re.compile(r"\b([A-Za-z_]\w*)\s*\(\s*\)\s*(?:const\s*)?\{\s*return\s+(?:this->)?([A-Za-z_]\w*)\s*;\s*\}")


class ClassIndex:
    def __init__(self, files: List[str]):
        self.fields: Dict[str, Dict[str, int]] = {}   # class -> name -> offset
        self.byoff: Dict[str, Dict[int, List[str]]] = {}
        self.bases: Dict[str, List[str]] = {}
        self.acc: Dict[str, Dict[str, str]] = {}      # class -> accessor -> field
        self.owners: Dict[str, List[str]] = {}        # field name -> classes
        for f in files:
            try:
                self.add(open(f, encoding="utf-8", errors="replace").read())
            except OSError:
                pass

    def add(self, text: str):
        m = C.mask(text)
        sc = C.scopes(text, m)
        for s in sc:
            if s.kind != "class":
                continue
            inner = [x for x in sc if s.open < x.open and x.close < s.close]
            head = m[max(0, m.rfind(";", 0, s.open), m.rfind("}", 0, s.open)) + 1:s.open]
            bm = re.search(r"\b" + re.escape(s.name) + r"\b[^:]*:([^{]*)$", head)
            if bm and not bm.group(1).startswith(":"):
                bases = [re.sub(r"<.*", "", re.sub(r"\b(public|private|protected|virtual)\b", "", b)).strip()
                         for b in C.split_params(bm.group(1))]
                bases = [b.split("::")[-1] for b in bases if b]
                if bases:
                    self.bases.setdefault(s.name, bases)
            body = list(text[s.open + 1:s.close])
            for x in inner:  # blank nested scopes (method bodies, nested classes)
                for k in range(x.open - s.open, x.close - s.open):
                    if 0 <= k < len(body) and body[k] != "\n":
                        body[k] = " "
            btxt = "".join(body)
            fd = self.fields.setdefault(s.name, {})
            bo = self.byoff.setdefault(s.name, {})
            for fm in FIELD_RX.finditer(btxt):
                off, name = int(fm.group(1), 16), fm.group(3)
                if name in ("const", "volatile"):
                    continue
                if name not in fd:
                    fd[name] = off
                    bo.setdefault(off, []).append(name)
                    self.owners.setdefault(name, []).append(s.name)
            ad = self.acc.setdefault(s.name, {})
            for am in ACC_RX.finditer(text[s.open:s.close]):
                ad.setdefault(am.group(1), am.group(2))

    def chain(self, cls: str, depth: int = 0) -> List[str]:
        out = [cls]
        if depth < 12:
            for b in self.bases.get(cls, []):
                out += self.chain(b, depth + 1)
        return out

    def field_off(self, cls: str, name: str) -> Optional[int]:
        for c in self.chain(cls):
            if name in self.fields.get(c, {}):
                return self.fields[c][name]
        return None

    def names_at(self, cls: str, off: int) -> List[str]:
        out = []
        for c in self.chain(cls):
            out += self.byoff.get(c, {}).get(off, [])
        return out

    def has_member(self, cls: str, name: str) -> bool:
        return any(name in self.fields.get(c, {}) or name in self.acc.get(c, {}) for c in self.chain(cls))

    def accessor_field(self, cls: str, name: str) -> Optional[str]:
        for c in self.chain(cls):
            if name in self.acc.get(c, {}):
                return self.acc[c][name]
        return None

    def accessor_for(self, cls: str, field: str) -> Optional[str]:
        for c in self.chain(cls):
            for a, f in self.acc.get(c, {}).items():
                if f == field:
                    return a
        return None


def header_files(root: str) -> List[str]:
    out = []
    for pat in ("include/**/*.h", "include/**/*.hpp", "libs/*/include/**/*.h", "libs/*/include/**/*.hpp"):
        out += glob.glob(os.path.join(root, pat), recursive=True)
    return out


# ---------------------------------------------------------------------------
# name mapping for one undefined identifier
# ---------------------------------------------------------------------------

def map_candidates(ident: str, fncls: Optional[str], owner: Optional[str], theirs: ClassIndex, ours: ClassIndex,
                   their_unit: Optional[ClassIndex], our_unit: Optional[ClassIndex]) -> List[Tuple[str, str]]:
    """Possible replacements for `ident` (field or accessor name): [(new, why)]."""

    def fo(idx, extra, cls, name):
        for ix in (extra, idx):
            if ix is None:
                continue
            v = ix.field_off(cls, name)
            if v is not None:
                return v
        return None

    def names(idx, extra, cls, off):
        out = []
        for ix in (extra, idx):
            if ix is not None:
                out += ix.names_at(cls, off)
        return out

    classes = []
    if owner:
        classes = [owner]
    else:
        if fncls:
            classes.append(fncls)
        for ix in (their_unit, theirs):
            if ix is not None:
                classes += ix.owners.get(ident, [])
    seen, out = set(), []
    for cls in dict.fromkeys(classes):
        off = fo(theirs, their_unit, cls, ident)
        if off is None:
            # an accessor in the other tree
            fld = None
            for ix in (their_unit, theirs):
                if ix is not None and fld is None:
                    fld = ix.accessor_field(cls, ident)
            if fld is None:
                continue
            off = fo(theirs, their_unit, cls, fld)
            if off is None:
                continue
            for n in names(ours, our_unit, cls, off):
                a = (our_unit.accessor_for(cls, n) if our_unit else None) or ours.accessor_for(cls, n)
                for cand in ([a] if a else []) + [n + "@FIELD"]:
                    if cand not in seen:
                        seen.add(cand)
                        out.append((cand, "%s::%s()->%#x" % (cls, ident, off)))
            continue
        for n in names(ours, our_unit, cls, off):
            if n != ident and n not in seen:
                seen.add(n)
                out.append((n, "%s::%s@%#x" % (cls, ident, off)))
    if not owner:
        # without a known owner, only an unambiguous rename is taken
        news = set(n for n, _ in out)
        if len(news) > 1:
            return []
    return out


def owner_of(ident: str, line: str, body: str, fncls: Optional[str]) -> Optional[str]:
    """The class of the object `ident` is read from on this line, when the
    body declares it plainly (`T* x`, `T& x`, `T x`), or the function's own
    class for `this->ident` and bare reads."""
    m = re.search(r"(\w+)\s*(?:->|\.)\s*" + re.escape(ident) + r"\b", line)
    if not m:
        if re.search(r"(?<![\w.>])" + re.escape(ident) + r"\b", line):
            return fncls
        return None
    var = m.group(1)
    if var == "this":
        return fncls
    dm = re.search(r"\b([A-Za-z_]\w*)\s*(?:<[^;()]*>)?\s*[*&]?\s*(?:const\s*)?\b" + re.escape(var) + r"\s*[=;,)]", body)
    if dm and dm.group(1) not in ("return", "const", "else"):
        return dm.group(1)
    return None


# ---------------------------------------------------------------------------
# compile and score
# ---------------------------------------------------------------------------

_ndb = {}


def ninja_db():
    if "db" not in _ndb:
        _ndb["db"] = LS.NinjaDB(os.path.join(ROOT, "build.ninja"))
    return _ndb["db"]


ERR_RX = re.compile(r"^#\s+(\d+):.*\n#\s+Error:.*\n#\s+(.*)$", re.M)


def compile_text(sh, unit_obj: str, rel_src: str, text: str, maxerrors: Optional[int] = None):
    sh.write(text)
    base = os.path.splitext(os.path.basename(rel_src))[0]
    out = os.path.join(sh.out, base + ".o")
    if os.path.exists(out):
        os.remove(out)
    cmd = ninja_db().compile_command(unit_obj, rel_src, sh.out)
    if maxerrors is not None:
        cmd = cmd.replace("-maxerrors 1", "-maxerrors %d" % maxerrors)
    r = subprocess.run(cmd, shell=True, cwd=sh.dir, capture_output=True, text=True, errors="replace")
    o = r.stdout + r.stderr
    if r.returncode == 0 and os.path.exists(out):
        return out, []
    errs = []
    for blk in o.split("### mwcceppc.exe Compiler:"):
        if "Error:" not in blk:
            continue
        fm = re.search(r"#\s+(?:File|In):\s*(\S+)", blk)
        lm = re.search(r"^#\s+(\d+):(.*)$", blk, re.M)
        em = re.search(r"#\s+Error:.*\n((?:#\s+.*\n?)+)", blk)
        msg = ""
        if em:
            msg = " ".join(x.strip("# ").strip() for x in em.group(1).splitlines()
                           if "Too many errors" not in x)
        errs.append((fm.group(1) if fm else "?", int(lm.group(1)) if lm else -1,
                     lm.group(2) if lm else "", msg.strip()))
    if not errs:
        errs.append(("?", -1, "", " ".join(o.splitlines()[-3:])))
    return None, errs


def objdiff_scores(target: str, obj: str, tmpdir: str) -> Dict[str, float]:
    out = os.path.join(tmpdir, "od-%d.json" % threading.get_ident())
    r = subprocess.run([OBJDIFF, "diff", "-1", target, "-2", obj, "-o", out, "--format", "json"],
                       capture_output=True, text=True)
    if r.returncode:
        return {}
    d = json.load(open(out))
    res = {}
    for s in d.get("left", {}).get("symbols", []):
        if s.get("kind") == "SYMBOL_FUNCTION" or "instructions" in s:
            res[s["name"]] = s.get("match_percent", 0.0) or 0.0
    return res


# ---------------------------------------------------------------------------
# the per-unit job
# ---------------------------------------------------------------------------

def their_path(other: str, rel_src: str) -> Optional[str]:
    p = rel_src.split("/")
    cands = []
    if p[0] == "libs":
        cands.append(os.path.join(other, "src", p[1], *p[3:]))
    cands.append(os.path.join(other, rel_src))
    for c in cands:
        if os.path.exists(c):
            return c
    base = os.path.basename(rel_src)
    hits = glob.glob(os.path.join(other, "src", "**", base), recursive=True)
    return hits[0] if len(hits) == 1 else None


def file_scope_def(text: str, m: str, sc, ident: str) -> Optional[str]:
    """The other tree's file-scope definition of `ident` (a function, a macro or a
    variable), whole lines, or None."""
    d = C.find_defs(text, C.Demangled([], ident, [], False), m, sc)
    if d:
        ls = text.rfind("\n", 0, d[0].qstart) + 1
        # take a preceding `template <...>` line along
        prev = text.rfind("\n", 0, max(ls - 1, 0)) + 1
        if re.match(r"\s*template\s*<", text[prev:ls]):
            ls = prev
        return text[ls:d[0].end] + "\n"
    mm = re.search(r"^[ \t]*#\s*define\s+" + re.escape(ident) + r"\b.*(?:\\\n.*)*", text, re.M)
    if mm:
        return mm.group(0) + "\n"
    for vm in re.finditer(r"\b" + re.escape(ident) + r"\b\s*(?:\[[^\]]*\]\s*)*(=|;|\()", m):
        pos = vm.start()
        if [x for x in C.enclosing(sc, pos) if x.kind != "ns"]:
            continue
        ls = m.rfind("\n", 0, pos) + 1
        head = m[ls:pos]
        if not re.search(r"\w", head) or re.search(r"[;{}]", head):
            continue
        # to the terminating ';' at brace depth 0
        d, j = 0, pos
        while j < len(m):
            if m[j] == "{":
                d += 1
            elif m[j] == "}":
                d -= 1
            elif m[j] == ";" and d == 0:
                break
            j += 1
        if vm.group(1) == "(" and not re.match(r"\s*\)?\s*;", m[pos:j + 1][len(ident):].split(")", 1)[-1]):
            continue
        return text[ls:j + 1] + "\n"
    return None


def skeleton(body: str) -> Tuple[int, int, str]:
    mb = C.mask(body)
    stm = mb.count(";")
    calls = len(re.findall(r"\b(?!if|for|while|switch|return|sizeof)[A-Za-z_]\w*\s*\(", mb))
    sk = re.sub(r"\b[A-Za-z_]\w*\b", "I", mb)
    sk = re.sub(r"\b\d[\w.]*\b", "N", sk)
    sk = re.sub(r"\s+", "", sk)
    return stm, calls, sk


REGION_RX = re.compile(r"VERSION_SELECT|VERSION_GMS[JP]01")


def region_variants(t: str) -> List[Tuple[str, str]]:
    if not REGION_RX.search(t):
        return [("as-is", t)]
    out = []
    a = t
    out.append(("as-is", a))
    jp = re.sub(r"\bVERSION_GMSJ01\b", "VERSION_GMSE01", t)
    jp = re.sub(r"\bGMSJ01\(", "GMSE01(", jp)
    pal = re.sub(r"\bVERSION_GMSP01\b", "VERSION_GMSE01", t)
    pal = re.sub(r"\bGMSP01\(", "GMSE01(", pal)
    if jp != t:
        out.append(("jp", jp))
    if pal != t:
        out.append(("pal", pal))
    return out


class Job:
    def __init__(self, args, unit: dict, fns: List[dict], theirs: ClassIndex, ours: ClassIndex, sink):
        self.args, self.unit, self.fns = args, unit, fns
        self.theirs, self.ours, self.sink = theirs, ours, sink
        self.rel_src = unit["metadata"]["source_path"]
        self.target = os.path.join(ROOT, unit["target_path"])
        self.obj = unit["base_path"]

    def row(self, fn, ours, theirs, status, others="", variant="", fixes="", note=""):
        delta = ""
        if isinstance(theirs, float) and isinstance(ours, float):
            delta = "%+.3f" % (theirs - ours)
        th = "%.3f" % theirs if isinstance(theirs, float) else theirs
        us = "%.3f" % ours if isinstance(ours, float) else ours
        self.sink([self.unit["name"].split("/", 1)[1], fn["name"], us, th, delta, status, others,
                   variant, fixes, note.replace("\t", " ").replace("\n", " ")[:300]])

    def run(self):
        ours_txt = open(os.path.join(ROOT, self.rel_src), encoding="utf-8", errors="replace").read()
        tp = their_path(self.args.other, self.rel_src)
        base_scores = objdiff_scores(self.target, os.path.join(ROOT, self.obj), self.args.work)
        th_txt = open(tp, encoding="utf-8", errors="replace").read() if tp else None
        om, osc = C.mask(ours_txt), None
        osc = C.scopes(ours_txt, om)
        if th_txt is not None:
            tm = C.mask(th_txt)
            tsc = C.scopes(th_txt, tm)
            self.th_txt, self.tm, self.tsc = th_txt, tm, tsc
            tunit = ClassIndex([])
            tunit.add(th_txt)
        ounit = ClassIndex([])
        ounit.add(ours_txt)
        work = tempfile.mkdtemp(prefix="tp-", dir=self.args.work)
        sh = None
        try:
            for fn in self.fns:
                ours_sc = base_scores.get(fn["name"], fn.get("fuzzy_match_percent", 0.0))
                dname = fn.get("metadata", {}).get("demangled_name", fn["name"])
                dname = re.sub(r"\$\d+\w*?_c(pp|p)?\b", "", dname)
                dm = C.parse_demangled(dname)
                dm.quals = [q for q in dm.quals if not q.startswith("@unnamed@")]
                od = C.find_defs(ours_txt, dm, om, osc)
                if th_txt is None:
                    self.row(fn, ours_sc, "missing", "missing", note="no unit in the other tree")
                    continue
                td = C.find_defs(th_txt, dm, tm, tsc)
                if not td:
                    self.row(fn, ours_sc, "missing", "missing", note="no definition in the other tree")
                    continue
                if not od:
                    self.row(fn, ours_sc, "missing", "header",
                             note="ours is not defined in the unit's source (header or template)")
                    continue
                o, t = od[0], td[0]
                if len(od) > 1 or len(td) > 1:
                    note_amb = "ambiguous overloads %d/%d" % (len(od), len(td))
                else:
                    note_amb = ""
                if o.form == t.form:
                    their_def = th_txt[t.start:t.end]
                    o_start = o.start
                else:
                    their_def = th_txt[t.bstart:t.end]
                    o_start = o.bstart
                if C.normalize(their_def) == C.normalize(ours_txt[o_start:o.end]):
                    self.row(fn, ours_sc, ours_sc, "ident", note=note_amb)
                    continue
                if sh is None:
                    sh = LS.Shadow(work, self.rel_src, 0)
                self.one(sh, fn, ours_sc, ours_txt, o_start, o.end, their_def, dm, base_scores, note_amb, o.qstart)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def one(self, sh, fn, ours_sc, ours_txt, s, e, their_def, dm, base_scores, note, qstart):
        fncls = dm.quals[-1] if dm.quals else None
        ins = ours_txt.rfind("\n", 0, qstart) + 1
        while True:
            prev = ours_txt.rfind("\n", 0, max(ins - 1, 0)) + 1
            pl = ours_txt[prev:ins].strip()
            if ins == 0 or not pl or re.search(r"[;{}]|\*/|^//|^#", pl):
                break
            ins = prev
        o_stm, o_calls, o_sk = skeleton(ours_txt[s:e])
        t_stm, t_calls, t_sk = skeleton(their_def)
        shape = "spelling" if o_sk == t_sk else "struct"
        note = ("%s stmts %d/%d calls %d/%d " % (shape, o_stm, t_stm, o_calls, t_calls) + note).strip()
        best = None
        tunit = ClassIndex([])
        tp = their_path(self.args.other, self.rel_src)
        tunit.add(open(tp, encoding="utf-8", errors="replace").read())
        ounit = ClassIndex([])
        ounit.add(ours_txt)
        last_errs = None
        for vname, body in region_variants(their_def):
            fixes = []
            tried = set()
            helpers = ""
            for _ in range(self.args.max_fixes + 1):
                text = ours_txt[:ins] + helpers + ours_txt[ins:s] + body + ours_txt[e:]
                out, errs = compile_text(sh, self.obj, self.rel_src, text)
                if out:
                    break
                last_errs = errs
                f, line, src_line, msg = errs[0]
                um = re.match(r"undefined identifier '(\w+)'", msg) or \
                    re.match(r"'(\w+)' is not a (?:struct/union/class )?member", msg)
                if not um:
                    break
                ident = um.group(1)
                body_line0 = text[:s + len(helpers)].count("\n") + 1
                body_lines = body.count("\n") + 1
                if not (body_line0 <= line < body_line0 + body_lines):
                    break
                owner = owner_of(ident, src_line, body, fncls)
                cands = [c for c in map_candidates(ident, fncls, owner, self.theirs, self.ours, tunit, ounit)
                         if (ident, c[0]) not in tried]
                if not cands:
                    hd = None
                    if ("helper", ident) not in tried:
                        tried.add(("helper", ident))
                        hd = file_scope_def(self.th_txt, self.tm, self.tsc, ident)
                    if not hd:
                        break
                    helpers = hd + helpers
                    fixes.append("helper:" + ident)
                    continue
                new, why = cands[0]
                tried.add((ident, new))
                pre = r"(?<![\w:])" + re.escape(ident)
                if new.endswith("@FIELD"):
                    rep = new[:-6]
                    body2 = re.sub(pre + r"\s*\(\s*\)", rep, body)
                elif "()" in why:
                    rep = new
                    body2 = re.sub(pre + r"(?=\s*\()", rep, body)
                else:
                    rep = new
                    body2 = re.sub(pre + r"\b(?!\s*\()", rep, body)
                if body2 == body:
                    break
                body = body2
                fixes.append("%s->%s(%s)" % (ident, rep, why))
            else:
                out = None
            if not out:
                continue
            scores = objdiff_scores(self.target, out, self.args.work)
            sc = scores.get(fn["name"])
            if sc is None:
                continue
            drops = [k for k, v in base_scores.items() if k != fn["name"] and scores.get(k, 0.0) + 1e-6 < v]
            cand = (sc, -len(drops), vname, fixes, drops, helpers + "\n// ----\n" + body)
            if best is None or cand[:2] > best[:2]:
                best = cand
        if best is None:
            err = last_errs[0] if last_errs else ("?", -1, "", "no score")
            # count errors with a longer leash, for the record
            n_err = "?"
            try:
                text = ours_txt[:ins] + helpers + ours_txt[ins:s] + their_def + ours_txt[e:]
                _, errs = compile_text(sh, self.obj, self.rel_src, text, maxerrors=50)
                n_err = str(len(errs)) if errs else "0"
            except Exception:
                pass
            self.row(fn, ours_sc, "compile-fail", "needs-port", fixes="",
                     note="%s errors; first: L%s %s | %s %s" % (n_err, err[1], err[3], err[2].strip()[:80], note))
            return
        sc, nd, vname, fixes, drops, body = best
        if abs(sc - ours_sc) < 1e-6:
            st = "same"
        elif sc > ours_sc:
            st = "better"
        else:
            st = "worse"
        self.row(fn, ours_sc, sc, st, others=";".join(drops[:5]) + ("(+%d)" % (len(drops) - 5) if len(drops) > 5 else ""),
                 variant=vname, fixes=" ".join(fixes), note=note)
        if st == "better" and self.args.patches:
            os.makedirs(self.args.patches, exist_ok=True)
            safe = re.sub(r"[^\w.-]", "_", fn["name"])[:120]
            with open(os.path.join(self.args.patches, safe + ".txt"), "w") as f:
                f.write("// unit %s fn %s ours %.3f theirs %.3f variant %s fixes %s\n" %
                        (self.unit["name"], fn["name"], ours_sc, sc, vname, " ".join(fixes)))
                f.write(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--other", required=True, help="root of the other decomp tree")
    ap.add_argument("--out", required=True, help="results TSV (appended; existing rows are skipped)")
    ap.add_argument("--patches", help="directory for the transplanted text of each better function")
    ap.add_argument("--work", default=None, help="scratch directory for shadow roots")
    ap.add_argument("-j", type=int, default=2)
    ap.add_argument("--unit", action="append", default=[])
    ap.add_argument("--fn", default=None, help="only functions whose mangled name contains this")
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--max-fixes", type=int, default=12)
    ap.add_argument("--report", default=os.path.join(ROOT, "build", "GMSE01", "report.json"))
    args = ap.parse_args()
    args.work = args.work or tempfile.mkdtemp(prefix="transplant-")
    os.makedirs(args.work, exist_ok=True)

    rep = json.load(open(args.report))
    od = {u["name"]: u for u in json.load(open(os.path.join(ROOT, "objdiff.json")))["units"]}
    done = set()
    if os.path.exists(args.out) and not args.rerun:
        for line in open(args.out):
            p = line.rstrip("\n").split("\t")
            if len(p) > 2:
                done.add((p[0], p[1]))
    else:
        with open(args.out, "w") as f:
            f.write("unit\tfn\tours\ttheirs\tdelta\tstatus\tothers\tvariant\tfixes\tnote\n")
    lock = threading.Lock()

    def sink(row):
        with lock:
            with open(args.out, "a") as f:
                f.write("\t".join(str(x) for x in row) + "\n")
            print("\t".join(str(x) for x in row[:6]), flush=True)

    print("indexing headers...", flush=True)
    ours = ClassIndex(header_files(ROOT))
    theirs = ClassIndex(header_files(args.other))
    jobs = []
    for u in rep["units"]:
        name = u["name"]
        short = name.split("/", 1)[1]
        if args.unit and short not in args.unit:
            continue
        fns = [f for f in u.get("functions", []) if f.get("fuzzy_match_percent", 0) < 100]
        if args.fn:
            fns = [f for f in fns if args.fn in f["name"]]
        fns = [f for f in fns if (short, f["name"]) not in done]
        if not fns or name not in od:
            continue
        uu = dict(od[name])
        uu["metadata"] = u["metadata"]
        jobs.append(Job(args, uu, fns, theirs, ours, sink))
    print("%d units, %d functions" % (len(jobs), sum(len(j.fns) for j in jobs)), flush=True)
    with cf.ThreadPoolExecutor(args.j) as ex:
        futs = {ex.submit(j.run): j for j in jobs}
        for fu in cf.as_completed(futs):
            try:
                fu.result()
            except Exception as ex_:
                import traceback
                traceback.print_exc()
                print("unit failed: %s: %s" % (futs[fu].unit["name"], ex_), flush=True)


if __name__ == "__main__":
    main()
