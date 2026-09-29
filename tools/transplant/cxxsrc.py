"""Just enough C++ source structure to find one function definition.

`mask()` blanks comments and literal contents (positions are kept), and
`find_defs()` returns the definitions of one demangled function in a file:
qualified out-of-class definitions (`T::f(...) {`, also inside namespace
blocks), in-class definitions inside `class T { ... }`, and free functions.
Spans start at the function's own name token (after any `T::`), so a body
can be swapped without touching the return type or the qualification.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

IDENT = re.compile(r"[A-Za-z_]\w*")


def mask(text: str) -> str:
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1
        elif c == "#" and (i == 0 or text[i - 1] == "\n" or text[:i].rsplit("\n", 1)[-1].strip() == ""):
            # preprocessor line: keep #if/#else text visible but blank #define bodies
            j = i
            while True:
                e = text.find("\n", j)
                if e < 0:
                    e = n
                    break
                if text[e - 1] != "\\":
                    break
                j = e + 1
            if re.match(r"#\s*(define|include|pragma|error)", text[i:e]):
                for k in range(i, e):
                    if out[k] != "\n":
                        out[k] = " "
            i = e
        else:
            i += 1
    return "".join(out)


def match_close(m: str, i: int, o: str, c: str) -> int:
    """Index of the bracket closing m[i] (which is `o`), or -1."""
    d = 0
    for j in range(i, len(m)):
        ch = m[j]
        if ch == o:
            d += 1
        elif ch == c:
            d -= 1
            if d == 0:
                return j
    return -1


@dataclass
class Scope:
    open: int
    close: int
    kind: str  # 'ns', 'class', 'block'
    name: str = ""


def scopes(orig: str, m: str) -> List[Scope]:
    out = []
    stack = []
    last = 0
    for i, ch in enumerate(m):
        if ch == "{":
            head = m[last:i]
            ohead = orig[last:i]
            kind, name = "block", ""
            if re.search(r"\bnamespace\s*\w*\s*$", head) or re.search(r"\bextern\s*\"C\"\s*$", ohead):
                kind = "ns"
            else:
                cm = re.search(r"\b(class|struct|union)\s+(\w+)[^;{}()]*$", head)
                if cm and not re.search(r"\)\s*(const\s*)?$", head):
                    kind, name = "class", cm.group(2)
            stack.append(Scope(i, -1, kind, name))
            last = i + 1
        elif ch == "}":
            if stack:
                s = stack.pop()
                s.close = i
                out.append(s)
            last = i + 1
        elif ch == ";":
            last = i + 1
    return out


def enclosing(sc: List[Scope], pos: int) -> List[Scope]:
    return sorted([s for s in sc if s.open < pos < s.close], key=lambda s: s.open)


def split_params(p: str) -> List[str]:
    p = p.strip()
    if p in ("", "void"):
        return []
    out, d, cur = [], 0, ""
    for ch in p:
        if ch in "<([":
            d += 1
        elif ch in ">)]":
            d -= 1
        if ch == "," and d == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return out


@dataclass
class Demangled:
    quals: List[str]   # namespace/class components, template args stripped
    name: str          # final component: method, ~T, T (ctor), operator...
    params: List[str]
    const: bool


def parse_demangled(d: str) -> Optional[Demangled]:
    d = d.strip()
    const = d.endswith(" const")
    if const:
        d = d[: -len(" const")].rstrip()
    if not d.endswith(")"):
        return Demangled(d.split("::")[:-1], d.split("::")[-1], [], False)
    # the parameter list is the last balanced (...) group
    depth, i = 0, len(d) - 1
    while i >= 0:
        if d[i] == ")":
            depth += 1
        elif d[i] == "(":
            depth -= 1
            if depth == 0:
                break
        i -= 1
    head, params = d[:i], d[i + 1:-1]
    comps, cur, dd = [], "", 0
    j = 0
    while j < len(head):
        ch = head[j]
        if ch == "<":
            dd += 1
        elif ch == ">":
            dd -= 1
        if dd == 0 and head.startswith("::", j):
            comps.append(cur)
            cur = ""
            j += 2
            continue
        cur += ch
        j += 1
    comps.append(cur)
    comps = [re.sub(r"<.*>$", "", c) if not c.startswith("operator") else c for c in comps]
    return Demangled(comps[:-1], comps[-1], split_params(params), const)


@dataclass
class Def:
    start: int      # first char of the function's own name token
    qstart: int     # first char of the qualification (== start when none)
    pstart: int     # '(' of the parameter list
    pend: int       # ')' of the parameter list
    bstart: int     # '{' of the body
    end: int        # one past the closing '}'
    params: List[str]
    const: bool
    inclass: bool
    form: str = "plain"  # or "nerve": DEFINE_NERVE(T, A) { ... }

    def text(self, src: str) -> str:
        return src[self.start:self.end]


def _name_rx(name: str) -> str:
    if name.startswith("operator"):
        op = name[len("operator"):].strip()
        return r"operator\s*" + r"\s*".join(re.escape(c) for c in op)
    if name.startswith("~"):
        return r"~\s*" + re.escape(name[1:]) + r"\b"
    return r"\b" + re.escape(name) + r"\b"


def _norm_type(t: str) -> List[str]:
    return [x for x in IDENT.findall(t) if x not in ("const", "volatile", "unsigned", "signed", "struct", "class")]


def find_defs(src: str, dm: Demangled, m: Optional[str] = None, sc: Optional[List[Scope]] = None) -> List[Def]:
    m = m if m is not None else mask(src)
    sc = sc if sc is not None else scopes(src, m)
    cls = dm.quals[-1] if dm.quals else None
    out = []
    if cls and dm.name == "execute" and len(dm.params) == 1:
        rx = r"\bDEFINE_NERVE\s*\(\s*" + re.escape(cls) + r"\s*,[^()]*\)\s*\{"
        for mt in re.finditer(rx, m):
            k = mt.end() - 1
            e = match_close(m, k, "{", "}")
            if e > 0 and not [s for s in enclosing(sc, mt.start()) if s.kind != "ns"]:
                p0 = m.index("(", mt.start())
                out.append(Def(mt.start(), mt.start(), p0, match_close(m, p0, "(", ")"), k, e + 1,
                               ["TSpineBase<T>* spine"], True, False, "nerve"))
        if out:
            return out
    for mt in re.finditer(_name_rx(dm.name) + r"\s*\(", m):
        start = mt.start()
        # qualification written before the name
        before = m[:start]
        qm = re.search(r"((?:\b\w+\s*(?:<[^;{}()]*>)?\s*::\s*)+)$", before)
        qual = []
        qstart = start
        if qm:
            qual = [re.sub(r"<.*>", "", x).strip() for x in re.split(r"::", qm.group(1)) if x.strip()]
            qstart = qm.start(1)
        prev = before[:qstart].rstrip()
        if prev.endswith((".", "->")) or re.search(r"\b(return|new|delete|throw)$", prev):
            continue
        encl = enclosing(sc, start)
        nonns = [s for s in encl if s.kind != "ns"]
        inclass = False
        if cls:
            if qual:
                if qual[-1] != cls or nonns:
                    continue
            else:
                if len(nonns) != 1 or nonns[0].kind != "class" or nonns[0].name != cls:
                    continue
                inclass = True
        else:
            if qual or nonns:
                continue
        pstart = m.index("(", mt.end() - 1)
        pend = match_close(m, pstart, "(", ")")
        if pend < 0:
            continue
        k = pend + 1
        tail = re.match(r"\s*(const\b)?\s*(throw\s*\([^)]*\))?\s*", m[k:])
        is_const = bool(tail.group(1))
        k += tail.end()
        if k >= len(m):
            continue
        if m[k] == ":" and not m.startswith("::", k):
            j = k
            d = 0
            while j < len(m):
                if m[j] == "(":
                    d += 1
                elif m[j] == ")":
                    d -= 1
                elif m[j] in "{" and d == 0:
                    break
                elif m[j] == ";" and d == 0:
                    j = -1
                    break
                j += 1
            if j < 0 or j >= len(m):
                continue
            k = j
        if m[k] != "{":
            continue
        e = match_close(m, k, "{", "}")
        if e < 0:
            continue
        params = split_params(src[pstart + 1:pend])
        out.append(Def(start, qstart, pstart, pend, k, e + 1, params, is_const, inclass))
    if len(out) > 1:
        want = len(dm.params)
        f = [d for d in out if len(d.params) == want and d.const == dm.const]
        if f:
            out = f
    if len(out) > 1:
        want = [_norm_type(p) for p in dm.params]

        def fit(d: Def) -> int:
            s = 0
            for w, p in zip(want, d.params):
                pt = set(_norm_type(p))
                s += sum(1 for x in w if x in pt)
            return s
        best = max(fit(d) for d in out)
        out = [d for d in out if fit(d) == best]
    return out


def normalize(body: str) -> str:
    return re.sub(r"\s+", "", mask(body))
