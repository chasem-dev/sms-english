#!/usr/bin/env python3

"""
Deterministic lever search for near-exact functions.

Given a unit and a mangled function, generate plausible source rewrites of the
function body from the lever catalogue (docs/catalog/RULES.md), compile each
variant, score it against the retail object with objdiff-cli, and search
single rewrites then beam combinations until the function is exact or the
budget runs out.

The worktree is never written to: every variant is compiled inside a private
"shadow root" (symlinks to the worktree with only the unit's .cpp replaced),
using the unit's own compile command from build.ninja, so the object is
byte-identical to what ninja would build.  Winning variants are written as
patch files; nothing is applied.

Levers (see --list-cands): accessor <-> raw member per site, including indexed
accessors (getCollision(i) <-> mCollisions[i]) and every 2-3 site subset of a
3-5 site group; null tests; integer types; unnaming, splitting, hoisting and
reordering declarations; naming call results and receivers; TU-local forks
and binders; compound assignments; setters.  From docs/catalog/frame-model.md:
  rot-helper    hand-written rotation matrix -> MsMtxSetRotX/Y/Z(m, deg)
  static-color  constant local GXColor -> file-scope static const GXColor
  unused-stub   statements equal to an UNUSED stub's body -> a call to it
  chain-binder  { T* r = a->getB()->getC(); return r; } over a chain's end
  chain-fork    { return a->getB()->getC(); } (the head binder is `binder`)
  name-conv     name an argument its parameter type converts (u32 id = s32v)
  decl-hoist    move a declaration (split from its value) to an outer block top
  vec-set       TVec3 v(a, b, c) <-> TVec3 v; v.set(a, b, c)
  name-read     name a single-use member/param read (f32 d = p->m.get();)

Usage:
  tools/lever-search.py -u Player/MarioPhysics -f barProcess__6TMarioFv
  tools/lever-search.py -u Enemy/rocket -f bind__7TRocketFv --source my.cpp
  tools/lever-search.py --list ranked.tsv --out results_dir --budget 90
  tools/lever-search.py -u Enemy/rocket -f bind__7TRocketFv --list-cands
"""

import argparse
import concurrent.futures as cf
import difflib
import hashlib
import itertools
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
OBJDIFF = os.path.join(ROOT, "build", "tools", "objdiff-cli")

# --------------------------------------------------------------------------
# Build description: compile command per object, parsed from build.ninja
# --------------------------------------------------------------------------


def _ninja_unescape(s: str) -> str:
    return re.sub(r"\$([$ :])", r"\1", s)


class NinjaDB:
    def __init__(self, path: str):
        raw = open(path, encoding="utf-8").read()
        raw = re.sub(r"\$\n\s*", "", raw)  # line continuations
        self.rules: Dict[str, Dict[str, str]] = {}
        self.builds: Dict[str, Tuple[str, List[str], Dict[str, str]]] = {}
        cur = None
        for line in raw.split("\n"):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith("rule "):
                cur = self.rules.setdefault(line[5:].strip(), {})
                continue
            if line.startswith("build "):
                m = re.match(r"build (.*?)(?<!\$): (\S+)(.*)", line)
                if not m:
                    cur = None
                    continue
                outs = [_ninja_unescape(x) for x in m.group(1).split()]
                ins = m.group(3).split("|")[0].split()
                v: Dict[str, str] = {}
                for o in outs:
                    self.builds[o] = (m.group(2), [_ninja_unescape(x) for x in ins], v)
                cur = v
                continue
            if line.startswith("  ") and cur is not None and "=" in line:
                k, _, val = line.strip().partition("=")
                cur[k.strip()] = val.strip()
                continue
            if not line.startswith(" "):
                cur = None
        self.globals = {}
        for line in raw.split("\n"):
            m = re.match(r"^(\w+) = (.*)$", line)
            if m:
                self.globals[m.group(1)] = m.group(2)

    def compile_command(self, obj: str, src: str, outdir: str) -> str:
        rule, ins, v = self.builds[obj]
        cmd = self.rules[rule]["command"]
        # Drop the depfile post-processing; keep everything else (extab clean).
        cmd = re.split(r"\s*&&\s*\$python tools/transform_dep\.py", cmd)[0]
        base = os.path.splitext(os.path.basename(src))[0]
        env = dict(self.globals)
        env.update(v)
        env.update({
            "in": src,
            "out": os.path.join(outdir, base + ".o"),
            "basedir": outdir,
            "basefile": os.path.join(outdir, base),
        })

        def sub(m):
            return env.get(m.group(1) or m.group(2), "")

        cmd = re.sub(r"\$\{(\w+)\}|\$(\w+)", sub, cmd)
        return cmd.replace("-MMD ", "")


# --------------------------------------------------------------------------
# Shadow roots: private build trees that share everything but one .cpp
# --------------------------------------------------------------------------


class Shadow:
    def __init__(self, base: str, rel_src: str, idx: int):
        self.dir = os.path.join(base, "sh%d" % idx)
        self.out = os.path.join(base, "o%d" % idx)
        os.makedirs(self.dir)
        os.makedirs(self.out)
        parts = rel_src.split("/")
        real, sh = ROOT, self.dir
        for depth, part in enumerate(parts):
            for e in os.listdir(real):
                if e == part or e.startswith("."):
                    continue
                os.symlink(os.path.join(real, e), os.path.join(sh, e))
            real = os.path.join(real, part)
            sh = os.path.join(sh, part)
            if depth < len(parts) - 1:
                os.makedirs(sh)
        self.src = sh

    def write(self, text: str):
        with open(self.src, "w", encoding="utf-8", newline="") as f:
            f.write(text)


# --------------------------------------------------------------------------
# objdiff scoring
# --------------------------------------------------------------------------

R1 = re.compile(r"(-?(?:0x[0-9a-f]+|\d+))\(r1\)")
ADDI_R1 = re.compile(r"^addi r\d+, r1, (-?(?:0x[0-9a-f]+|\d+))")


def _r1_offset(fmt: str) -> Optional[int]:
    if fmt.startswith("stwu r1,") or fmt.startswith("stmw") or fmt.startswith("lmw"):
        return None
    m = R1.search(fmt) or ADDI_R1.match(fmt)
    return int(m.group(1), 0) if m else None


def _neutral(fmt: str) -> str:
    fmt = R1.sub("S(r1)", fmt)
    return re.sub(r"^(addi r\d+, r1, )\S+", r"\1S", fmt)


@dataclass
class Score:
    ok: bool
    err: str = ""
    fuzzy: float = 0.0
    n_diff: int = 0
    n_struct: int = 0
    n_slot: int = 0
    frame_t: int = 0
    frame_o: int = 0
    size_t: int = 0
    size_o: int = 0
    slotdist: int = 0
    sig: str = ""
    deltas: List[Tuple[int, int, int]] = field(default_factory=list)

    @property
    def exact(self) -> bool:
        return self.ok and self.n_diff == 0 and self.size_t == self.size_o

    @property
    def dist(self) -> float:
        if not self.ok:
            return 1e9
        return (100 * self.n_struct + 25 * abs(self.size_t - self.size_o) // 4
                + 2 * abs(self.frame_t - self.frame_o) + 4 * self.n_slot
                + self.slotdist + (100.0 - self.fuzzy))

    def short(self) -> str:
        if not self.ok:
            return "FAIL " + self.err[:80]
        fr = "frame %#x" % self.frame_o if self.frame_o == self.frame_t else \
            "frame %#x/%#x" % (self.frame_t, self.frame_o)
        return "%.3f%% struct %d slot %d dist %d %s%s" % (
            self.fuzzy, self.n_struct, self.n_slot, self.slotdist, fr,
            "" if self.size_t == self.size_o else " size %d/%d" % (self.size_t, self.size_o))


def objdiff_json(target: str, base: str) -> dict:
    r = subprocess.run([OBJDIFF, "diff", "-c", "functionRelocDiffs=data_value", "-1", target,
                        "-2", base, "-o", "-", "--format", "json"], capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors="replace")[-300:])
    return json.loads(r.stdout)


def score_function(data: dict, fn: str) -> Score:
    ls = data["left"]["symbols"]
    rs = data["right"]["symbols"]
    lsym = next((s for s in ls if s.get("name") == fn), None)
    if lsym is None:
        return Score(False, "no retail symbol")
    ts = lsym.get("target_symbol")
    if ts is None:
        return Score(False, "function missing from our object")
    rsym = rs[ts]
    li, ri = lsym.get("instructions", []), rsym.get("instructions", [])
    sc = Score(True, fuzzy=float(lsym.get("match_percent") or 0.0),
               size_t=int(lsym.get("size", 0)), size_o=int(rsym.get("size", 0)))
    sig = []
    for i in range(max(len(li), len(ri))):
        a = li[i] if i < len(li) else {}
        b = ri[i] if i < len(ri) else {}
        fa = a.get("instruction", {}).get("formatted", "")
        fb = b.get("instruction", {}).get("formatted", "")
        sig.append(fb)
        if fa.startswith("stwu r1,"):
            sc.frame_t = -int(R1.search(fa).group(1), 0)
        if fb.startswith("stwu r1,"):
            sc.frame_o = -int(R1.search(fb).group(1), 0)
        kind = a.get("diff_kind") or b.get("diff_kind") or ""
        if not kind:
            continue
        sc.n_diff += 1
        oa, ob = _r1_offset(fa), _r1_offset(fb)
        if kind == "DIFF_ARG_MISMATCH" and _neutral(fa) == _neutral(fb):
            if fa.startswith("stwu"):
                continue
            sc.n_slot += 1
            if oa is not None and ob is not None:
                d = oa - ob
                sc.slotdist += min(abs(d), 64)
                sc.deltas.append((i, oa, ob))
        else:
            sc.n_struct += 1
    sc.sig = hashlib.md5("\n".join(sig).encode()).hexdigest()
    return sc


def unit_profile(data: dict) -> Dict[str, float]:
    """match_percent of every retail symbol (functions and data), plus extras."""
    out = {}
    for s in data["left"]["symbols"]:
        if s.get("kind") in ("SYMBOL_FUNCTION", "SYMBOL_OBJECT") and int(s.get("size", 0)):
            mp = s.get("match_percent")
            out[s["name"]] = float(mp) if mp is not None else (100.0 if s.get("target_symbol") is not None else 0.0)
    for s in data["left"].get("sections", []):
        if s.get("match_percent") is not None:
            out["[section]" + s["name"]] = float(s["match_percent"])
    extra = sorted(s["name"] for s in data["right"]["symbols"]
                   if s.get("target_symbol") is None and int(s.get("size", 0))
                   and s.get("kind") in ("SYMBOL_FUNCTION", "SYMBOL_OBJECT"))
    out["[extra]"] = extra
    return out


def regressions(base: Dict, var: Dict, fn: str) -> List[str]:
    bad = []
    for k, v in base.items():
        if k in ("[extra]", fn):
            continue
        nv = var.get(k)
        if nv is None or nv + 1e-6 < v:
            bad.append("%s %.2f->%s" % (k, v, "gone" if nv is None else "%.2f" % nv))
    # @NNNN pool/string symbols renumber freely; their bytes are covered by the section scores
    new_extra = set(x for x in var.get("[extra]", []) if not x.startswith("@")) - set(base.get("[extra]", []))
    bad += ["extra symbol " + x for x in sorted(new_extra)]
    return bad


# --------------------------------------------------------------------------
# Lexer and C++ heuristics
# --------------------------------------------------------------------------

TOKEN_RE = re.compile(r"""
 (?P<ws>\s+)
|(?P<lc>//[^\n]*)
|(?P<bc>/\*.*?\*/)
|(?P<pp>(?<![^\n])[ \t]*\#(?:[^\n\\]|\\.)*)
|(?P<str>"(?:[^"\\\n]|\\.)*")
|(?P<chr>'(?:[^'\\\n]|\\.)*')
|(?P<num>(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)[uUlLfF]*)
|(?P<id>[A-Za-z_]\w*)
|(?P<op>::|->|\+\+|--|<<=|>>=|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|<<|>>|\.\.\.|[{}()\[\];,.<>=!~?:+\-*/%&|^\\])
""", re.S | re.X)


@dataclass
class Tok:
    kind: str
    text: str
    s: int
    e: int


def lex(text: str) -> List[Tok]:
    out = []
    pos = 0
    n = len(text)
    while pos < n:
        m = TOKEN_RE.match(text, pos)
        if not m:
            out.append(Tok("op", text[pos], pos, pos + 1))
            pos += 1
            continue
        k = m.lastgroup
        if k not in ("ws", "lc", "bc", "pp"):
            out.append(Tok(k, m.group(), m.start(), m.end()))
        pos = m.end()
    return out


KEYWORDS = set("""if else while for do switch case default return break continue goto
sizeof new delete this operator static const volatile inline virtual extern typedef
struct class union enum public private protected template typename namespace using
true false nullptr NULL""".split())
INT_TYPES = ["int", "s32", "u32", "s16", "u16", "u8", "s8", "BOOL", "bool"]
INT_SWAPS = {
    "int": ["s32", "u32"], "s32": ["int", "u32"], "u32": ["int", "s32"],
    "u16": ["u32", "s32", "int"], "s16": ["int", "s32"], "u8": ["u32", "int"],
    "s8": ["int", "s32"], "bool": ["BOOL", "u8"], "BOOL": ["bool", "int"],
}
SCALAR_TYPES = set(INT_TYPES + ["f32", "f64", "float", "double", "long", "short", "char",
                                "unsigned", "signed", "u64", "s64"])
PREC = {"*": 10, "/": 10, "%": 10, "+": 9, "-": 9, "<<": 8, ">>": 8, "<": 7, ">": 7,
        "<=": 7, ">=": 7, "==": 6, "!=": 6, "&": 5, "^": 4, "|": 3, "&&": 2, "||": 1,
        "?": 0}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}


def bracket_map(toks: List[Tok]) -> Dict[int, int]:
    m, st = {}, []
    for i, t in enumerate(toks):
        if t.text in "([{" and t.kind == "op":
            st.append(i)
        elif t.text in ")]}" and t.kind == "op" and st:
            j = st.pop()
            m[j] = i
            m[i] = j
    return m


# --------------------------------------------------------------------------
# Header index: getters, setters, return types, globals
# --------------------------------------------------------------------------

GETTER_RE = re.compile(
    r"\b(\w+)\s*\(\s*\)\s*(?:const\s*)?\{\s*return\s+(\*?\s*(?:this\s*->\s*)?[A-Za-z_]\w*"
    r"(?:\s*(?:\.|->)\s*[A-Za-z_]\w*)*)\s*;\s*\}")
SETTER_RE = re.compile(
    r"\bvoid\s+(\w+)\s*\(\s*(?:const\s+)?[\w:<>]+\s*[&*]?\s*(\w+)\s*\)\s*\{\s*"
    r"(?:this\s*->\s*)?([A-Za-z_]\w*)\s*=\s*\2\s*;\s*\}")
DECL_RE = re.compile(
    r"(?:^|[;{}:]|\n)\s*(?:(?:static|virtual|inline|extern|friend)\s+)*"
    r"((?:const\s+)?[A-Za-z_][\w:]*(?:\s*<[^;{}()<>]*(?:<[^;{}()<>]*>[^;{}()<>]*)?>)?"
    r"(?:\s*::\s*\w+)?(?:\s*const)?\s*[*&]+|(?:const\s+)?[A-Za-z_][\w:]*(?:\s*<[^;{}()<>]*>)?)"
    r"\s+(\w+)\s*\(([^;{}]*)\)\s*(?:const\s*)?[{;]")
PTR_MEMBER_RE = re.compile(r"^\s*((?:const\s+)?[A-Za-z_][\w:]*(?:<[^;()]*>)?\s*\*)\s*(\w+)\s*;", re.M)
# T f(int i) { return mArr[i]; }  (indexed accessor, default argument allowed)
GETTER1_RE = re.compile(
    r"\b(\w+)\s*\(\s*(?:const\s+)?(?:unsigned\s+)?\w+\s+(\w+)\s*(?:=\s*\w+\s*)?\)\s*(?:const\s*)?"
    r"\{\s*return\s+(?:this\s*->\s*)?([A-Za-z_]\w*)\s*\[\s*\2\s*\]\s*;\s*\}")
MEMBER_SCALAR_RE = re.compile(
    r"^[ \t]*(f32|f64|float|double|s32|u32|int|s16|u16|u8|s8|bool|BOOL)\s+([A-Za-z_]\w*)\s*;", re.M)
MEMBER_OBJ_RE = re.compile(r"^[ \t]*([A-Za-z_][\w:]*(?:\s*<[^;(){}]*>)?)\s+([A-Za-z_]\w*)\s*;", re.M)
PARAM_MEMBER_RE = re.compile(r"\bTParam\w*\s*<\s*(\w+)\s*>\s+(\w+)\s*;")
CLASS_RE = re.compile(r"\b(?:class|struct)\s+(\w+)\s*(?::([^;{()]*))?\{")
EXTERN_RE = re.compile(r"\bextern\s+((?:const\s+)?[\w:]+(?:\s*<[^;]*?>)?\s*[*&]*)\s*(\w+)\s*;")


class Index:
    def __init__(self, texts: List[str]):
        self.getters: Dict[str, set] = {}
        self.setters: Dict[str, set] = {}
        self.rettypes: Dict[str, set] = {}
        self.globals: Dict[str, str] = {}
        self.ptr_members: set = set()
        self.getters1: Dict[str, set] = {}   # indexed accessor -> array members
        self.member_types: Dict[str, set] = {}  # scalar data member -> types
        self.param_members: Dict[str, set] = {}  # TParam member -> value types
        self.class_rettypes: Dict[Tuple[str, str], set] = {}  # (class, method) -> return types
        self.class_bases: Dict[str, set] = {}
        self.class_getters: Dict[Tuple[str, str], set] = {}
        self.sigs: Dict[str, set] = {}  # function -> {(param types...)}
        self.pnames: Dict[str, set] = {}  # function -> {(param names...)}
        for t in texts:
            self.add(t)

    def add(self, t: str):
        # offset comments (`/* 0x42A0 */ TEMario* mEMario;`) hide members from the
        # line-anchored patterns; strings keep their text
        t = re.sub(r"/\*.*?\*/", " ", t, flags=re.S)
        t = re.sub(r"//[^\n]*", "", t)
        for m in GETTER_RE.finditer(t):
            name, expr = m.group(1), re.sub(r"\s+", "", m.group(2)).replace("this->", "")
            if name in KEYWORDS or name == expr or expr.lstrip("*") in KEYWORDS:
                continue
            self.getters.setdefault(name, set()).add(expr)
        for m in SETTER_RE.finditer(t):
            self.setters.setdefault(m.group(1), set()).add(m.group(3))
        for m in DECL_RE.finditer(t):
            ty, name = re.sub(r"\s+", " ", m.group(1)).strip(), m.group(2)
            if name in KEYWORDS or ty.split()[0] in KEYWORDS - {"const"} or ty in ("return", "else"):
                continue
            ps = _parse_params(m.group(3))
            if ps is not None:
                self.sigs.setdefault(name, set()).add(tuple(x[0] for x in ps))
                self.pnames.setdefault(name, set()).add(tuple(x[1] for x in ps))
            if ty == "void":
                continue
            self.rettypes.setdefault(name, set()).add(_norm_type(ty))
        # per-class return types (resolves getGamePad() on TMarDirector vs others)
        for cm in CLASS_RE.finditer(t):
            ob = cm.end() - 1
            depth, j = 0, ob
            while j < len(t):
                if t[j] == "{":
                    depth += 1
                elif t[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            cls = cm.group(1)
            bases = [b for b in re.findall(r"(\w+)\s*(?:<[^<>]*>)?\s*(?:,|$)", (cm.group(2) or "").strip())
                     if b not in ("public", "private", "protected", "virtual")]
            if bases:
                self.class_bases.setdefault(cls, set()).update(bases)
            for m in GETTER_RE.finditer(t[ob + 1:j]):
                expr = re.sub(r"\s+", "", m.group(2)).replace("this->", "")
                self.class_getters.setdefault((cls, m.group(1)), set()).add(expr)
            for m in DECL_RE.finditer(t[ob + 1:j]):
                ty, name = re.sub(r"\s+", " ", m.group(1)).strip(), m.group(2)
                if name in KEYWORDS or ty.split()[0] in KEYWORDS - {"const"} or ty in ("void", "return", "else"):
                    continue
                self.class_rettypes.setdefault((cls, name), set()).add(_norm_type(ty))
        for m in GETTER1_RE.finditer(t):
            if m.group(1) not in KEYWORDS:
                self.getters1.setdefault(m.group(1), set()).add(m.group(3))
        for m in MEMBER_SCALAR_RE.finditer(t):
            if m.group(2) in SCALAR_TYPES or m.group(2) in KEYWORDS:
                continue
            ty = {"float": "f32", "double": "f64"}.get(m.group(1), m.group(1))
            self.member_types.setdefault(m.group(2), set()).add(ty)
        for m in MEMBER_OBJ_RE.finditer(t):
            if m.group(1).split("<")[0].strip() in KEYWORDS | SCALAR_TYPES or m.group(2) in KEYWORDS:
                continue
            self.member_types.setdefault(m.group(2), set()).add(_norm_type(m.group(1)))
        for m in PARAM_MEMBER_RE.finditer(t):
            ty = {"float": "f32", "double": "f64"}.get(m.group(1), m.group(1))
            self.param_members.setdefault(m.group(2), set()).add(ty)
        for m in PTR_MEMBER_RE.finditer(t):
            self.ptr_members.add(m.group(2))
            if not m.group(1).startswith(("return", "delete", "else")):
                self.member_types.setdefault(m.group(2), set()).add(_norm_type(m.group(1)))
        for m in EXTERN_RE.finditer(t):
            self.globals[m.group(2)] = re.sub(r"\s+", " ", m.group(1)).strip()

    def method_rettype(self, cls: Optional[str], name: str) -> Optional[str]:
        """Return type of cls::name (searching bases), else the unique global one."""
        seen, todo = set(), [cls] if cls else []
        while todo:
            c = todo.pop(0)
            if c in seen:
                continue
            seen.add(c)
            v = self.class_rettypes.get((c, name))
            if v:
                return _pick(v)
            todo += sorted(self.class_bases.get(c, ()))
        return self.rettype(name)

    def class_lookup(self, table: str, cls: Optional[str], name: str) -> Optional[set]:
        """The first class in cls's hierarchy that defines `name` in `table`."""
        seen, todo = set(), [cls] if cls else []
        while todo:
            c = todo.pop(0)
            if c in seen:
                continue
            seen.add(c)
            v = getattr(self, table).get((c, name))
            if v:
                return v
            todo += sorted(self.class_bases.get(c, ()))
        return None

    def unique(self, table: str, name: str) -> Optional[str]:
        v = getattr(self, table).get(name)
        return next(iter(v)) if v and len(v) == 1 else None

    def sig(self, name: str, arity: int) -> Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]]:
        """The one known signature of `name` with `arity` parameters, with names."""
        ss = [x for x in self.sigs.get(name, ()) if len(x) == arity]
        if len(ss) != 1:
            return None
        ns = [x for x in self.pnames.get(name, ()) if len(x) == arity]
        return ss[0], (ns[0] if len(ns) == 1 else tuple("" for _ in ss[0]))

    def all_pointer(self, name: str) -> bool:
        s = self.rettypes.get(name)
        return bool(s) and all(x.endswith("*") for x in s)

    def rettype(self, name: str) -> Optional[str]:
        return _pick(self.rettypes.get(name))


def _pick(types) -> Optional[str]:
    """The one type in the set; a const/non-const overload pair counts as one."""
    if not types:
        return None
    if len(types) == 1:
        return next(iter(types))
    plain = set(re.sub(r"^const\s+", "", x) for x in types)
    return next(iter(plain)) if len(plain) == 1 else None


def _norm_type(ty: str) -> str:
    ty = re.sub(r"\s*([*&<>,])\s*", r"\1", ty).replace(">", "> ").strip()
    return ty.replace("> *", ">*").replace("> &", ">&")


def _type_class(ty: Optional[str]) -> Optional[str]:
    """TMarDirector* -> TMarDirector (last component, no template args)."""
    if not ty:
        return None
    ty = re.sub(r"<.*", "", re.sub(r"^const\s+", "", ty.strip())).rstrip("*& ")
    return ty.split("::")[-1].strip() or None


def _parse_params(ptext: str) -> Optional[List[Tuple[str, str]]]:
    """(type, name) per parameter of a declaration, or None when unparsable."""
    ptext = ptext.strip()
    if not ptext or ptext == "void":
        return []
    out = []
    for p in ptext.split(","):
        p = p.split("=")[0].strip()
        if "(" in p or "<" in p or ">" in p or "[" in p:
            return None
        toks = re.findall(r"[A-Za-z_]\w*|[*&]", p)
        toks = [x for x in toks if x not in ("const", "volatile", "struct", "class")]
        if not toks:
            return None
        if len(toks) > 1 and re.match(r"[A-Za-z_]", toks[-1]) and toks[-1] not in SCALAR_TYPES:
            name, ty = toks[-1], toks[:-1]
        else:
            name, ty = "", toks
        ty = " ".join(ty).replace(" *", "*").replace(" &", "&")
        out.append(({"float": "f32", "double": "f64"}.get(ty, ty), name))
    return out


_INDEX_CACHE = {}


def header_index() -> Index:
    if "idx" not in _INDEX_CACHE:
        texts = []
        for dp, _, fs in os.walk(os.path.join(ROOT, "include")):
            for f in fs:
                if f.endswith((".h", ".hpp", ".inc")):
                    texts.append(open(os.path.join(dp, f), encoding="utf-8", errors="replace").read())
        _INDEX_CACHE["idx"] = Index(texts)
    return _INDEX_CACHE["idx"]


def merged_index(text: str) -> Index:
    """The header index plus the unit's own declarations."""
    idx, local, merged = header_index(), Index([text]), Index([])
    for d, v0 in vars(merged).items():
        if isinstance(v0, dict) and d != "globals":
            for src in (idx, local):
                for k, v in getattr(src, d).items():
                    v0.setdefault(k, set()).update(v)
    merged.ptr_members = idx.ptr_members | local.ptr_members
    merged.globals = dict(idx.globals)
    merged.globals.update(local.globals)
    return merged


# --------------------------------------------------------------------------
# Function location
# --------------------------------------------------------------------------

@dataclass
class FnInfo:
    klass: str
    name: str
    params: List[str]
    const: bool


def parse_demangled(dm: str) -> FnInfo:
    const = dm.rstrip().endswith(" const")
    depth, cut = 0, None
    for i, c in enumerate(dm):
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
        elif c == "(" and depth == 0:
            cut = i
            break
    q = dm[:cut] if cut is not None else dm
    ps = dm[cut + 1:dm.rfind(")")] if cut is not None else ""
    comps, depth, cur = [], 0, ""
    i = 0
    while i < len(q):
        if q[i] == "<":
            depth += 1
        elif q[i] == ">":
            depth -= 1
        if depth == 0 and q.startswith("::", i):
            comps.append(cur)
            cur = ""
            i += 2
            continue
        cur += q[i]
        i += 1
    comps.append(cur)
    params, depth, cur = [], 0, ""
    for c in ps:
        if c in "<(":
            depth += 1
        elif c in ">)":
            depth -= 1
        if c == "," and depth == 0:
            params.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip() and cur.strip() != "void":
        params.append(cur.strip())
    klass = re.sub(r"<.*", "", comps[-2]) if len(comps) > 1 else ""
    return FnInfo(klass, re.sub(r"<.*", "", comps[-1]), params, const)


@dataclass
class FnLoc:
    info: FnInfo
    name_tok: int
    lp: int
    rp: int
    bo: int
    bc: int
    head: int  # token index where the definition starts


def locate_function(toks: List[Tok], bm: Dict[int, int], info: FnInfo) -> Optional[FnLoc]:
    name = info.name
    cands = []
    for i, t in enumerate(toks):
        if t.text != name or i + 1 >= len(toks) or toks[i + 1].text != "(":
            continue
        if i > 0 and toks[i - 1].text == "~" and not name.startswith("~"):
            continue
        lp = i + 1
        rp = bm.get(lp)
        if rp is None:
            continue
        k = rp + 1
        while k < len(toks) and toks[k].text in ("const", "volatile"):
            k += 1
        if k >= len(toks):
            continue
        if toks[k].text == ":":  # ctor initialiser list
            while k < len(toks) and toks[k].text != "{":
                k = bm.get(k, k) + 1 if toks[k].text == "(" else k + 1
        if k >= len(toks) or toks[k].text != "{":
            continue
        # qualification check
        j = i - 1
        if toks[j].text == "~":
            j -= 1
        if info.klass:
            if not (toks[j].text == "::"):
                continue
            q = j - 1
            if toks[q].text == ">":
                d = 0
                while q >= 0:
                    if toks[q].text == ">":
                        d += 1
                    elif toks[q].text == "<":
                        d -= 1
                        if d == 0:
                            q -= 1
                            break
                    q -= 1
            if toks[q].text != info.klass:
                continue
        elif toks[j].text in ("::", ".", "->"):
            continue
        # count params
        pc = 0 if rp == lp + 1 or (rp == lp + 2 and toks[lp + 1].text == "void") else 1
        d = 0
        for x in range(lp + 1, rp):
            if toks[x].text in "([<":
                d += 1
            elif toks[x].text in ")]>":
                d -= 1
            elif toks[x].text == "," and d == 0:
                pc += 1
        cands.append((pc == len(info.params), i, lp, rp, k))
    # macro-defined bodies: IDENT(Class, ...) { ... }  (DEFINE_NERVE and friends)
    if not cands and info.klass:
        for i, t in enumerate(toks):
            if t.kind == "id" and t.text.isupper() and i + 2 < len(toks) and toks[i + 1].text == "(" \
                    and toks[i + 2].text == info.klass:
                rp = bm.get(i + 1)
                if rp is not None and rp + 1 < len(toks) and toks[rp + 1].text == "{":
                    cands.append((True, i, i + 1, rp, rp + 1))
    if not cands:
        return None
    cands.sort(key=lambda c: not c[0])
    _, i, lp, rp, bo = cands[0]
    # definition head: first token after the previous top-level ; or }
    h = i
    while h > 0 and toks[h - 1].text not in (";", "}", "{"):
        h -= 1
    return FnLoc(info, i, lp, rp, bo, bm[bo], h)


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------

@dataclass
class Cand:
    lever: str
    desc: str
    edits: List[Tuple[int, int, str]]
    prio: int = 5
    cid: int = 0


LEVER_PRIO = {
    "acc->raw": 1, "raw->acc": 1, "null-test": 1, "int-type": 2, "unname": 2,
    "name-call": 3, "decl-split": 3, "decl-order": 4, "fork": 4, "binder": 5,
    "compound": 5, "split-sum": 5, "set-assign": 5,
    # frame-model.md levers (2026-09-27)
    "rot-helper": 1, "static-color": 1, "unused-stub": 1, "name-conv": 3, "chain-binder": 4,
    "chain-fork": 4, "decl-hoist": 4, "vec-set": 4, "name-read": 5,
}


class Gen:
    """Generate candidate rewrites for one function body."""

    def __init__(self, text: str, loc: FnLoc, toks: List[Tok], bm: Dict[int, int], idx: Index):
        self.text, self.loc, self.toks, self.bm, self.idx = text, loc, toks, bm, idx
        self.bo, self.bc = loc.bo, loc.bc
        self.cands: List[Cand] = []
        self.used = set(t.text for t in toks if t.kind == "id")
        self.used_fn = set(t.text for t in toks[loc.head:loc.bc + 1] if t.kind == "id")
        self.names: Dict = {}
        self.scoped: Dict = {}
        self.pdepth = {}
        d = 0
        for k in range(self.bo, self.bc + 1):
            t = toks[k].text
            if t in (")", "]"):
                d -= 1
            self.pdepth[k] = d
            if t in ("(", "["):
                d += 1
        # prelude insertion point: start of the line after the previous top-level token
        h = loc.head
        if h > 0:
            nl = text.find("\n", toks[h - 1].e)
            self.pre_pos = nl + 1 if nl >= 0 else toks[h].s
        else:
            self.pre_pos = 0
        self.stem = (loc.info.klass[1:] if loc.info.klass.startswith("T") and len(loc.info.klass) > 1
                     and loc.info.klass[1].isupper() else loc.info.klass) or loc.info.name
        self.stem = self.stem[:1].lower() + self.stem[1:]
        # parameters: (name, type text, token indices)
        self.params = []
        d, cur = 0, []
        for q in range(loc.lp + 1, loc.rp):
            if toks[q].text in "(<[":
                d += 1
            elif toks[q].text in ")>]":
                d -= 1
            if toks[q].text == "," and d == 0:
                self.params.append(cur)
                cur = []
            else:
                cur.append(q)
        if cur:
            self.params.append(cur)
        self.var_types: Dict[str, str] = {}
        for p in self.params:
            if len(p) >= 2 and toks[p[-1]].kind == "id":
                self.var_types[toks[p[-1]].text] = text[toks[p[0]].s:toks[p[-2]].e]
        try:
            for s_, te, ni, _, _ in self.decls():
                self.var_types.setdefault(toks[ni].text, self.span(s_, te))
        except (IndexError, KeyError):
            pass

    # ---- helpers ----
    def add(self, lever: str, k: int, desc: str, edits, prio: Optional[int] = None):
        line = self.text.count("\n", 0, self.toks[k].s) + 1
        desc = re.sub(r"\s+", " ", desc)
        self.cands.append(Cand(lever, "%s L%d: %s" % (lever, line, desc), list(edits),
                               LEVER_PRIO.get(lever, 5) if prio is None else prio))

    def fresh(self, base: str, scope=None, site=None) -> str:
        """A name not in the source.  Helpers (scope None) are keyed by base so
        every candidate spelling the same helper shares one definition and must
        avoid every identifier of the file; locals only avoid the function's
        identifiers and the names other sites of the same block were given, so
        combinable candidates never collide."""
        base = re.sub(r"\W", "", base) or "v"
        if base in KEYWORDS:
            base += "_"
        key = (base, scope, site)
        if key in self.names:
            return self.names[key]
        taken = self.used if scope is None else self.used_fn | self.scoped.get(scope, set())
        n, i = base, 1
        while n in taken:
            i += 1
            n = "%s%d" % (base, i)
        if scope is None:
            self.used.add(n)
        else:
            self.scoped.setdefault(scope, set()).add(n)
        self.names[key] = n
        return n

    def helper_edit(self, helper: str) -> Tuple[int, int, str]:
        return (self.pre_pos, self.pre_pos, "\n" + helper.rstrip("\n") + "\n")

    def span(self, a: int, b: int) -> str:
        return self.text[self.toks[a].s:self.toks[b].e]

    def line_start(self, k: int) -> Tuple[int, str, bool]:
        s = self.toks[k].s
        ls = self.text.rfind("\n", 0, s) + 1
        pre = self.text[ls:s]
        return ls, pre, pre.strip() == ""

    def postfix_end(self, k: int) -> int:
        """k starts a postfix expression; return its last token index."""
        t = self.toks
        if t[k].text == "(":
            k = self.bm[k]
        elif t[k].kind == "id":
            while k + 2 < len(t) and t[k + 1].text == "::" and t[k + 2].kind == "id":
                k += 2
            if t[k + 1].text == "<":
                j = k + 2
                while j < min(k + 12, len(t)) and (t[j].kind == "id" or t[j].text in ("::", "*", ",", "<", ">")):
                    if t[j].text == ">" and t[j + 1].text == "(":
                        k = j
                        break
                    j += 1
        else:
            return k
        while k + 1 < len(t):
            n = t[k + 1].text
            if n in ("(", "["):
                k = self.bm[k + 1]
            elif n in (".", "->") and k + 2 < len(t) and t[k + 2].kind == "id":
                k += 2
            else:
                break
        return k

    def postfix_start(self, k: int) -> int:
        """k ends a postfix expression; return its first token index."""
        t = self.toks
        while True:
            if t[k].text in (")", "]"):
                o = self.bm[k]
                p = o - 1
                if t[k].text == "]" or (t[p].kind == "id" and t[p].text not in KEYWORDS) or t[p].text in (">", ")", "]"):
                    if t[p].text == ">":  # template args of a call
                        d, q = 0, p
                        while q > self.bo:
                            if t[q].text == ">":
                                d += 1
                            elif t[q].text == "<":
                                d -= 1
                                if d == 0:
                                    break
                            q -= 1
                        p = q - 1
                    k = p
                    continue
                return o
            if t[k].kind == "id" or t[k].text == "this":
                if t[k - 1].text in (".", "->", "::"):
                    k -= 2
                    if t[k].text == ">" and t[k + 1].text == "::":  # X<T>::f
                        d = 0
                        while k > self.bo:
                            if t[k].text == ">":
                                d += 1
                            elif t[k].text == "<":
                                d -= 1
                                if d == 0:
                                    break
                            k -= 1
                        k -= 1
                    continue
                return k
            return k

    def stmt_start(self, k: int) -> Optional[int]:
        """Start of the statement containing token k, or None when a declaration
        cannot be inserted before it (loop header, braceless body, else, case)."""
        t = self.toks
        j = k - 1
        while j > self.bo:
            x = t[j].text
            if x in (")", "]") and j in self.bm:
                j = self.bm[j] - 1
                continue
            if x in (";", "{", "}"):
                if self.pdepth.get(j, 0) != 0:
                    return None
                break
            if x == ":" and (t[j - 1].text in ("default",) or any(
                    t[q].text == "case" for q in range(j - 1, max(self.bo, j - 8), -1))):
                return None
            j -= 1
        s = j + 1
        kw = t[s].text
        if kw in ("else", "do", "case", "default", "while", "for"):
            return None
        if kw in ("if", "switch"):
            close = self.bm.get(s + 1)
            if close is None or k > close:
                return None
        return s

    def stmt_end(self, s: int) -> Optional[int]:
        k = s
        while k < self.bc:
            x = self.toks[k].text
            if x in ("(", "[", "{"):
                if x == "{":
                    return None
                k = self.bm[k] + 1
                continue
            if x == ";":
                return k
            k += 1
        return None

    def block_of(self, k: int) -> int:
        """index of the { opening the block containing k."""
        j = k - 1
        while j >= self.bo:
            if self.toks[j].text == "}" and j in self.bm:
                j = self.bm[j] - 1
                continue
            if self.toks[j].text == "{":
                return j
            j -= 1
        return self.bo

    def in_loop_between(self, outer_block: int, k: int) -> bool:
        """True if some loop body lies between block outer_block and token k."""
        b = self.block_of(k)
        while b > outer_block:
            p = b - 1
            if self.toks[p].text == "do":
                return True
            if self.toks[p].text == ")" and p in self.bm and self.toks[self.bm[p] - 1].text in ("for", "while"):
                return True
            b = self.block_of(b)
        s = self.stmt_start(k)
        return s is None

    def decls(self):
        """Statement-level declarations: (start, type_end, name_idx, init_start|None, end)."""
        t = self.toks
        out = []
        k = self.bo + 1
        starts = []
        for j in range(self.bo, self.bc):
            if t[j].text in (";", "{", "}") and self.pdepth.get(j, 0) == 0:
                starts.append(j + 1)
        for s in starts:
            if s >= self.bc or t[s].kind != "id":
                continue
            k = s
            if t[k].text == "const":
                k += 1
            if t[k].kind != "id" or t[k].text in KEYWORDS:
                continue
            # type: qualified name with optional template args
            while t[k + 1].text == "::" and t[k + 2].kind == "id":
                k += 2
            if t[k + 1].text == "<":
                d, j = 0, k + 1
                while j < self.bc:
                    if t[j].text == "<":
                        d += 1
                    elif t[j].text == ">":
                        d -= 1
                    elif t[j].text == ">>":
                        d -= 2
                    elif t[j].text in (";", "(", "{"):
                        break
                    if d <= 0:
                        break
                    j += 1
                if d > 0 or t[j].text not in (">", ">>"):
                    continue
                k = j
            if t[k + 1].text in ("unsigned", "long", "int", "short", "char"):
                k += 1
            while t[k + 1].text in ("*", "&", "const"):
                k += 1
            ni = k + 1
            if t[ni].kind != "id" or t[ni].text in KEYWORDS:
                continue
            nx = t[ni + 1].text
            if nx == "=":
                e = self.stmt_end(ni)
                if e is None:
                    continue
                # single declarator only
                if any(t[q].text == "," and self.pdepth[q] == self.pdepth[ni] for q in range(ni + 2, e)):
                    continue
                out.append((s, k, ni, ni + 2, e))
            elif nx == ";":
                out.append((s, k, ni, None, ni + 1))
        return out

    def uses(self, name: str, after: int) -> List[int]:
        t = self.toks
        return [q for q in range(after, self.bc) if t[q].text == name and t[q].kind == "id"
                and t[q - 1].text not in (".", "->", "::")]

    def delete_stmt_edit(self, s: int, e: int) -> Tuple[int, int, str]:
        ls, pre, alone = self.line_start(s)
        end = self.toks[e].e
        if alone and self.text[end:end + 1] == "\n" and (self.text.find("\n", end) == end):
            return (ls, end + 1, "")
        return (self.toks[s].s, end, "")

    def insert_before_stmt(self, s: int, code: str) -> Tuple[int, int, str]:
        ls, pre, alone = self.line_start(s)
        if alone:
            return (ls, ls, pre + code + "\n")
        return (self.toks[s].s, self.toks[s].s, code + " ")

    def is_value_context(self, a: int, b: int) -> bool:
        t = self.toks
        nxt = t[b + 1].text
        prv = t[a - 1].text
        if nxt in ASSIGN_OPS or nxt in ("++", "--") or prv in ("++", "--", "&"):
            return False
        return True

    # ---- levers ----
    def gen_accessors(self):
        t = self.toks
        idx = self.idx
        is_member = bool(self.loc.info.klass)
        groups: Dict[Tuple[str, str], List[Tuple[int, int, int, str]]] = {}
        # getter -> raw
        for k in range(self.bo + 1, self.bc):
            g = t[k].text
            if t[k].kind != "id" or g not in idx.getters or t[k + 1].text != "(" or t[k + 2].text != ")":
                continue
            prv = t[k - 1].text
            if prv == "::":
                continue
            recv = prv in (".", "->")
            exprs = idx.getters[g]
            cls = _type_class(self.expr_type_any(self.postfix_start(k - 2), k - 2)) if recv else \
                (self.loc.info.klass or None)
            known = idx.class_lookup("class_getters", cls, g) if cls else None
            if known:
                exprs = known  # the receiver's own accessor, not a namesake's
            for expr in sorted(exprs):
                is_global = expr.startswith("*") or re.match(r"g[A-Z]", expr) is not None
                if recv and is_global:
                    continue
                if not recv and not is_global and not is_member:
                    continue
                rep = expr
                if expr.startswith("*") and t[k + 3].text in (".", "->", "["):
                    rep = "(%s)" % expr
                edit = (t[k].s, t[k + 2].e, rep)
                self.add("acc->raw", k, "%s() -> %s" % (g, rep), [edit], prio=_acc_prio(g, expr))
                groups.setdefault(("acc->raw", "%s() -> %s" % (g, rep)), []).append(edit)
        # raw -> getter (single-identifier and *global forms)
        raw: Dict[str, List[str]] = {}
        for g, exprs in idx.getters.items():
            for e in exprs:
                if re.fullmatch(r"\*?[A-Za-z_]\w*", e):
                    raw.setdefault(e, []).append(g)
        for k in range(self.bo + 1, self.bc):
            if t[k].kind != "id":
                continue
            star = t[k - 1].text == "*" and ("*" + t[k].text) in raw and \
                (t[k - 2].kind not in ("id", "num") or t[k - 2].text in KEYWORDS) and t[k - 2].text not in (")", "]")
            key = ("*" + t[k].text) if star else t[k].text
            if key not in raw or t[k + 1].text == "(":
                continue
            a = k - 1 if star else k
            prv = t[a - 1].text
            if prv == "::":
                continue
            recv = prv in (".", "->")
            is_global = key.startswith("*") or re.match(r"g[A-Z]", key) is not None
            if recv and is_global:
                continue
            if not recv and not is_global and not is_member:
                continue
            if not self.is_value_context(a, k):
                continue
            for g in sorted(set(raw[key])):
                edit = (t[a].s, t[k].e, g + "()")
                self.add("raw->acc", k, "%s -> %s()" % (key, g), [edit], prio=_acc_prio(g, key))
                groups.setdefault(("raw->acc", "%s -> %s()" % (key, g)), []).append(edit)
        # indexed accessors: getCollision(i) <-> mCollisions[i]
        raw1: Dict[str, List[str]] = {}
        for g, mems in idx.getters1.items():
            for mem in mems:
                raw1.setdefault(mem, []).append(g)
        for k in range(self.bo + 1, self.bc):
            if t[k].kind != "id" or t[k - 1].text == "::":
                continue
            recv = t[k - 1].text in (".", "->")
            if not recv and not is_member:
                continue
            g = t[k].text
            if g in idx.getters1 and t[k + 1].text == "(" and t[k + 2].text != ")":
                cp = self.bm.get(k + 1)
                if cp is None or any(t[q].text == "," and self.pdepth[q] == self.pdepth[k + 2]
                                     for q in range(k + 2, cp)):
                    continue
                for mem in sorted(idx.getters1[g]):
                    edit = (t[k].s, t[cp].e, "%s[%s]" % (mem, self.span(k + 2, cp - 1)))
                    d = "%s(i) -> %s[i]" % (g, mem)
                    self.add("acc->raw", k, d, [edit], prio=_acc_prio(g, mem))
                    groups.setdefault(("acc->raw", d), []).append(edit)
            elif g in raw1 and t[k + 1].text == "[":
                cb = self.bm.get(k + 1)
                if cb is None or t[cb + 1].text == "[" or not self.is_value_context(k, cb):
                    continue
                for acc in sorted(set(raw1[g])):
                    edit = (t[k].s, t[cb].e, "%s(%s)" % (acc, self.span(k + 2, cb - 1)))
                    d = "%s[i] -> %s(i)" % (g, acc)
                    self.add("raw->acc", k, d, [edit], prio=_acc_prio(acc, g))
                    groups.setdefault(("raw->acc", d), []).append(edit)
        for (lever, d), eds in groups.items():
            if len(eds) > 1:
                g = re.search(r"(\w+)\((?:i)?\)", d).group(1)
                pr = _acc_prio(g, re.sub(r"\[i\]", "", d.split(" -> ")[0 if lever == "raw->acc" else 1]))
                self.cands.append(Cand(lever, "%s all %d sites: %s" % (lever, len(eds), d), eds, pr))
                # accessor prices are per site and saturate: offer the site subsets
                # (several closes needed e.g. 2 of 4 sites raw)
                n = len(eds)
                if 3 <= n <= 5:
                    for r in range(2, n if n <= 4 else 3):
                        for sub in itertools.combinations(range(n), r):
                            self.cands.append(Cand(lever, "%s sites %s of %d: %s" % (
                                lever, "+".join(str(i + 1) for i in sub), n, d), [eds[i] for i in sub], pr + 1))

    def gen_setters(self):
        t = self.toks
        setnames = set(re.findall(r"(\w+)\s*\.\s*set\s*\(", self.text))
        for k in range(self.bo + 1, self.bc):
            # E.set(x);  ->  E = x;   and  E.member = x
            if t[k].text in (".", "->") and t[k + 1].text in self.idx.setters and t[k + 2].text == "(":
                cp = self.bm[k + 2]
                if t[cp + 1].text != ";":
                    continue
                s = self.stmt_start(k)
                if s is None:
                    continue
                arg = self.text[t[k + 2].e:t[cp].s].strip()
                recv = self.text[t[s].s:t[k].s]
                if t[k + 1].text == "set" and t[k].text == ".":
                    self.add("set-assign", k, "%s.set(x) -> = x" % recv,
                             [(t[s].s, t[cp + 1].e, "%s = %s;" % (recv, arg))])
                for mem in sorted(self.idx.setters[t[k + 1].text]):
                    self.add("set-assign", k, "%s(x) -> %s = x" % (t[k + 1].text, mem),
                             [(t[s].s, t[cp + 1].e, "%s%s%s = %s;" % (recv, t[k].text, mem, arg))])
            # E = x;  ->  E.set(x)  (flag-typed members used with .set elsewhere)
            if t[k].text == "=" and t[k - 1].kind == "id" and t[k - 1].text in setnames:
                s = self.stmt_start(k)
                e = self.stmt_end(k)
                if s is None or e is None or self.pdepth[k] != 0:
                    continue
                lhs = self.text[t[s].s:t[k].s].rstrip()
                rhs = self.text[t[k].e:t[e].s].strip()
                self.add("set-assign", k, "%s = x -> .set(x)" % lhs, [(t[s].s, t[e].e, "%s.set(%s);" % (lhs, rhs))])

    def impure(self, a: int, b: int) -> bool:
        """Does a..b call something that is not an accessor-looking function?
        Reordering two such expressions would reorder their side effects."""
        t = self.toks
        for q in range(a, b):
            if t[q].kind == "id" and t[q + 1].text == "(" and t[q].text not in KEYWORDS:
                n = t[q].text
                if n in self.idx.getters or re.match(r"(get|is|has|check|calc)[A-Z_]", n) or n in SCALAR_TYPES:
                    continue
                return True
            if t[q].text in ("++", "--", "new", "delete") or t[q].text in ASSIGN_OPS:
                return True
        return False

    def is_pointer(self, a: int, e: int) -> bool:
        """Best-effort: does the postfix expression a..e have pointer type?"""
        t = self.toks
        if t[e].text == ")":
            rt = self.call_type(self.postfix_start(e), e)
            if rt:
                return rt.endswith("*")
            o = self.bm.get(e, e) - 1
            return t[o].kind == "id" and self.idx.all_pointer(t[o].text)
        if t[e].kind != "id":
            return False
        if a == e and t[e].text in self.var_types:
            return self.var_types[t[e].text].rstrip().endswith("*")
        if t[e].text in self.idx.globals:
            return self.idx.globals[t[e].text].endswith("*")
        return t[e].text in self.idx.ptr_members

    def gen_null_tests(self):
        t = self.toks
        BOOLCTX_L = ("(", "&&", "||", "!")
        BOOLCTX_R = (")", "&&", "||")
        for k in range(self.bo + 1, self.bc):
            x = t[k].text
            # !E  ->  E == nullptr
            if x == "!" and (t[k + 1].kind == "id" and t[k + 1].text not in KEYWORDS or t[k + 1].text == "("):
                e = self.postfix_end(k + 1)
                if t[e + 1].text not in (")", "&&", "||", ";", "?", ","):
                    continue
                if not self.is_pointer(k + 1, e):
                    continue
                expr = self.span(k + 1, e)
                if t[k - 1].text in ("(", "&&", "||", "return", "=", ","):
                    rep = "%s == nullptr" % expr
                else:
                    rep = "(%s == nullptr)" % expr
                self.add("null-test", k, "!%s -> == nullptr" % expr, [(t[k].s, t[e].e, rep)])
            # E == nullptr  ->  !E ;  E != nullptr  ->  E
            elif x in ("==", "!=") and t[k + 1].text in ("nullptr", "NULL", "0") \
                    and (t[k - 1].kind == "id" or t[k - 1].text in (")", "]")):
                a = self.postfix_start(k - 1)
                if t[a - 1].text not in ("(", "&&", "||", "return", "=", ",", "!"):
                    continue
                if t[k + 2].text not in (")", "&&", "||", ";", "?", ","):
                    continue
                expr = self.span(a, k - 1)
                if x == "==":
                    self.add("null-test", k, "%s == %s -> !" % (expr, t[k + 1].text),
                             [(t[a].s, t[k + 1].e, "!" + expr)])
                elif t[a - 1].text in BOOLCTX_L and t[k + 2].text in BOOLCTX_R:
                    self.add("null-test", k, "%s != %s -> bare" % (expr, t[k + 1].text),
                             [(t[a].s, t[k + 1].e, expr)])
                if t[k + 1].text == "0":
                    self.add("null-test", k, "%s 0 -> nullptr" % x, [(t[k + 1].s, t[k + 1].e, "nullptr")])
            # bare E in a boolean context  ->  E != nullptr
            elif t[k].kind == "id" and t[k].text not in KEYWORDS and t[k - 1].text not in (".", "->", "::"):
                p = t[k - 1].text
                if not (p in ("&&", "||") or (p == "(" and t[k - 2].text in ("if", "while"))):
                    continue
                e = self.postfix_end(k)
                if t[e + 1].text not in BOOLCTX_R or not self.is_pointer(k, e):
                    continue
                expr = self.span(k, e)
                self.add("null-test", k, "%s -> != nullptr" % expr, [(t[k].s, t[e].e, "%s != nullptr" % expr)])

    def gen_int_types(self):
        t = self.toks
        groups: Dict[Tuple[str, str], List] = {}
        for s, te, ni, init, e in self.decls():
            k = te
            if t[k].text in INT_SWAPS and (k == s or (k == s + 1 and t[s].text == "const")):
                for alt in INT_SWAPS[t[k].text]:
                    self.add("int-type", k, "%s %s -> %s" % (t[k].text, t[ni].text, alt), [(t[k].s, t[k].e, alt)])
                    groups.setdefault((t[k].text, alt), []).append((t[k].s, t[k].e, alt))
        for (a, b), eds in groups.items():
            if len(eds) > 1:
                self.add("int-type", self.bo, "all %d %s locals -> %s" % (len(eds), a, b), eds)
        # for-loop counters
        for k in range(self.bo + 1, self.bc):
            if t[k].text == "for" and t[k + 1].text == "(" and t[k + 2].text in INT_SWAPS and t[k + 3].kind == "id":
                for alt in INT_SWAPS[t[k + 2].text]:
                    self.add("int-type", k, "for %s %s -> %s" % (t[k + 2].text, t[k + 3].text, alt),
                             [(t[k + 2].s, t[k + 2].e, alt)])

    def gen_decls(self):
        t = self.toks
        ds = self.decls()
        for n, (s, te, ni, init, e) in enumerate(ds):
            name = t[ni].text
            tytoks = [t[q].text for q in range(s, te + 1)]
            ty = self.span(s, te)
            scalar = tytoks[-1] == "*" or all(x in SCALAR_TYPES or x == "const" for x in tytoks)
            is_ref = tytoks[-1] == "&"
            # un-name a single-use initialised local
            if init is not None and not is_ref:
                us = self.uses(name, e + 1)
                if len(us) == 1:
                    u = us[0]
                    if self.is_value_context(u, u) and not self.in_loop_between(self.block_of(s), u):
                        itext = self.span(init, e - 1)
                        ops = [q for q in range(init, e) if self.pdepth[q] == self.pdepth[init]
                               and (t[q].text in PREC or t[q].text in ASSIGN_OPS)]
                        if ops and not (t[u - 1].text in ("(", ",", "=", "return") and t[u + 1].text in (")", ",", ";")):
                            itext = "(%s)" % itext
                        if tytoks[-1] == "*" and t[init].text != "(" and len(tytoks) and \
                                not re.match(r"\(\s*" + re.escape(ty.replace("const ", "")), itext):
                            pass
                        self.add("unname", s, "inline %s" % name,
                                 [self.delete_stmt_edit(s, e), (t[u].s, t[u].e, itext)])
                        # the inlined value under a null test: also offer the other spelling
                        if tytoks[-1] == "*" and t[u - 1].text == "!" and t[u + 1].text in (")", "&&", "||"):
                            self.add("unname", s, "inline %s as == nullptr" % name,
                                     [self.delete_stmt_edit(s, e), (t[u - 1].s, t[u].e, "%s == nullptr" % itext)])
                        if tytoks[-1] == "*" and t[u + 1].text in ("==", "!=") and t[u + 2].text in ("nullptr", "NULL", "0") \
                                and t[u - 1].text in ("(", "&&", "||"):
                            neg = "!" if t[u + 1].text == "==" else ""
                            self.add("unname", s, "inline %s as %s test" % (name, neg or "bare"),
                                     [self.delete_stmt_edit(s, e), (t[u].s, t[u + 2].e, neg + itext)])
            # split / hoist a scalar or pointer declaration
            if init is not None and scalar:
                bty = re.sub(r"^const\s+", "", ty) if tytoks[-1] != "*" else ty
                ls, pre, alone = self.line_start(s)
                assign = "%s = %s;" % (name, self.span(init, e - 1))
                if alone:
                    self.add("decl-split", s, "split %s in place" % name,
                             [(t[s].s, t[e].e, "%s %s;\n%s%s" % (bty, name, pre, assign))])
                blk = self.block_of(s)
                first = blk + 1
                if first < s:
                    self.add("decl-split", s, "hoist %s to block top" % name,
                             [self.insert_before_stmt(first, "%s %s;" % (bty, name)), (t[s].s, t[e].e, assign)])
                # one statement earlier
                prev = s - 1
                if t[prev].text == ";" and prev > blk:
                    ps = prev - 1
                    while ps > blk and t[ps].text not in (";", "{", "}"):
                        ps = self.bm.get(ps, ps) - 1 if t[ps].text in (")", "]") else ps - 1
                    ps += 1
                    if ps != first and ps < s and t[ps].text not in ("if", "for", "while", "else", "do", "switch", "case"):
                        self.add("decl-split", s, "declare %s one statement earlier" % name,
                                 [self.insert_before_stmt(ps, "%s %s;" % (bty, name)), (t[s].s, t[e].e, assign)])
            # swap with the next adjacent declaration
            if n + 1 < len(ds):
                s2, te2, ni2, init2, e2 = ds[n + 1]
                if s2 == e + 1 and self.block_of(s2) == self.block_of(s):
                    refs = any(t[q].text == name for q in range(s2, e2))
                    if not refs and not (init is not None and init2 is not None
                                                    and self.impure(init, e) and self.impure(init2, e2)):
                        a, b = self.span(s, e), self.span(s2, e2)
                        self.add("decl-order", s, "swap %s/%s" % (name, t[ni2].text),
                                 [(t[s].s, t[e].e, b), (t[s2].s, t[e2].e, a)])

    def call_type(self, a: int, b: int) -> Optional[str]:
        """Return type of the call expression spanning a..b (b is the closing paren)."""
        t = self.toks
        o = self.bm.get(b)
        if o is None or t[b].text != ")":
            return None
        p = o - 1
        targ = None
        if t[p].text == ">":
            q = p
            while q > a and t[q].text != "<":
                q -= 1
            targ = self.text[t[q].e:t[p].s].strip()
            p = q - 1
        if t[p].kind != "id":
            return None
        rt = self.idx.rettype(t[p].text)
        if rt is None:  # ambiguous by name: resolve through the receiver's class
            if t[p - 1].text in ("->", ".") and p - 2 > a - 1:
                cls = _type_class(self.expr_type_any(self.postfix_start(p - 2), p - 2))
            elif t[p - 1].text not in ("::", ".", "->"):
                cls = self.loc.info.klass or None
            else:
                cls = None
            rt = self.idx.method_rettype(cls, t[p].text) if cls else None
        if rt is None:
            return None
        if targ and re.fullmatch(r"(const )?[A-Z]\*", rt.replace(" ", "")):
            rt = targ + "*"
        if re.search(r"\b[A-Z]\b", rt):  # unresolved template parameter
            return None
        if rt.startswith("virtual") or rt.startswith("static"):
            return None
        return {"float": "f32", "double": "f64"}.get(rt, rt)

    def expr_type_any(self, a: int, e: int) -> Optional[str]:
        """Best-effort type of the postfix expression a..e (pointer or scalar)."""
        t = self.toks
        if a > e:
            return None
        if t[e].text == ")":
            return self.call_type(self.postfix_start(e), e)
        if t[e].kind != "id":
            return None
        if a == e:
            if t[e].text in self.var_types:
                return self.var_types[t[e].text].strip()
            if t[e].text in self.idx.globals:
                return self.idx.globals[t[e].text]
            if t[e].text == "this" and self.loc.info.klass:
                return self.loc.info.klass + "*"
            return self.idx.unique("member_types", t[e].text) if self.loc.info.klass else None
        if t[e - 1].text in (".", "->"):
            return self.idx.unique("member_types", t[e].text)
        return None

    def gen_name_calls(self):
        t = self.toks
        for k in range(self.bo + 1, self.bc):
            if t[k].text != ")" or k not in self.bm:
                continue
            o = self.bm[k]
            if t[o - 1].kind != "id" and t[o - 1].text != ">":
                continue
            if t[o - 1].text in KEYWORDS:
                continue
            a = self.postfix_start(k)
            if t[a - 1].text in (".", "->", "::", "new", "&"):
                continue
            rt = self.call_type(a, k)
            if rt is None:
                continue
            nxt = t[k + 1].text
            if nxt in ASSIGN_OPS or nxt in ("++", "--", "("):
                continue
            if not (t[a].kind == "id" or t[a].text in ("(", "this")):
                continue
            # already the whole value of a declaration, assignment or return:
            # naming it again would only add a copy
            if t[a - 1].text in ("=", "return") and t[k + 1].text == ";":
                continue
            s = self.stmt_start(a)
            if s is None or (s == a and nxt not in (".", "->")):
                continue  # (a receiver at the head of its statement is rule 16's lever)
            # hoisting a right operand of && / || / ?: out of its statement
            # would evaluate it unconditionally
            if any(t[q].text in ("&&", "||", "?") for q in range(s, a)):
                continue
            by_value_class = not (rt.endswith("*") or rt.endswith("&") or rt.split()[-1] in SCALAR_TYPES)
            fname = t[o - 1].text if t[o - 1].kind == "id" else "obj"
            base = re.sub(r"^(SMS_?|MS)?(get|Get|search|find|calc|is)?(?=[A-Z])", "", fname)
            base = base[:1].lower() + base[1:] if base else "obj"
            v = self.fresh(base, self.block_of(s), s)
            decl = "%s %s = %s;" % (rt, v, self.span(a, k))
            role = "receiver" if nxt in (".", "->") else "value"
            self.add("name-call", a, "name %s %s %s" % (role, self.span(a, k)[:40], rt),
                     [self.insert_before_stmt(s, decl), (t[a].s, t[k].e, v)])
            if by_value_class:
                self.cands[-1].prio = 6

    def gen_wrappers(self):
        """TU-local direct-return forks and name-and-return binders."""
        t = self.toks
        info = self.loc.info
        seen = set()
        for k in range(self.bo + 1, self.bc):
            # implicit-this getter call: getX()
            if t[k].kind == "id" and t[k + 1].text == "(" and t[k + 2].text == ")" and info.klass \
                    and t[k - 1].text not in (".", "->", "::") and t[k].text not in KEYWORDS:
                rt = self.idx.rettype(t[k].text)
                if rt is None or re.search(r"\b[A-Z]\b", rt) or rt == "void":
                    continue
                if not self.is_value_context(k, k + 2):
                    continue
                cq = "const " if info.const else ""
                for form in ("fork", "binder"):
                    key = (form, t[k].text)
                    fn = self.fresh(self.stem + t[k].text[:1].upper() + t[k].text[1:] + ("" if form == "fork" else "Bound"))
                    if form == "fork":
                        body = "\treturn self->%s();" % t[k].text
                    else:
                        body = "\t%s r = self->%s();\n\treturn r;" % (rt, t[k].text)
                    helper = "static inline %s %s(%s%s* self)\n{\n%s\n}\n\n" % (rt, fn, cq, info.klass, body)
                    self.add(form, k, "%s(this) over %s()" % (fn, t[k].text),
                             [self.helper_edit(helper), (t[k].s, t[k + 2].e, "%s(this)" % fn)])
                    seen.add(key)
            # global reads: gpFoo
            if t[k].kind == "id" and t[k].text in self.idx.globals and t[k - 1].text not in (".", "->", "::") \
                    and t[k + 1].text != "(" and self.is_value_context(k, k):
                gty = self.idx.globals[t[k].text]
                if not gty.endswith("*"):
                    continue
                for form in ("fork", "binder"):
                    gname = re.sub(r"^gp?(?=[A-Z])", "", t[k].text)
                    fn = self.fresh(self.stem + gname[:1].upper() + gname[1:] + ("" if form == "fork" else "Bound"))
                    body = "\treturn %s;" % t[k].text if form == "fork" else \
                        "\t%s r = %s;\n\treturn r;" % (gty, t[k].text)
                    helper = "static inline %s %s()\n{\n%s\n}\n\n" % (gty, fn, body)
                    self.add(form, k, "%s() over %s" % (fn, t[k].text),
                             [self.helper_edit(helper), (t[k].s, t[k].e, "%s()" % fn)])
        # parameter forks: static inline P f(P p) { return p; }
        params = self.params
        for p in params:
            if len(p) < 2 or t[p[-1]].kind != "id" or t[p[-2]].text != "*":
                continue
            pname = t[p[-1]].text
            pty = self.text[t[p[0]].s:t[p[-2]].e]
            us = self.uses(pname, self.bo + 1)
            if not us or len(us) > 6:
                continue
            fn = self.fresh(self.stem + pname[:1].upper() + pname[1:])
            helper = "static inline %s %s(%s p)\n{\n\treturn p;\n}\n\n" % (pty, fn, pty)
            vu = [u for u in us if self.is_value_context(u, u)]
            for u in vu:
                self.add("fork", u, "%s(%s) at one site" % (fn, pname),
                         [self.helper_edit(helper), (t[u].s, t[u].e, "%s(%s)" % (fn, pname))])
            if len(vu) > 1:
                self.add("fork", vu[0], "%s(%s) at all %d sites" % (fn, pname, len(vu)),
                         [self.helper_edit(helper)] + [(t[u].s, t[u].e, "%s(%s)" % (fn, pname)) for u in vu])
            # a named copy of the parameter (plain or through the fork) bound
            # before a statement that uses it, standing in for every use there
            by_stmt: Dict[int, List[int]] = {}
            for u in vu:
                st = self.stmt_start(u)
                if st is not None:
                    by_stmt.setdefault(st, []).append(u)
            for st, uu in by_stmt.items():
                base = "pos" if "pos" in pname.lower() else pname[:1].lower() + pname[1:] + "Val"
                for via in (None, fn):
                    v = self.fresh(base, self.block_of(st), st)
                    init = "%s(%s)" % (via, pname) if via else pname
                    eds = [self.insert_before_stmt(st, "%s %s = %s;" % (pty, v, init))]
                    eds += [(t[u].s, t[u].e, v) for u in uu]
                    if via:
                        eds.append(self.helper_edit(helper))
                    self.add("fork" if via else "name-call", st, "bind %s = %s for one statement" % (v, init), eds)

    def gen_compound(self):
        t = self.toks
        for k in range(self.bo + 1, self.bc):
            if self.pdepth.get(k) != 0:
                continue
            x = t[k].text
            if x == "=":
                s, e = self.stmt_start(k), self.stmt_end(k)
                if s is None or e is None or t[s].text in ("return",) or any(t[q].text in ("(", "++", "--") for q in range(s, k)):
                    continue
                L = [t[q].text for q in range(s, k)]
                n = len(L)
                if [t[q].text for q in range(k + 1, k + 1 + n)] == L and t[k + 1 + n].text in PREC:
                    op = t[k + 1 + n].text
                    if op in ("&&", "||", "?", "<", ">", "<=", ">=", "==", "!="):
                        continue
                    rest = range(k + 2 + n, e)
                    d0 = self.pdepth[k]
                    if any(t[q].text in PREC and PREC[t[q].text] <= PREC[op] and self.pdepth[q] == d0 for q in rest):
                        continue
                    self.add("compound", k, "x = x %s y -> %s=" % (op, op),
                             [(t[k].s, t[k + 1 + n].e, op + "=")])
                # split sums: x = a + b  ->  x = a; x += b
                tops = [q for q in range(k + 1, e) if t[q].text in ("+", "-") and self.pdepth[q] == self.pdepth[k]
                        and t[q - 1].text not in PREC and t[q - 1].text not in ASSIGN_OPS and t[q - 1].text not in ("(", ",")]
                if tops and not any(t[q].text in PREC and PREC[t[q].text] < 9 and self.pdepth[q] == self.pdepth[k] for q in range(k + 1, e)):
                    q = tops[-1]
                    ls, pre, alone = self.line_start(s)
                    lhs = self.span(s, k - 1)
                    if alone and not any(t[r].text == L[-1] for r in range(q + 1, e)):
                        self.add("split-sum", k, "split %s at last %s" % (lhs, t[q].text),
                                 [(t[q].s, t[e].e, ";\n%s%s %s= %s;" % (pre, lhs, t[q].text, self.span(q + 1, e - 1)))])
            elif x in ("+=", "-=", "*=", "/=", "|=", "&=", "^=", "<<=", ">>="):
                s, e = self.stmt_start(k), self.stmt_end(k)
                if s is None or e is None or any(t[q].text in ("(", "++", "--") for q in range(s, k)):
                    continue
                op = x[:-1]
                lhs = self.span(s, k - 1)
                rhs = self.span(k + 1, e - 1)
                if any(t[q].text in PREC and PREC[t[q].text] <= PREC[op] and self.pdepth[q] == self.pdepth[k] for q in range(k + 1, e)):
                    rhs = "(%s)" % rhs
                self.add("compound", k, "%s -> x = x %s y" % (x, op), [(t[k].s, t[e - 1].e, "= %s %s %s" % (lhs, op, rhs))])
                if x in ("+=", "-="):
                    tops = [q for q in range(k + 1, e) if t[q].text in ("+", "-") and self.pdepth[q] == self.pdepth[k]
                            and t[q - 1].text not in PREC and t[q - 1].text not in ("(", ",")]
                    ls, pre, alone = self.line_start(s)
                    if tops and alone and not any(t[q].text in PREC and PREC[t[q].text] < 9 and self.pdepth[q] == self.pdepth[k] for q in range(k + 1, e)):
                        q = tops[-1]
                        op2 = t[q].text if x == "+=" else ("-" if t[q].text == "+" else "+")
                        self.add("split-sum", k, "split %s %s" % (lhs, x),
                                 [(t[q].s, t[e].e, ";\n%s%s %s= %s;" % (pre, lhs, op2, self.span(q + 1, e - 1)))])

    # ---- levers recorded in docs/catalog/frame-model.md (2026-09-27 batches) ----
    # Every generator below only emits a rewrite that is textually safe and that
    # reads as ordinary source: no padding, casts, pragmas or unused locals.

    def _stmt_extent(self, k: int) -> Optional[int]:
        """Last token of the statement starting at k (None when unparsable)."""
        t = self.toks
        x = t[k].text
        if x == "{":
            return self.bm.get(k)
        if x in ("if", "while", "for", "switch") and t[k + 1].text == "(":
            cp = self.bm.get(k + 1)
            if cp is None:
                return None
            e = self._stmt_extent(cp + 1)
            if e is not None and x == "if" and t[e + 1].text == "else":
                return self._stmt_extent(e + 2)
            return e
        if x == "do":
            e = self._stmt_extent(k + 1)
            if e is None or t[e + 1].text != "while" or t[e + 2].text != "(":
                return None
            return self.bm[e + 2] + 1
        if x in ("case", "default"):
            j = k
            while j < self.bc and t[j].text != ":":
                j += 1
            return j
        j = k
        while j < self.bc:
            y = t[j].text
            if y in ("(", "[", "{"):
                j = self.bm.get(j, j) + 1
                continue
            if y == "}":
                return None
            if y == ";":
                return j
            j += 1
        return None

    def block_stmts(self, blk: int) -> List[Tuple[int, int]]:
        end = self.bm.get(blk)
        out, k = [], blk + 1
        while end is not None and k < end:
            e = self._stmt_extent(k)
            if e is None or e >= end:
                return out
            out.append((k, e))
            k = e + 1
        return out

    def blocks(self) -> List[int]:
        t = self.toks
        return [k for k in range(self.bo, self.bc) if t[k].text == "{" and
                (k == self.bo or t[k - 1].text in (")", "else", "do", "{", "}", ";", ":"))]

    def _expr_type(self, a: int, e: int) -> Optional[str]:
        """Scalar type of the postfix read a..e: a local, a data member, a param value."""
        t, idx = self.toks, self.idx
        if a == e and t[e].kind == "id":
            vt = self.var_types.get(t[e].text)
            if vt is not None:
                vt = re.sub(r"^const\s+", "", vt.strip())
                return {"float": "f32", "double": "f64"}.get(vt, vt)
            if self.loc.info.klass:
                return idx.unique("member_types", t[e].text)
            return None
        if t[e].text == ")" and t[e - 1].text == "(" and t[e - 2].text == "get" and t[e - 3].text == "." \
                and t[e - 4].kind == "id":
            return idx.unique("param_members", t[e - 4].text)
        if t[e].kind == "id" and t[e - 1].text == "." and t[e].text == "value" and t[e - 2].kind == "id":
            return idx.unique("param_members", t[e - 2].text)
        if t[e].kind == "id" and t[e - 1].text in (".", "->"):
            return idx.unique("member_types", t[e].text)
        return None

    def _hoistable(self, s: int, a: int) -> bool:
        """Can the value at token a be computed before statement s?"""
        t = self.toks
        if any(t[q].text in ("&&", "||", "?") for q in range(s, a)):
            return False
        return not self.impure(s, a)

    def gen_name_reads(self):
        """Name a single-use member or parameter read (`f32 x = p->m.get(); if (d < x)`)."""
        t = self.toks
        for k in range(self.bo + 1, self.bc):
            if t[k].kind != "id" or t[k].text in KEYWORDS or t[k - 1].text in (".", "->", "::"):
                continue
            e = self.postfix_end(k)
            if t[e + 1].text in ("(", "[", ".", "->") or not self.is_value_context(k, e):
                continue
            if k == e and (t[k].text in self.var_types or t[k].text in SCALAR_TYPES):
                continue  # a copy of a local, or a type name
            if t[k - 1].text == "<" and t[e + 1].text == ">":
                continue  # template argument
            if self.impure(k, e + 1):
                continue  # only accessor calls inside the read
            ty = self._expr_type(k, e)
            if ty is None or ty not in SCALAR_TYPES:
                continue
            if not (t[k - 1].text in PREC and t[k - 1].text != "?") and not (t[e + 1].text in PREC and t[e + 1].text != "?"):
                continue  # only operands of arithmetic and comparisons
            s = self.stmt_start(k)
            if s is None or s == k or not self._hoistable(s, k):
                continue
            mem = t[e - 4].text if t[e].text == ")" else (t[e - 2].text if t[e].text == "value" else t[e].text)
            base = re.sub(r"^(m|unk)(?:SL)?(?=[A-Z0-9])", "", mem)
            base = base[:1].lower() + base[1:] if base[:1].isalpha() else "v" + base
            v = self.fresh(base, self.block_of(s), s)
            self.add("name-read", k, "name %s %s" % (ty, self.span(k, e)[:40]),
                     [self.insert_before_stmt(s, "%s %s = %s;" % (ty, v, self.span(k, e))), (t[k].s, t[e].e, v)])

    def gen_name_conv(self):
        """Name a call argument that the callee's parameter type converts
        (`u32 id = eventId; setEventId(id);`)."""
        t = self.toks
        for k in range(self.bo + 1, self.bc):
            if t[k].kind != "id" or t[k + 1].text != "(" or t[k].text in KEYWORDS or t[k - 1].text == "::":
                continue
            cp = self.bm.get(k + 1)
            if cp is None or cp == k + 2:
                continue
            args, cur = [], [k + 2]
            for q in range(k + 2, cp):
                if t[q].text == "," and self.pdepth[q] == self.pdepth[k + 2]:
                    cur.append(q - 1)
                    args.append(tuple(cur))
                    cur = [q + 1]
            cur.append(cp - 1)
            args.append(tuple(cur))
            sg = self.idx.sig(t[k].text, len(args))
            if sg is None:
                continue
            s = self.stmt_start(k)
            if s is None:
                continue
            for i, (a, b) in enumerate(args):
                p = sg[0][i]
                if p not in SCALAR_TYPES or self.postfix_end(a) != b:
                    continue
                if self.impure(a, b + 1):
                    continue
                v_ty = self._expr_type(a, b)
                if v_ty is None or v_ty not in SCALAR_TYPES or v_ty == p:
                    continue
                if {v_ty, p} <= {"f32", "float"} or {v_ty, p} <= {"f64", "double"}:
                    continue
                if not self._hoistable(s, k) or self.impure(k + 2, a) or self.impure(b + 1, cp):
                    continue
                base = sg[1][i] or "arg"
                v = self.fresh(base, self.block_of(s), s)
                self.add("name-conv", a, "name %s arg of %s as %s" % (self.span(a, b)[:30], t[k].text, p),
                         [self.insert_before_stmt(s, "%s %s = %s;" % (p, v, self.span(a, b))), (t[a].s, t[b].e, v)])

    POD_RE = re.compile(r"(?:JGeometry::)?TVec3<\w+>|Mtx|Mtx44|MtxPtr|Vec|GXColor|S16Vec|Quaternion")

    def gen_hoist(self):
        """Move a declaration (split from its initialiser) to the top of an
        enclosing block or of the function (rule 8a: named slots follow it)."""
        t = self.toks
        ds = self.decls()
        names = [t[d[2]].text for d in ds]
        for s, te, ni, init, e in ds:
            name = t[ni].text
            if names.count(name) != 1:
                continue
            tytoks = [t[q].text for q in range(s, te + 1)]
            ty = self.span(s, te)
            if tytoks[-1] == "&":
                continue
            scalar = tytoks[-1] == "*" or all(x in SCALAR_TYPES or x == "const" for x in tytoks)
            if not scalar and not (init is None and self.POD_RE.fullmatch(re.sub(r"\s+", "", ty))):
                continue
            if "const" in tytoks and (init is not None or tytoks[-1] != "*"):
                continue
            bty = ty
            own = self.block_of(s)
            own_end = self.bm.get(own, self.bc)
            if any(t[q].text == name and t[q - 1].text not in (".", "->", "::")
                   for q in list(range(self.bo, s)) + list(range(own_end, self.bc))):
                continue  # the name means something else elsewhere in the function
            chain, b = [], own
            while True:
                chain.append(b)
                if b <= self.bo:
                    break
                b = self.block_of(b)
            for b in chain:
                first = b + 1
                if first >= s or (b == own and init is not None):
                    continue  # already first, or decl-split's own-block hoist
                if t[first].text in ("}", "case", "default"):
                    continue
                where = "function" if b == self.bo else "block L%d" % (self.text.count("\n", 0, t[b].s) + 1)
                eds = [self.insert_before_stmt(first, "%s %s;" % (bty, name))]
                if init is None:
                    eds.append(self.delete_stmt_edit(s, e))
                else:
                    eds.append((t[s].s, t[e].e, "%s = %s;" % (name, self.span(init, e - 1))))
                self.add("decl-hoist", s, "declare %s at top of %s" % (name, where), eds)

    VEC_DECL_RE = re.compile(r"((?:const\s+)?(?:JGeometry\s*::\s*)?TVec3\s*<\s*\w+\s*>)\s+(\w+)\s*(\((.*)\))?\s*;", re.S)

    def gen_vec_ctor(self):
        """`TVec3 v(a, b, c);` <-> `TVec3 v; v.set(a, b, c);`."""
        t = self.toks
        for blk in self.blocks():
            st = self.block_stmts(blk)
            for n, (s, e) in enumerate(st):
                m = self.VEC_DECL_RE.fullmatch(self.span(s, e))
                if not m or m.group(1).startswith("const"):
                    continue
                ty, name = m.group(1), m.group(2)
                ls, pre, alone = self.line_start(s)
                if m.group(3):
                    if not m.group(4).strip() or not alone:
                        continue
                    self.add("vec-set", s, "%s(...) -> .set(...)" % name,
                             [(t[s].s, t[e].e, "%s %s;\n%s%s.set(%s);" % (ty, name, pre, name, m.group(4).strip()))])
                    continue
                # declared bare: its first reference must be a set() statement of this block
                for s2, e2 in st[n + 1:]:
                    refs = [q for q in range(s2, e2 + 1) if t[q].text == name and t[q - 1].text not in (".", "->", "::")]
                    if not refs:
                        continue
                    if refs != [s2] or t[s2 + 1].text != "." or t[s2 + 2].text != "set" or t[s2 + 3].text != "(" \
                            or self.bm.get(s2 + 3) != e2 - 1:
                        break
                    args = self.span(s2 + 4, e2 - 2) if e2 - 2 >= s2 + 4 else ""
                    if not args:
                        break
                    self.add("vec-set", s2, "%s.set(...) -> %s(...)" % (name, name),
                             [self.delete_stmt_edit(s, e), (t[s2].s, t[e2].e, "%s %s(%s);" % (ty, name, args))])
                    break

    ROT = {
        "X": {(0, 0): "1", (0, 1): "0", (0, 2): "0", (0, 3): "0", (1, 0): "0", (1, 1): "c", (1, 2): "-s",
              (1, 3): "0", (2, 0): "0", (2, 1): "s", (2, 2): "c", (2, 3): "0"},
        "Y": {(0, 0): "c", (0, 1): "0", (0, 2): "s", (0, 3): "0", (1, 0): "0", (1, 1): "1", (1, 2): "0",
              (1, 3): "0", (2, 0): "-s", (2, 1): "0", (2, 2): "c", (2, 3): "0"},
        "Z": {(0, 0): "c", (0, 1): "-s", (0, 2): "0", (0, 3): "0", (1, 0): "s", (1, 1): "c", (1, 2): "0",
              (1, 3): "0", (2, 0): "0", (2, 1): "0", (2, 2): "1", (2, 3): "0"},
    }

    def _mtx_store(self, s: int, e: int):
        """`M[i][j] = v;` -> (M text, (i, j), normalised v) or None."""
        t = self.toks
        m = re.fullmatch(r"(.+?)\s*\[\s*([0-3])\s*\]\s*\[\s*([0-3])\s*\]\s*=\s*(-?)\s*([\w.]+)\s*;", self.span(s, e), re.S)
        if not m:
            return None
        v = m.group(5)
        if re.fullmatch(r"[\d.]+[fF]?", v):
            try:
                f = float(v.rstrip("fF"))
            except ValueError:
                return None
            if f not in (0.0, 1.0) or m.group(4):
                return None
            v = "%d" % f
        elif re.fullmatch(r"[A-Za-z_]\w*", v):
            v = m.group(4) + v
        else:
            return None
        return re.sub(r"\s+", "", m.group(1)), (int(m.group(2)), int(m.group(3))), v

    _TRIG = re.compile(r"(JMA(S?)(Sin|Cos))\s*\((.*)\)", re.S)
    _SHORT = [re.compile(r"DEG2SHORTANGLE\s*\((.*)\)", re.S),
              re.compile(r"\(\s*s16\s*\)\s*\(\s*182\.0444\d*f?\s*\*\s*(.*)\)", re.S),
              re.compile(r"\(\s*s16\s*\)\s*\(\s*(.*?)\s*\*\s*182\.0444\d*f?\s*\)", re.S),
              re.compile(r"(.*?)\s*\*\s*\(\s*65536\.0f\s*/\s*360\.0f\s*\)", re.S)]

    def _deg(self, text: str) -> Optional[str]:
        text = text.strip()
        for r in self._SHORT:
            m = r.fullmatch(text)
            if m:
                return m.group(1).strip()  # a whole call argument: no parentheses needed
        return None

    def gen_rot_helper(self):
        """A hand-written rotation matrix -> MsMtxSetRotX/Y/Z (sin/cos/angle become callee objects)."""
        t = self.toks
        ds = {t[ni].text: (s, te, ni, init, e) for s, te, ni, init, e in self.decls()}
        for blk in self.blocks():
            st = self.block_stmts(blk)
            for n in range(len(st) - 11):
                run = [self._mtx_store(*x) for x in st[n:n + 12]]
                if any(r is None for r in run) or len(set(r[0] for r in run)) != 1:
                    continue
                vals = {r[1]: r[2] for r in run}
                if len(vals) != 12:
                    continue
                for axis, tpl in self.ROT.items():
                    cpos = next(p for p, v in tpl.items() if v == "c")
                    spos = next(p for p, v in tpl.items() if v == "s")
                    cn, sn = vals[cpos], vals[spos]
                    if not re.fullmatch(r"[A-Za-z_]\w*", cn) or not re.fullmatch(r"[A-Za-z_]\w*", sn) or cn == sn:
                        continue
                    sub = {"c": cn, "s": sn, "-s": "-" + sn}
                    want = {p: sub.get(v, v) for p, v in tpl.items()}
                    if want != vals:
                        continue
                    self._emit_rot(axis, run[0][0], cn, sn, st, n, ds, blk)

    def _emit_rot(self, axis, mtx, cn, sn, st, n, ds, blk):
        t = self.toks
        run_s, run_e = st[n][0], st[n + 11][1]
        dels, angles = [], {}
        for nm, fn in ((sn, "Sin"), (cn, "Cos")):
            d = ds.get(nm)
            if d is None or d[3] is None or self.block_of(d[0]) != blk or d[0] > run_s:
                return
            uses = [q for q in range(self.bo, self.bc) if t[q].text == nm and t[q - 1].text not in (".", "->")]
            if len([q for q in uses if not (run_s <= q <= run_e)]) != 1:
                return  # used outside its declaration and the run
            m = self._TRIG.fullmatch(self.span(d[3], d[4] - 1))
            if not m or m.group(3) != fn:
                return
            arg = m.group(4).strip()
            if not m.group(2):  # JMASin(deg)
                angles[nm] = arg
            else:
                deg = self._deg(arg)
                if deg is None and re.fullmatch(r"[A-Za-z_]\w*", arg) and arg in ds and ds[arg][3] is not None:
                    a = ds[arg]
                    auses = [q for q in range(self.bo, self.bc) if t[q].text == arg and t[q - 1].text not in (".", "->")]
                    if len(auses) != 3:
                        return
                    deg = self._deg(self.span(a[3], a[4] - 1))
                    if deg is None or self.block_of(a[0]) != blk:
                        return
                    dels.append(a)
                if deg is None:
                    return
                angles[nm] = deg
            dels.append(d)
        if angles[sn] != angles[cn]:
            return
        dels = sorted(set(dels))
        # everything between the first removed declaration and the run must be
        # another removed declaration or a bare declaration (nothing reordered)
        first = dels[0][0]
        for s, e in st:
            if first <= s < run_s and not any(s == d[0] for d in dels):
                if self._stmt_extent(s) != e or t[e - 1].kind != "id" or t[e].text != ";" or \
                        any(t[q].text in ASSIGN_OPS or t[q].text == "(" for q in range(s, e)):
                    return
        eds = [self.delete_stmt_edit(d[0], d[4]) for d in dels]
        eds.append((t[run_s].s, t[run_e].e, "MsMtxSetRot%s(%s, %s);" % (axis, mtx, angles[sn])))
        self.add("rot-helper", run_s, "MsMtxSetRot%s(%s, %s)" % (axis, mtx, angles[sn][:30]), eds)

    GXCOLOR_RE = re.compile(r"(?:const\s+)?GXColor\s+(\w+)\s*=\s*(\{[\s\w,()]*\})\s*;")

    def gen_static_color(self):
        """A constant local GXColor -> a file-scope `static const GXColor`."""
        t = self.toks
        for blk in self.blocks():
            for s, e in self.block_stmts(blk):
                m = self.GXCOLOR_RE.fullmatch(self.span(s, e))
                if not m:
                    continue
                name = m.group(1)
                uses = [q for q in range(e + 1, self.bc) if t[q].text == name and t[q - 1].text not in (".", "->", "::")]
                if not uses or any(t[q - 1].text == "&" or t[q + 1].text in ASSIGN_OPS
                                   or (t[q + 1].text == "." and t[q + 3].text in ASSIGN_OPS) for q in uses):
                    continue
                st = self.stem[:1].upper() + self.stem[1:]
                gname = self.fresh("s" + st + name[:1].upper() + name[1:])
                helper = "static const GXColor %s = %s;\n" % (gname, re.sub(r"\s+", " ", m.group(2)))
                eds = [self.helper_edit(helper), self.delete_stmt_edit(s, e)]
                eds += [(t[q].s, t[q].e, gname) for q in uses]
                self.add("static-color", s, "%s -> file-scope %s" % (name, gname), eds)

    def _unused_stubs(self):
        t = self.toks
        out = []
        for i in range(1, len(t) - 5):
            if not (t[i].kind == "id" and t[i + 1].text == "::" and t[i + 2].kind == "id"
                    and t[i + 3].text == "(" and t[i + 4].text == ")"):
                continue
            k = i + 5
            while t[k].text == "const":
                k += 1
            if t[k].text != "{" or k not in self.bm or t[i - 1].text != "void":
                continue
            if self.loc.bo == k:
                continue
            h = i - 1
            gap = self.text[t[h - 1].e if h > 0 else 0:t[h].s]
            if "UNUSED" not in gap:
                continue
            body = [x.text for x in t[k + 1:self.bm[k]]]
            body = _strip_recv(body, "this")
            if body:
                out.append((t[i].text, t[i + 2].text, body))
        return out

    def gen_unused_stub(self):
        """Statements equal to an UNUSED stub's body -> a call to the stub
        (its locals and temporaries become callee objects, one depth down)."""
        stubs = self._unused_stubs()
        if not stubs:
            return
        t = self.toks
        klass = self.loc.info.klass
        for blk in self.blocks():
            st = self.block_stmts(blk)
            for i in range(len(st)):
                first = st[i][0]
                recv = t[first].text if t[first].kind == "id" and t[first + 1].text == "->" else None
                for j in range(i, min(len(st), i + 8)):
                    toks = [x.text for x in t[first:st[j][1] + 1]]
                    # a local of this function would bind to a member in the stub
                    free = [q for q in range(first, st[j][1] + 1) if t[q].kind == "id"
                            and t[q - 1].text not in (".", "->", "::") and t[q].text != recv]
                    if any(t[q].text in self.var_types for q in free):
                        continue
                    for cls, name, body in stubs:
                        if len(toks) > len(body) + 64:
                            continue
                        hit = None
                        if cls == klass and _strip_recv(toks, "this") == body:
                            hit = "%s();" % name
                        elif recv and recv != "this" and _strip_recv(toks, recv) == body:
                            vt = self.var_types.get(recv, "")
                            if vt and cls not in vt:
                                continue
                            hit = "%s->%s();" % (recv, name)
                        if hit:
                            self.add("unused-stub", first, "%s for %d statement(s)" % (hit, j - i + 1),
                                     [(t[first].s, t[st[j][1]].e, hit)])

    def gen_chain_binders(self):
        """Bind the END of an accessor chain in a TU-local inline
        (`{ T* p = a->getB()->getC(); return p; }`), or fork it."""
        t, idx, info = self.toks, self.idx, self.loc.info
        tu_static = set(re.findall(r"\bstatic\s+inline\s+[\w:<>\s*&]*?\b(\w+)\s*\(\s*\)", self.text))
        unwrap = {}
        for nm in tu_static:
            m = re.search(r"\b%s\s*\(\s*\)\s*\{\s*(?:[\w:<>]+\s*\*\s*(\w+)\s*=\s*([\w>\-.()]+?)\s*;\s*return\s+\1\s*;"
                          r"|return\s+([\w>\-.()]+?)\s*;)\s*\}" % re.escape(nm), self.text)
            if m:
                unwrap[nm] = m.group(2) or m.group(3)
        cq = "const " if info.const else ""
        groups: Dict[str, List] = {}
        for k in range(self.bo + 1, self.bc):
            if t[k].kind != "id" or t[k - 1].text in (".", "->", "::") or t[k].text in KEYWORDS:
                continue
            name = t[k].text
            heads = []  # (head_end, [head exprs], params, callargs, counts_as_link)
            if name in idx.globals and idx.globals[name].endswith("*") and t[k + 1].text == "->":
                heads = [(k, [name], "", "", False)]
            elif t[k + 1].text == "(" and t[k + 2].text == ")" and t[k + 3].text in ("->", "."):
                if name in tu_static or (name[:1].isupper() and idx.rettype(name)):
                    hx = ["%s()" % name] + ([unwrap[name]] if name in unwrap else [])
                    heads = [(k + 2, hx, "", "", True)]
                elif info.klass and name[:1].islower() and idx.rettype(name) and name not in tu_static:
                    heads = [(k + 2, ["self->%s()" % name], "%s%s* self" % (cq, info.klass), "this", True)]
            elif name in self.var_types and t[k + 1].text == "->" and self.var_types[name].rstrip().endswith("*"):
                heads = [(k, [name], "%s %s" % (self.var_types[name].strip(), name), name, False)]
            elif info.klass and t[k + 1].text == "->" and (idx.unique("member_types", name) or "").endswith("*"):
                heads = [(k, ["self->%s" % name], "%s%s* self" % (cq, info.klass), "this", False)]
            for head_end, hexprs, params, callargs, head_link in heads:
                links, j = [], head_end
                if name in idx.globals:
                    cur = idx.globals[name]
                elif t[k + 1].text == "->" and name not in self.var_types:
                    cur = idx.unique("member_types", name)
                elif t[k + 1].text == "(":
                    cur = idx.method_rettype(info.klass if callargs == "this" else None, name)
                else:
                    cur = self.var_types.get(name)
                while t[j + 1].text in ("->", ".") and t[j + 2].kind == "id" and t[j + 3].text == "(" \
                        and t[j + 4].text == ")" and (t[j + 2].text in idx.getters
                                                     or re.match(r"(get|Get|is)[A-Z_]", t[j + 2].text)):
                    rt = idx.method_rettype(_type_class(cur), t[j + 2].text)
                    cur = rt
                    if rt is None or rt == "void" or re.search(r"\b[A-Z]\b", rt) or \
                            rt.split()[0] in ("virtual", "static", "inline"):
                        break
                    links.append((j + 4, t[j + 2].text, rt))
                    if not rt.endswith("*"):
                        break
                    j += 4
                for li, (end, g, rt) in enumerate(links):
                    if params and callargs != "this" and li < 1:
                        continue  # one accessor over a local receiver: rule 4, inert
                    if not (rt.endswith("*") or rt in SCALAR_TYPES):
                        continue
                    if t[end + 1].text in ASSIGN_OPS or t[end + 1].text in ("++", "--"):
                        continue
                    tail = self.span(head_end + 1, end)
                    gname = re.sub(r"^(get|Get)(?=[A-Z])", "", g)
                    for hi, hx in enumerate(hexprs):
                        expr = hx + tail
                        for form in ("binder", "fork"):
                            body = ("\t%s r = %s;\n\treturn r;" % (rt, expr)) if form == "binder" else \
                                "\treturn %s;" % expr
                            base = self.stem + "Get" + gname[:1].upper() + gname[1:] + \
                                ("Bound" if form == "binder" else "")
                            fn = self.fresh(base, None, body)
                            helper = "static inline %s %s(%s)\n{\n%s\n}\n\n" % (rt, fn, params, body)
                            site = (t[k].s, t[end].e, "%s(%s)" % (fn, callargs))
                            self.add("chain-" + form, k, "%s(%s) = %s" % (fn, callargs, expr[:50]),
                                     [self.helper_edit(helper), site])
                            groups.setdefault((form, fn), [self.helper_edit(helper)]).append(site)
        for (form, fn), eds in groups.items():
            if len(eds) > 2:
                self.add("chain-" + form, self.bo, "%s at all %d sites" % (fn, len(eds) - 1), eds)

    def lint(self) -> List[str]:
        """Implausible spellings in the current body (reject exact variants with these)."""
        t = self.toks
        pnames = set(t[p[-1]].text for p in self.params if p and t[p[-1]].kind == "id")
        local: Dict[str, str] = {}
        bad = []
        for s_, te, ni, init, e in self.decls():
            ty = re.sub(r"\s+", " ", self.span(s_, te))
            if init is not None and e == init + 1 and t[init].kind == "id" \
                    and t[init].text in local and t[init].text not in pnames \
                    and local[t[init].text] == ty:  # a converting copy is real code
                bad.append("copy of local %s into %s" % (t[init].text, t[ni].text))
            local[t[ni].text] = ty
        return bad

    def generate(self, levers=None) -> List[Cand]:
        gens = [self.gen_accessors, self.gen_null_tests, self.gen_int_types, self.gen_decls,
                self.gen_name_calls, self.gen_wrappers, self.gen_compound, self.gen_setters,
                self.gen_rot_helper, self.gen_static_color, self.gen_unused_stub, self.gen_chain_binders,
                self.gen_name_conv, self.gen_hoist, self.gen_vec_ctor, self.gen_name_reads]
        for g in gens:
            try:
                g()
            except (IndexError, KeyError, TypeError, ValueError, AttributeError) as ex:  # heuristic parser hit something odd
                print("  (generator %s skipped: %r)" % (g.__name__, ex), file=sys.stderr)
        out, seen = [], set()
        for c in self.cands:
            if levers and c.lever not in levers:
                continue
            key = tuple(sorted(c.edits))
            if key in seen or not c.edits:
                continue
            seen.add(key)
            out.append(c)
        out.sort(key=lambda c: (c.prio, c.edits[0][0]))
        for i, c in enumerate(out):
            c.cid = i
        return out


def _strip_recv(toks: List[str], recv: str) -> List[str]:
    """Drop every `recv ->` that heads a postfix expression."""
    out, i = [], 0
    while i < len(toks):
        if toks[i] == recv and i + 1 < len(toks) and toks[i + 1] == "->" and \
                (i == 0 or toks[i - 1] not in (".", "->", "::")):
            i += 2
            continue
        out.append(toks[i])
        i += 1
    return out


def _acc_prio(getter: str, expr: str) -> int:
    """Accessors whose name spells the member (getSpine/mSpine) rank first."""
    g = re.sub(r"^(get|is)", "", getter).lower()
    m = re.sub(r"^\*?(this->)?(m|gp|g|unk)?", "", expr.split(".")[-1].split("->")[-1]).lower()
    return 1 if g and (g == m or g in m or m in g) else 3


def apply_edits(text: str, edits: List[Tuple[int, int, str]]) -> Optional[str]:
    uniq = []
    for e in edits:  # the same helper inserted by two combined candidates
        if e not in uniq:
            uniq.append(e)
    eds = sorted(enumerate(uniq), key=lambda x: (x[1][0], x[1][1], x[0]))
    out, pos = [], 0
    for _, (s, e, r) in eds:
        if s < pos:
            return None
        out.append(text[pos:s])
        out.append(r)
        pos = e
    out.append(text[pos:])
    return "".join(out)


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

class Searcher:
    def __init__(self, unit: str, fn: str, source: Optional[str], jobs: int, work: str,
                 verbose: bool = True):
        self.unit, self.fn, self.jobs, self.verbose = unit, fn, jobs, verbose
        ud = json.load(open(os.path.join(ROOT, "objdiff.json")))
        us = [u for u in ud["units"] if u["name"] == unit or u["name"].endswith("/" + unit)]
        if not us:
            raise SystemExit("unit not found: " + unit)
        self.u = us[0]
        self.rel_src = self.u["metadata"]["source_path"]
        self.target = os.path.join(ROOT, self.u["target_path"])
        self.obj = self.u["base_path"]
        if not os.path.exists(self.target):
            raise SystemExit("retail object missing (run ninja once): " + self.target)
        self.ninja = _NINJA.get() if _NINJA else NinjaDB(os.path.join(ROOT, "build.ninja"))
        self.orig_path = os.path.join(ROOT, self.rel_src)
        with open(source or self.orig_path, encoding="utf-8", newline="") as f:
            self.base_text = f.read()
        self.work = tempfile.mkdtemp(prefix="lever-", dir=work)
        self.shadows = queue.Queue()
        for i in range(jobs):
            self.shadows.put(Shadow(self.work, self.rel_src, i))
        self.cache: Dict[str, Tuple[Score, Optional[dict]]] = {}
        self.builds = 0
        self.build_time = 0.0
        self.lock = threading.Lock()

    def close(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    def compile(self, text: str, keep_json: bool = False) -> Tuple[Score, Optional[dict]]:
        h = hashlib.md5(text.encode("utf-8", "surrogateescape")).hexdigest()
        with self.lock:
            if h in self.cache and (not keep_json or self.cache[h][1] is not None):
                return self.cache[h]
        sh = self.shadows.get()
        try:
            sh.write(text)
            out = os.path.join(sh.out, os.path.splitext(os.path.basename(self.rel_src))[0] + ".o")
            if os.path.exists(out):
                os.remove(out)
            cmd = self.ninja.compile_command(self.obj, self.rel_src, sh.out)
            t0 = time.time()
            r = subprocess.run(cmd, shell=True, cwd=sh.dir, capture_output=True, text=True, errors="replace")
            dt = time.time() - t0
            with self.lock:
                self.builds += 1
                self.build_time += dt
            if r.returncode or not os.path.exists(out):
                msg = [l for l in (r.stdout + r.stderr).splitlines() if l.strip() and not l.startswith("#   ")]
                res = (Score(False, " ".join(msg[-3:]) if msg else "compile failed"), None)
            else:
                data = objdiff_json(self.target, out)
                res = (score_function(data, self.fn), data if keep_json else None)
        finally:
            self.shadows.put(sh)
        with self.lock:
            self.cache[h] = res
        return res

    def tidy_names(self, text: str) -> str:
        """Rename generated `name2`, `name3`... locals to `name` where the function
        leaves it free (local names do not reach the object code; re-verified)."""
        toks = lex(text)
        bm = bracket_map(toks)
        loc = locate_function(toks, bm, self.info)
        if loc is None:
            return text
        old_ids = set(t.text for t in lex(self.base_text) if t.kind == "id")
        fn_ids = set(t.text for t in toks[loc.head:loc.bc + 1] if t.kind == "id")
        ren = {}
        for n in sorted(fn_ids - old_ids):
            m = re.fullmatch(r"([A-Za-z_]\w*?)(\d+)", n)
            if m and m.group(1) not in fn_ids and m.group(1) not in ren.values() and m.group(1) not in KEYWORDS:
                ren[n] = m.group(1)
        if not ren:
            return text
        out, pos = [], 0
        for t in toks[loc.head:loc.bc + 1]:
            if t.kind == "id" and t.text in ren:
                out.append(text[pos:t.s])
                out.append(ren[t.text])
                pos = t.e
        out.append(text[pos:])
        return "".join(out)

    def lint(self, text: str) -> List[str]:
        toks = lex(text)
        bm = bracket_map(toks)
        loc = locate_function(toks, bm, self.info)
        if loc is None:
            return []
        return ["implausible: " + x for x in Gen(text, loc, toks, bm, self.index).lint()
                if x not in self.base_lint]

    def check_unit(self, text: str) -> List[str]:
        _, bd = self.compile(self.base_text, keep_json=True)
        _, vd = self.compile(text, keep_json=True)
        if bd is None or vd is None:
            return ["compile failed"]
        # a file-scope constant the variant introduced (static-color) takes the
        # place of the compiler's @NNNN literal: its bytes are scored by the section
        decl = r"\bstatic\s+const\s+[\w:]+\s+(\w+)\s*="
        new_statics = set(re.findall(decl, text)) - set(re.findall(decl, self.base_text))
        bad = regressions(unit_profile(bd), unit_profile(vd), self.fn)
        if new_statics:
            bad = [b for b in bad if not re.match(r"@\d+ [\d.]+->gone$|@\d+ 100\.00->0\.00$", b)
                   and b[len("extra symbol "):] not in new_statics]
        return bad

    def run(self, budget: float, max_builds: int, beam: int = 6, topk: int = 16, depth: int = 4,
            levers=None, list_only: bool = False, rounds: int = 3):
        t_start = time.time()
        text = self.base_text
        toks = lex(text)
        bm = bracket_map(toks)
        merged = merged_index(text)

        base_score, base_json = self.compile(text, keep_json=True)
        result = {"unit": self.unit, "fn": self.fn, "base": base_score, "best": base_score, "best_desc": "",
                  "exact": base_score.exact, "patch": None, "status": "base", "cands": 0, "regress": []}
        if not base_score.ok:
            result["status"] = "base-fail: " + base_score.err
            return result
        dm = next((s.get("demangled_name", s["name"]) for s in base_json["left"]["symbols"]
                   if s.get("name") == self.fn), self.fn)
        self.log("%s  [%s]" % (dm, self.unit))
        self.log("  base: %s" % base_score.short())
        if base_score.exact:
            result["status"] = "already-exact"
            return result
        info = parse_demangled(dm)
        self.info, self.index, self.base_lint = info, merged, set()
        self.base_lint = set(x[len("implausible: "):] for x in self.lint(text))
        deadline = t_start + budget
        builds0 = self.builds
        found = []
        root_text, root_score, chain = text, base_score, []
        best_all = (base_score.dist, base_score, [], None)

        def over():
            return time.time() > deadline or self.builds - builds0 >= max_builds

        for rnd in range(1, max(1, rounds) + 1):
            toks = lex(root_text)
            bm = bracket_map(toks)
            loc = locate_function(toks, bm, info)
            if loc is None:
                result["status"] = "not-located"
                self.log("  could not locate the definition of %s in %s" % (dm, self.rel_src))
                return result
            cands = Gen(root_text, loc, toks, bm, merged).generate(levers)
            if rnd == 1:
                result["cands"] = len(cands)
            self.log("  round %d: %d candidates (%s)" % (rnd, len(cands), ", ".join(
                "%s %d" % (l, n) for l, n in sorted(_count(c.lever for c in cands).items()))))
            if list_only:
                for c in cands:
                    self.log("   [%d] %s" % (c.cid, c.desc))
                result["status"] = "listed"
                return result
            done, best = self._round(root_text, root_score, cands, found, chain, over, beam, topk, depth, t_start)
            if best[1].ok and best[0] < best_all[0]:
                best_all = best
            if done or over() or not best[2] or best[0] >= root_score.dist:
                break
            # re-root on the best variant and regenerate: lets dependent levers chain
            root_text, root_score = best[3], best[1]
            chain = best[2]
            self.log("  re-rooting on: %s" % " + ".join(chain))

        clean = [f for f in found if not f[2]]
        pick = clean[0] if clean else (found[0] if found else None)
        if pick:
            descs, t2, reg = pick
            if not reg:
                t3 = self.tidy_names(t2)
                if t3 != t2 and self.compile(t3)[0].exact and not self.check_unit(t3):
                    t2 = t3
            result.update(exact=not reg, status="exact" if not reg else "exact-regresses",
                          best=self.compile(t2)[0], best_desc=" + ".join(descs), regress=reg, text=t2)
        else:
            result.update(best=best_all[1], best_desc=" + ".join(best_all[2]),
                          status="improved" if best_all[2] else "no-gain",
                          text=best_all[3] if best_all[2] else None)
        result["builds"] = self.builds - builds0
        result["secs"] = time.time() - t_start
        self.log("  result: %s  %s  (%d builds, %.1fs, %.1f builds/s)" % (
            result["status"], result["best"].short(), result["builds"], result["secs"],
            result["builds"] / max(result["secs"], 1e-6)))
        if result["best_desc"]:
            self.log("  levers: " + result["best_desc"])
        return result

    def _round(self, text, base_score, cands, found, chain, over, beam, topk, depth, t_start):
        """Singles then a beam over combinations, all rooted at `text`.
        Returns (exact_found, (dist, score, lever_descs, text))."""
        best = (base_score.dist, base_score, [], text)

        def evaluate(combos):
            jobs = []
            for combo in combos:
                nt = apply_edits(text, [e for c in combo for e in cands[c].edits])
                if nt is not None:
                    jobs.append((combo, nt))
            out = []
            hit = False
            with cf.ThreadPoolExecutor(self.jobs) as ex:
                futs = {}
                for combo, nt in jobs:
                    if over() or hit:
                        break
                    futs[ex.submit(self.compile, nt)] = (combo, nt)
                    if len(futs) >= self.jobs * 2:
                        fin, _ = cf.wait(futs, return_when=cf.FIRST_COMPLETED)
                        for f in fin:
                            c, t2 = futs.pop(f)
                            out.append((c, f.result()[0], t2))
                            hit = hit or out[-1][1].exact
                for f in cf.as_completed(futs):
                    c, t2 = futs[f]
                    out.append((c, f.result()[0], t2))
            return out

        def consider(res):
            nonlocal best
            for combo, sc, t2 in sorted(res, key=lambda r: r[1].dist):
                descs = chain + [cands[c].desc for c in combo]
                if sc.ok and sc.dist < best[0]:
                    best = (sc.dist, sc, descs, t2)
                if sc.exact:
                    reg = self.lint(t2) + self.check_unit(t2)
                    self.log("  EXACT%s: %s" % (" (regresses: %s)" % "; ".join(reg[:3]) if reg else "",
                                               " + ".join(descs)))
                    found.append((descs, t2, reg))
                    if not reg:
                        return True
            return False

        res = evaluate([(c.cid,) for c in cands])
        singles = {r[0][0]: r[1] for r in res}
        nfail = sum(1 for x in singles.values() if not x.ok)
        self.log("  singles: %d built (%d failed to compile), %.1fs" % (len(singles), nfail, time.time() - t_start))
        if consider(res):
            return True, best
        pool = sorted((cid for cid, x in singles.items() if x.ok and
                       (x.dist < base_score.dist or (x.sig != base_score.sig and x.n_struct <= base_score.n_struct))),
                      key=lambda c: singles[c].dist)
        uniq, sigs = [], set()  # distinct objects only: many rewrites compile identically
        for c in pool:
            if singles[c].sig not in sigs:
                sigs.add(singles[c].sig)
                uniq.append(c)
        pool = uniq[:topk]
        inert = [cid for cid, x in sorted(singles.items()) if x.ok and x.sig == base_score.sig
                 and cands[cid].lever in ("acc->raw", "raw->acc") and len(cands[cid].edits) == 1]
        pool += inert[:max(0, min(4, topk - len(pool)))]
        for c in pool[:8]:
            self.log("    %-52s %s" % (cands[c].desc[:52], singles[c].short()))
        states = [((c,), singles[c]) for c in pool[:beam]]
        seen = set((c,) for c in pool)
        d = 1
        while not over() and states and d < depth:
            d += 1
            combos = []
            for combo, _ in states:
                for c in pool:
                    nc = tuple(sorted(set(combo + (c,))))
                    if len(nc) == len(combo) + 1 and nc not in seen:
                        seen.add(nc)
                        combos.append(nc)
            if not combos:
                break
            res = evaluate(combos)
            if consider(res):
                return True, best
            nxt, sg = [], set()
            for combo, sc, _ in sorted((r for r in res if r[1].ok), key=lambda r: r[1].dist):
                if sc.sig not in sg:
                    sg.add(sc.sig)
                    nxt.append((combo, sc))
            states = nxt[:beam]
            if states:
                self.log("  depth %d: %d combos, best %s  [%s]" % (
                    d, len(res), states[0][1].short(), " + ".join(cands[c].desc[:40] for c in states[0][0])))
        return False, best

    def patch(self, text: str) -> str:
        a = self.base_text.splitlines(keepends=True)
        b = text.splitlines(keepends=True)
        return "".join(difflib.unified_diff(a, b, "a/" + self.rel_src, "b/" + self.rel_src))


def _count(it):
    d = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return d


class _NinjaCache:
    def __init__(self):
        self.db = None

    def get(self):
        if self.db is None:
            self.db = NinjaDB(os.path.join(ROOT, "build.ninja"))
        return self.db


_NINJA = _NinjaCache()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def safe_name(fn: str) -> str:
    return re.sub(r"[^\w.-]", "_", fn)[:120]


def run_one(args, unit, fn, source=None):
    s = Searcher(unit, fn, source, args.jobs, args.work, verbose=not args.quiet)
    try:
        r = s.run(args.budget, args.max_builds, beam=args.beam, topk=args.topk, depth=args.depth,
                  rounds=args.rounds,
                  levers=set(args.levers.split(",")) if args.levers else None, list_only=args.list_cands)
        if r.get("text") and (r["exact"] or args.save_improved):
            os.makedirs(args.out, exist_ok=True)
            p = os.path.join(args.out, safe_name(fn) + (".patch" if r["exact"] else ".improved.patch"))
            with open(p, "w", encoding="utf-8") as f:
                f.write("# %s %s: %s\n# levers: %s\n" % (unit, fn, r["status"], r["best_desc"]))
                f.write(s.patch(r["text"]))
            r["patch"] = p
            if not args.quiet:
                print("  patch: " + p)
        return r
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="Deterministic lever search for near-exact functions")
    ap.add_argument("-u", "--unit", help="unit, e.g. Enemy/rocket (objdiff name suffix)")
    ap.add_argument("-f", "--function", help="mangled function name")
    ap.add_argument("--source", help="score this file in place of the unit's .cpp (never written)")
    ap.add_argument("--list", help="TSV (size, fuzzy, unit, function) for batch mode")
    ap.add_argument("--min-fuzzy", type=float, default=99.0)
    ap.add_argument("--limit", type=int, default=0, help="batch: stop after N functions")
    ap.add_argument("--skip", type=int, default=0, help="batch: skip the first N rows")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "lever-search"),
                    help="directory for results.tsv and patches")
    ap.add_argument("--work", default=tempfile.gettempdir(), help="parent for temporary shadow roots")
    ap.add_argument("--budget", type=float, default=90.0, help="seconds per function")
    ap.add_argument("--max-builds", type=int, default=3000)
    ap.add_argument("-j", "--jobs", type=int, default=max(1, min(3, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--beam", type=int, default=6)
    ap.add_argument("--topk", type=int, default=16)
    ap.add_argument("--depth", type=int, default=4, help="max rewrites combined per round")
    ap.add_argument("--rounds", type=int, default=3, help="re-root on the best variant this many times")
    ap.add_argument("--levers", help="comma-separated lever filter (e.g. acc->raw,raw->acc,null-test)")
    ap.add_argument("--list-cands", action="store_true", help="only print the generated candidates")
    ap.add_argument("--save-improved", action="store_true", help="also save non-exact best variants")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    if args.list:
        rows = []
        for line in open(args.list, encoding="utf-8"):
            p = line.rstrip("\n").split("\t")
            if len(p) < 4 or not p[0].isdigit():
                continue
            if float(p[1]) < args.min_fuzzy:
                continue
            rows.append((int(p[0]), float(p[1]), p[2], p[3]))
        rows = rows[args.skip:]
        if args.limit:
            rows = rows[:args.limit]
        os.makedirs(args.out, exist_ok=True)
        res_path = os.path.join(args.out, "results.tsv")
        done = set()
        if os.path.exists(res_path):
            for line in open(res_path, encoding="utf-8"):
                done.add(tuple(line.split("\t")[:2]))
        else:
            with open(res_path, "w", encoding="utf-8") as f:
                f.write("unit\tfunction\tsize\tfuzzy_before\tbest_fuzzy\tbest_dist\texact\tstatus\t"
                        "builds\tsecs\tpatch\tlevers\n")
        for size, fz, unit, fn in rows:
            if (unit, fn) in done:
                continue
            try:
                r = run_one(args, unit, fn)
            except SystemExit as ex:
                r = {"status": "error: %s" % ex, "best": Score(False), "exact": False, "builds": 0, "secs": 0}
            except Exception as ex:  # keep the batch going
                r = {"status": "error: %r" % ex, "best": Score(False), "exact": False, "builds": 0, "secs": 0}
            b = r.get("best") or Score(False)
            with open(res_path, "a", encoding="utf-8") as f:
                f.write("\t".join(str(x) for x in (
                    unit, fn, size, fz, "%.3f" % b.fuzzy if b.ok else "-", "%.1f" % b.dist if b.ok else "-",
                    "yes" if r.get("exact") else "no", r.get("status", ""), r.get("builds", 0),
                    "%.1f" % r.get("secs", 0), r.get("patch") or "", re.sub(r"\s+", " ", r.get("best_desc") or ""))) + "\n")
        print("results: " + res_path)
        return
    if not (args.unit and args.function):
        ap.error("need -u and -f, or --list")
    r = run_one(args, args.unit, args.function, args.source)
    sys.exit(0 if r.get("exact") else 1)


if __name__ == "__main__":
    main()
