"""In-process scoring of one function against the retail object.

Ranking leads with objdiff's fuzzy match percent (`fuzzy_match`), the metric
`ninja changes_all` gates on; the finer in-process counts break its ties.

No objdiff: both objects are parsed here (ELF32 big-endian, MWCC output and
the dtk-split retail objects), the function's words are compared with every
relocated field masked, and relocation targets are compared by identity
(named symbols by name, compiler-numbered `@NNN` and section symbols by the
bytes they point at, `name$NNN` statics by their name without the number).

The score is lexicographic, lower is better:
    (not exact, -fuzzy match percent, differing instructions, |frame delta|,
     register mismatches, r1 slot-offset mismatches)
"""

import difflib
import json
import os
import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

R_PPC_ADDR32 = 1
R_PPC_ADDR24 = 2
R_PPC_ADDR16 = 3
R_PPC_ADDR16_LO = 4
R_PPC_ADDR16_HI = 5
R_PPC_ADDR16_HA = 6
R_PPC_ADDR14 = 7
R_PPC_REL24 = 10
R_PPC_REL14 = 11
R_PPC_EMB_SDA21 = 109


class Elf:
    """Just enough of an ELF32 big-endian relocatable object."""

    def __init__(self, path: str):
        with open(path, "rb") as f:
            d = f.read()
        self.data = d
        if d[:4] != b"\x7fELF":
            raise ValueError("not an ELF: " + path)
        shoff, = struct.unpack_from(">I", d, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from(">HHH", d, 0x2E)
        self.sections = []
        for i in range(shnum):
            v = struct.unpack_from(">IIIIIIIIII", d, shoff + i * shentsize)
            self.sections.append({"name_off": v[0], "type": v[1], "flags": v[2], "offset": v[4],
                                  "size": v[5], "link": v[6], "info": v[7], "entsize": v[9]})
        strsec = self.sections[shstrndx]
        for s in self.sections:
            s["name"] = self._str(strsec, s["name_off"])
        self.syms = []
        symtab = next((i for i, s in enumerate(self.sections) if s["type"] == 2), None)
        if symtab is not None:
            st = self.sections[symtab]
            strs = self.sections[st["link"]]
            for off in range(st["offset"], st["offset"] + st["size"], 16):
                name, value, size, info, other, shndx = struct.unpack_from(">IIIBBH", d, off)
                self.syms.append({"name": self._str(strs, name), "value": value, "size": size,
                                  "type": info & 0xF, "bind": info >> 4, "shndx": shndx})
        # relocations per target section: {secidx: [(offset, type, symidx, addend)]}
        self.relocs: Dict[int, List[Tuple[int, int, int, int]]] = {}
        for s in self.sections:
            if s["type"] != 4:  # SHT_RELA
                continue
            out = self.relocs.setdefault(s["info"], [])
            for off in range(s["offset"], s["offset"] + s["size"], 12):
                r_off, r_info, r_add = struct.unpack_from(">IIi", d, off)
                out.append((r_off, r_info & 0xFF, r_info >> 8, r_add))
            out.sort()

    def _str(self, sec, off):
        d = self.data
        a = sec["offset"] + off
        b = d.index(b"\0", a)
        return d[a:b].decode("latin-1")

    def sec_bytes(self, idx: int) -> bytes:
        s = self.sections[idx]
        if s["type"] == 8:  # NOBITS
            return b"\0" * s["size"]
        return self.data[s["offset"]:s["offset"] + s["size"]]

    def sized_in(self, shndx: int) -> List[dict]:
        if not hasattr(self, "_sized"):
            self._sized = {}
            for s in self.syms:
                if s["size"] and s["type"] != 2:
                    self._sized.setdefault(s["shndx"], []).append(s)
        return self._sized.get(shndx, [])

    def functions(self) -> Dict[str, dict]:
        out = {}
        for s in self.syms:
            if s["type"] == 2 and s["size"] and 0 < s["shndx"] < len(self.sections):
                out.setdefault(s["name"], s)
        return out


_NUM_SUFFIX = re.compile(r"\$\d+$")


def _target_key(elf: Elf, symidx: int, addend: int):
    s = elf.syms[symidx] if symidx < len(elf.syms) else None
    if s is None:
        return RelKey("?", None, None)
    name = s["name"]
    in_sec = 0 < s["shndx"] < len(elf.sections)
    local_data = in_sec and (name.startswith("@") or s["type"] == 3 or name == ""
                             or (s["bind"] == 0 and "$" not in name and s["type"] != 2))
    if local_data:
        sec = elf.sections[s["shndx"]]
        base = s["value"] + addend
        if s["size"] > addend:
            n = s["size"] - addend
        else:  # a section or label symbol: size it by the sized symbol it lands in
            n = 8
            for o in elf.sized_in(s["shndx"]):
                if o["value"] <= base < o["value"] + o["size"]:
                    n = o["value"] + o["size"] - base
                    break
        n = min(max(n, 1), 64)
        body = elf.sec_bytes(s["shndx"])[base:base + n]
        return RelKey(None, sec["name"], body)
    return RelKey(_NUM_SUFFIX.sub("", name) + ("+%d" % addend if addend else ""), None, None)


class RelKey:
    """A relocation target: a real symbol name, or the bytes an unnamed one points at.
    Two targets match when their names match, or their bytes do, or one side has
    only a name and the other only bytes (a static that one object names and the
    other does not)."""
    __slots__ = ("name", "sec", "body")

    def __init__(self, name, sec, body):
        self.name, self.sec, self.body = name, sec, body

    def __eq__(self, o):
        if not isinstance(o, RelKey):
            return NotImplemented
        if self.name is not None and o.name is not None:
            return self.name == o.name
        if self.name is None and o.name is None:
            return self.sec == o.sec and self.body == o.body
        return True

    def __ne__(self, o):
        r = self.__eq__(o)
        return r if r is NotImplemented else not r

    def __hash__(self):
        return 0

    def __repr__(self):
        return self.name if self.name is not None else "%s:%s" % (self.sec, self.body.hex()[:24])


@dataclass
class Func:
    words: List[int]
    masked: List[int]
    relocs: Dict[int, tuple]  # instruction index -> (type, target key)


def extract(elf: Elf, name: str) -> Optional[Func]:
    s = elf.functions().get(name)
    if s is None:
        return None
    sec = s["shndx"]
    body = elf.sec_bytes(sec)[s["value"]:s["value"] + s["size"]]
    words = list(struct.unpack(">%dI" % (len(body) // 4), body[:len(body) // 4 * 4]))
    masked = list(words)
    rel = {}
    lo, hi = s["value"], s["value"] + s["size"]
    for off, typ, symidx, add in elf.relocs.get(sec, ()):
        if off < lo or off >= hi:
            continue
        i = (off - lo) // 4
        if i >= len(words):
            continue
        if typ in (R_PPC_ADDR16_LO, R_PPC_ADDR16_HI, R_PPC_ADDR16_HA, R_PPC_ADDR16):
            masked[i] &= ~0xFFFF
        elif typ == R_PPC_EMB_SDA21:
            masked[i] &= ~0x1FFFFF
        elif typ in (R_PPC_REL24, R_PPC_ADDR24):
            masked[i] &= ~0x03FFFFFC
        elif typ in (R_PPC_REL14, R_PPC_ADDR14):
            masked[i] &= ~0xFFFC
        elif typ == R_PPC_ADDR32:
            masked[i] = 0
        masked[i] &= 0xFFFFFFFF
        rel[i] = (typ, _target_key(elf, symidx, add))
    return Func(words, masked, rel)


# --------------------------------------------------------------------------
# Instruction fields
# --------------------------------------------------------------------------

D_FORM = set([7, 8, 10, 11, 12, 13, 14, 15, 24, 25, 26, 27, 28, 29] + list(range(32, 56)))
PSQ = {56, 57, 60, 61}
MEM_D = set(range(32, 56)) | PSQ | {14}  # r1-relative when rA == 1 (14: addi rD, r1, d)


def op_key(w: int) -> int:
    """Opcode identity: primary opcode, plus the extended opcode where there is one."""
    p = w >> 26
    if p in (31, 19):
        return (p << 16) | ((w >> 1) & 0x3FF)
    if p in (59, 63, 4):
        xo5 = (w >> 1) & 0x1F
        if xo5 >= 16 or p == 59:
            return (p << 16) | xo5
        return (p << 16) | ((w >> 1) & 0x3FF) | 0x8000
    if p == 30:
        return (p << 16) | ((w >> 1) & 0xF)
    return p << 16


def reg_mask(w: int) -> int:
    """Bits of w that name registers (GPR/FPR/CR fields)."""
    p = w >> 26
    if p in D_FORM or p in PSQ or p in (20, 21):
        return 0x03FF0000
    if p == 23:
        return 0x03FFF800
    if p in (31, 19):
        return 0x03FFF800
    if p in (59, 63, 4):
        if ((w >> 1) & 0x1F) >= 16 or p == 59:
            return 0x03FFFFC0
        return 0x03FFF800
    if p == 16:
        return 0x03FF0000
    return 0


@dataclass
class Score:
    ok: bool
    err: str = ""
    exact: bool = False
    n_struct: int = 0
    frame_t: int = 0
    frame_o: int = 0
    n_reg: int = 0
    n_slot: int = 0
    n_t: int = 0
    n_o: int = 0
    sig: str = ""
    fuzzy: Optional[float] = None  # objdiff's fuzzy_match_percent, as report.json has it
    detail: List[str] = field(default_factory=list)

    @property
    def key(self) -> tuple:
        if not self.ok:
            return (2, 0.0, 10 ** 6, 0, 0, 0)
        return (0 if self.exact else 1, -round(self.fuzzy or 0.0, 4), self.n_struct,
                abs(self.frame_t - self.frame_o), self.n_reg, self.n_slot)

    @property
    def match(self) -> float:
        if not self.ok:
            return 0.0
        n = max(self.n_t, self.n_o, 1)
        bad = self.n_struct + self.n_reg + self.n_slot
        return max(0.0, 100.0 * (n - bad) / n)

    def short(self) -> str:
        if not self.ok:
            return "FAIL " + self.err[:100]
        if self.exact:
            return "EXACT"
        fr = "frame %#x" % self.frame_o if self.frame_t == self.frame_o else "frame %#x/%#x" % (self.frame_t, self.frame_o)
        sz = "" if self.n_t == self.n_o else " len %d/%d" % (self.n_t, self.n_o)
        fz = "" if self.fuzzy is None else "fuzzy %.2f%% " % self.fuzzy
        return "%sinsn %d reg %d slot %d %s%s" % (fz, self.n_struct, self.n_reg, self.n_slot, fr, sz)

    def to_row(self) -> str:
        return "%d,%d,%#x,%#x,%d,%d,%d,%d,%s" % (self.exact, self.n_struct, self.frame_t, self.frame_o,
                                                 self.n_reg, self.n_slot, self.n_t, self.n_o,
                                                 "" if self.fuzzy is None else "%.5f" % self.fuzzy)

    @classmethod
    def from_row(cls, row: str, sig: str = "") -> "Score":
        if row.startswith("FAIL"):
            return cls(False, row[5:])
        v = row.split(",")
        fz = float(v[8]) if len(v) > 8 and v[8] else None
        return cls(True, "", bool(int(v[0])), int(v[1]), int(v[2], 0), int(v[3], 0), int(v[4]), int(v[5]),
                   int(v[6]), int(v[7]), sig, fuzzy=fz)


def _frame(words: List[int]) -> int:
    for w in words[:12]:
        if w >> 16 == (37 << 10 | 1 << 5 | 1):  # stwu r1, d(r1)
            d = w & 0xFFFF
            return 0x10000 - d if d & 0x8000 else -d
    return 0


def _cmp_pair(a: int, ra, b: int, rb) -> str:
    """'' equal, 'reg', 'slot', 'regslot', 'frame', or 'insn'."""
    if a == b and ra == rb:
        return ""
    if ra != rb:
        return "insn"
    if op_key(a) != op_key(b):
        return "insn"
    p = a >> 26
    if p == 37 and (a >> 16) & 0x1F == 1 and (a >> 21) & 0x1F == 1 and (b >> 16) & 0x1F == 1:
        return "frame"
    m = reg_mask(a)
    if p in MEM_D and (a >> 16) & 0x1F == 1 and (b >> 16) & 0x1F == 1 and ra is None:
        imm_diff = (a & 0xFFFF) != (b & 0xFFFF) if p not in PSQ else (a & 0xFFF) != (b & 0xFFF)
        reg_diff = (a >> 21) & 0x1F != (b >> 21) & 0x1F
        rest_equal = (a & ~m & ~0xFFFF) == (b & ~m & ~0xFFFF)
        if not rest_equal:
            return "insn"
        if imm_diff and reg_diff:
            return "regslot"
        if imm_diff:
            return "slot"
        return "reg"
    if (a & ~m) == (b & ~m):
        return "reg"
    return "insn"


def compare(t: Func, o: Func, detail: bool = False) -> Score:
    tw, ow = t.masked, o.masked
    sc = Score(True, n_t=len(tw), n_o=len(ow), frame_t=_frame(t.words), frame_o=_frame(o.words))
    if len(tw) == len(ow):
        pairs = [(i, i) for i in range(len(tw))]
        extra = 0
    else:
        ka = [op_key(w) for w in tw]
        kb = [op_key(w) for w in ow]
        sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
        pairs, extra = [], 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                pairs += list(zip(range(i1, i2), range(j1, j2)))
            elif tag == "replace":
                n = min(i2 - i1, j2 - j1)
                pairs += list(zip(range(i1, i1 + n), range(j1, j1 + n)))
                extra += max(i2 - i1, j2 - j1) - n
            else:
                extra += max(i2 - i1, j2 - j1)
    sc.n_struct = extra
    for i, j in pairs:
        k = _cmp_pair(tw[i], t.relocs.get(i), ow[j], o.relocs.get(j))
        if not k:
            continue
        if k == "insn":
            sc.n_struct += 1
        elif k == "reg":
            sc.n_reg += 1
        elif k == "slot":
            sc.n_slot += 1
        elif k == "regslot":
            sc.n_reg += 1
            sc.n_slot += 1
        if detail:
            sc.detail.append("%4x %08x %08x %s" % (i * 4, tw[i], ow[j], k))
    sc.exact = (len(tw) == len(ow) and sc.n_struct == 0 and sc.n_reg == 0 and sc.n_slot == 0
                and sc.frame_t == sc.frame_o)
    import hashlib
    h = hashlib.md5()
    for w in o.masked:
        h.update(struct.pack(">I", w))
    h.update(repr(sorted(o.relocs.items())).encode("latin-1", "replace"))
    sc.sig = h.hexdigest()[:16]
    return sc


OBJDIFF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "build", "tools", "objdiff-cli")


def fuzzy_match(target_path: str, obj_path: str) -> Dict[str, float]:
    """objdiff's fuzzy match percent of every function in `obj_path` against
    the retail object, with the options `objdiff-cli report generate` uses
    (function relocation diffs off), so the values equal report.json's
    `fuzzy_match_percent` and what `ninja changes_all` compares.
    Empty when objdiff-cli is missing or fails."""
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        r = subprocess.run([OBJDIFF, "diff", "-c", "functionRelocDiffs=none", "-1", target_path,
                            "-2", obj_path, "-o", out], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            return {}
        with open(out) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    finally:
        os.remove(out)
    return {s["name"]: float(s.get("match_percent") or 0.0) for s in d.get("right", {}).get("symbols", [])
            if s.get("kind") == "SYMBOL_FUNCTION"}


class Target:
    """The retail object of one unit, parsed once."""

    def __init__(self, path: str):
        self.path = path
        self.elf = Elf(path)
        self.funcs = {n: extract(self.elf, n) for n in self.elf.functions()}
        self.data = self._data_sections(self.elf)

    @staticmethod
    def _data_sections(elf: Elf) -> Dict[str, bytes]:
        out = {}
        for i, s in enumerate(elf.sections):
            if s["name"] in (".rodata", ".data", ".sdata", ".sdata2", ".ctors", ".dtors") and s["type"] == 1:
                out[s["name"]] = elf.sec_bytes(i)
        return out

    def score(self, obj_path: str, fn: str, detail: bool = False) -> Score:
        try:
            elf = Elf(obj_path)
        except (OSError, ValueError) as ex:
            return Score(False, str(ex))
        sc = self.score_elf(elf, fn, detail)
        if sc.ok:
            sc.fuzzy = 100.0 if sc.exact else fuzzy_match(self.path, obj_path).get(fn)
        return sc

    def score_elf(self, elf: Elf, fn: str, detail: bool = False) -> Score:
        t = self.funcs.get(fn)
        if t is None:
            return Score(False, "no retail symbol " + fn)
        o = extract(elf, fn)
        if o is None:
            return Score(False, "function missing from our object")
        return compare(t, o, detail)

    def profile(self, obj_path: str) -> Dict[str, tuple]:
        """Score key of every retail function, plus data-section equality."""
        elf = Elf(obj_path)
        fz = fuzzy_match(self.path, obj_path)
        out = {}
        for n, t in self.funcs.items():
            o = extract(elf, n)
            if o is None:
                out[n] = (3, 0.0, 0, 0, 0, 0)
                continue
            sc = compare(t, o)
            sc.fuzzy = fz.get(n)
            out[n] = sc.key
        ours = self._data_sections(elf)
        for n, b in self.data.items():
            out["[data]" + n] = (0,) if ours.get(n) == b else (1,)
        return out


def regressions(base: Dict[str, tuple], var: Dict[str, tuple], fn: str) -> List[str]:
    bad = []
    for k, v in base.items():
        if k == fn:
            continue
        nv = var.get(k)
        if nv is None or nv > v:
            bad.append("%s %s->%s" % (k, v, nv))
    return bad
