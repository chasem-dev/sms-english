#!/usr/bin/env python3
"""Find non-exact functions that repeat another function's body.

A statement sequence in a non-exact function that repeats the body of another
function (a member of the same TU, an inline body in one of the TU's headers,
or an UNUSED map function whose body is in some .cpp) usually means retail
called that function and it was inlined (docs/catalog/frame-model.md,
"Refinements c-k13": the copied `r != 0 ? true : false` of
isTouchedWallsAndMoveXZ).

Matching is a normalised token-sequence alignment:
- comments and preprocessor lines are dropped;
- `this->` is dropped and every member chain `a->b.c` collapses to its last
  component, so `p->mData->f(x)` in the caller matches `mData->f(x)` in a
  member body;
- a candidate's parameters become wildcards matching any balanced run of up
  to 12 caller tokens, and its locals match any single token or wildcard;
- `return` and its `;` are dropped from the candidate (its value is folded
  into the caller's expression).
The alignment is an edit distance of the whole candidate against the best
window of the caller (a caller token inside the copy costs 0.3, so a named local
split off the copied expression still matches); a hit needs similarity
>= --min-sim and at least
--min-info informative tokens (identifiers other than keywords, and literals).

Usage:
  python3 tools/find-copied-bodies.py [-u UNIT ...] [--min-sim 0.85]
      [--min-info 4] [--source FILE] [-v]
Run from the repository root after a build (it reads build/GMSE01/report.json).
"""

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "build", "GMSE01", "report.json")
MAP = os.path.join(ROOT, "orig", "GMSE01", "files", "marioUS.MAP")
INCLUDE_DIRS = [os.path.join(ROOT, "include")] + sorted(glob.glob(os.path.join(ROOT, "libs", "*", "include")))

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

KEYWORDS = set("""if else while for do switch case default return break continue goto
sizeof new delete this operator static const volatile inline virtual extern typedef
struct class union enum public private protected template typename namespace using
true false nullptr NULL void int unsigned signed char short long float double bool
u8 s8 u16 s16 u32 s32 f32 f64 BOOL TRUE FALSE""".split())
NOT_FUNCS = {"if", "while", "for", "switch", "catch", "return", "sizeof", "defined"}
INFO_EXCLUDE = KEYWORDS - {"true", "false", "nullptr", "TRUE", "FALSE", "NULL"}


@dataclass
class Tok:
    kind: str
    text: str
    line: int


def lex(text: str) -> List[Tok]:
    out, pos, line = [], 0, 1
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            pos += 1
            continue
        k = m.lastgroup
        if k not in ("ws", "lc", "bc", "pp"):
            out.append(Tok(k, m.group(), line))
        line += m.group().count("\n")
        pos = m.end()
    return out


def bracket_map(toks: List[Tok]) -> Dict[int, int]:
    m, st = {}, []
    for i, t in enumerate(toks):
        if t.kind != "op":
            continue
        if t.text in "([{":
            st.append(i)
        elif t.text in ")]}" and st:
            j = st.pop()
            m[j], m[i] = i, j
    return m


@dataclass
class Func:
    qname: str          # Class::name (best effort)
    name: str
    params: List[str]
    body: List[Tok]     # tokens between the braces
    path: str
    line: int
    origin: str = ""    # tu / header / unused
    local_inline: bool = False  # `static inline` / `inline` written in this file
    norm: List[str] = field(default_factory=list)


def _param_names(toks: List[Tok], lp: int, rp: int) -> List[str]:
    names, depth, cur = [], 0, []
    for t in toks[lp + 1:rp] + [Tok("op", ",", 0)]:
        if t.text in "(<[":
            depth += 1
        elif t.text in ")>]":
            depth -= 1
        if t.text == "," and depth == 0:
            ids = []
            d2 = 0
            for c in cur:
                if c.text == "=" and d2 == 0:
                    break
                if c.text in "(<[":
                    d2 += 1
                elif c.text in ")>]":
                    d2 -= 1
                elif c.kind == "id" and d2 == 0:
                    ids.append(c.text)
            if len(ids) >= 2 and ids[-1] not in KEYWORDS:
                names.append(ids[-1])
            cur = []
        else:
            cur.append(t)
    return names


def extract_functions(path: str, text: Optional[str] = None) -> List[Func]:
    if text is None:
        text = open(path, encoding="utf-8", errors="replace").read()
    toks = lex(text)
    bm = bracket_map(toks)
    out: List[Func] = []
    scopes: List[str] = []   # class names ('' for other scopes)
    ends: List[int] = []
    i = 0
    n = len(toks)
    while i < n:
        while ends and i > ends[-1]:
            ends.pop()
            scopes.pop()
        t = toks[i]
        if not (t.kind == "op" and t.text == "{") or i not in bm:
            i += 1
            continue
        close = bm[i]
        # function body?  ... name ( params ) [const] [: init-list] {
        k = i - 1
        while k >= 0 and toks[k].text in ("const", "volatile"):
            k -= 1
        # skip a constructor initialiser list backwards
        j = k
        if toks[j].text == ")" and j in bm:
            lp = bm[j]
            # walk back over "a(b), c(d)" to a ':' preceded by ')'
            q = lp - 1
            while q > 0 and toks[q].kind == "id" and toks[q - 1].text in (",", ":"):
                if toks[q - 1].text == ":" and toks[q - 2].text == ")":
                    j = q - 2
                    break
                if toks[q - 2].text == ")" and (q - 2) in bm:
                    q = bm[q - 2] - 1
                else:
                    break
        fn = None
        if toks[j].text == ")" and j in bm:
            lp = bm[j]
            nm = lp - 1
            if nm >= 0 and toks[nm].kind == "id" and toks[nm].text not in NOT_FUNCS:
                name = toks[nm].text
                params = _param_names(toks, lp, j)
                qual = []
                q = nm - 1
                if q >= 0 and toks[q].text == "~":
                    name = "~" + name
                    q -= 1
                while q >= 1 and toks[q].text == "::":
                    r = q - 1
                    if toks[r].text == ">":
                        d = 0
                        while r >= 0:
                            if toks[r].text == ">":
                                d += 1
                            elif toks[r].text == "<":
                                d -= 1
                                if d == 0:
                                    r -= 1
                                    break
                            r -= 1
                    if r >= 0 and toks[r].kind == "id":
                        qual.insert(0, toks[r].text)
                        q = r - 1
                    else:
                        break
                if name.isupper() and "_" in name and lp + 1 < n and toks[lp + 1].kind == "id":
                    # DEFINE_NERVE(Name, T) { ... }
                    qual, name = [toks[lp + 1].text], "execute"
                    params = ["spine"]
                cls = [s for s in scopes if s]
                qname = "::".join((cls[-1:] if not qual else []) + qual + [name])
                h = nm - 1
                while h >= 0 and toks[h].text not in (";", "{", "}"):
                    h -= 1
                head = {x.text for x in toks[h + 1:nm]}
                fn = Func(qname, name, params, toks[i + 1:close], path, t.line,
                          local_inline=bool(head & {"static", "inline"}) and not cls and not qual)
        if fn is not None:
            out.append(fn)
            i = close + 1
            continue
        # class / namespace / other scope
        cname = ""
        q = i - 1
        d = 0
        while q >= 0 and toks[q].text not in (";", "{", "}"):
            if toks[q].text in ("class", "struct", "union") and d == 0:
                if q + 1 < n and toks[q + 1].kind == "id":
                    cname = toks[q + 1].text
                break
            q -= 1
        scopes.append(cname)
        ends.append(close)
        i += 1
    return out


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def _local_names(body: List[Tok]) -> set:
    """Names declared in a body: `Type name =|;|(|[|,` at statement starts."""
    names = set()
    for x in range(1, len(body) - 1):
        t = body[x]
        if t.kind != "id" or t.text in KEYWORDS:
            continue
        prev, nxt = body[x - 1], body[x + 1]
        if nxt.text not in ("=", ";", "(", "[", ",", ":"):
            continue
        if nxt.text == ":" and not (x >= 2 and body[x - 2].text == "("):
            continue  # only `for (T x : ...)`
        if not (prev.kind == "id" or prev.text in ("*", "&", ">")):
            continue
        if prev.kind == "id" and prev.text in ("return", "case", "goto", "delete", "new", "else", "sizeof"):
            continue
        # the token before the type must start a statement or a declaration
        y = x - 1
        while y >= 0 and (body[y].kind == "id" or body[y].text in ("*", "&", "::", "<", ">", ",")):
            if body[y].text == "," and not any(b.text == "<" for b in body[max(0, y - 6):y]):
                break
            y -= 1
        if y < 0 or body[y].text in (";", "{", "}", "(", ")", ","):
            names.add(t.text)
    return names


def normalise(fn: Func, extra_locals: Optional[set] = None) -> List[str]:
    params = set(fn.params)
    locs = _local_names(fn.body) | (extra_locals or set())
    out: List[str] = []
    body = fn.body
    x = 0
    member_next = False
    drop_semi = -1   # paren depth at which a return statement's ';' is dropped
    depth = 0
    while x < len(body):
        t = body[x]
        s = t.text
        if s in ("(", "["):
            depth += 1
        elif s in (")", "]"):
            depth -= 1
        if s == ";" and drop_semi == depth:
            drop_semi = -1
            x += 1
            continue
        if s == "this" and x + 1 < len(body) and body[x + 1].text == "->":
            x += 2
            continue
        if s in ("->", "."):
            # collapse the receiver: drop the primary expression before it
            if out and out[-1] in (")", "]"):
                d = 0
                while out:
                    c = out.pop()
                    if c in (")", "]"):
                        d += 1
                    elif c in ("(", "["):
                        d -= 1
                        if d == 0:
                            break
                if out and (out[-1] not in KEYWORDS or out[-1] == "this") and re.match(r"[\w$]", out[-1]):
                    out.pop()
            elif out and re.match(r"[\w$]", out[-1]):
                out.pop()
            member_next = True
            x += 1
            continue
        if s == "return":
            drop_semi = depth
            x += 1
            continue
        if t.kind == "id" and member_next:
            member_next = False
        elif t.kind == "id":
            if s in params:
                s = "$P"
            elif s in locs:
                s = "$L"
        if t.kind == "num":
            s = re.sub(r"[uUlLfF]+$", "", s)
            s = re.sub(r"\.0*$", "", s)
        out.append(s)
        x += 1
    return out


def info_count(norm: List[str]) -> int:
    return sum(1 for s in norm if (re.match(r"[A-Za-z_]", s) and s not in INFO_EXCLUDE) or re.match(r"\d", s))


# --------------------------------------------------------------------------
# Alignment
# --------------------------------------------------------------------------

def _balanced_spans(text: List[str], start: int, maxlen: int = 12) -> List[int]:
    """Ends e (exclusive) such that text[start:e] is a balanced run."""
    res, d = [], 0
    for e in range(start, min(len(text), start + maxlen)):
        c = text[e]
        if c in ("(", "[", "{"):
            d += 1
        elif c in (")", "]", "}"):
            d -= 1
            if d < 0:
                break
        elif c in (";", ",", "?", ":") and d == 0:
            break
        if d == 0:
            res.append(e + 1)
    return res


INS = 0.3   # a caller token inside the copy (a named local split off it)


def align(pat: List[str], text: List[str]) -> Tuple[float, int, int]:
    """Semi-global edit distance of pat against text; returns (cost, start, end)."""
    m, n = len(pat), len(text)
    INF = 10 ** 9
    # prev[j]: best cost of aligning pat[:i] ending at text position j; start[j]
    prev = [0] * (n + 1)
    pstart = list(range(n + 1))
    spans = {}
    for i in range(1, m + 1):
        p = pat[i - 1]
        cur = [INF] * (n + 1)
        cst = [0] * (n + 1)
        cur[0] = prev[0] + 1
        cst[0] = pstart[0]
        for j in range(1, n + 1):
            best, bs = prev[j] + 1, pstart[j]          # delete pattern token
            if cur[j - 1] + INS < best:                # insert text token
                best, bs = cur[j - 1] + INS, cst[j - 1]
            tj = text[j - 1]
            if p == tj or (p == "$L" and (re.match(r"[\w$]", tj) is not None)):
                if prev[j - 1] < best:
                    best, bs = prev[j - 1], pstart[j - 1]
            elif p != "$P" and prev[j - 1] + 1 < best:
                best, bs = prev[j - 1] + 1, pstart[j - 1]
            cur[j] = best
            cst[j] = bs
        if p == "$P":
            for s0 in range(n):
                if prev[s0] >= INF:
                    continue
                if s0 not in spans:
                    spans[s0] = _balanced_spans(text, s0)
                for e in spans[s0]:
                    if prev[s0] < cur[e]:
                        cur[e] = prev[s0]
                        cst[e] = pstart[s0]
        prev, pstart = cur, cst
    j = min(range(n + 1), key=lambda j: prev[j])
    return prev[j], pstart[j], j


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

_FUNCS_CACHE: Dict[str, List[Func]] = {}


def funcs_of(path: str) -> List[Func]:
    if path not in _FUNCS_CACHE:
        try:
            fs = extract_functions(path)
        except (OSError, RecursionError):
            fs = []
        for f in fs:
            f.norm = normalise(f)
        _FUNCS_CACHE[path] = fs
    return _FUNCS_CACHE[path]


INC_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)


def headers_of(path: str, seen=None) -> List[str]:
    seen = set() if seen is None else seen
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    for inc in INC_RE.findall(text):
        for d in [os.path.dirname(path)] + INCLUDE_DIRS:
            p = os.path.normpath(os.path.join(d, inc))
            if os.path.isfile(p):
                if p not in seen:
                    seen.add(p)
                    headers_of(p, seen)
                break
    return sorted(seen)


def _mangled_key(sym: str) -> Optional[Tuple[str, str]]:
    """(class, name) from a CodeWarrior mangled symbol, best effort."""
    m = re.match(r"^(__\w+?|[A-Za-z_]\w*?)__(\d+)([A-Za-z_]\w*)", sym)
    if m:
        ln = int(m.group(2))
        cls = m.group(3)[:ln]
        cls = re.sub(r"<.*", "", cls)
        return cls, m.group(1)
    m = re.match(r"^(__\w+?|[A-Za-z_]\w*?)__Q(\d)", sym)
    if m:
        rest = sym[m.end():]
        cls = ""
        for _ in range(int(m.group(2))):
            mm = re.match(r"(\d+)", rest)
            if not mm:
                break
            ln = int(mm.group(1))
            cls = rest[len(mm.group(1)):len(mm.group(1)) + ln]
            rest = rest[len(mm.group(1)) + ln:]
        return re.sub(r"<.*", "", cls), m.group(1)
    m = re.match(r"^([A-Za-z_]\w*?)__F", sym)
    if m:
        return "", m.group(1)
    return None


def unused_keys() -> set:
    keys = set()
    for line in open(MAP, encoding="utf-8", errors="replace"):
        if "UNUSED" not in line:
            continue
        parts = line.split()
        if len(parts) >= 4:
            k = _mangled_key(parts[3])
            if k:
                keys.add(k)
    return keys


def fn_key(f: Func) -> Tuple[str, str]:
    comps = f.qname.split("::")
    name = f.name
    if name.startswith("~"):
        name = "__dt"
    cls = comps[-2] if len(comps) > 1 else ""
    if len(comps) > 1 and comps[-2] == f.name:
        name = "__ct"
    return cls, name


def short_dm(dm: str) -> Tuple[str, str]:
    d, cut = 0, len(dm)
    for i, c in enumerate(dm):
        if c == "<":
            d += 1
        elif c == ">":
            d -= 1
        elif c == "(" and d == 0:
            cut = i
            break
    q = re.sub(r"<[^<>]*(?:<[^<>]*>[^<>]*)*>", "", dm[:cut])
    comps = q.split("::")
    return (comps[-2] if len(comps) > 1 else ""), comps[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-u", "--unit", action="append", help="unit name (mario/Dir/Unit), repeatable")
    ap.add_argument("--min-sim", type=float, default=0.85)
    ap.add_argument("--min-info", type=int, default=4)
    ap.add_argument("--max-pat", type=int, default=160, help="skip candidates longer than this")
    ap.add_argument("--min-pat", type=int, default=10, help="skip candidates shorter than this (tokens)")
    ap.add_argument("--cross-unused", action="store_true",
                    help="also match UNUSED bodies written in other .cpp files")
    ap.add_argument("--source", help="scan this file instead of the unit's source (single unit)")
    ap.add_argument("--all", action="store_true", help="scan every function, not only non-exact ones")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the matched caller text")
    args = ap.parse_args()

    report = json.load(open(REPORT))
    ukeys = unused_keys()
    # every function body of every .cpp that the map lists as UNUSED
    unused_bodies: List[Func] = []
    for p in [] if not args.cross_unused else sorted(glob.glob(os.path.join(ROOT, "src", "**", "*.cpp"), recursive=True)) + \
            sorted(glob.glob(os.path.join(ROOT, "libs", "**", "*.c*"), recursive=True)):
        for f in funcs_of(p):
            if fn_key(f) in ukeys:
                unused_bodies.append(f)

    hits = 0
    for u in report["units"]:
        if args.unit and u["name"] not in args.unit:
            continue
        src = u.get("metadata", {}).get("source_path")
        if not src:
            continue
        spath = os.path.join(ROOT, src)
        if not os.path.isfile(spath):
            continue
        targets = [f for f in u.get("functions", []) if args.all or f.get("fuzzy_match_percent", 100) < 100]
        if not targets:
            continue
        if args.source:
            _FUNCS_CACHE[spath] = extract_functions(spath, open(args.source).read())
            for f in _FUNCS_CACHE[spath]:
                f.norm = normalise(f)
        tu = funcs_of(spath)
        hdr = [f for h in headers_of(spath) for f in funcs_of(h)]
        # A body in another .cpp cannot be inlined here; an UNUSED one there is
        # listed only with --cross-unused (a header inline written in a .cpp).
        # A file-scope `static inline` of our own is a reconstruction ("helper"):
        # a copy of it is no evidence of a retail call.
        cands: List[Tuple[str, Func]] = \
            [("unused" if fn_key(f) in ukeys else "helper" if f.local_inline else "tu", f) for f in tu] + \
            [("header", f) for f in hdr] + \
            ([("x-unused", f) for f in unused_bodies if f.path != spath] if args.cross_unused else [])
        seen_c = set()
        uniq = []
        for org, c in cands:
            key = (c.path, c.line, c.qname)
            if key in seen_c or not args.min_pat <= len(c.norm) <= args.max_pat \
                    or info_count(c.norm) < args.min_info:
                continue
            seen_c.add(key)
            uniq.append((org, c))
        for tf in targets:
            dm = tf.get("metadata", {}).get("demangled_name", tf["name"])
            cls, name = short_dm(dm)
            callers = [f for f in tu if f.name == name and (not cls or f.qname.split("::")[-2:-1] in ([cls], []))]
            if not callers:
                continue
            caller = callers[0]
            text = caller.norm
            tset = set(text)
            for org, c in uniq:
                if c is caller or c.qname == caller.qname:
                    continue
                infos = [s for s in c.norm if re.match(r"[A-Za-z_]", s) and s not in INFO_EXCLUDE]
                if not infos:
                    continue
                if sum(1 for s in infos if s in tset) < 0.8 * len(infos):
                    continue
                # nerve bodies are only reached through the vtable, never inlined
                if c.name == "execute" and c.params == ["spine"]:
                    continue
                # a body that merely forwards to the caller (overloads) is not a copy
                if name in c.norm[:3]:
                    continue
                cost, s0, e0 = align(c.norm, text)
                sim = 1 - cost / max(1, len(c.norm))
                if sim < args.min_sim:
                    continue
                window = text[s0:e0]
                if c.name in window:
                    continue  # the caller already calls it there
                hits += 1
                rel = os.path.relpath(c.path, ROOT)
                print(f"{u['name']}\t{dm}\t{tf.get('fuzzy_match_percent', 0):.2f}\t{sim:.2f}\t{org}\t{c.qname}\t{rel}:{c.line}")
                if args.verbose:
                    print("    cand: " + " ".join(c.norm))
                    print("    here: " + " ".join(window))
    print(f"# {hits} hits", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
