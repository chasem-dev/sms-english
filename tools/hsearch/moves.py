"""The honest move set.

Every move is a value-preserving source rewrite that reads as something a
person could have written.  The catalog levers come from lever-search's
generators (a whitelist of them: its forks, binders, chain helpers and
parameter aliases are left out), and the levers proved in the c-k/c-f
batches are generated here.

A move is a list of text edits (start, end, replacement) on one source text,
so several moves generated on the same text combine when their edits do not
overlap.
"""

import importlib.util
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def _load_ls():
    name = "lever_search"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", "lever-search.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


LS = _load_ls()
lex, bracket_map, locate_function, parse_demangled = LS.lex, LS.bracket_map, LS.locate_function, LS.parse_demangled
apply_edits, merged_index = LS.apply_edits, LS.merged_index
KEYWORDS, SCALAR_TYPES, PREC, ASSIGN_OPS = LS.KEYWORDS, LS.SCALAR_TYPES, LS.PREC, LS.ASSIGN_OPS

# lever-search levers that are honest (see README): everything except the
# TU-local forks/binders, chain helpers, file-scope colours and the
# parameter-alias "bind" names.
LS_KEEP = {"acc->raw", "raw->acc", "null-test", "int-type", "unname", "decl-split", "decl-order",
           "decl-hoist", "name-call", "name-read", "name-conv", "compound", "split-sum", "set-assign",
           "vec-set", "unused-stub", "rot-helper"}

PRIO = {
    # catalog levers (lever-search generators)
    "acc->raw": 2, "raw->acc": 2, "null-test": 2, "int-type": 4, "unname": 2, "decl-split": 3,
    "decl-order": 2, "decl-hoist": 3, "name-call": 3, "name-read": 4, "name-conv": 4, "compound": 3,
    "split-sum": 3, "set-assign": 3, "vec-set": 4, "unused-stub": 1, "rot-helper": 3,
    # generated here
    "commute": 2, "ternary": 3, "cond-zero": 2, "stmt-swap": 2, "for-scope": 3, "accumulate": 3,
    "merge-assign": 3, "addr-assign": 2, "pred-level": 3, "extract": 4, "conv-raw": 3,
    "sound-short": 2,
}


@dataclass
class Move:
    kind: str
    desc: str
    edits: List[Tuple[int, int, str]]
    prio: int = 5

    @property
    def key(self):
        return tuple(sorted(self.edits))


def _int_ok(desc: str) -> bool:
    """int-type moves: only the value-preserving int <-> s32 spelling."""
    m = re.search(r"(\w+) \w+ -> (\w+)$|all \d+ (\w+) locals -> (\w+)$", desc)
    if not m:
        return False
    a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return {a, b} == {"int", "s32"}


class HGen(LS.Gen):
    """lever-search's generator with the honest subset and this package's levers."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        t = self.toks
        for k in range(self.bo, self.bc):  # constructor-style declarations too
            if t[k].text in (";", "{", "}") and self.pdepth.get(k, 0) == 0:
                try:
                    d = self._decl_at(k + 1)
                except (IndexError, KeyError):
                    d = None
                if d:
                    self.var_types.setdefault(t[d[2]].text, self.span(d[0], d[1]))

    def honest(self) -> List[Move]:
        gens = [self.gen_accessors, self.gen_null_tests, self.gen_int_types, self.gen_decls,
                self.gen_name_calls, self.gen_compound, self.gen_setters, self.gen_rot_helper,
                self.gen_unused_stub, self.gen_name_conv, self.gen_hoist, self.gen_vec_ctor,
                self.gen_name_reads,
                self.gen_commute, self.gen_ternary, self.gen_cond_zero, self.gen_stmt_swap,
                self.gen_for_scope, self.gen_accumulate, self.gen_merge_assign, self.gen_addr_assign,
                self.gen_pred_level, self.gen_extract, self.gen_conv_raw, self.gen_sound_short]
        for g in gens:
            try:
                g()
            except (IndexError, KeyError, TypeError, ValueError, AttributeError, RecursionError):
                pass
        out, seen = [], set()
        for c in self.cands:
            if c.lever not in PRIO:
                continue
            if c.lever == "int-type" and not _int_ok(c.desc):
                continue
            if c.lever == "name-call" and ": bind " in c.desc:
                continue
            if not c.edits:
                continue
            try:
                if not self._plausible(c):
                    continue
            except (IndexError, KeyError, ValueError):
                continue
            key = tuple(sorted(c.edits))
            if key in seen:
                continue
            seen.add(key)
            out.append(Move(c.lever, c.desc, list(c.edits), PRIO[c.lever]))
        out.sort(key=lambda m: (m.prio, m.edits[0][0]))
        return out

    # ------------------------------------------------------------------
    # plausibility: refuse spellings no one would write next to each other
    # ------------------------------------------------------------------
    def _window(self, pos: int, n: int = 4) -> Tuple[int, int]:
        """Character range of the n lines around pos, clipped to the function body."""
        import bisect
        if not hasattr(self, "_ls"):
            self._ls = [0] + [m.end() for m in re.finditer("\n", self.text)]
        i = bisect.bisect_right(self._ls, pos) - 1
        lo = self._ls[max(0, i - n)]
        hi = self._ls[i + n + 1] if i + n + 1 < len(self._ls) else len(self.text)
        return max(lo, self.toks[self.bo].s), min(hi, self.toks[self.bc].e)

    def _found_outside(self, rx, edits, pos) -> bool:
        lo, hi = self._window(pos)
        for m in rx.finditer(self.text, lo, hi):
            if not any(s <= m.start() < max(e, s + 1) for s, e, _ in edits):
                return True
        return False

    def _recv(self, k: int) -> str:
        """The receiver text of the member or call at token k ('' for implicit this)."""
        if self.toks[k - 1].text in (".", "->"):
            return self._norm(self.span(self.postfix_start(k - 2), k - 2))
        return ""

    def _is_read(self, k: int) -> bool:
        """Is the member at token k read (not the target of an assignment through
        `m = `, `m.x = `, `m[i] += `, `++m` or `&m`)?"""
        t = self.toks
        if not self.is_value_context(k, k):
            return False
        j = k + 1
        while True:
            if t[j].text in (".", "->") and t[j + 1].kind == "id":
                j += 2
            elif t[j].text == "[" and j in self.bm:
                j = self.bm[j] + 1
            else:
                break
        return t[j].text not in ASSIGN_OPS and t[j].text not in ("++", "--")

    def _mixed_left(self, lever: str, eds) -> bool:
        """Would this accessor move leave the same member, on the same receiver,
        spelled the other way anywhere in the function?  Subsets such as
        `raw->acc sites 3+4 of 4` read as two authors."""
        t = self.toks
        edited = [s for s, e, _ in eds if e > s]
        sites = []  # (name, receiver) of each edited site
        for s in edited:
            k = next((q for q in range(self.bo, self.bc) if t[q].s == s), None)
            if k is None:
                continue
            if t[k].text == "*":
                k += 1
            sites.append((t[k].text, self._recv(k)))
        for name, recv in set(sites):
            for q in range(self.bo + 1, self.bc):
                if t[q].text != name or t[q - 1].text == "::" or any(s <= t[q].s < e for s, e, _ in eds):
                    continue
                if self._recv(q) != recv:
                    continue
                if lever == "raw->acc":
                    if t[q + 1].text != "(" and self._is_read(q):
                        return True  # a raw read of the member stays
                elif t[q + 1].text == "(" and t[q + 2].text == ")":
                    return True  # a call of the accessor stays
        return False

    WRITE_RX = re.compile(r"\s*(?:=(?!=)|[-+*/%&|^]=|<<=|>>=|\+\+|--)")

    def _found_in_function(self, rx, edits) -> bool:
        """Another read matching rx anywhere in the function, outside the edits
        (a match that is assigned to is a write, not a second read)."""
        lo, hi = self.toks[self.bo].s, self.toks[self.bc].e
        for m in rx.finditer(self.text, lo, hi):
            if any(s <= m.start() < max(e, s + 1) for s, e, _ in edits):
                continue
            if not self.WRITE_RX.match(self.text, m.end()):
                return True
        return False

    @staticmethod
    def _bare_rx(member: str):
        return re.compile(r"(?<![\w])" + re.escape(member) + r"\b(?!\s*\()")

    @staticmethod
    def _call_rx(acc: str):
        return re.compile(r"(?<![\w])" + re.escape(acc) + r"\s*\(\s*\)")

    def _getter_member(self, acc: str) -> Optional[str]:
        ms = [e for e in self.idx.getters.get(acc, ()) if re.fullmatch(r"\*?[A-Za-z_]\w*", e)]
        return ms[0] if len(ms) == 1 else None

    def _getter_of(self, s: int, member: str, acc: str) -> bool:
        """Is acc() really the accessor of this member at this site?  The header
        index is keyed by accessor name alone, so a namesake from another class
        (or a local that shares a member's name) must not be rewritten."""
        if member.startswith("*"):
            return True  # a global through its accessor (SMS_GetMarioPos)
        if member in self.var_types:
            return False
        k = next(q for q in range(self.bo, self.bc) if self.toks[q].s == s)
        prv = self.toks[k - 1].text
        if prv in (".", "->"):
            cls = LS._type_class(self.expr_type_any(self.postfix_start(k - 2), k - 2))
        else:
            cls = self.loc.info.klass or None
        known = self.idx.class_lookup("class_getters", cls, acc) if cls else None
        if known is not None:
            return member in known
        if prv not in (".", "->"):
            return False
        base = re.sub(r"^m(?=[A-Z])", "", member).lower()
        return acc.lower() in ("get" + base, "is" + base)

    # levers that respell a value in place: parallel identical statements must agree
    SPELLING = {"acc->raw", "raw->acc", "null-test", "name-call", "name-read", "name-conv", "cond-zero",
                "commute", "sound-short", "set-assign", "vec-set", "compound", "split-sum",
                "accumulate", "merge-assign", "conv-raw", "int-type"}

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    def _twin_untouched(self, eds) -> bool:
        """Does an edited line (or run of lines) have an identical twin anywhere in
        the function that this move leaves alone?  Three identical `if (a->isVisible())`
        blocks cannot be spelled two ways, however far apart they are."""
        import bisect
        if not hasattr(self, "_ls"):
            self._ls = [0] + [m.end() for m in re.finditer("\n", self.text)]
        ls = self._ls
        f0, f1 = self.toks[self.bo].e, self.toks[self.bc].s
        lf0, lf1 = bisect.bisect_right(ls, f0) - 1, bisect.bisect_right(ls, f1) - 1
        def line_end(i):
            return ls[i + 1] if i + 1 < len(ls) else len(self.text)
        for s, e, _ in eds:
            if e <= s:
                continue
            a, b = bisect.bisect_right(ls, s) - 1, bisect.bisect_right(ls, max(s, e - 1)) - 1
            key = self._norm(self.text[ls[a]:line_end(b)])
            if len(key) < 3:
                continue
            n = b - a
            for i in range(lf0 + 1, lf1 - n):
                if i == a or self._norm(self.text[ls[i]:line_end(i + n)]) != key:
                    continue
                lo, hi = ls[i], line_end(i + n)
                if not any(lo <= s2 < hi for s2, e2, _ in eds if e2 > s2):
                    return True
        return False

    def _plausible(self, c) -> bool:
        t = self.toks
        eds = [e for e in c.edits if e[0] >= self.toks[self.bo].s]  # helpers go above the function
        if c.lever in self.SPELLING and self._twin_untouched(eds):
            return False
        if c.lever in ("acc->raw", "raw->acc"):
            # a flipped site next to a site still spelled the other way
            # (`unk18.x; unk18.y; getUnk18().z`) is not how anyone writes it
            for s, e, rep in eds:
                old = self.text[s:e]
                if c.lever == "raw->acc" and re.fullmatch(r"\*?\w+", old) and re.fullmatch(r"\w+\(\)", rep):
                    if not self._getter_of(s, old, rep[:-2]):
                        return False
                    if self._found_outside(self._bare_rx(old), eds, s):
                        return False
                elif c.lever == "acc->raw" and re.fullmatch(r"\w+\s*\(\s*\)", old):
                    if self._found_outside(self._call_rx(old.split("(")[0].strip()), eds, s):
                        return False
            # and anywhere else in the function on the same receiver
            if self._mixed_left(c.lever, eds):
                return False
            return True
        if c.lever in ("name-call", "name-read", "name-conv"):
            # naming one of two identical reads a line apart leaves the second
            # read of the same value unexplained
            for s, e, rep in eds:
                if e <= s:
                    continue
                old = self.text[s:e]
                k = next(q for q in range(self.bo, self.bc) if t[q].s == s)
                if c.lever == "name-read" and t[k - 1].text in (">>", "<<"):
                    return False  # `stream >> m` writes m: a copy would not be read back
                if c.lever == "name-call" and (not re.search(r"\)\s*$", old) or
                                               (t[k - 1].kind == "id" and t[k - 1].text not in KEYWORDS)):
                    return False  # `T name(args)` is a declaration, not a call
                # a lone named value at one of several identical sites, however far apart
                if self._found_in_function(re.compile(r"(?<![\w.])(?<!->)(?<!::)" + re.escape(old) + r"(?!\w)"), eds):
                    return False
                m = re.fullmatch(r"(\w+)\s*\(\s*\)", old)
                mem = self._getter_member(m.group(1)) if m else None
                if mem and self._found_outside(self._bare_rx(mem), eds, s):
                    return False
            return True
        if c.lever == "null-test" and c.desc.endswith("0 -> nullptr"):
            s = eds[0][0]
            k = next(q for q in range(self.bo, self.bc) if t[q].s == s)
            a = self.postfix_start(k - 2)
            return self.is_pointer(a, k - 2)
        return True

    # ------------------------------------------------------------------
    # MSound::startSoundActor: the six-argument call against the two- and
    # three-argument overloads (codegen-tells.md, batch 82: a per-site lever)
    # ------------------------------------------------------------------
    def _sound_calls(self, lo: int, hi: int):
        t = self.toks
        for k in range(lo + 1, hi):
            if t[k].text != "startSoundActor" or t[k - 1].text != "->" or t[k + 1].text != "(":
                continue
            cp = self.bm.get(k + 1)
            if cp is None:
                continue
            d = self.pdepth.get(k + 2)
            cuts = [q for q in range(k + 2, cp) if t[q].text == "," and self.pdepth.get(q) == d]
            bounds = [k + 1] + cuts + [cp]
            yield k, cp, [self.text[t[bounds[i]].e:t[bounds[i + 1]].s].strip() for i in range(len(bounds) - 1)]

    @staticmethod
    def _sound_form(args) -> Optional[str]:
        if len(args) == 6 and args[2] == "0" and args[4] == "0" and args[5] == "4":
            return "six"
        if len(args) in (2, 3) and args[0] and args[1]:
            return "short"
        return None

    def gen_sound_short(self):
        """Offer a form only when the file's other calls spell it that way often
        enough to be the file's style (a quarter of them), and also offer every
        site of the function at once."""
        t = self.toks
        pdepth = self.pdepth
        if not hasattr(self, "_file_pdepth"):  # the file's other calls: its own depths
            self._file_pdepth = {}
            st = []
            for q, tk in enumerate(t):
                if tk.text == "(":
                    st.append(q)
                self._file_pdepth[q] = len(st)
                if tk.text == ")" and st:
                    st.pop()
        self.pdepth = self._file_pdepth
        try:
            other = [self._sound_form(args) for k, cp, args in self._sound_calls(0, len(t) - 1)
                     if not (self.bo < k < self.bc)]
        finally:
            self.pdepth = pdepth
        used = {f for f in ("six", "short") if other.count(f) and 4 * other.count(f) >= len(other)}
        per = {"six": [], "short": []}
        for k, cp, args in self._sound_calls(self.bo, self.bc):
            form = self._sound_form(args)
            if form == "six" and "short" in used:
                short = args[:2] if args[3] in ("nullptr", "NULL", "0") else args[:2] + [args[3]]
                ed = (t[k + 1].e, t[cp].s, ", ".join(short))
                self.add("sound-short", k, "startSoundActor six arguments -> %d" % len(short), [ed])
                per["six"].append((k, ed))
            elif form == "short" and "six" in used:
                full = args[:2] + ["0", args[2] if len(args) == 3 else "nullptr", "0", "4"]
                ed = (t[k + 1].e, t[cp].s, ", ".join(full))
                self.add("sound-short", k, "startSoundActor %d arguments -> six" % len(args), [ed])
                per["short"].append((k, ed))
        for form, sites in per.items():
            if len(sites) > 1:
                self.add("sound-short", sites[0][0], "startSoundActor all %d sites %s -> %s" % (
                    len(sites), form, "six" if form == "short" else "short"), [e for _, e in sites])

    # ------------------------------------------------------------------
    # expression helpers
    # ------------------------------------------------------------------
    def _operand_end_tok(self, k: int) -> bool:
        t = self.toks[k]
        return (t.kind in ("id", "num", "str", "chr") and (t.text not in KEYWORDS or t.text == "this")) \
            or t.text in (")", "]")

    def _prefix_start(self, j: int) -> Optional[int]:
        """j ends a unary expression; return its first token (None for casts)."""
        t = self.toks
        a = self.postfix_start(j)
        if t[a].text == "(" and t[a - 1].text == ")":
            return None
        while a - 1 > self.bo and t[a - 1].text in ("-", "!", "~", "*", "&", "+") and \
                not self._operand_end_tok(a - 2):
            a -= 1
        if t[a - 1].text == ")" and a - 1 in self.bm:  # a C cast
            return None
        return a

    def _unary_end(self, k: int) -> Optional[int]:
        t = self.toks
        while t[k].text in ("-", "!", "~", "*", "&", "+"):
            k += 1
        if t[k].text == "(" and k in self.bm:
            inner = self.bm[k]
            if t[k + 1].kind == "id" and (t[k + 1].text in SCALAR_TYPES or t[inner - 1].text in ("*", "&")) \
                    and t[inner + 1].text not in (";", ")", ",", "+", "-", "*", "/"):
                return None  # a cast
        if t[k].kind not in ("id", "num", "str", "chr") and t[k].text not in ("(", "this"):
            return None
        return self.postfix_end(k)

    def _operands(self, k: int):
        """Operand token ranges (a0, a1, b0, b1) of the binary operator at k."""
        t = self.toks
        p = PREC.get(t[k].text)
        if p is None or not self._operand_end_tok(k - 1):
            return None
        # left
        j = k - 1
        a0 = self._prefix_start(j)
        if a0 is None:
            return None
        while True:
            q = a0 - 1
            if t[q].text in PREC and PREC[t[q].text] >= p and t[q].text != "?" and self._operand_end_tok(q - 1):
                s = self._prefix_start(q - 1)
                if s is None:
                    return None
                a0 = s
                continue
            break
        # right
        b1 = self._unary_end(k + 1)
        if b1 is None:
            return None
        while t[b1 + 1].text in PREC and PREC[t[b1 + 1].text] > p and t[b1 + 1].text != "?":
            e = self._unary_end(b1 + 2)
            if e is None:
                return None
            b1 = e
        stop_l = t[a0 - 1].text
        stop_r = t[b1 + 1].text
        if stop_l in ("::", ".", "->") or stop_r in ("(", "[", ".", "->", "::"):
            return None
        return a0, k - 1, k + 1, b1

    def _has_top(self, a: int, b: int, ops) -> bool:
        d = self.pdepth.get(a, 0)
        return any(self.toks[q].text in ops and self.pdepth.get(q) == d for q in range(a, b + 1))

    def _has_binop(self, a: int, b: int, ops) -> bool:
        d = self.pdepth.get(a, 0)
        return any(self.toks[q].text in ops and self.pdepth.get(q) == d and q > a
                   and self._operand_end_tok(q - 1) for q in range(a, b + 1))

    def _side_effects(self, a: int, b: int) -> bool:
        t = self.toks
        return any(t[q].text in ASSIGN_OPS or t[q].text in ("++", "--", "new", "delete") for q in range(a, b + 1))

    def _in_decl_type(self, k: int) -> bool:
        """Is the `*` at k part of a declarator (`T* p`) or a template argument?"""
        t = self.toks
        prv = t[k - 1]
        if prv.text in SCALAR_TYPES or prv.text in ("const", "void"):
            return True
        if prv.kind == "id" and t[k + 1].kind == "id" and t[k + 2].text in ("=", ";", ",", ")", "["):
            if prv.text[:1].isupper() or prv.text in self.idx.class_bases or prv.text.endswith("_t"):
                return True
        if t[k + 1].text in (")", ">", ",", "&", "*"):
            return True
        return False

    # ------------------------------------------------------------------
    # commutative operand order (integer and IEEE + and *)
    # ------------------------------------------------------------------
    def gen_commute(self):
        t = self.toks
        for k in range(self.bo + 1, self.bc):
            if t[k].text not in ("+", "*") or t[k].kind != "op":
                continue
            if t[k].text == "*" and self._in_decl_type(k):
                continue
            ops = self._operands(k)
            if ops is None:
                continue
            a0, a1, b0, b1 = ops
            if self._side_effects(a0, b1):
                continue
            if self.impure(a0, a1 + 1) and self.impure(b0, b1 + 1):
                continue
            if t[a0].kind == "str" or t[b0].kind == "str":
                continue
            # a literal operand is canonicalised to the left at parse time (c-r11): inert
            if (a0 == a1 or (a1 == a0 + 1 and t[a0].text == "-")) and t[a1].kind == "num":
                continue
            if (b0 == b1 or (b1 == b0 + 1 and t[b0].text == "-")) and t[b1].kind == "num":
                continue
            left, right = self.span(a0, a1), self.span(b0, b1)
            if self._has_binop(a0, a1, {x for x, v in PREC.items() if v <= PREC[t[k].text]}):
                left = "(%s)" % left
            self.add("commute", k, "%s %s %s -> %s %s %s" % (left[:20], t[k].text, right[:20], right[:20],
                                                            t[k].text, left[:20]),
                     [(t[a0].s, t[b1].e, "%s %s %s" % (right, t[k].text, left))])

    # ------------------------------------------------------------------
    # if/return vs ternary; if/else assignment vs ternary
    # ------------------------------------------------------------------
    def _single_stmt(self, k: int) -> Optional[Tuple[int, int]]:
        """The one statement of a branch starting at k (braced or not)."""
        t = self.toks
        if t[k].text == "{":
            st = self.block_stmts(k)
            if len(st) != 1 or st[0][1] + 1 != self.bm.get(k):
                return None
            return st[0]
        e = self._stmt_extent(k)
        return (k, e) if e is not None else None

    def _ternary_parts(self, a: int, e: int):
        """a..e is `C ? X : Y` at top level: return token ranges."""
        t = self.toks
        d = self.pdepth.get(a)
        q = [x for x in range(a, e + 1) if t[x].text == "?" and self.pdepth.get(x) == d]
        if len(q) != 1:
            return None
        q = q[0]
        c = [x for x in range(q + 1, e + 1) if t[x].text == ":" and self.pdepth.get(x) == d]
        if len(c) != 1:
            return None
        c = c[0]
        if self._has_top(a, q - 1, {"=", "+=", "-=", ","}):
            return None
        return (a, q - 1), (q + 1, c - 1), (c + 1, e)

    def _indent(self, k: int) -> str:
        ls, pre, alone = self.line_start(k)
        return re.match(r"[ \t]*", pre).group(0)

    def gen_ternary(self):
        t = self.toks
        for blk in self.blocks():
            st = self.block_stmts(blk)
            for n, (s, e) in enumerate(st):
                ind = self._indent(s)
                if t[s].text == "if" and t[s + 1].text == "(":
                    cp = self.bm[s + 1]
                    cond = self.span(s + 2, cp - 1)
                    then = self._single_stmt(cp + 1)
                    if then is None:
                        continue
                    th_s, th_e = then
                    then_end = self.bm[cp + 1] if t[cp + 1].text == "{" else th_e
                    els = None
                    if t[then_end + 1].text == "else":
                        els = self._single_stmt(then_end + 2)
                        if els is None:
                            continue
                    # if (C) return X; [else] return Y;
                    if t[th_s].text == "return" and th_e > th_s + 1:
                        X = self.span(th_s + 1, th_e - 1)
                        if els is not None and t[els[0]].text == "return" and els[1] > els[0] + 1:
                            Y = self.span(els[0] + 1, els[1] - 1)
                            self.add("ternary", s, "if/else return -> ?:",
                                     [(t[s].s, t[e].e, "return %s ? %s : %s;" % (cond, X, Y))])
                        elif els is None and n + 1 < len(st) and t[st[n + 1][0]].text == "return" \
                                and st[n + 1][1] > st[n + 1][0] + 1:
                            s2, e2 = st[n + 1]
                            Y = self.span(s2 + 1, e2 - 1)
                            self.add("ternary", s, "if-return; return -> ?:",
                                     [(t[s].s, t[e2].e, "return %s ? %s : %s;" % (cond, X, Y))])
                    # if (C) v = X; else v = Y;
                    if els is not None and t[th_s + 1].text == "=" and t[els[0] + 1].text == "=" \
                            and t[th_s].kind == "id" and t[th_s].text == t[els[0]].text:
                        v = t[th_s].text
                        X = self.span(th_s + 2, th_e - 1)
                        Y = self.span(els[0] + 2, els[1] - 1)
                        self.add("ternary", s, "if/else %s = -> ?:" % v,
                                 [(t[s].s, t[e].e, "%s = %s ? %s : %s;" % (v, cond, X, Y))])
                    continue
                # return C ? X : Y;  ->  if (C) { return X; } return Y;
                if t[s].text == "return" and e > s + 1:
                    parts = self._ternary_parts(s + 1, e - 1)
                    if parts:
                        (c0, c1), (x0, x1), (y0, y1) = parts
                        C, X, Y = self.span(c0, c1), self.span(x0, x1), self.span(y0, y1)
                        if C.startswith("(") and self.bm.get(c0) == c1:
                            C = C[1:-1]
                        rep = "if (%s) {\n%s\treturn %s;\n%s}\n%sreturn %s;" % (C, ind, X, ind, ind, Y)
                        self.add("ternary", s, "?: -> if-return; return", [(t[s].s, t[e].e, rep)])
                        rep = "if (%s) {\n%s\treturn %s;\n%s} else {\n%s\treturn %s;\n%s}" % (
                            C, ind, X, ind, ind, Y, ind)
                        self.add("ternary", s, "?: -> if/else return", [(t[s].s, t[e].e, rep)])
                    continue
                # v = C ? X : Y;
                if t[s].kind == "id" and t[s + 1].text == "=" and t[s].text not in KEYWORDS:
                    parts = self._ternary_parts(s + 2, e - 1)
                    if parts:
                        (c0, c1), (x0, x1), (y0, y1) = parts
                        C, X, Y = self.span(c0, c1), self.span(x0, x1), self.span(y0, y1)
                        if C.startswith("(") and self.bm.get(c0) == c1:
                            C = C[1:-1]
                        v = t[s].text
                        rep = "if (%s) {\n%s\t%s = %s;\n%s} else {\n%s\t%s = %s;\n%s}" % (
                            C, ind, v, X, ind, ind, v, Y, ind)
                        self.add("ternary", s, "%s = ?: -> if/else" % v, [(t[s].s, t[e].e, rep)])

    # ------------------------------------------------------------------
    # if (f()) vs if (f() != 0)
    # ------------------------------------------------------------------
    def gen_cond_zero(self):
        t = self.toks
        for k in range(self.bo + 1, self.bc):
            x = t[k].text
            # bare call in a condition
            if t[k].kind == "id" and t[k].text not in KEYWORDS and t[k - 1].text not in (".", "->", "::"):
                p = t[k - 1].text
                neg = p == "!"
                pp = t[k - 2].text if neg else p
                ctx = pp in ("&&", "||") or (pp == "(" and t[k - 3 if neg else k - 2].text in ("if", "while"))
                if not ctx:
                    continue
                e = self.postfix_end(k)
                if t[e].text != ")" or t[e + 1].text not in (")", "&&", "||"):
                    continue
                if self.is_pointer(k, e):
                    continue
                rt = self.call_type(self.postfix_start(e), e)
                if rt in ("bool",) or (rt and rt.endswith("*")):
                    continue
                expr = self.span(k, e)
                if neg:
                    self.add("cond-zero", k, "!%s -> == 0" % expr[:30], [(t[k - 1].s, t[e].e, "%s == 0" % expr)])
                else:
                    self.add("cond-zero", k, "%s -> != 0" % expr[:30], [(t[k].s, t[e].e, "%s != 0" % expr)])
            elif x in ("==", "!=") and t[k + 1].text == "0" and t[k - 1].text == ")" \
                    and t[k + 2].text in (")", "&&", "||"):
                a = self.postfix_start(k - 1)
                if t[a - 1].text not in ("(", "&&", "||"):
                    continue
                if t[a - 1].text == "(" and t[a - 2].text not in ("if", "while") and \
                        t[a - 1].text != "&&":
                    if t[a - 1].text == "(":
                        continue
                if self.is_pointer(a, k - 1):
                    continue
                expr = self.span(a, k - 1)
                rep = expr if x == "!=" else "!" + expr
                self.add("cond-zero", k, "%s %s 0 -> %s" % (expr[:30], x, "bare" if x == "!=" else "!"),
                         [(t[a].s, t[k + 1].e, rep)])

    # ------------------------------------------------------------------
    # statement order where there is no dependency
    # ------------------------------------------------------------------
    def _effects(self, s: int, e: int):
        """(reads, writes, mem_reads, mem_writes, impure) of a simple statement, or None."""
        t = self.toks
        if t[s].text in ("if", "for", "while", "do", "switch", "return", "break", "continue", "goto",
                         "case", "default", "{", "else"):
            return None
        reads, writes, mr, mw = set(), set(), set(), set()
        impure = False
        decl = None
        for d in self.decls():
            if d[0] == s:
                decl = d
        start = s
        if decl is not None:
            writes.add(t[decl[2]].text)
            start = decl[3] if decl[3] is not None else e
            if not (all(t[q].text in SCALAR_TYPES or t[q].text in ("*", "const") for q in range(s, decl[1] + 1))):
                if t[decl[1]].text != "*":
                    return None  # a class object: its constructor may do anything
        for q in range(start, e):
            x = t[q]
            if x.kind == "id" and x.text in self.var_types and t[q + 1].text != "." and \
                    (t[q + 1].kind == "op" or t[q - 1].kind == "op") and t[q - 1].text not in (".", "->"):
                ty = self.var_types[x.text].replace("const", "").strip()
                if not (ty.endswith("*") or ty.split()[-1] in SCALAR_TYPES):
                    impure = True  # an operator on a class object is a call that may change it
                    writes.add(x.text)
            if x.kind == "id" and x.text not in KEYWORDS:
                if t[q - 1].text in (".", "->"):
                    mr.add(x.text)
                elif t[q + 1].text != "(" and t[q + 1].text != "::" and t[q - 1].text != "::":
                    reads.add(x.text)
            if x.text == "(" and q > 0 and t[q - 1].kind == "id" and t[q - 1].text not in KEYWORDS:
                n = t[q - 1].text
                if not (n in self.idx.getters or re.match(r"(get|is|has)[A-Z_]", n) or n in SCALAR_TYPES):
                    impure = True
            if x.text in ASSIGN_OPS or x.text in ("++", "--"):
                # the lvalue left of the operator (or the operand of ++/--)
                if x.text in ("++", "--"):
                    j = q + 1 if t[q + 1].kind == "id" else q - 1
                else:
                    j = q - 1
                if t[j].text == "]":
                    j = self.bm[j] - 1
                if t[j].kind != "id":
                    return None
                if t[j - 1].text in (".", "->"):
                    mw.add(t[j].text)
                elif t[j - 1].text == "*":
                    return None
                else:
                    writes.add(t[j].text)
            if x.text in ("new", "delete", "asm"):
                return None
        return reads, writes, mr, mw, impure

    def _independent(self, A, B) -> bool:
        ra, wa, mra, mwa, ia = A
        rb, wb, mrb, mwb, ib = B
        if wa & (rb | wb) or wb & ra:
            return False
        if mwa & (mrb | mwb) or mwb & mra:
            return False
        if ia and (ib or mrb or mwb or rb & self._globals() or wb & self._globals()):
            return False
        if ib and (mra or mwa or ra & self._globals() or wa & self._globals()):
            return False
        # a write to a member of the class (implicit this) vs any call
        return True

    def _globals(self):
        return set(self.idx.globals) | {"this"}

    def gen_stmt_swap(self):
        t = self.toks
        for blk in self.blocks():
            st = self.block_stmts(blk)
            eff = [self._effects(s, e) for s, e in st]
            for n in range(len(st) - 1):
                A, B = eff[n], eff[n + 1]
                if A is None or B is None or not self._independent(A, B):
                    continue
                (s1, e1), (s2, e2) = st[n], st[n + 1]
                self.add("stmt-swap", s1, "swap L%d/L%d" % (self.text.count("\n", 0, t[s1].s) + 1,
                                                           self.text.count("\n", 0, t[s2].s) + 1),
                         [(t[s1].s, t[e1].e, self.span(s2, e2)), (t[s2].s, t[e2].e, self.span(s1, e1))])

    # ------------------------------------------------------------------
    # for-loop counter scope
    # ------------------------------------------------------------------
    def gen_for_scope(self):
        t = self.toks
        by_block: Dict[Tuple[int, str, str], List[Tuple[int, int, int]]] = {}
        for k in range(self.bo + 1, self.bc):
            if t[k].text == "for" and t[k + 1].text == "(" and t[k + 2].text in SCALAR_TYPES \
                    and t[k + 3].kind == "id" and t[k + 4].text == "=":
                s = self.stmt_start(k) if t[k - 1].text in (";", "{", "}") else None
                if s is None:
                    continue
                ty, name = t[k + 2].text, t[k + 3].text
                blk = self.block_of(k)
                by_block.setdefault((blk, ty, name), []).append((k, s, k + 2))
        for (blk, ty, name), sites in by_block.items():
            eds = []
            for k, s, tyk in sites:
                eds.append((t[tyk].s, t[tyk + 1].s, ""))
            # declare once at the first loop
            k0, s0, _ = sites[0]
            e1 = eds + [self.insert_before_stmt(s0, "%s %s;" % (ty, name))]
            self.add("for-scope", k0, "hoist %s %s out of %d for" % (ty, name, len(sites)), e1)
            first = blk + 1
            if first < s0 and t[first].text not in ("}", "case", "default"):
                e2 = eds + [self.insert_before_stmt(first, "%s %s;" % (ty, name))]
                self.add("for-scope", k0, "declare %s %s at block top for %d for" % (ty, name, len(sites)), e2)
        # reverse: `T i;` declared alone, every use inside one for header/body
        for s, te, ni, init, e in self.decls():
            if init is not None or te != s or t[s].text not in SCALAR_TYPES:
                continue
            name = t[ni].text
            us = self.uses(name, e + 1)
            if not us:
                continue
            f = us[0] - 2
            if t[f].text != "(" or t[f - 1].text != "for" or t[us[0] + 1].text != "=":
                continue
            cp = self.bm.get(f)
            body_end = self._stmt_extent(cp + 1) if cp else None
            if body_end is None or any(u > body_end for u in us):
                continue
            if any(t[q].text == "for" and t[q + 2].text == name for q in range(body_end, self.bc)):
                continue
            self.add("for-scope", s, "declare %s in the for header" % name,
                     [self.delete_stmt_edit(s, e), (t[us[0]].s, t[us[0]].s, "%s " % t[s].text)])

    # ------------------------------------------------------------------
    # accumulate into one local: T x = a OP b;  ->  T x = a; x OP= b;
    # ------------------------------------------------------------------
    SPLIT_OPS = ("+", "-", "*", "|", "&", "^", "<<", ">>")

    def _split_point(self, a: int, b: int) -> Optional[int]:
        t = self.toks
        d = self.pdepth.get(a)
        tops = [q for q in range(a, b + 1) if self.pdepth.get(q) == d and t[q].text in PREC]
        if not tops or any(t[q].text in ("?", "&&", "||", "==", "!=", "<", ">", "<=", ">=") for q in tops):
            return None
        tops = [q for q in tops if self._operand_end_tok(q - 1)]
        if not tops:
            return None
        lo = min(PREC[t[q].text] for q in tops)
        cand = [q for q in tops if PREC[t[q].text] == lo]
        q = cand[-1]
        return q if t[q].text in self.SPLIT_OPS else None

    def gen_accumulate(self):
        t = self.toks
        for s, te, ni, init, e in self.decls():
            if init is None:
                continue
            tytoks = [t[q].text for q in range(s, te + 1)]
            if not all(x in SCALAR_TYPES or x == "const" for x in tytoks) or "const" in tytoks:
                continue
            if self.stmt_start(s) is None:
                continue
            q = self._split_point(init, e - 1)
            if q is None:
                continue
            ls, pre, alone = self.line_start(s)
            if not alone:
                continue
            name = t[ni].text
            ty = self.span(s, te)
            A = self.span(init, q - 1)
            B = self.span(q + 1, e - 1)
            self.add("accumulate", s, "%s = A %s B -> %s= B" % (name, t[q].text, t[q].text),
                     [(t[s].s, t[e].e, "%s %s = %s;\n%s%s %s= %s;" % (ty, name, A, pre, name, t[q].text, B))])
        # a sum compared in a condition: if (t > a + b)  ->  T x = a; x += b; if (t > x)
        for k in range(self.bo + 1, self.bc):
            if t[k].text not in ("<", ">", "<=", ">=") or self.pdepth.get(k) is None:
                continue
            for side in ("r", "l"):
                if side == "r":
                    a0 = k + 1
                    b0 = a0
                    while b0 + 1 < self.bc and t[b0 + 1].text not in (")", "&&", "||", ";", "?", ","):
                        b0 += 1
                else:
                    b0 = k - 1
                    a0 = b0
                    while a0 - 1 > self.bo and t[a0 - 1].text not in ("(", "&&", "||", "=", ",", "return"):
                        a0 -= 1
                if a0 >= b0:
                    continue
                q = self._split_point(a0, b0)
                if q is None or t[q].text not in ("+", "-"):
                    continue
                if q - 1 != a0 and self.postfix_start(q - 1) != a0:
                    continue
                ty = self._expr_type(a0, q - 1)
                if ty is None or ty not in SCALAR_TYPES:
                    continue
                s = self.stmt_start(k)
                if s is None or self.impure(s, q) or any(t[x].text in ("&&", "||", "?") for x in range(s, a0)):
                    continue
                base = "sum"
                v = self.fresh(base, self.block_of(s), s)
                ls, pre, alone = self.line_start(s)
                code = "%s %s = %s;\n%s%s %s= %s;" % (ty, v, self.span(a0, q - 1), pre, v, t[q].text,
                                                      self.span(q + 1, b0))
                self.add("accumulate", k, "name %s as %s += ..." % (self.span(a0, b0)[:30], v),
                         [self.insert_before_stmt(s, code), (t[a0].s, t[b0].e, v)])

    def gen_merge_assign(self):
        """T x = A; x OP= B;  ->  T x = A OP B;  (the reverse of accumulate)."""
        t = self.toks
        ds = {d[0]: d for d in self.decls()}
        for blk in self.blocks():
            st = self.block_stmts(blk)
            for n in range(len(st) - 1):
                s, e = st[n]
                d = ds.get(s)
                if d is None or d[3] is None:
                    continue
                name = t[d[2]].text
                s2, e2 = st[n + 1]
                if t[s2].text != name or t[s2 + 1].text not in ("+=", "-=", "*=", "|=", "&=", "^=", "<<=", ">>="):
                    continue
                op = t[s2 + 1].text[:-1]
                if any(t[q].text == name for q in range(s2 + 2, e2)):
                    continue
                A = self.span(d[3], e - 1)
                B = self.span(s2 + 2, e2 - 1)
                if self._has_top(d[3], e - 1, {x for x, v in PREC.items() if v < PREC[op]}):
                    A = "(%s)" % A
                if self._has_top(s2 + 2, e2 - 1, {x for x, v in PREC.items() if v <= PREC[op]}):
                    B = "(%s)" % B
                self.add("merge-assign", s, "%s = A; %s %s= B -> one expression" % (name, name, op),
                         [(t[d[3]].s, t[e2].e, "%s %s %s;" % (A, op, B))])

    # ------------------------------------------------------------------
    # (p = &G)->m = ...
    # ------------------------------------------------------------------
    def gen_addr_assign(self):
        t = self.toks
        for s, te, ni, init, e in self.decls():
            if init is None or t[init].text != "&" or t[te].text != "*":
                continue
            name = t[ni].text
            us = self.uses(name, e + 1)
            if not us or t[us[0] + 1].text != "->":
                continue
            u = us[0]
            if self.stmt_start(u) != u:
                continue
            ty = self.span(s, te)
            val = self.span(init, e - 1)
            self.add("addr-assign", s, "(%s = %s)->... at the first store" % (name, val[:30]),
                     [(t[s].s, t[e].e, "%s %s;" % (ty, name)), (t[u].s, t[u].e, "(%s = %s)" % (name, val))])

    # ------------------------------------------------------------------
    # predicate level: return E;  <->  bool result = false; if (E) result = true; return result;
    # ------------------------------------------------------------------
    def gen_pred_level(self):
        t = self.toks
        head = [x.text for x in t[self.loc.head:self.loc.name_tok]]
        rt = "bool" if "bool" in head else ("BOOL" if "BOOL" in head else None)
        if rt is None:
            return
        f, tr = ("false", "true") if rt == "bool" else ("FALSE", "TRUE")
        for blk in self.blocks():
            st = self.block_stmts(blk)
            for n, (s, e) in enumerate(st):
                if t[s].text != "return" or e <= s + 1:
                    continue
                E = self.span(s + 1, e - 1)
                if not self._has_top(s + 1, e - 1, {"&&", "||", "==", "!=", "<", ">", "<=", ">=", "!"}) and \
                        t[s + 1].text != "!":
                    continue
                if E.startswith("(") and self.bm.get(s + 1) == e - 1:
                    E = E[1:-1]
                if "result" in self.used_fn:
                    continue
                ind = self._indent(s)
                rep = "%s result = %s;\n%sif (%s) {\n%s\tresult = %s;\n%s}\n%sreturn result;" % (
                    rt, f, ind, E, ind, tr, ind, ind)
                self.add("pred-level", s, "return E -> %s result local" % rt, [(t[s].s, t[e].e, rep)])
            # reverse: T r = false; if (E) r = true; return r;
            for n in range(len(st) - 2):
                d = [x for x in self.decls() if x[0] == st[n][0]]
                if not d or d[0][3] is None:
                    continue
                s, te, ni, init, e = d[0]
                name = t[ni].text
                if self.span(init, e - 1) not in ("false", "FALSE", "0"):
                    continue
                s2, e2 = st[n + 1]
                s3, e3 = st[n + 2]
                if t[s2].text != "if" or t[s3].text != "return" or t[s3 + 1].text != name or e3 != s3 + 2:
                    continue
                cp = self.bm[s2 + 1]
                then = self._single_stmt(cp + 1)
                if then is None or (then[1] != e2 and self.bm.get(cp + 1) != e2):
                    continue
                if self.span(then[0], then[1]).replace(" ", "") not in ("%s=true;" % name, "%s=TRUE;" % name,
                                                                        "%s=1;" % name):
                    continue
                cond = self.span(s2 + 2, cp - 1)
                self.add("pred-level", s, "%s local -> return E" % name,
                         [(t[s].s, t[e3].e, "return %s;" % cond)])

    # ------------------------------------------------------------------
    # a TU-local inline level with real content
    # ------------------------------------------------------------------
    CONTENT_OPS = {"+", "-", "*", "/", "%", "<<", ">>", "&", "|", "^", "<", ">", "<=", ">=", "==", "!=",
                   "&&", "||"}

    def _free_ids(self, a: int, b: int, declared: Set[str]):
        """Classify the free identifiers of a..b: locals (-> params), members (-> self->), others."""
        t = self.toks
        locals_, members, calls_self = [], [], []
        klass = self.loc.info.klass
        for q in range(a, b + 1):
            x = t[q]
            if x.kind != "id" or x.text in KEYWORDS - {"this"}:
                continue
            if t[q - 1].text in (".", "->", "::") or t[q + 1].text == "::":
                continue
            n = x.text
            if n in declared:
                continue
            if n == "this":
                locals_.append(q)
                continue
            if n in self.var_types:
                locals_.append(q)
                continue
            if n in self.idx.globals or n in SCALAR_TYPES:
                continue
            if t[q + 1].text == "(":
                if klass and n in class_methods(klass, self.text):
                    calls_self.append(q)
                continue
            if n[:1].isupper() or n.isupper() or re.match(r"k[A-Z]", n) or t[q + 1].text in ("<",):
                continue  # types, enum constants, macros
            if klass and self.idx.unique("member_types", n) is not None:
                members.append(q)
            elif klass and re.match(r"(m|unk|_)[A-Z0-9_]", n):
                members.append(q)
        return locals_, members, calls_self

    def _declared_name(self, k: int) -> Optional[str]:
        """The name declared by a statement starting at k (any initialiser form), or None."""
        d = self._decl_at(k)
        return self.toks[d[2]].text if d else None

    def _decl_at(self, k: int):
        """(start, type_end, name_idx) of a declaration statement starting at k, or None."""
        t = self.toks
        k0 = k
        if k >= self.bc:
            return None
        if t[k].text == "const":
            k += 1
        if t[k].kind != "id" or (t[k].text in KEYWORDS and t[k].text not in SCALAR_TYPES):
            return None
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
                elif t[j].text in (";", "{", "}"):
                    return None
                if d <= 0:
                    break
                j += 1
            k = j
        while t[k + 1].text in ("unsigned", "long", "int", "short", "char"):
            k += 1
        while t[k + 1].text in ("*", "&", "const"):
            k += 1
        if t[k + 1].kind == "id" and t[k + 1].text not in KEYWORDS and t[k + 2].text in ("=", "(", ";", "[", "{", ","):
            return (k0, k, k + 1)
        return None

    def _writes_any(self, a: int, b: int, names: Set[str]) -> bool:
        t = self.toks
        for q in range(a, b + 1):
            if t[q].text in names and t[q].kind == "id" and t[q - 1].text not in (".", "->", "::"):
                if t[q + 1].text in ASSIGN_OPS or t[q + 1].text in ("++", "--") or t[q - 1].text in ("++", "--", "&"):
                    return True
        return False

    def gen_extract(self):
        t = self.toks
        info = self.loc.info
        klass = info.klass
        cq = "const " if info.const else ""
        ds = self.decls()
        dnames = {d[0]: t[d[2]].text for d in ds}
        count = 0
        for blk in self.blocks():
            st = self.block_stmts(blk)
            blk_end = self.bm.get(blk, self.bc)
            for i in range(len(st)):
                for j in range(i, min(len(st), i + 4)):
                    a, b = st[i][0], st[j][1]
                    body_toks = [x.text for x in t[a:b + 1]]
                    if any(x in ("return", "break", "continue", "goto", "case", "default") for x in body_toks):
                        continue
                    nstmt = j - i + 1
                    has_ctl = t[a].text in ("for", "while", "do", "if", "switch")
                    has_op = any(t[q].text in self.CONTENT_OPS and t[q].kind == "op" and self._operand_end_tok(q - 1)
                                 for q in range(a + 1, b + 1))
                    if nstmt < 2 and not has_ctl and not has_op:
                        continue
                    declared = set()
                    for q in range(i, j + 1):
                        if st[q][0] in dnames:
                            declared.add(dnames[st[q][0]])
                    for q in range(a, b + 1):
                        if t[q].text in (";", "{", "}") or q == a:
                            n_ = self._declared_name(q if q == a else q + 1)
                            if n_:
                                declared.add(n_)
                    for d in ds:  # declarations nested in the run (loop bodies)
                        if a <= d[0] <= b:
                            declared.add(t[d[2]].text)
                    for q in range(a, b):
                        if t[q].text == "for" and t[q + 1].text == "(" and t[q + 3].kind == "id" and \
                                t[q + 2].text in SCALAR_TYPES:
                            declared.add(t[q + 3].text)
                    # a name declared in the run and used after it cannot move
                    if any(t[q].text in declared and t[q].kind == "id" and t[q - 1].text not in (".", "->", "::")
                           for q in range(b + 1, blk_end)):
                        continue
                    locs, mems, selfcalls = self._free_ids(a, b, declared)
                    lnames = []
                    for q in locs:
                        if t[q].text not in lnames:
                            lnames.append(t[q].text)
                    if self._writes_any(a, b, set(lnames) - {"this"}):
                        continue
                    need_self = bool(mems or selfcalls or "this" in lnames)
                    if need_self and not klass:
                        continue
                    params, args = [], []
                    if need_self:
                        params.append("%s%s* self" % (cq, klass))
                        args.append("this")
                    ok = True
                    for n in lnames:
                        if n == "this":
                            continue
                        ty = self.var_types.get(n)
                        if ty is None or "[" in ty:
                            ok = False
                            break
                        ty = ty.strip()
                        if not (ty.endswith("*") or ty.endswith("&") or ty.split()[-1] in SCALAR_TYPES):
                            mut = any(t[q].text == n and ((t[q + 1].text == "." and t[q + 2].kind == "id"
                                      and (t[q + 3].text == "(" or t[q + 3].text in ASSIGN_OPS))
                                      or t[q - 1].text == "&" and not self._operand_end_tok(q - 2)
                                      or t[q - 1].text in ("(", ",") and t[q + 1].text in (")", ","))
                                      for q in range(a, b + 1))
                            if mut and not ty.startswith("const"):
                                ty = ty + "&"
                            else:
                                ty = "const %s&" % ty.replace("const ", "") if not ty.startswith("const") else ty + "&"
                        params.append("%s %s" % (ty, n))
                        args.append(n)
                    if not ok or len(params) > 5:
                        continue
                    # body text with implicit members made explicit
                    reps = {q: "self->" + t[q].text for q in mems + selfcalls}
                    for q in locs:
                        if t[q].text == "this":
                            reps[q] = "self"
                    out, pos = [], t[a].s
                    for q in sorted(reps):
                        out.append(self.text[pos:t[q].s])
                        out.append(reps[q])
                        pos = t[q].e
                    out.append(self.text[pos:t[b].e])
                    body = "".join(out)
                    ind = self._indent(a)
                    lines = body.split("\n")
                    body = "\n".join(("\t" + l[len(ind):] if l.startswith(ind) else "\t" + l.lstrip())
                                     if n_ else "\t" + l for n_, l in enumerate(lines))
                    count += 1
                    fn = self.fresh("%sPart%d" % (info.name if info.name[0].islower() else "fn", count))
                    helper = "static inline void %s(%s)\n{\n%s\n}\n\n" % (fn, ", ".join(params), body)
                    self.add("extract", a, "lines %d-%d into %s()" % (
                        self.text.count("\n", 0, t[a].s) + 1, self.text.count("\n", 0, t[b].s) + 1, fn),
                        [self.helper_edit(helper), (t[a].s, t[b].e, "%s(%s);" % (fn, ", ".join(args)))])

    # ------------------------------------------------------------------
    # raw matrix array vs the conversion operator
    # ------------------------------------------------------------------
    def gen_conv_raw(self):
        t = self.toks
        for k in range(self.bo + 1, self.bc):
            if t[k].text == "mMtx" and t[k - 1].text == "." and t[k + 1].text in (",", ")"):
                a = self.postfix_start(k - 2)
                if t[a - 1].text not in ("(", ","):
                    continue
                self.add("conv-raw", k, "%s -> conversion operator" % self.span(a, k)[:30],
                         [(t[k - 1].s, t[k].e, "")])
            elif t[k].kind == "id" and t[k + 1].text in (",", ")") and t[k - 1].text in ("(", ",", ".", "->"):
                ty = self.var_types.get(t[k].text) if t[k - 1].text in ("(", ",") else \
                    self.idx.unique("member_types", t[k].text)
                if not ty or not re.search(r"TMatrix34|TRotation3|TPosition3|TMatrix44|TSRT", ty):
                    continue
                if ty.rstrip().endswith("*"):
                    continue
                a = self.postfix_start(k)
                if t[a - 1].text not in ("(", ","):
                    continue
                self.add("conv-raw", k, "%s -> raw .mMtx" % self.span(a, k)[:30],
                         [(t[k].e, t[k].e, ".mMtx")])


_CLASS_CACHE: Dict[str, Tuple[Dict[str, Set[str]], Dict[str, List[str]]]] = {}


def _scan_classes(text: str, methods: Dict[str, Set[str]], bases: Dict[str, List[str]]):
    text = re.sub(r"//[^\n]*|/\*.*?\*/", " ", text, flags=re.S)
    for cm in LS.CLASS_RE.finditer(text):
        ob = cm.end() - 1
        depth, j = 0, ob
        body = []
        while j < len(text):
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            elif depth == 1:
                body.append(c)
            if depth == 1 and c in "{}":
                body.append(" ")
            j += 1
        cls = cm.group(1)
        names = set(re.findall(r"~?\b([A-Za-z_]\w*)\s*\(", "".join(body)))
        methods.setdefault(cls, set()).update(names)
        bs = [b for b in re.findall(r"(\w+)\s*(?:<[^<>]*>)?\s*(?:,|$)", (cm.group(2) or "").strip())
              if b not in ("public", "private", "protected", "virtual")]
        bases.setdefault(cls, []).extend(bs)


def class_methods(klass: str, unit_text: str = "") -> Set[str]:
    """Method names of klass and its bases, from include/ and the unit."""
    if "hdr" not in _CLASS_CACHE:
        m, b = {}, {}
        for dp, _, fs in os.walk(os.path.join(ROOT, "include")):
            for f in fs:
                if f.endswith((".h", ".hpp", ".inc")):
                    _scan_classes(open(os.path.join(dp, f), encoding="utf-8", errors="replace").read(), m, b)
        _CLASS_CACHE["hdr"] = (m, b)
    m, b = _CLASS_CACHE["hdr"]
    key = "u:%d" % hash(unit_text)
    if key not in _CLASS_CACHE:
        um, ub = {k: set(v) for k, v in m.items()}, {k: list(v) for k, v in b.items()}
        _scan_classes(unit_text, um, ub)
        _CLASS_CACHE.clear()
        _CLASS_CACHE["hdr"] = (m, b)
        _CLASS_CACHE[key] = (um, ub)
    um, ub = _CLASS_CACHE[key]
    out, todo, seen = set(), [klass], set()
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        out |= um.get(c, set())
        todo += ub.get(c, [])
    return out


def generate(text: str, info, idx=None) -> List[Move]:
    toks = lex(text)
    bm = bracket_map(toks)
    loc = locate_function(toks, bm, info)
    if loc is None:
        return []
    if idx is None:
        idx = merged_index(text)
    return HGen(text, loc, toks, bm, idx).honest()


def apply(text: str, moves: List[Move]) -> Optional[str]:
    # apply_edits merges an edit two moves share, which is right for a helper
    # both insert, but two moves that rewrite or delete the same text (two
    # decl-hoists of one declaration to different blocks) conflict: merged,
    # they leave a dead outer declaration shadowed by the inner one
    owner = {}
    for i, m in enumerate(moves):
        for e in m.edits:
            if e[0] < e[1] and owner.setdefault(e, i) != i:
                return None
    return apply_edits(text, [e for m in moves for e in m.edits])


# ----------------------------------------------------------------------
# Honesty lint: refuse a winner whose text gained any of these
# ----------------------------------------------------------------------

BANNED = [
    (re.compile(r"\bvolatile\b"), "volatile"),
    (re.compile(r"#\s*pragma"), "pragma"),
    (re.compile(r"reinterpret_cast"), "reinterpret_cast"),
    (re.compile(r"\b(?:trash|pad|padding)\s*\["), "padding array"),
    (re.compile(r"\(void\)\s*0\s*;"), "(void)0 filler"),
    (re.compile(r"\bif\s*\([^;{}]*\)\s*\{\s*\}"), "empty if body"),
    (re.compile(r"static\s+inline\s+[^{;]*\(([^()]*)\)\s*\{\s*return\s+[\w>.\-()]*\s*;\s*\}"), "pass-through helper"),
    (re.compile(r"static\s+inline\s+[^{;]*\{\s*[\w:<>]+\s*\*?\s*(\w+)\s*=\s*[^;]*;\s*return\s+\1\s*;\s*\}"),
     "binder helper"),
]


def shadowed_decls(text: str) -> int:
    """Count local declarations (`T name;`, `T* name = ...;`) that shadow a
    declaration of the same name made in an enclosing, still open block."""
    toks = lex(text)
    n, stack = 0, []
    for k, tk in enumerate(toks):
        if tk.text == "{":
            stack.append(set())
        elif tk.text == "}":
            if stack:
                stack.pop()
        elif stack and tk.kind == "id" and tk.text not in KEYWORDS and k + 1 < len(toks) \
                and toks[k + 1].text in (";", "="):
            j = k - 1
            while j > 0 and toks[j].text in ("*", "&"):
                j -= 1
            if toks[j].text == ">":  # a template type: back to its '<'
                d = 0
                while j > 0:
                    d += {">": 1, "<": -1}.get(toks[j].text, 0)
                    if d == 0:
                        break
                    j -= 1
                j -= 1
            if toks[j].kind != "id" or (toks[j].text in KEYWORDS and toks[j].text != "const"):
                continue
            while j > 0 and (toks[j - 1].text == "::" or toks[j - 1].text in ("const", "unsigned", "signed")):
                j -= 2 if toks[j - 1].text == "::" else 1
            if toks[j - 1].text not in (";", "{", "}", "("):
                continue
            if any(tk.text in sc for sc in stack[:-1]):
                n += 1
            stack[-1].add(tk.text)
    return n


def lint(base: str, text: str) -> List[str]:
    bad = []
    for rx, name in BANNED:
        if len(rx.findall(text)) > len(rx.findall(base)):
            bad.append(name)
    if shadowed_decls(text) > shadowed_decls(base):
        bad.append("shadowed declaration")
    return bad
