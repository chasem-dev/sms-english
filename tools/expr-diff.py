#!/usr/bin/env python3
"""Compare retail and our code per function by symbolic effects, not by features.

Run from the repo root (or a worktree) after a full build:

    tools/expr-diff.py --unit Dir/Unit [--func SYMBOL] [-v]  # non-identical functions
    tools/expr-diff.py --tsv semantic.tsv [--cls CLEAN] [--out OUT.tsv] [-v]
    tools/expr-diff.py --exact N              # self-check on N byte-identical functions
    tools/expr-diff.py --a RETAIL.o --b OURS.o [--func SYMBOL]   # e.g. an old build

The --tsv input is tools/semantic-diff.py --tsv output (unit, symbol, ...).
EXPR_DIFF_SIGS=1 prints branch successor signatures in clear, EXPR_DIFF_HASH=N
raises the length (default 3000) above which subexpressions are hashed.
The first run builds an index of the retail binary (build/GMSE01/
expr-diff-ret.pickle, rebuilt when retail objects change).

tools/semantic-diff.py compares feature multisets; it cannot see swapped
operands of non-commutative operations, an inverted branch condition, two
stores whose values are exchanged, swapped call arguments or a value read from
the wrong load.  This tool executes both versions symbolically and compares
what each one does.

Machine model (PowerPC, objdump -M raw,gekko):
  values   register-independent expression trees.  Entry registers are P0..P7
           (r3..r10) and F0..F7 (f1..f8); loads are ld<w>(address).  A global
           is A(name); a compiler-named object (@1234, ...data.0, section+
           offset, and file-static read-only data or pointer tables) is named
           by the content at the address (a string, 'zero', 'jtab', its first
           word), because pooling differs between builds; a load from read-
           only or pooled data is the value read (float constants by numeric
           value, F:x, so 0.001f and the same number as a double are equal).
           Commutative operators sort their operands; integer sums are
           flattened with constants folded; slwi/mulli are products; rlwinm
           is shift+mask; extsh/extsb are sx16/sx8, and masks/extensions that
           cannot change the bits later used are dropped; the int->float
           idiom (0x43300000 pair + lfd) is i2d/u2d; float operations on two
           constants are folded (single ops rounded to single), x*1.0 and
           x+0.0 are x.
  fused    multiply-add is NOT expanded: fmadds(a,b,c) = a*b+c rounded once
           stays distinct from fadds(fmuls(a,b),c), because the result can
           differ in the last bit (only a*b+0.0 is folded to a*b).  frsp of a
           value that is already single is dropped.
  stack    r1-relative accesses are memory cells keyed by offset, so spills,
           reloads and different frame layouts are transparent.  A pointer to
           a stack object that escapes (call argument, store, compare) is
           &loc(contents of its first words, as many as the callee's
           signature says the object has).  After a call the object passed is
           post(call, offset): all of it when its size is known, else its
           first 12 bytes plus words nothing else wrote.
  memory   a load from an address stored earlier reads the stored value until
           a call; joins keep only what all predecessors agree on.
  calls    return R(callee, hash of the call) in r3 and FR(...) in f1; the
           other volatile registers become 'junk'.  Arguments are the
           registers the callee reads: the mangled signature, intersected
           with the callee's own live-in registers computed from the retail
           binary (so the unused `this` of a static member drops out); C
           functions use their live-in set; a virtual call through `this` is
           resolved from the class's retail vtable; other virtual calls read
           what any function at that vtable offset reads, and calls through
           unknown pointers r3.. and f1.. up to the highest register written
           since the previous call (arguments then compared position by
           position, ignoring positions only one build writes).  A narrow
           integer argument (u8, s16...) is compared on its low bits.
           Straight-line leaf functions in retail (TVec3::set, copy
           constructors, getters) are executed in place of the call, so a
           body one build calls and the other expands compares equal.
  control  basic blocks run in reverse post-order; a register dead at a
           block entry is dropped; at a join a value is kept when all forward
           predecessors agree and becomes phi{values} otherwise.  At a loop
           header (target of a DFS back edge) every value that differs around
           a back edge becomes loop<depth>(entry value), iterated to a fixed
           point; loops are named by nesting depth.
  effects  the multiset compared is: every non-stack store (width, address,
           value; 1- and 2-byte stores by their low bits), every call (target,
           arguments), the set of values returned (r3 or f1, depending on
           what the function returns), every conditional branch and every
           switch.  A branch is (kind, a, b, outcomes, successor when taken,
           successor when not): operands are sorted (mirroring <,>), and of a
           condition and its complement the smaller outcome set is kept with
           the successors swapped to match, so an inverted branch with
           swapped blocks is the same effect while a wrong or inverted
           condition is not.  A successor is the set of effects of the first
           block reached that has any (or the next branch's predicate),
           evaluated along that edge: a test of constants (a materialised
           bool tested again) is followed, and an equality the edge implies
           is substituted.  Compare kinds: s/u (signed/unsigned ordering),
           i (integer equality), f (float; fcmpo and fcmpu are the same
           test).  A branch on constants is not an effect.

Return kind: a function returns in r3 (or f1) when a caller anywhere in the
retail binary reads r3 (f1) after calling it, or, for functions nobody calls
directly, when retail writes r3 (else f1) after the last call on every path to
every return.

An identical function always compares equal (--exact checks it).  A mismatch
is a lead for a human: some remain harmless (two equivalent spellings the
model does not normalise), listed with a verdict in docs/audit/expr-diff.md.
"""
import argparse, collections, hashlib, importlib.util, os, re, struct, subprocess, sys

ROOT = os.getcwd()
B = os.path.join(ROOT, 'build')
OD = os.path.join(B, 'binutils', 'powerpc-eabi-objdump')
HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

SEMDIFF = _load('semantic_diff', os.path.join(HERE, 'semantic-diff.py'))   # ELF reader
read_elf = SEMDIFF.read_elf


# ================================================================ mangled signatures
BASE = {'i': 4, 'l': 4, 's': 2, 'c': 1, 'b': 1, 'w': 2, 'x': 8, 'f': 4, 'd': 8, 'v': 0, 'e': 0, 'r': 8}
CLASS_SIZE = {'TVec3<f>': 12, 'Vec': 12, 'TVec2<f>': 8, 'S16Vec': 6, 'Quaternion': 16, 'TRot3<f>': 12,
              'TVec3<s>': 6, 'TVec2<s>': 4, 'GXColor': 4, 'TColor': 4}


def ptype(m, i):
    """Parse one mangled type at m[i]: (next, 'i'|'f'|'v'|'e', own size, pointee size)."""
    while i < len(m) and m[i] in 'CV':
        i += 1
    c = m[i]
    if c in 'PR':
        j, cls, sz, _ = ptype(m, i + 1)
        if m[i + 1:i + 5] == 'A4_f':
            sz = 48                                  # Mtx decays to f32 (*)[4]
        return j, 'i', 4, sz
    if c in 'US':
        j, cls, sz, _ = ptype(m, i + 1)
        return j, cls, sz, None
    if c in BASE:
        return i + 1, 'f' if c in 'fdr' else ('v' if c == 'v' else ('e' if c == 'e' else 'i')), BASE[c], None
    if c == 'Q':
        n = int(m[i + 1])
        j = i + 2
        last = ''
        for _ in range(n):
            k = j
            while m[k].isdigit():
                k += 1
            last = m[k:k + int(m[j:k])]
            j = k + int(m[j:k])
        return j, 'i', CLASS_SIZE.get(last), None
    if c.isdigit():
        k = i
        while m[k].isdigit():
            k += 1
        name = m[k:k + int(m[i:k])]
        return k + int(m[i:k]), 'i', CLASS_SIZE.get(name), None
    if c == 'A':
        k = i + 1
        while m[k].isdigit():
            k += 1
        n = int(m[i + 1:k])
        j, cls, sz, _ = ptype(m, k + 1)
        return j, 'i', n * sz if sz else None, None
    if c == 'F':
        j = i + 1
        while m[j] != '_':
            j, _, _, _ = ptype(m, j)
        j, _, _, _ = ptype(m, j + 1)
        return j, 'i', 4, None
    if c == 'M':
        j, _, _, _ = ptype(m, i + 1)
        return ptype(m, j)
    raise ValueError(m[i:])


def argspec(name):
    """[(register, bytes of the object it points to, or -bits of a narrow integer)] for a
    call, or None when the name carries no signature (C functions)."""
    name = re.sub(r'\+0x[0-9a-f]+$', '', name)
    name = re.sub(r'^@\d+@', '', name)
    m = re.search(r'.__(?=[0-9QFC])', name)
    if not m:
        return None
    rest = name[m.start() + 3:]
    try:
        j = 0
        this = None
        member = rest[:1].isdigit() or rest[:1] == 'Q'
        if member:
            j, _, this, _ = ptype(rest, 0)
            while j < len(rest) and rest[j] == 'C':
                j += 1
        if j >= len(rest) or rest[j] != 'F':
            return None
        j += 1
        regs = []
        ni, nf = 0, 0
        if member and not name.startswith('__ct') or name.startswith('__ct'):
            regs.append(('r3', this or 4))
            ni = 1
        while j < len(rest) and rest[j] != '_':
            k = j
            while rest[k] in 'CV':
                k += 1
            narrow = None
            if rest[k] in 'US' and rest[k + 1] in 'scb' or rest[k] in 'scb':
                narrow = 16 if rest[k:k + 2] in ('Us', 'Ss') or rest[k] == 's' else 8
            j, cls, sz, pointee = ptype(rest, j)
            if cls == 'e':
                break
            if cls == 'f':
                if nf < 8:
                    regs.append(('f%d' % (nf + 1), None))
                nf += 1
            elif cls == 'i':
                if ni < 8:
                    regs.append(('r%d' % (ni + 3), -narrow if narrow else (pointee or 4)))
                ni += 1
                if sz == 8 and ni < 8:              # long long takes a register pair
                    regs.append(('r%d' % (ni + 3), None))
                    ni += 1
        if not member and not name.startswith('__ct'):
            pass
        return regs
    except (ValueError, IndexError):
        return None


# ================================================================ expressions
HASH_AT = int(os.environ.get('EXPR_DIFF_HASH', '3000'))   # longer expressions are hashed


class E:
    """An expression node: op, args, and its canonical string s (identity)."""
    __slots__ = ('op', 'a', 's', 'v')

    def __init__(self, op, a=(), s=None, v=None):
        self.op, self.a, self.v = op, a, v
        if s is None:
            s = op + '(' + ','.join(x.s for x in a) + ')' if a else op
            if len(s) > HASH_AT:
                s = 'H' + hashlib.sha1(s.encode()).hexdigest()[:16]
                self.op, self.a = 'H', ()
        self.s = s

    def __eq__(self, o):
        return isinstance(o, E) and o.s == self.s

    def __hash__(self):
        return hash(self.s)

    def __repr__(self):
        return self.s


def A(op, *args):
    return E(op, tuple(args))

def atom(name):
    return E(name, (), name)

def C(v):
    v &= 0xffffffff
    if v & 0x80000000:
        v -= 1 << 32
    return E('c', (), 'c%d' % v, v)

def isc(e):
    return e.op == 'c'

JUNK = atom('junk')
UNK = atom('?')
SP = atom('SP')
ZERO = C(0)
COMM = {'mullw', 'and', 'or', 'xor', 'nand', 'nor', 'eqv', 'fadds', 'fadd', 'fmuls', 'fmul',
        'mulhw', 'mulhwu'}
SINGLE = {'fadds', 'fsubs', 'fmuls', 'fdivs', 'fmadds', 'fmsubs', 'fnmadds', 'fnmsubs', 'fres',
          'frsp', 'Kf', 'psq'}


def iadd(*terms):
    k = 0
    ts = []
    def add(t):
        nonlocal k
        if isc(t):
            k += t.v
        elif t.op == 'add':
            for u in t.a:
                add(u)
        else:
            ts.append(t)
    for t in terms:
        add(t)
    # x + neg(x) cancels
    out = []
    negs = collections.Counter(t.a[0].s for t in ts if t.op == 'neg')
    pos = collections.Counter(t.s for t in ts if t.op != 'neg')
    for t in ts:
        key = t.a[0].s if t.op == 'neg' else t.s
        if t.op == 'neg':
            if pos[key] > 0 and negs[key] > 0:
                continue
        elif negs[key] > 0 and pos[key] > 0:
            pos[key] -= 1
            negs[key] -= 1
            continue
        out.append(t)
    k &= 0xffffffff
    if k:
        ads = [t for t in out if t.op == 'AD']
        if len(ads) == 1:
            obj, sec, off = ads[0].v
            out[out.index(ads[0])] = obj.at(sec, off + (k - (1 << 32) if k & 0x80000000 else k))
            k = 0
    if k:
        out.append(C(k))
    if not out:
        return C(0)
    if len(out) == 1:
        return out[0]
    return E('add', tuple(sorted(out, key=lambda x: x.s)))


def ineg(x):
    if isc(x):
        return C(-x.v)
    if x.op == 'neg':
        return x.a[0]
    if x.op == 'add':
        return iadd(*[ineg(t) for t in x.a])
    if x.op == 'mul' and isc(x.a[1]):
        return imul(x.a[0], C(-x.a[1].v))
    return A('neg', x)


def imul(x, c):
    """x * constant."""
    if c.v == 1:
        return x
    if c.v == 0:
        return C(0)
    if isc(x):
        return C(x.v * c.v)
    if x.op == 'mul' and isc(x.a[1]):
        return imul(x.a[0], C(x.a[1].v * c.v))
    return E('mul', (x, c))


def mask(mb, me):
    m = 0
    i = mb
    while True:
        m |= 1 << (31 - i)
        if i == me:
            break
        i = (i + 1) % 32
    return m


ZX = {'ld1': 0xff, 'ld2': 0xffff}

def iand(x, m):
    """x & constant mask m (unsigned 32)."""
    m &= 0xffffffff
    if m == 0xffffffff:
        return x
    if m == 0:
        return C(0)
    if isc(x):
        return C(x.v & m)
    if x.op in ZX and (m & ZX[x.op]) == ZX[x.op]:
        return x
    if x.op == 'and' and len(x.a) == 2 and isc(x.a[1]):
        return iand(x.a[0], (x.a[1].v & 0xffffffff) & m)
    if x.op == 'srw' and isc(x.a[1]) and (m | (0xffffffff >> x.a[1].v)) == m:
        return x
    if x.op in ('sx16', 'sx8'):
        w = 0xffff if x.op == 'sx16' else 0xff
        if (m & ~w) & 0xffffffff == 0:
            return iand(x.a[0], m)
    if x.op == 'phi':
        return phi([iand(t, m) for t in x.a])     # a mask applies to each alternative
    if m & (m + 1) == 0:                          # a low mask: only low bits of x matter
        k = m.bit_length()
        y = modbits(x, k)
        if y.s != x.s:
            return iand(y, m)
    return E('and', (x, C(m)))


def sx(x, bits):
    op = 'sx16' if bits == 16 else 'sx8'
    if isc(x):
        v = x.v & ((1 << bits) - 1)
        if v >> (bits - 1):
            v -= 1 << bits
        return C(v)
    if x.op == 'sx8':
        return x
    if x.op == 'sx16' and bits == 16:
        return x
    if x.op == 'and' and len(x.a) == 2 and isc(x.a[1]) and (x.a[1].v & ((1 << bits) - 1)) == (1 << bits) - 1:
        return sx(x.a[0], bits)
    if x.op in ZX and bits == 16 and x.op == 'ld1':
        return x
    if x.op == 'phi':
        return phi([sx(t, bits) for t in x.a])
    return A(op, modbits(x, bits))


def modbits(x, bits, depth=0):
    """x where only its low `bits` bits are used: inside sums, negations and constant
    products, extensions and masks of at least that width change nothing."""
    if depth > 8:
        return x
    lm = (1 << bits) - 1
    if x.op in ('sx16', 'sx8') and (16 if x.op == 'sx16' else 8) >= bits:
        return modbits(x.a[0], bits, depth + 1)
    if x.op == 'and' and len(x.a) == 2 and isc(x.a[1]) and (x.a[1].v & lm) == lm:
        return modbits(x.a[0], bits, depth + 1)
    if x.op == 'add':
        return iadd(*[modbits(t, bits, depth + 1) for t in x.a])
    if x.op == 'neg':
        return ineg(modbits(x.a[0], bits, depth + 1))
    if x.op == 'mul' and len(x.a) == 2 and isc(x.a[1]):
        return imul(modbits(x.a[0], bits, depth + 1), x.a[1])
    return x


def fconst(v, n):
    """A float constant loaded with lfs/lfd, named by its value: 0.001f read as a float
    and the same number stored as a double are one constant."""
    m = re.fullmatch(r'K([48]):([0-9a-f]+)', v.s)
    if not m or int(m.group(1)) != n:
        return v
    b = bytes.fromhex(m.group(2))
    x = struct.unpack('>f', b)[0] if n == 4 else struct.unpack('>d', b)[0]
    single = n == 4 or (x == x and abs(x) < 3.4e38 and struct.unpack('>f', struct.pack('>f', x))[0] == x)
    return E('Kf' if single else 'Kd', (), 'F:%r' % x)


def constbytes(v, n):
    """Big-endian bytes of a constant value held in an n-byte cell, else None."""
    if isc(v) and n == 4:
        return (v.v & 0xffffffff).to_bytes(4, 'big')
    m = re.fullmatch(r'K(\d):([0-9a-f]+)', v.s)
    if m and int(m.group(1)) == n:
        return bytes.fromhex(m.group(2))
    return None


def vslot(tgt):
    """The vtable offset of a virtual call target *(*(obj) + off), else None."""
    if tgt.op != 'ld4' or not tgt.a:
        return None
    a = tgt.a[0]
    if a.op == 'add' and len(a.a) == 2 and isc(a.a[0]) and a.a[1].op == 'ld4':
        return a.a[0].v
    if a.op == 'ld4':
        return 0
    return None


def refine(st, p, swap, taken):
    """The state on one edge of a branch: where the edge implies an integer operand equals
    a constant, registers holding that operand hold the constant (so a materialised
    bool tested again folds)."""
    kind, a, b, rep = p
    if kind not in ('i', 's', 'u') or len(p) != 4:
        return st
    edge = set(rep) if taken != swap else set('<=>') - set(rep)
    if edge != {'='}:
        return st
    if isc(b) and not isc(a):
        var, val = a, b
    elif isc(a) and not isc(b):
        var, val = b, a
    else:
        return st
    st = st.copy()
    for r, v in list(st.R.items()):
        if v.s == var.s:
            st.R[r] = val
    for k, v in list(st.mem.items()):
        if v.s == var.s:
            st.mem[k] = val                          # the same value kept in memory
    for k, v in list(st.stk.items()):
        if v.s == var.s:
            st.stk[k] = val
    if var.op in ('ld1', 'ld2', 'ld4') and var.a:
        st.mem[(var.a[0].s, int(var.op[2:]))] = val   # a reload of the tested word sees it too
    for k, c in list(st.cr.items()):
        if c.a.s == var.s or c.b.s == var.s:
            st.cr[k] = CR(c.k, val if c.a.s == var.s else c.a, val if c.b.s == var.s else c.b, c.bits)
    return st


def constpred(p, swap):
    """Whether a canonical branch predicate on two constants is taken (None: not constant)."""
    kind, a, b, rep = p
    if kind in ('s', 'u', 'i') and isc(a) and isc(b):
        x, y = a.v, b.v
        if kind == 'u':
            x, y = x & 0xffffffff, y & 0xffffffff
        out = '<' if x < y else ('>' if x > y else '=')
    elif kind in ('s', 'u', 'i') and a.s == b.s:
        out = '='
    else:
        return None
    return (out in rep) != swap


def lowbits(v, bits):
    """v where only its low `bits` bits matter: drop masks and sign extensions that keep them."""
    v = modbits(v, bits)
    lm = (1 << bits) - 1
    while True:
        if v.op == 'and' and len(v.a) == 2 and isc(v.a[1]) and (v.a[1].v & lm) == lm:
            v = v.a[0]
        elif v.op in ('sx16', 'sx8') and (16 if v.op == 'sx16' else 8) >= bits:
            v = v.a[0]
        elif isc(v):
            return C(v.v & lm)
        elif v.op == 'phi':
            return phi([lowbits(x, bits) for x in v.a])
        else:
            return v


def rlwinm(x, sh, mb, me):
    m = mask(mb, me)
    if sh == 0:
        return iand(x, m)
    low = (1 << sh) - 1
    if m & low == 0:
        return iand(imul(x, C(1 << sh)), m | low)            # (x << sh) & m
    if m & ~low & 0xffffffff == 0:
        return iand(srw(x, 32 - sh), m)                      # (x >> (32-sh)) & m
    return A('rotl&', x, C(sh), C(m))


def srw(x, n):
    if n == 0:
        return x
    if isc(x):
        return C((x.v & 0xffffffff) >> n)
    return A('srw', x, C(n))


def fneg(x):
    if x.op == 'fneg':
        return x.a[0]
    return A('fneg', x)


def frsp(x):
    if x.op in SINGLE or x.op == 'ld4' or x.op == 'post':
        return x
    if x.op in ('fneg', 'fabs', 'fnabs') and frsp(x.a[0]) is x.a[0]:
        return x
    return A('frsp', x)


def fval(e):
    return float(e.s[2:]) if e.op in ('Kf', 'Kd') else None


def op2(op, a, b):
    if op in ('fadds', 'fsubs', 'fmuls', 'fdivs', 'fadd', 'fsub', 'fmul', 'fdiv'):
        x, y = fval(a), fval(b)
        if x is not None and y is not None and not (op.startswith('fdiv') and y == 0):
            r = {'fa': x + y, 'fs': x - y, 'fm': x * y, 'fd': x / y if y else 0.0}[op[:2]]
            if op.endswith('s'):
                r = struct.unpack('>f', struct.pack('>f', r))[0]
            return fconst_value(r)
        if op.startswith('fmul') and (x == 1.0 or y == 1.0):
            return b if x == 1.0 else a             # exact
        if op.startswith('fadd') and (x == 0.0 or y == 0.0):
            return b if x == 0.0 else a             # exact but for the sign of a zero sum
    if op in COMM:
        a, b = sorted((a, b), key=lambda x: x.s)
    return A(op, a, b)


def fconst_value(x):
    single = x == x and abs(x) < 3.4e38 and struct.unpack('>f', struct.pack('>f', x))[0] == x
    return E('Kf' if single else 'Kd', (), 'F:%r' % x)


def fused(op, a, c, b):
    a, c = sorted((a, c), key=lambda x: x.s)
    if op in ('fmadds', 'fmadd') and fval(b) == 0.0:
        return op2('fmuls' if op == 'fmadds' else 'fmul', a, c)   # a*c+0 rounds once, as a*c
    return A(op, a, c, b)


def has_sp(e, depth=0):
    if e is SP or e.s == 'SP':
        return True
    if depth > 6 or e.op == 'H':
        return False
    return any(has_sp(x, depth + 1) for x in e.a)


def sp_off(e):
    """Offset of SP+c, else None."""
    if e.s == 'SP':
        return 0
    if e.op == 'add' and len(e.a) == 2 and e.a[0].s == 'SP' and isc(e.a[1]):
        return e.a[1].v
    if e.op == 'add' and len(e.a) == 2 and e.a[1].s == 'SP' and isc(e.a[0]):
        return e.a[0].v
    return None


# ================================================================ objects
FUNC_RE = re.compile(r'^([0-9a-f]+) <(.*)>:$')
INS_RE = re.compile(r'^\s*([0-9a-f]+):\t(\S+)\s*(.*)$')
REL_RE = re.compile(r'^\s*[0-9a-f]+: (R_PPC_\S+)\s+(\S+)$')
DATA_SECS = ('.rodata', '.sdata2', '.data', '.sdata', '.bss', '.sbss', '.ctors', '.dtors')
READONLY = ('.rodata', '.sdata2')


def rela(path):
    """{section name: {offset: target string}} for every relocation section."""
    with open(path, 'rb') as f:
        d = f.read()
    shoff, = struct.unpack_from('>I', d, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from('>HHH', d, 0x2e)
    sh = [struct.unpack_from('>IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    stro = sh[shstrndx][4]
    def nm(o, base):
        return d[base + o:d.index(b'\0', base + o)].decode('latin1')
    names = [nm(s[0], stro) for s in sh]
    out = {}
    for s in sh:
        if s[1] != 4:
            continue
        symtab = sh[s[6]]
        strtab = sh[symtab[6]]
        tgt = names[s[7]]
        m = out.setdefault(tgt, {})
        for j in range(s[5] // 12):
            off, info, add = struct.unpack_from('>IIi', d, s[4] + j * 12)
            si = info >> 8
            snm, val, size, sinfo, other, shndx = struct.unpack_from('>IIIBBH', d, symtab[4] + si * 16)
            n = nm(snm, strtab[4]) if snm else (names[shndx] if shndx < len(names) else '?')
            m[off] = (n, add, info & 0xff)
    return out


def normname(n):
    """Drop compiler numbering from a symbol: static-local suffixes (init$1143) and the
    numbers of local classes (26TSetup1$2172ShadowUtil_cpp), whose mangled length prefix
    changes with them."""
    out, i = [], 0
    for m in re.finditer(r'(?<!\d)\d+', n):
        k = int(m.group(0))
        tok = n[m.end():m.end() + k]
        if len(tok) == k and re.search(r'\$\d', tok) and re.match(r'[A-Za-z_]', tok):
            out.append(n[i:m.start()])
            out.append(re.sub(r'\$\d+', '$', tok))
            i = m.end() + k
    out.append(n[i:])
    return re.sub(r'\$\d+', '$', ''.join(out))


def anon_name(n):
    return (n.startswith('@') and not n.startswith('@LOCAL@') or n.startswith('...') or n.startswith('.')
            or n.startswith('lbl_'))


class Obj:
    def __init__(self, path):
        self.path = path
        self.secs, self.syms = read_elf(path)
        self.local = self.syms.get('#local', set())
        self.rel = rela(path)
        self.funcs = self._disasm()
        # symbols per section, for section+offset targets
        self.bysec = collections.defaultdict(list)
        for n, t in self.syms.items():
            if n.startswith('#'):
                continue
            shndx, val, size = t
            if 0 < shndx < len(self.secs):
                self.bysec[self.secs[shndx]['name']].append((val, size, n))
        for v in self.bysec.values():
            v.sort()
        self.keyloc = {}                    # key -> (section, offset) for data reads
        self._keys = {}
        self._at = {}

    def _disasm(self):
        out = subprocess.run([OD, '-dr', '--no-show-raw-insn', '-M', 'raw,gekko', self.path],
                             capture_output=True, text=True, errors='replace').stdout
        res = {}
        cur = None
        for l in out.split('\n'):
            m = FUNC_RE.match(l)
            if m:
                cur = m.group(2)
                res[cur] = []
                continue
            if cur is None:
                continue
            m = REL_RE.match(l)
            if m and res[cur]:
                res[cur][-1][3] = (m.group(1), m.group(2))
                continue
            m = INS_RE.match(l)
            if m:
                ops = re.sub(r' <.*>$', '', m.group(3)).strip()
                res[cur].append([int(m.group(1), 16), m.group(2), ops, None])
        return res

    # ------------------------------------------------------------ symbols
    def sec(self, name):
        return next((s for s in self.secs if s['name'] == name), None)

    def resolve(self, target):
        """(section, offset, bytes left in the object, symbol name, offset inside it) of a
        relocation target, None when undefined.  A reference through a section symbol or
        an anonymous one ('...bss.0', '@123') is re-attributed to the named object that
        covers the address, since retail's split objects often name what ours reaches
        through the section."""
        m = re.match(r'^(.*?)(?:([+-])0x([0-9a-f]+))?$', target)
        name, sign, add = m.group(1), m.group(2), m.group(3)
        addend = int(add, 16) * (-1 if sign == '-' else 1) if add else 0
        t = self.syms.get(name)
        if t is None and name in DATA_SECS + ('.text',):
            sec, off = name, addend
        elif t is None:
            return None
        else:
            shndx, val, size = t
            if shndx == 0 or shndx >= len(self.secs):
                return None
            sec, off = self.secs[shndx]['name'], val + addend
            if not anon_name(name):
                return sec, off, size - addend if size else 0, name, addend
        best = None
        for val, size, n in self.bysec.get(sec, ()):
            if val <= off < val + max(size, 1) and (best is None or anon_name(best[2]) and not anon_name(n)):
                best = (val, size, n)
        if best and (not anon_name(best[2]) or t is None):
            return sec, off, best[1] - (off - best[0]), best[2], off - best[0]
        if t is not None:
            return sec, off, t[2] - addend if t[2] else 0, name, addend
        return sec, off, 0, name, 0

    def addr(self, target):
        """The address a relocation names: A(global) + addend, or, for a compiler-named
        object, AD: a location named by the content found there."""
        m = re.match(r'^(.*?)(?:([+-])0x([0-9a-f]+))?$', target)
        name = m.group(1)
        r = self.resolve(target)
        if r is not None:
            sec, off, rest, sname, inner = r
            anon = all(anon_name(x) for x in (name, sname))
            # a file-static table in one build is often an anonymous pool object in the
            # other: initialised local data is named by its content too
            if not anon and sname in self.local and (
                    sec in READONLY or sec in ('.data', '.sdata') and off in self.rel.get(sec, {})
                    and self.syms[sname][2] >= 8):
                anon = self.content_key(sec, off) != 'zero'     # read-only data or a pointer table
            if anon and sec in DATA_SECS and sec not in ('.bss', '.sbss'):
                return self.at(sec, off)
        return A('A', atom(self.key(target)))

    def at(self, sec, off):
        k = (sec, off)
        e = self._at.get(k)
        if e is None:
            key = self.content_key(sec, off)
            self.keyloc.setdefault(key, (sec, off, True))
            e = E('AD', (), 'A(%s)' % key, (self, sec, off))
            self._at[k] = e
        return e

    def content_key(self, sec, off, depth=0):
        """Name a location in a data section by what is stored there: a string, 'zero', a
        jump table, or its first word (with the content keys of any pointers in it).
        Symbol sizes are ignored: pooled and split objects bound the same data differently."""
        s = self.sec(sec)
        data = s['data'] if s else b''
        rels = self.rel.get(sec, {})
        win = [(o - off, rels[o]) for o in range(off, min(off + 4, len(data))) if o in rels]
        if win and all(t[1][0] == '.text' or (self.syms.get(t[1][0], (0,))[0] and
                       self.secs[self.syms[t[1][0]][0]]['name'] == '.text') for t in win):
            return 'jtab'
        z = data.find(b'\0', off)
        if not win and z > off and all(32 <= c < 127 or c in (9, 10) or c >= 0x80 for c in data[off:z]):
            return 'str:' + repr(data[off:z].decode('latin1'))[:80]
        if not win and not any(data[off:off + 4]):
            return 'zero'
        parts = []
        for o, (n, add, typ) in win:
            if depth < 1 and anon_name(n):
                r = self.resolve(n + ('+0x%x' % add if add else ''))
                parts.append('%d=%s' % (o, self.content_key(r[0], r[1], depth + 1) if r else n))
            else:
                parts.append('%d=%s' % (o, n if not anon_name(n) else 'anon'))
        return 'D:' + data[off:off + 4].hex() + (('|' + ','.join(parts)) if parts else '')

    def key(self, target):
        """A build-independent name for a relocation target (see the module doc)."""
        k = self._keys.get(target)
        if k is None:
            k = self._key(target)
            self._keys[target] = k
        return k

    def _key(self, target):
        m = re.match(r'^(.*?)(?:([+-])0x([0-9a-f]+))?$', target)
        name, sign, add = m.group(1), m.group(2), m.group(3)
        addend = int(add, 16) * (-1 if sign == '-' else 1) if add else 0
        r = self.resolve(target)
        anon = (name.startswith('@') or name.startswith('...') or name.startswith('.')
                or name.startswith('lbl_'))
        if r is None:
            return normname(name) + ('+%d' % addend if addend else '')
        sec, off, rest, sname, inner = r
        anon = anon and (sname.startswith('@') or sname.startswith('...') or sname.startswith('.')
                         or sname.startswith('lbl_'))
        if sec == '.text':
            return normname(sname) + ('+%d' % inner if inner else '')
        if not anon:
            k = normname(sname) + ('+%d' % inner if inner else '')
            if sname in self.local and sec in DATA_SECS:
                self.keyloc.setdefault(k, (sec, off, sname in self.local))
            else:
                self.keyloc.setdefault(k, (sec, off, False))
            return k
        s = self.sec(sec)
        data = s['data'] if s else b''
        if sec in ('.bss', '.sbss') or not data:
            return 'bss:anon+%d' % inner
        rels = self.rel.get(sec, {})
        n = rest if rest > 0 else 16
        n = min(n, 64)
        win = [(o - off, rels[o]) for o in range(off, off + n, 1) if o in rels]
        if win and all(t[1][0] == '.text' or (self.syms.get(t[1][0], (0,))[0] and
                       self.secs[self.syms[t[1][0]][0]]['name'] == '.text') for t in win):
            k = 'jtab'
        else:
            body = data[off:off + n]
            end = data.find(b'\0', off)
            if not win and not any(data[off:off + max(4, min(n, 16))]):
                k = 'zero'
            elif not win and end > off and all(32 <= c < 127 or c in (9, 10) or c >= 0x80
                                               for c in data[off:end]):
                k = 'str:' + repr(data[off:end].decode('latin1'))[:80]
            else:
                rs = ','.join('%d=%s' % (o, re.sub(r'\d+', 'N', t[0]) if t[0].startswith('@') else t[0])
                              for o, t in win)
                k = 'D:' + body[:16].hex() + (('|' + rs) if rs else '')
        self.keyloc.setdefault(k, (sec, off, True))
        return k

    def readat(self, sec, o, n):
        if sec not in READONLY and sec not in ('.data', '.sdata'):
            return None
        s = self.sec(sec)
        if s is None or o < 0 or o + n > len(s['data']):
            return None
        return s['data'][o:o + n], self.rel.get(sec, {}).get(o)

    def read(self, key, extra, n):
        """(bytes, reloc target) of a constant read at key+extra, else None."""
        loc = self.keyloc.get(key)
        if loc is None:
            return None
        sec, off, const = loc
        if not (sec in READONLY or const and sec in ('.data', '.sdata')):
            return None
        s = self.sec(sec)
        o = off + extra
        if s is None or o < 0 or o + n > len(s['data']):
            return None
        r = self.rel.get(sec, {}).get(o)
        return s['data'][o:o + n], r


# ================================================================ return kinds
class RetIndex:
    """From every retail function: which callees' results are read (r3 / f1) after a
    direct call, and which argument registers each function reads before writing them."""
    def __init__(self):
        self.r3 = set()
        self.f1 = set()
        self.called = set()
        self.livein = {}
        self.bodies = {}
        self.vt = {}
        self.weak = set()
        self.leaf = {}
        self.ambiguous = set()

    def scan_vtables(self, path):
        secs, syms = read_elf(path)
        self.weak |= syms.get('#weak', set())
        rels = rela(path)
        for n, t in syms.items():
            if not n.startswith('__vt__'):
                continue
            shndx, val, size = t
            if not (0 < shndx < len(secs)):
                continue
            rs = rels.get(secs[shndx]['name'], {})
            slots = {}
            for o in range(val, val + size, 4):
                if o in rs:
                    slots[o - val] = rs[o][0]
            self.vt[n] = slots

    def compute_leaves(self):
        """Straight-line functions that touch no stack, no relocated data and call
        nothing (TVec3::set, copy constructors, getters): their retail body runs in place
        of the call, so a body one build calls and the other expands compares equal.
        (Each such function is still compared on its own.)"""
        for n in self.bodies:
            ins = self.bodies.get(n)
            if not ins or len(ins) > 40:
                continue
            ok = ins[-1][1] == 'bclr' and ins[-1][2].startswith('20,')
            for ad, mn, ops, rel in ins[:-1]:
                if rel or mn.startswith('b') or mn in ('mfspr', 'mtspr', 'stwu', 'stmw', 'lmw') \
                        or 'r1)' in ops or re.search(r'\br1\b', ops):
                    ok = False
                    break
            if ok:
                self.leaf[n] = ins

    def compute_slots(self):
        """For each vtable offset, the argument registers any function found at that
        offset in any vtable reads: an upper bound on what a virtual call passes."""
        self.slotregs = {}
        for vt in self.vt.values():
            for off, fn in vt.items():
                li = self.livein.get(fn)
                spec = argspec(fn)
                if li is None and spec is None:
                    regs = None
                elif li is None:
                    regs = {r for r, _ in spec}
                else:
                    regs = set(li) if spec is None else {r for r, _ in spec} & set(li)
                    if len(li) >= 12:
                        regs = None
                cur = self.slotregs.get(off, set())
                if regs is None or cur is None:
                    self.slotregs[off] = None
                else:
                    self.slotregs[off] = cur | regs

    def compute_livein(self):
        busy = set()
        def get(n):
            if n in self.livein:
                return self.livein[n]
            if n in busy or self.bodies.get(n) is None:
                return None
            busy.add(n)
            self.livein[n] = livein(self.bodies[n], get)
            busy.discard(n)
            return self.livein[n]
        for n in list(self.bodies):
            if self.bodies[n] is not None:
                get(n)

    def scan(self, ins, name=None):
        if name is not None and ins:
            if name in self.bodies and [x[1:3] for x in self.bodies[name] or []] != [x[1:3] for x in ins]:
                self.bodies[name] = None             # two different bodies share a (local) name
                self.ambiguous.add(name)
            elif name not in self.ambiguous:
                self.bodies[name] = ins
        for i, (ad, mn, ops, rel) in enumerate(ins):
            if mn not in ('bl', 'b') or not rel or rel[0] != 'R_PPC_REL24':
                continue
            tgt = re.sub(r'\+0x[0-9a-f]+$', '', rel[1])
            self.called.add(tgt)
            if mn == 'b':
                continue
            for reg, bag in (('r3', self.r3), ('f1', self.f1)):
                if tgt in bag:
                    continue
                for ad2, mn2, ops2, rel2 in ins[i + 1:i + 40]:
                    o = [x.strip() for x in ops2.split(',')] if ops2 else []
                    reads, writes = regs_rw(mn2, o)
                    if reg in reads:
                        bag.add(tgt)
                        break
                    if reg in writes or mn2.startswith('b'):
                        break


def livein(ins, callee_livein=lambda n: None):
    """Argument registers a function reads before writing them on some path: backward
    liveness over its basic blocks.  A call reads the arguments its signature names (or,
    for a C function, what that function itself reads) and writes every volatile
    register; a return reads nothing.  A bcctr may reach every block nothing else does."""
    if not ins:
        return ()
    addrs = [i[0] for i in ins]
    idx = {a: k for k, a in enumerate(addrs)}
    leaders = {addrs[0]}
    for k, (ad, mn, ops, rel) in enumerate(ins):
        if mn in ('b', 'bc', 'bclr', 'bcctr') and not (rel and rel[0] == 'R_PPC_REL24' and mn == 'bl'):
            if k + 1 < len(ins):
                leaders.add(addrs[k + 1])
            if mn in ('b', 'bc') and not (rel and rel[0] == 'R_PPC_REL24'):
                try:
                    t = int(ops.split(',')[-1], 16)
                    if t in idx:
                        leaders.add(t)
                except ValueError:
                    pass
    L = sorted(leaders)
    blocks = {}
    for i, l in enumerate(L):
        blocks[l] = (idx[l], idx[L[i + 1]] if i + 1 < len(L) else len(ins))
    succ = {}
    targeted = set()
    for l, (k0, k1) in blocks.items():
        ad, mn, ops, rel = ins[k1 - 1]
        o = ops.split(',') if ops else []
        nxt = addrs[k1] if k1 < len(ins) else None
        ss = []
        tail = rel and rel[0] == 'R_PPC_REL24'
        if mn in ('b', 'bc') and not tail:
            try:
                t = int(o[-1], 16)
                if t in idx:
                    ss.append(t)
            except ValueError:
                pass
            if mn == 'bc' and (int(o[0]) & 0x14) != 0x14 and nxt is not None:
                ss.append(nxt)
        elif mn in ('bclr', 'bcctr'):
            if (int(o[0]) & 0x14) != 0x14 and nxt is not None:
                ss.append(nxt)
            if mn == 'bcctr':
                ss.append('jt')
        elif not (mn == 'b' and tail) and nxt is not None:
            ss.append(nxt)
        succ[l] = ss
        targeted.update(x for x in ss if x != 'jt')
    orphans = [l for l in L if l not in targeted and l != L[0]]
    ud = {}
    for l, (k0, k1) in blocks.items():
        use, dfn = set(), set()
        for ad, mn, ops, rel in ins[k0:k1]:
            o = [x.strip() for x in ops.split(',')] if ops else []
            if mn in ('bl', 'bclrl', 'bcctrl') or (rel and rel[0] == 'R_PPC_REL24'):
                rd = set()
                if rel and rel[0] == 'R_PPC_REL24':
                    n = re.sub(r'\+0x[0-9a-f]+$', '', rel[1])
                    spec = argspec(n)
                    li = callee_livein(n)
                    if li is not None and len(li) >= 12:
                        li = ('r3',)                # varargs: the caller sets what it passes
                    if spec is not None:
                        rd = {r for r, _ in spec}
                        if li is not None:
                            rd &= set(li)
                    elif li is not None:
                        rd = set(li)
                    else:
                        rd = {'r3'}
                else:
                    rd = {'r3'}
                use |= rd - dfn
                dfn |= set(VOLATILE)
                continue
            if mn.startswith('b'):
                continue
            rd, wr = regs_rw(mn, o)
            if mn in ('rlwimi', 'rlwimi.'):
                rd |= wr
            use |= (rd & ARGREGS) - dfn
            dfn |= wr
        ud[l] = (use, dfn)
    live = {l: set() for l in L}
    for _ in range(len(L) + 2):
        changed = False
        for l in reversed(L):
            out = set()
            for t in succ[l]:
                if t == 'jt':
                    for x in orphans:
                        out |= live[x]
                else:
                    out |= live[t]
            use, dfn = ud[l]
            new = use | (out - dfn)
            if new != live[l]:
                live[l] = new
                changed = True
        if not changed:
            break
    return tuple(sorted(live[L[0]], key=lambda r: (r[0], int(r[1:]))))


def regs_rw(mn, o):
    """(read, written) register names of one instruction (approximate, for the index)."""
    toks = []
    for x in o:
        m = re.fullmatch(r'-?\d+\((r\d+)\)', x)
        toks.append(m.group(1) if m else x)
    regs = [t for t in toks if re.fullmatch(r'[rf]\d+', t)]
    if not regs:
        return set(), set()
    if mn.startswith('st') or mn.startswith('psq_st') or mn.startswith('cmp') or mn.startswith('fcmp') \
            or mn in ('mtspr', 'mtcrf', 'dcbf', 'dcbst', 'dcbz', 'dcbi', 'icbi', 'mtmsr'):
        return set(regs), set()
    return set(regs[1:]), {regs[0]}


# ================================================================ execution
LS = {'lwz': ('ld4', 4), 'lwzu': ('ld4', 4), 'lwzx': ('ld4', 4), 'lwzux': ('ld4', 4),
      'lhz': ('ld2', 2), 'lhzu': ('ld2', 2), 'lhzx': ('ld2', 2), 'lhzux': ('ld2', 2),
      'lha': ('ld2', 2), 'lhau': ('ld2', 2), 'lhax': ('ld2', 2), 'lhaux': ('ld2', 2),
      'lbz': ('ld1', 1), 'lbzu': ('ld1', 1), 'lbzx': ('ld1', 1), 'lbzux': ('ld1', 1),
      'lfs': ('ld4', 4), 'lfsu': ('ld4', 4), 'lfsx': ('ld4', 4), 'lfsux': ('ld4', 4),
      'lfd': ('ld8', 8), 'lfdu': ('ld8', 8), 'lfdx': ('ld8', 8), 'lfdux': ('ld8', 8)}
SS = {'stw': 4, 'stwu': 4, 'stwx': 4, 'stwux': 4, 'sth': 2, 'sthu': 2, 'sthx': 2, 'sthux': 2,
      'stb': 1, 'stbu': 1, 'stbx': 1, 'stbux': 1, 'stfs': 4, 'stfsu': 4, 'stfsx': 4, 'stfsux': 4,
      'stfd': 8, 'stfdu': 8, 'stfdx': 8, 'stfdux': 8, 'stfiwx': 4}
FBIN = {'fadds', 'fsubs', 'fmuls', 'fdivs', 'fadd', 'fsub', 'fmul', 'fdiv'}
FUSED = {'fmadds', 'fmsubs', 'fnmadds', 'fnmsubs', 'fmadd', 'fmsub', 'fnmadd', 'fnmsub'}
FUN1 = {'fabs', 'fnabs', 'fctiwz', 'fctiw', 'fres', 'frsqrte'}
VOLATILE = ['r0'] + ['r%d' % i for i in range(3, 13)] + ['f%d' % i for i in range(0, 14)]
UNIV = {'s': frozenset('<=>'), 'u': frozenset('<=>'), 'i': frozenset('<=>'), 'f': frozenset('<=>U')}
MIRROR = {'<': '>', '>': '<', '=': '=', 'U': 'U'}
BITS = {'lt': 0, 'gt': 1, 'eq': 2, 'so': 3, 'un': 3}
K43300000 = 0x43300000
I2F_BIAS = 0x4330000080000000
U2F_BIAS = 0x4330000000000000


def crbit(tok):
    """'eq' / '4*cr1+gt' / '6' -> (field, bit index)."""
    m = re.fullmatch(r'(?:4\*cr(\d)\+)?(lt|gt|eq|so|un)', tok)
    if m:
        return int(m.group(1) or 0), BITS[m.group(2)]
    if tok.isdigit():
        n = int(tok)
        return n // 4, n % 4
    m = re.fullmatch(r'cr(\d)', tok)
    if m:
        return int(m.group(1)), 0
    return None


class CR:
    """A condition-register field: compare kind, operands, and which outcomes set each bit."""
    __slots__ = ('k', 'a', 'b', 'bits')

    def __init__(self, k, a, b, bits=None):
        self.k, self.a, self.b = k, a, b
        self.bits = bits or [frozenset('<'), frozenset('>'), frozenset('='),
                             frozenset('U') if k == 'f' else frozenset()]

    def key(self):
        return (self.k, self.a.s, self.b.s, tuple(''.join(sorted(x)) for x in self.bits))


class State:
    __slots__ = ('R', 'stk', 'mem', 'cr', 'wr')

    def __init__(self):
        self.R, self.stk, self.mem, self.cr, self.wr = {}, {}, {}, {}, frozenset()

    def copy(self):
        s = State()
        s.R, s.stk, s.mem, s.cr, s.wr = dict(self.R), dict(self.stk), dict(self.mem), dict(self.cr), self.wr
        return s


def entry_state():
    s = State()
    for i in range(8):
        s.R['r%d' % (3 + i)] = atom('P%d' % i)
        s.R['f%d' % (1 + i)] = atom('F%d' % i)
    s.R['r1'] = SP
    return s


def phi(vals):
    """Join of values from several predecessors: a flat, sorted set of the distinct ones."""
    ss = {}
    for v in vals:
        for x in (v.a if v.op == 'phi' else (v,)):
            ss.setdefault(x.s, x)
    if len(ss) == 1:
        return next(iter(ss.values()))
    return E('phi', tuple(ss[k] for k in sorted(ss)))


def merge(states):
    if len(states) == 1:
        return states[0].copy()
    s = State()
    keys = set().union(*[set(x.R) for x in states])
    for k in keys:
        s.R[k] = phi([x.R.get(k, UNK) for x in states])
    for k in set.intersection(*[set(x.stk) for x in states]):
        s.stk[k] = phi([x.stk[k] for x in states])
    for k in set.intersection(*[set(x.mem) for x in states]):
        vs = [x.mem[k] for x in states]
        if len(set(v.s for v in vs)) == 1:
            s.mem[k] = vs[0]
    for k in set.intersection(*[set(x.cr) for x in states]):
        cs = [x.cr[k] for x in states]
        if len(set(c.key() for c in cs)) == 1:
            s.cr[k] = cs[0]
    s.wr = frozenset().union(*[x.wr for x in states])
    return s


class Func:
    def __init__(self, obj, name, ret_kind):
        self.obj, self.name = obj, name
        self.ins = obj.funcs[name]
        self.ret_kind = ret_kind            # set of 'r3' / 'f1'
        self.cfg()

    # ------------------------------------------------------------ CFG
    def cfg(self):
        ins = self.ins
        self.addrs = [i[0] for i in ins]
        idx = {a: k for k, a in enumerate(self.addrs)}
        self.idx = idx
        leaders = {self.addrs[0]} if ins else set()
        succ = collections.defaultdict(list)          # branch insn addr -> targets
        self.jt = {}
        self.taken = set()                             # address-taken stack offsets
        for k, (ad, mn, ops, rel) in enumerate(ins):
            o = ops.split(',') if ops else []
            if mn == 'addi' and len(o) == 3 and o[1] == 'r1' and o[0] != 'r1':
                self.taken.add(int(o[2]))
            nxt = self.addrs[k + 1] if k + 1 < len(ins) else None
            if rel and rel[0] == 'R_PPC_REL24':
                if mn == 'b' and nxt is not None:
                    leaders.add(nxt)
                continue
            if mn in ('b', 'bc'):
                try:
                    t = int(o[-1], 16)
                except ValueError:
                    t = None
                if t is not None and t in idx:
                    leaders.add(t)
                    succ[ad].append(t)
                if nxt is not None:
                    leaders.add(nxt)
            elif mn in ('bclr', 'bcctr', 'rfi'):
                if mn == 'bcctr':
                    ts = self.jumptable(k)
                    if ts:
                        self.jt[ad] = ts
                        for t in ts:
                            leaders.add(t)
                            succ[ad].append(t)
                if nxt is not None:
                    leaders.add(nxt)
        self.leaders = sorted(leaders)
        self.blocks = {}
        for i, l in enumerate(self.leaders):
            end = self.leaders[i + 1] if i + 1 < len(self.leaders) else None
            k0 = idx[l]
            k1 = idx[end] if end is not None else len(ins)
            self.blocks[l] = (k0, k1)
        self.succ = {}
        for l, (k0, k1) in self.blocks.items():
            last = ins[k1 - 1]
            ad, mn, ops, rel = last
            o = ops.split(',') if ops else []
            nxt = self.addrs[k1] if k1 < len(ins) else None
            ss = []
            if rel and rel[0] == 'R_PPC_REL24':
                if mn != 'b' and nxt is not None:
                    ss.append(nxt)
            elif mn == 'b':
                ss += succ.get(ad, [])
            elif mn == 'bc':
                bo = int(o[0])
                if nxt is not None and not (bo & 0x14) == 0x14:
                    ss.append(nxt)
                ss += succ.get(ad, [])
            elif mn in ('bclr', 'bcctr'):
                bo = int(o[0])
                if (bo & 0x14) != 0x14 and nxt is not None:
                    ss.append(nxt)
                ss += succ.get(ad, [])
            elif mn == 'rfi':
                pass
            elif nxt is not None:
                ss.append(nxt)
            self.succ[l] = list(dict.fromkeys(ss))
        self.preds = collections.defaultdict(list)
        for l, ss in self.succ.items():
            for t in ss:
                self.preds[t].append(l)
        # DFS: reverse post-order and back edges
        order, seen, onstk, back = [], set(), set(), set()
        stack = [(self.leaders[0], iter(self.succ[self.leaders[0]]))] if self.leaders else []
        if self.leaders:
            seen.add(self.leaders[0])
            onstk.add(self.leaders[0])
        while stack:
            n, it = stack[-1]
            t = next(it, None)
            if t is None:
                stack.pop()
                onstk.discard(n)
                order.append(n)
                continue
            if t in onstk:
                back.add((n, t))
            elif t not in seen:
                seen.add(t)
                onstk.add(t)
                stack.append((t, iter(self.succ[t])))
        rpo = order[::-1]
        rest = [l for l in self.leaders if l not in seen]
        self.rpo = rpo + rest
        self.back = back
        # loops are named by nesting depth (layout-independent): L1 outermost
        body = collections.defaultdict(set)
        for u, h in back:
            b = body[h]
            b.add(h)
            work = [u]
            while work:
                n = work.pop()
                if n in b:
                    continue
                b.add(n)
                work += self.preds.get(n, [])
        self.loopno = {h: sum(1 for h2 in body if h in body[h2]) for h in body}
        self.live = self.liveness()

    def liveness(self):
        """Registers live at each block entry (backward, all registers).  A call reads its
        argument registers and writes the volatile ones; a return reads r3 and f1."""
        ud = {}
        for l, (k0, k1) in self.blocks.items():
            use, dfn = set(), set()
            for ad, mn, ops, rel in self.ins[k0:k1]:
                o = [x.strip() for x in ops.split(',')] if ops else []
                if mn in ('bl', 'bclrl', 'bcctrl') or (rel and rel[0] == 'R_PPC_REL24'):
                    rd = ARGREGS | {'ctr', 'lr'} if mn != 'bl' or not rel else set(ARGREGS)
                    use |= rd - dfn
                    dfn |= set(VOLATILE) | {'CA', 'ctr'}
                    if mn == 'b':
                        use |= {'r3', 'f1'} - dfn
                    continue
                if mn in ('bclr', 'bcctr'):
                    rd = {'r3', 'f1', 'lr'} if mn == 'bclr' else {'ctr'}
                    use |= rd - dfn
                    continue
                if mn == 'bc':
                    if not int(o[0]) & 0x04:
                        use |= {'ctr'} - dfn
                    continue
                if mn == 'mtspr':
                    reg = {'8': 'lr', '9': 'ctr'}.get(o[0])
                    if o[1] not in dfn:
                        use.add(o[1])
                    if reg:
                        dfn.add(reg)
                    continue
                if mn == 'mfspr':
                    reg = {'8': 'lr', '9': 'ctr'}.get(o[1])
                    if reg and reg not in dfn:
                        use.add(reg)
                    dfn.add(o[0])
                    continue
                if mn in ('stmw', 'lmw'):
                    r0 = int(o[0][1:])
                    rs = {'r%d' % r for r in range(r0, 32)}
                    if mn == 'stmw':
                        use |= rs - dfn
                    else:
                        dfn |= rs
                    continue
                rd, wr = regs_rw(mn, o)
                if mn in ('rlwimi', 'rlwimi.'):
                    rd |= wr
                if mn in ('adde', 'addze', 'subfe', 'subfze', 'addme', 'subfme') or mn.rstrip('.') in (
                        'adde', 'addze', 'subfe', 'subfze'):
                    rd = rd | {'CA'}
                use |= rd - dfn
                dfn |= wr
                if mn in ('addic', 'addic.', 'subfic', 'subfc', 'addc', 'srawi', 'sraw', 'srawi.',
                          'adde', 'addze', 'subfe', 'subfze'):
                    dfn.add('CA')
            ud[l] = (use, dfn)
        live = {l: set() for l in self.leaders}
        order = list(reversed(self.rpo))
        for _ in range(len(order) + 2):
            changed = False
            for l in order:
                out = set()
                for t in self.succ.get(l, []):
                    out |= live.get(t, set())
                use, dfn = ud[l]
                new = use | (out - dfn)
                if new != live[l]:
                    live[l] = new
                    changed = True
            if not changed:
                break
        return live

    def defined_at_returns(self, reg):
        """Whether reg is written by an instruction other than a call, after the last
        call, on every path reaching every unconditional return."""
        out = {}
        found = False
        for it in range(len(self.rpo) + 2):
            changed = False
            for l in self.rpo:
                ps = [out[p] for p in self.preds.get(l, []) if p in out]
                d = all(ps) if ps else False
                k0, k1 = self.blocks[l]
                for ad, mn, ops, rel in self.ins[k0:k1]:
                    o = [x.strip() for x in ops.split(',')] if ops else []
                    if mn in ('bl', 'bclrl', 'bcctrl') or (rel and rel[0] == 'R_PPC_REL24'):
                        d = False
                        continue
                    if mn == 'bclr':
                        if int(o[0]) & 0x14 == 0x14 or True:
                            if it > 0 and not d:
                                return False
                            found = True
                        continue
                    rd, wr = regs_rw(mn, o)
                    if reg in wr:
                        d = True
                if out.get(l) != d:
                    out[l] = d
                    changed = True
            if not changed:
                break
        return found

    def jumptable(self, k):
        """Targets of the jump table used by the bcctr at index k, in index order."""
        for j in range(k - 1, max(-1, k - 16), -1):
            ad, mn, ops, rel = self.ins[j]
            if rel and rel[0] == 'R_PPC_ADDR16_LO':
                r = self.obj.resolve(rel[1])
                if not r:
                    return None
                sec, off, rest, sname, inner = r
                rels = self.obj.rel.get(sec, {})
                out = []
                o = off
                while o in rels:
                    n, add, typ = rels[o]
                    t = add + (0 if n == '.text' else self.obj.syms.get(n, (0, 0, 0))[1])
                    if t not in self.idx:
                        break
                    out.append(t)
                    o += 4
                return out
            if ad != self.ins[k][0] and self.ins[j][1] in ('bc', 'b', 'bclr', 'bcctr'):
                return None
        return None

    # ------------------------------------------------------------ run
    def run(self):
        tagged = collections.defaultdict(set)
        for it in range(10):
            self.effects = []
            ent, out = self.pass_(tagged)
            grew = False
            for u, h in self.back:
                if u not in out or h not in ent:
                    continue
                eo, eh = out[u], ent[h]
                for k in set(eo.R) | set(eh.R):
                    if k in tagged[h]:
                        continue
                    if eo.R.get(k, UNK).s != eh.R.get(k, UNK).s:
                        tagged[h].add(k)
                        grew = True
                for k in set(eh.stk):
                    kk = ('stk',) + k
                    if kk in tagged[h]:
                        continue
                    if k not in eo.stk or eo.stk[k].s != eh.stk[k].s:
                        tagged[h].add(kk)
                        grew = True
            if not grew:
                break
        self.finish()
        return self.effects

    def pass_(self, tagged):
        ent, out = {}, {}
        self.pending = []
        for l in self.rpo:
            fps = [p for p in self.preds.get(l, []) if (p, l) not in self.back and p in out]
            if l == self.leaders[0]:
                st = entry_state()
                if fps:
                    st = merge([st] + [out[p] for p in fps])
            elif fps:
                st = merge([out[p] for p in fps])
            else:
                st = State()
                st.R['r1'] = SP
            # a register dead at the block entry holds nothing: dropping it keeps junk
            # (a value left on one incoming path) out of joins
            lv = self.live.get(l)
            if lv is not None and l != self.leaders[0]:
                for k in [k for k in st.R if k not in lv and k != 'r1']:
                    del st.R[k]
            if l in self.loopno:
                n = self.loopno[l]
                for k in tagged[l]:
                    if isinstance(k, tuple):
                        kk = k[1:]
                        if kk in st.stk:
                            st.stk[kk] = A('loop%d' % n, st.stk[kk])
                    else:
                        st.R[k] = A('loop%d' % n, st.R.get(k, UNK))
                st.mem = {}
                st.cr = {}
            ent[l] = st
            out[l] = self.block(l, st.copy(), self.effects, self.pending)
        self.ent, self.out = ent, out
        return ent, out

    def finish(self):
        """Resolve branch successor signatures and append the branch effects.  All
        returns fold into one effect (the set of values returned): one build's shared
        epilogue and the other's separate ones return the same things."""
        rets = [e for e in self.effects if e.op == 'ret']
        if len(rets) > 1:
            self.effects = [e for e in self.effects if e.op != 'ret']
            n = max(len(e.a) for e in rets)
            self.effects.append(A('ret', *[phi([e.a[i] for e in rets if len(e.a) > i]) for i in range(n)]))
        for ad, blk, pred, tgt, nxt, swap in self.pending:
            st = self.out[blk]
            t = self.first(tgt, refine(st, pred, swap, True)) if tgt is not None else 'end'
            f = self.first(nxt, refine(st, pred, swap, False)) if nxt is not None else 'end'
            if swap:
                t, f = f, t
            if pred[0] == 'sw' or constpred(pred, swap) is not None:
                continue                            # a test of constants decides nothing
            self.effects.append(A('br', atom(pred[0]), pred[1], pred[2], atom(pred[3]), atom(t), atom(f)))

    def first(self, l, st, depth=0):
        """Signature of the first effect reached from block l with state st (lookahead)."""
        if l == 'ret':
            return self.retsig(st)
        if l == 'ctr':
            return 'ctr'
        seen = set()
        st = st.copy()
        while l is not None and depth < 8:
            if l in seen:
                return 'loop'
            seen.add(l)
            eff, pend = [], []
            st2 = self.block(l, st, eff, pend, look=True)
            if eff:
                # the block's effects as a set: scheduling reorders independent stores
                ks = sorted(e.s if e.op != 'icall' else e.a[0].s + '|' + (e.a[1].s if len(e.a) > 1 else '')
                            for e in eff)
                k = ' ; '.join(ks)
                if os.environ.get('EXPR_DIFF_SIGS'):
                    return '{' + k[:400] + '}'
                return hashlib.sha1(k.encode()).hexdigest()[:10] + ':' + eff[0].op
            if pend:
                p = pend[0][2]
                if p[0] == 'sw':
                    return 'switch'
                c = constpred(p, pend[0][5])
                if c is not None:
                    # a known outcome (a materialised bool tested again): follow it
                    l = pend[0][3] if c else pend[0][4]
                    if l == 'ret':
                        return self.retsig(st2)
                    if l in (None, 'ctr'):
                        return 'end'
                    st = st2
                    depth += 1
                    continue
                if os.environ.get('EXPR_DIFF_SIGS'):
                    return 'br:{%s %s %s %s}' % (p[0], p[1].s[:100], p[2].s[:100], p[3])
                return 'br:' + hashlib.sha1((p[0] + p[1].s + p[2].s + p[3]).encode()).hexdigest()[:10]
            ss = self.succ.get(l, [])
            if len(ss) != 1:
                return 'end'
            l = ss[0]
            st = st2
            depth += 1
        return 'deep'

    def retsig(self, st):
        e = self.reteffect(st)
        return hashlib.sha1(e.s.encode()).hexdigest()[:10] + ':ret' if e else 'ret'

    def reteffect(self, st):
        parts = []
        if 'r3' in self.ret_kind:
            parts.append(self.escape(st, st.R.get('r3', UNK)))
        if 'f1' in self.ret_kind:
            parts.append(self.escape(st, st.R.get('f1', UNK)))
        if not parts:
            return None
        return A('ret', *parts)

    # ------------------------------------------------------------ helpers
    def escape(self, st, v, depth=0, size=None):
        """Render stack addresses inside a value independently of the frame layout."""
        if depth > 4 or not has_sp(v):
            return v
        o = sp_off(v)
        if o is not None:
            return A('&loc', *self.window(st, o, depth, size or 4))
        if v.op == 'phi':
            return phi([self.escape(st, atom(x.s) if False else x, depth + 1) for x in v.a])
        return E(v.op, tuple(self.escape(st, x, depth + 1) for x in v.a))

    def window(self, st, o, depth=0, size=4):
        """Contents of a stack object of `size` bytes (from the callee's signature; one
        word when unknown), up to its first word that is not defined."""
        out = []
        p = o
        while p < o + min(size, 48):
            v = st.stk.get((p, 4))
            w = 4
            if v is None and (p, 8) in st.stk:
                v, w = st.stk[(p, 8)], 8
            if v is None or v.s in ('?', 'stk?'):
                break
            out.append(self.escape(st, v, depth + 1))
            p += w
        return out

    def load(self, st, a, kind, n):
        o = sp_off(a)
        if o is not None:
            v = st.stk.get((o, n))
            if v is not None:
                return v
            if n == 8 and (o, 4) in st.stk and (o + 4, 4) in st.stk:
                hi, lo = st.stk[(o, 4)], st.stk[(o + 4, 4)]
                if isc(hi) and hi.v == K43300000:
                    if lo.op == 'xor' and len(lo.a) == 2:
                        for p_, q_ in ((lo.a[0], lo.a[1]), (lo.a[1], lo.a[0])):
                            if isc(q_) and q_.v == C(0x80000000).v:
                                return A('i2d', p_)
                    return A('u2d', lo)
                return A('pair', hi, lo)
            if n == 4 and (o - 4, 8) in st.stk:
                return A('lo', st.stk[(o - 4, 8)])
            if n == 4 and (o, 8) in st.stk:
                return A('hi', st.stk[(o, 8)])
            for (oo, ss), v in st.stk.items():
                if oo <= o < oo + ss:
                    b = constbytes(v, ss)
                    if b is not None:
                        return C(int.from_bytes(b[o - oo:o - oo + n], 'big'))
                    return A('part', v, C(o - oo), C(n))
            return atom('stk?')
        if has_sp(a):
            return atom('stk?')
        k = (a.s, n)
        if k in st.mem:
            return st.mem[k]
        v = self.constload(a, kind, n)
        if v is not None:
            return v
        return A(kind, a)

    def constload(self, a, kind, n):
        base, extra = None, 0
        if a.op == 'AD':
            obj, sec, off = a.v
            r = obj.readat(sec, off, n)
            return self.constval(r, n)
        if a.op == 'A':
            base = a.a[0].s
        elif a.op == 'add' and len(a.a) == 2:
            for p, q in ((a.a[0], a.a[1]), (a.a[1], a.a[0])):
                if p.op == 'A' and isc(q):
                    base, extra = p.a[0].s, q.v
        if base is None:
            return None
        return self.constval(self.obj.read(base, extra, n), n)

    def constval(self, r, n):
        if r is None:
            return None
        b, rel = r
        if rel is not None:
            nm, add, typ = rel
            return self.obj.addr(nm + ('+0x%x' % add if add else ''))
        if n == 4:
            return atom('K4:%08x' % struct.unpack('>I', b)[0])
        if n == 8:
            return atom('K8:%016x' % struct.unpack('>Q', b)[0])
        return atom('K%d:%s' % (n, b.hex()))

    def store(self, st, a, n, v, eff):
        o = sp_off(a)
        if o is not None:
            for k in [k for k in st.stk if k[0] < o + n and o < k[0] + k[1]]:
                del st.stk[k]
            st.stk[(o, n)] = v
            return
        if has_sp(a):
            return
        st.mem[(a.s, n)] = v
        if n in (1, 2):
            v = lowbits(v, 8 * n)               # only the low bits reach memory
        eff.append(A('st%d' % n, self.escape(st, a), self.escape(st, v)))

    # ------------------------------------------------------------ one block
    def block(self, l, st, eff, pend, look=False, body=None):
        if body is None:
            k0, k1 = self.blocks[l]
            seq = range(k0, k1)
            insl = self.ins
        else:
            seq = range(len(body))
            insl = body
        R = st.R

        def g(r):
            if r == '0':
                return ZERO
            return R.get(r, UNK)

        def setr(r, v):
            if r == 'r1':
                return
            R[r] = v
            if r in ARGREGS:
                st.wr = st.wr | {r}

        def ea(tok, relv=None):
            m = re.fullmatch(r'(-?\d+)\((r\d+|0)\)', tok)
            if not m:
                return None
            if relv is not None:
                return relv
            return iadd(g(m.group(2)), C(int(m.group(1))))

        for k in seq:
            ad, mn, ops, rel = insl[k]
            o = [x.strip() for x in ops.split(',')] if ops else []
            if body is not None and mn == 'bclr':
                break
            relv = None
            if rel:
                rt, tg = rel
                if rt == 'R_PPC_REL24':
                    self.call(st, atom(self.obj.key(tg)), eff, name=tg)
                    if mn == 'b':
                        e = self.reteffect(st)
                        if e is not None:
                            eff.append(e)
                    continue
                key = atom(self.obj.key(tg))
                if rt.endswith('_HA') or rt.endswith('_HI'):
                    setr(o[0], A('HA', self.obj.addr(tg)))
                    continue
                if rt in ('R_PPC_ADDR16_LO', 'R_PPC_EMB_SDA21', 'R_PPC_ADDR16'):
                    relv = self.obj.addr(tg)
            if mn in LS:
                kind, n = LS[mn]
                if mn.endswith('x'):
                    a = iadd(g(o[1]), g(o[2]))
                else:
                    a = ea(o[1], relv)
                v = self.load(st, a, kind, n)
                if mn.startswith('lf'):
                    v = fconst(v, n)
                if mn.startswith('lha'):
                    v = sx(v, 16)
                if mn[:2] == 'lf' and n == 4:
                    pass
                if mn.endswith('u') or mn.endswith('ux'):
                    base = o[1] if mn.endswith('ux') else re.search(r'\((r\d+)\)', o[1]).group(1)
                    setr(base, a)
                setr(o[0], v)
                continue
            if mn in SS:
                n = SS[mn]
                if mn.endswith('x'):
                    a = iadd(g(o[1]), g(o[2]))
                else:
                    a = ea(o[1], relv)
                if mn == 'stwu' and o[1].endswith('(r1)') and o[0] == 'r1':
                    continue
                v = g(o[0])
                if mn.startswith('stfs'):
                    v = frsp(v)
                elif mn == 'stfiwx':
                    v = A('fbits', v)
                self.store(st, a, n, v, eff)
                if mn.endswith('u') or mn.endswith('ux'):
                    base = o[1] if mn.endswith('ux') else re.search(r'\((r\d+)\)', o[1]).group(1)
                    setr(base, a)
                continue
            if mn in ('stmw', 'lmw'):
                m = re.fullmatch(r'(-?\d+)\((r\d+)\)', o[1])
                a = iadd(g(m.group(2)), C(int(m.group(1))))
                r0 = int(o[0][1:])
                for i, r in enumerate(range(r0, 32)):
                    ai = iadd(a, C(4 * i))
                    if mn == 'stmw':
                        self.store(st, ai, 4, g('r%d' % r), eff)
                    else:
                        setr('r%d' % r, self.load(st, ai, 'ld4', 4))
                continue
            if mn in ('psq_l', 'psq_lu', 'psq_st', 'psq_stu', 'psq_lx', 'psq_stx', 'psq_lux', 'psq_stux'):
                if mn.endswith('x'):
                    a = iadd(g(o[1]), g(o[2]))
                    w, q = o[3], o[4]
                else:
                    a = ea(o[1], relv)
                    w, q = o[2], o[3]
                n = 4 if w == '1' else 8
                if mn.startswith('psq_l'):
                    v = self.load(st, a, 'ld%d' % n, n)
                    setr(o[0], A('psq', atom('w' + w), atom('q' + q), v))
                else:
                    v = g(o[0])
                    if v.op == 'psq' and v.a[0].s == 'w' + w and v.a[1].s == 'q' + q:
                        v = v.a[2]
                    else:
                        v = A('psqst', atom('w' + w), atom('q' + q), v)
                    self.store(st, a, n, v, eff)
                if mn.endswith('u') or mn.endswith('ux'):
                    base = re.search(r'\((r\d+)\)', o[1]).group(1) if not mn.endswith('x') else o[1]
                    setr(base, a)
                continue
            if mn in ('addi', 'addis'):
                if o[0] == 'r1':
                    continue
                if relv is not None:
                    base = g(o[1])
                    if base.op == 'HA' or o[1] == '0':
                        setr(o[0], relv)
                    else:
                        setr(o[0], iadd(base, A('LO', relv)))
                    continue
                imm = int(o[2], 0)
                if mn == 'addis':
                    imm <<= 16
                setr(o[0], iadd(g(o[1]), C(imm)))
                continue
            if mn in ('addic', 'addic.'):
                a = g(o[1])
                v = iadd(a, C(int(o[2], 0)))
                setr(o[0], v)
                R['CA'] = A('ca+', a, C(int(o[2], 0)))
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('add', 'add.'):
                v = iadd(g(o[1]), g(o[2]))
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('subf', 'subf.'):
                v = iadd(g(o[2]), ineg(g(o[1])))
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('neg', 'neg.'):
                v = ineg(g(o[1]))
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn == 'subfic':
                a = g(o[1])
                setr(o[0], iadd(C(int(o[2], 0)), ineg(a)))
                R['CA'] = A('ca-', C(int(o[2], 0)), a)
                continue
            if mn in ('subfc', 'subfc.'):
                a, b = g(o[1]), g(o[2])
                setr(o[0], iadd(b, ineg(a)))
                R['CA'] = A('ca-', b, a)
                continue
            if mn in ('addc', 'addc.'):
                a, b = g(o[1]), g(o[2])
                setr(o[0], iadd(a, b))
                R['CA'] = op2('ca+', a, b)
                continue
            if mn in ('adde', 'adde.', 'subfe', 'subfe.', 'addze', 'addze.', 'subfze', 'subfze.', 'addme', 'subfme'):
                srcs = [g(x) for x in o[1:]]
                ca = R.get('CA', UNK)
                base = mn.rstrip('.')
                if base == 'addze' and ca.op == 'ca_sra' and srcs[0].op == 'sra' and srcs[0].a[0].s == ca.a[0].s \
                        and srcs[0].a[1].s == ca.a[1].s:
                    v = A('sdiv', ca.a[0], C(1 << ca.a[1].v))
                else:
                    v = A(base, *(srcs + [ca]))
                setr(o[0], v)
                R['CA'] = A('ca_' + base, *(srcs + [ca]))
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('srawi', 'srawi.'):
                a = g(o[1])
                n = int(o[2])
                v = A('sra', a, C(n)) if n else a
                setr(o[0], v)
                R['CA'] = A('ca_sra', a, C(n))
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('sraw', 'sraw.'):
                v = A('sraw', g(o[1]), g(o[2]))
                setr(o[0], v)
                R['CA'] = A('ca_sraw', g(o[1]), g(o[2]))
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('mulli',):
                setr(o[0], imul(g(o[1]), C(int(o[2], 0))))
                continue
            if mn in ('rlwinm', 'rlwinm.'):
                v = rlwinm(g(o[1]), int(o[2]), int(o[3]), int(o[4]))
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('rlwimi', 'rlwimi.'):
                v = A('rlwimi', g(o[0]), g(o[1]), C(int(o[2])), C(mask(int(o[3]), int(o[4]))))
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('ori', 'oris', 'xori', 'xoris', 'andi.', 'andis.'):
                imm = int(o[2], 0) & 0xffff
                if mn.endswith('s') or mn == 'andis.':
                    imm <<= 16
                a = g(o[1])
                if mn.startswith('and'):
                    v = iand(a, imm)
                elif imm == 0:
                    v = a
                elif isc(a):
                    v = C(a.v | imm) if mn.startswith('or') else C(a.v ^ imm)
                else:
                    v = op2('or' if mn.startswith('or') else 'xor', a, C(imm))
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('or', 'or.') and o[1] == o[2]:
                v = g(o[1])
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('and', 'and.', 'or', 'or.', 'xor', 'xor.', 'nor', 'nor.', 'nand', 'eqv', 'andc',
                      'andc.', 'orc', 'slw', 'slw.', 'srw', 'srw.', 'mullw', 'mullw.', 'mulhw', 'mulhwu',
                      'divw', 'divw.', 'divwu', 'divwu.'):
                a, b = g(o[1]), g(o[2])
                base = mn.rstrip('.')
                if base == 'nor' and o[1] == o[2]:
                    v = A('not', a)
                elif base == 'and' and isc(b):
                    v = iand(a, b.v)
                elif base == 'and' and isc(a):
                    v = iand(b, a.v)
                else:
                    v = op2(base, a, b)
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('extsh', 'extsh.', 'extsb', 'extsb.'):
                v = sx(g(o[1]), 16 if 'sh' in mn else 8)
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('cntlzw', 'cntlzw.'):
                v = A('cntlzw', g(o[1]))
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            if mn in ('cmpi', 'cmpli', 'cmp', 'cmpl'):
                f = int(o[0][2:]) if o[0].startswith('cr') else 0
                a = g(o[2])
                b = C(int(o[3], 0)) if mn in ('cmpi', 'cmpli') else g(o[3])
                if mn == 'cmpli':
                    b = C(int(o[3], 0) & 0xffff)
                st.cr[f] = CR('u' if mn.startswith('cmpl') else 's', self.escape(st, a), self.escape(st, b))
                continue
            if mn in ('fcmpo', 'fcmpu'):
                f = int(o[0][2:])
                st.cr[f] = CR('f', g(o[1]), g(o[2]))
                continue
            if mn in ('cror', 'crnor', 'crand', 'crandc', 'crorc', 'crnand', 'creqv', 'crxor'):
                bs = [crbit(x) for x in o]
                if all(bs) and len({b[0] for b in bs}) == 1 and bs[0][0] in st.cr:
                    c = st.cr[bs[0][0]]
                    U = UNIV[c.k]
                    X, Y = c.bits[bs[1][1]], c.bits[bs[2][1]]
                    v = {'cror': X | Y, 'crnor': U - (X | Y), 'crand': X & Y, 'crandc': X - Y,
                         'crorc': X | (U - Y), 'crnand': U - (X & Y), 'creqv': U - (X ^ Y),
                         'crxor': X ^ Y}[mn]
                    nb = list(c.bits)
                    nb[bs[0][1]] = frozenset(v)
                    st.cr[bs[0][0]] = CR(c.k, c.a, c.b, nb)
                elif all(bs) and bs[1] == bs[2] and mn in ('crxor', 'creqv'):
                    pass                                   # crclr/crset (varargs float flag)
                elif all(bs):
                    st.cr.pop(bs[0][0], None)
                continue
            if mn in FBIN:
                a, b = g(o[1]), g(o[2])
                setr(o[0], op2(mn, a, b))
                continue
            if mn in FUSED:
                setr(o[0], fused(mn, g(o[1]), g(o[2]), g(o[3])))
                continue
            if mn in ('fmr', 'fmr.'):
                setr(o[0], g(o[1]))
                continue
            if mn == 'fneg':
                setr(o[0], fneg(g(o[1])))
                continue
            if mn == 'frsp':
                setr(o[0], frsp(g(o[1])))
                continue
            if mn in FUN1:
                setr(o[0], A(mn, g(o[1])))
                continue
            if mn == 'fsel':
                setr(o[0], A('fsel', g(o[1]), g(o[2]), g(o[3])))
                continue
            if mn == 'mfspr':
                spr = int(o[1])
                setr(o[0], R.get('ctr', UNK) if spr == 9 else (JUNK if spr == 8 else A('spr', C(spr))))
                continue
            if mn == 'mtspr':
                spr = int(o[0])
                if spr == 9:
                    R['ctr'] = g(o[1])
                elif spr == 8:
                    R['lr'] = g(o[1])
                else:
                    eff.append(A('mtspr', C(spr), g(o[1])))
                continue
            if mn == 'b':
                continue
            if mn in ('bc', 'bclr', 'bcctr', 'bclrl', 'bcctrl'):
                bo = int(o[0])
                if mn in ('bclrl', 'bcctrl'):
                    t = R.get('lr' if mn == 'bclrl' else 'ctr', UNK)
                    self.call(st, t, eff, indirect=True)
                    continue
                uncond_cr = bool(bo & 0x10)
                usectr = not (bo & 0x04)
                preds = []
                if usectr:
                    ctr = iadd(R.get('ctr', UNK), C(-1))
                    R['ctr'] = ctr
                    # BO&2: branch if ctr == 0
                    preds.append(('i', ctr, ZERO, frozenset('=') if bo & 0x02 else frozenset('<>')))
                if not uncond_cr:
                    fb = crbit(o[1])
                    c = st.cr.get(fb[0]) if fb else None
                    if c is None:
                        preds.append(('?', atom('cr%s' % (fb,)), ZERO, frozenset('=') if bo & 8 else frozenset('<>')))
                    else:
                        bits = c.bits[fb[1]]
                        taken = bits if bo & 0x08 else UNIV[c.k] - bits
                        preds.append((c.k, c.a, c.b, taken))
                if mn == 'bc':
                    tgt = int(o[2], 16) if len(o) > 2 else None
                else:
                    tgt = 'ret' if mn == 'bclr' else 'ctr'
                nxt = self.addrs[k + 1] if k + 1 < len(self.addrs) else None
                if not preds:
                    # unconditional
                    if mn == 'bclr':
                        e = self.reteffect(st)
                        if e is not None:
                            eff.append(e)
                    elif mn == 'bcctr':
                        self.switch(ad, st, eff, pend, look)
                    continue
                if len(preds) > 1:
                    pk = ('?', atom('ctr&cond'), ZERO, frozenset('='))
                else:
                    pk = preds[0]
                kind, a, b, taken = pk
                U = UNIV.get(kind, UNIV['s'])
                a, b = self.escape(st, a), self.escape(st, b)
                if a.s > b.s:
                    a, b = b, a
                    taken = frozenset(MIRROR[x] for x in taken)
                ts = ''.join(sorted(taken))
                cs = ''.join(sorted(U - taken))
                swap = cs < ts
                rep = cs if swap else ts
                if kind in ('s', 'u') and rep in ('=', '<>'):
                    kind = 'i'
                pend.append((ad, l, (kind, a, b, rep), tgt, nxt, swap))
                continue
            if mn in ('sync', 'isync', 'nop'):
                continue
            if mn == 'rfi' or mn == 'sc':
                eff.append(A(mn))
                continue
            # generic
            if o and re.fullmatch(r'[rf]\d+', o[0]):
                srcs = []
                for x in o[1:]:
                    if re.fullmatch(r'[rf]\d+', x):
                        srcs.append(g(x))
                    else:
                        try:
                            srcs.append(C(int(x, 0)))
                        except ValueError:
                            srcs.append(atom(x))
                base = mn.rstrip('.')
                v = A(base, *srcs)
                setr(o[0], v)
                if mn.endswith('.'):
                    st.cr[0] = CR('s', v, ZERO)
                continue
            srcs = []
            for x in o:
                m = re.fullmatch(r'(-?\d+)\((r\d+|0)\)', x)
                if m:
                    srcs.append(iadd(g(m.group(2)), C(int(m.group(1)))))
                elif re.fullmatch(r'[rf]\d+', x):
                    srcs.append(g(x))
                else:
                    srcs.append(atom(x))
            eff.append(A('asm:' + mn, *[self.escape(st, x) for x in srcs]))
        return st

    def switch(self, ad, st, eff, pend, look):
        ctr = st.R.get('ctr', UNK)
        ts = self.jt.get(ad)
        if not ts:
            eff.append(A('ijump', self.escape(st, ctr)))
            return
        idx = UNK
        a = ctr.a[0] if ctr.op == 'ld4' and ctr.a else None
        if a is not None and a.op == 'add':
            rest = [t for t in a.a if t.op != 'A']
            idx = iadd(*rest) if rest else ZERO
        if look:
            pend.append((ad, None, ('sw', idx, ZERO, ''), None, None, False))
            return
        sigs = [self.first(t, st) for t in ts]
        eff.append(A('switch', self.escape(st, idx), *[atom(s) for s in sigs]))

    def devirt(self, tgt):
        """The function a virtual call through this (P0) reaches when the current
        function's class has its own vtable: *(*(P0) + slot)."""
        if tgt.op != 'ld4' or not tgt.a:
            return None
        a = tgt.a[0]
        slot = 0
        if a.op == 'add' and len(a.a) == 2 and isc(a.a[0]) and a.a[1].s == 'ld4(P0)':
            slot, vp = a.a[0].v, a.a[1]
        elif a.s == 'ld4(P0)':
            vp = a
        else:
            return None
        m = re.search(r'.__(?=[0-9Q])', self.name)
        if not m:
            return None
        rest = self.name[m.start() + 3:]
        try:
            j = ptype(rest, 0)[0]
        except (ValueError, IndexError):
            return None
        vt = retindex().vt.get('__vt__' + rest[:j])
        if not vt:
            return None
        return vt.get(slot)

    def call(self, st, tgt, eff, indirect=False, name=None):
        R = st.R
        if not indirect and name is not None:
            body = retindex().leaf.get(re.sub(r'\+0x[0-9a-f]+$', '', name))
            if body is not None:
                before = dict(R)
                self.block(None, st, eff, [], body=body)
                for r in VOLATILE + ['CA', 'ctr']:
                    if r not in ('r3', 'f1') and R.get(r) is not before.get(r):
                        R[r] = JUNK
                st.wr = frozenset()
                return
        if indirect:
            dv = self.devirt(tgt)
            if dv is not None:
                indirect, name = False, dv
                tgt = A('virt', tgt)
        spec = argspec(name) if (not indirect and name is not None) else None
        if name == '__register_global_object':
            spec = [('r3', 4), ('r4', 4)]           # the destructor-chain node is anonymous storage
        li = retindex().livein.get(re.sub(r'\+0x[0-9a-f]+$', '', name)) if not indirect and name else None
        if li is not None and len(li) >= 12:
            li = None                               # a varargs function saves every register
            spec = None if spec is None else spec
        if spec is None and li is not None:
            vec = re.match(r'(PS|C_)?(VEC|MTX|QUAT)', name)
            spec = [(r, 12 if vec else 4) for r in li]
        elif spec is not None and li is not None:
            # an argument the callee never reads cannot matter (static members have no this)
            spec = [x for x in spec if x[0] in li]
        if spec is None and indirect:
            # a call through a pointer to known functions: what those functions read
            fns = [t for t in (tgt.a if tgt.op == 'phi' else (tgt,))]
            if fns and all(t.op == 'A' for t in fns):
                regs = set()
                for t in fns:
                    fn = t.a[0].s
                    li = retindex().livein.get(fn)
                    sp = argspec(fn)
                    if li is None and sp is None:
                        regs = None
                        break
                    rs = set(li) if li is not None else {r for r, _ in sp}
                    if sp is not None and li is not None:
                        rs &= {r for r, _ in sp}
                    regs |= rs
                if regs:
                    spec = sorted(((r, 4) for r in regs), key=lambda x: (x[0][0] == 'f', int(x[0][1:])))
        if spec is None and indirect:
            slot = vslot(tgt)
            sr = retindex().slotregs.get(slot) if slot is not None else None
            if sr:
                spec = sorted(((r, 4) for r in sr | {'r3'}), key=lambda x: (x[0][0] == 'f', int(x[0][1:])))
        if spec is None:
            ri = [int(r[1:]) for r in st.wr if r.startswith('r')]
            fi = [int(r[1:]) for r in st.wr if r.startswith('f')]
            top = max(ri) if ri else 2
            if indirect:
                top = max(top, 3)
            spec = [('r%d' % i, 4) for i in range(3, top + 1)] + \
                [('f%d' % i, None) for i in range(1, (max(fi) if fi else 0) + 1)]
        regs = [r for r, _ in spec]
        args = []
        for r, size in spec:
            v = R.get(r, UNK)
            if size in (-8, -16):
                v = lowbits(v, -size)       # a narrow parameter: only its low bits are passed
                size = 4
            args.append(self.escape(st, v, size=size))
        if indirect:
            # arity unknown: positional arguments, compared leniently (see compare())
            tgt = self.escape(st, tgt)
            e = A('icall', tgt, *[A('arg', atom(r), v) for r, v in zip(regs, args)])
        else:
            e = A('call', tgt, *args)
        eff.append(e)
        # the result is named by the call; an indirect call by its target and this only
        key = e.s if e.op == 'call' else tgt.s + '|' + (args[0].s if args else '')
        h = atom(hashlib.sha1(key.encode()).hexdigest()[:10])
        # stack objects passed by address may be written by the callee
        sizes = dict(spec)
        for r in regs:
            if not r.startswith('r'):
                continue
            o = sp_off(R.get(r, UNK))
            if o is None:
                continue
            # the callee may write the object: all of it when the signature gives its size
            # (12 bytes otherwise); beyond that, up to the next address-taken local, only
            # words nothing wrote yet or an earlier call wrote through this same address
            # (an output buffer), so that neighbouring locals survive any frame layout
            own = sizes.get(r)
            if own in (1, 2):
                # a narrow out-parameter (s16*, u8*): exactly its bytes
                for kk in [kk for kk in st.stk if kk[0] < o + own and o < kk[0] + kk[1]]:
                    del st.stk[kk]
                st.stk[(o, own)] = A('post', h, C(0))
                continue
            own = own if own and own > 0 else 12
            nxt = min([t for t in self.taken if t > o] + [o + 64])
            for p in range(o, max(nxt, o + own), 4):
                cur = [kk for kk in st.stk if kk[0] == p]
                if p >= o + own and any(not (st.stk[kk].op == 'post' and p - st.stk[kk].a[1].v == o)
                                       for kk in cur):
                    continue
                for kk in cur:
                    del st.stk[kk]
                st.stk[(p, 4)] = A('post', h, C(p - o))
        st.mem = {}
        for r in VOLATILE:
            R[r] = JUNK
        R['CA'] = JUNK
        R['ctr'] = JUNK
        R['r3'] = A('R', tgt, h)
        R['f1'] = A('FR', tgt, h)
        for f in (0, 1, 5, 6, 7):
            st.cr.pop(f, None)
        st.wr = frozenset()


ARGREGS = {'r%d' % i for i in range(3, 11)} | {'f%d' % i for i in range(1, 9)}


# ================================================================ return kinds
def ret_kind(func, name, idx):
    """{'r3'} / {'f1'} / both / neither: the registers a function returns in.

    A direct caller in retail that reads r3 (f1) after the call decides it.  For a
    function no one calls directly (virtual, callback), a register counts when retail
    writes it after the last call on every path to every return, and it is not simply
    the incoming parameter (constructors return `this`)."""
    k = set()
    if name in idx.r3:
        k.add('r3')
    if name in idx.f1:
        k.add('f1')
    if k or name in idx.called:
        return k
    if name.startswith('__ct__'):
        return {'r3'}
    for reg in ('r3', 'f1'):
        if func.defined_at_returns(reg):
            k.add(reg)
            break
    return k


# ================================================================ compare
def effects(obj, name, idx):
    f = Func(obj, name, set())
    f.ret_kind = ret_kind(f, name, idx)
    return f.run()


def compare(oa, ob, name, idx, rk=None):
    fa = Func(oa, name, set())
    fa.ret_kind = kind = rk if rk is not None else ret_kind(fa, name, idx)
    ea = fa.run()
    eb = Func(ob, name, kind).run()
    ca = collections.Counter(e.s for e in ea)
    cb = collections.Counter(e.s for e in eb)
    ra, rb = ca - cb, cb - ca
    # an indirect call's arity is unknown: registers written before it that are not
    # arguments differ between builds.  Two such calls to the same target agree when
    # every argument register holding a value in both holds the same value.
    ia = [e for e in ea if e.op == 'icall' and ra[e.s] > 0]
    ib = [e for e in eb if e.op == 'icall' and rb[e.s] > 0]
    for e in ia:
        if ra[e.s] <= 0:
            continue
        da = {x.a[0].s: x.a[1].s for x in e.a[1:]}
        for f in ib:
            if rb[f.s] <= 0 or f.a[0].s != e.a[0].s:
                continue
            db = {x.a[0].s: x.a[1].s for x in f.a[1:]}
            common = set(da) & set(db)
            if 'r3' in common and all(da[k] == db[k] for k in common if 'junk' not in (da[k], db[k])):
                ra[e.s] -= 1
                rb[f.s] -= 1
                break
    ra, rb = +ra, +rb
    return ea, eb, ra, rb


def pair(ea, eb, ra, rb):
    """Pair up differing effects for display: [(retail, ours)], either may be None."""
    la = [e for e in ea if ra[e.s] > 0 and not ra.subtract({e.s: 1})]
    lb = [e for e in eb if rb[e.s] > 0 and not rb.subtract({e.s: 1})]
    out, used = [], set()
    for e in la:
        best = None
        for j, f in enumerate(lb):
            if j in used or f.op != e.op:
                continue
            if e.op.startswith('st') and e.a[0].s != f.a[0].s:
                continue
            if e.op == 'call' and e.a[0].s != f.a[0].s:
                continue
            best = j
            break
        if best is None:
            best = next((j for j, f in enumerate(lb) if j not in used and f.op == e.op), None)
        if best is None:
            out.append((e, None))
        else:
            used.add(best)
            out.append((e, lb[best]))
    for j, f in enumerate(lb):
        if j not in used:
            out.append((None, f))
    return out


def tdiff(a, b, out, depth=0):
    """Smallest differing subterms of two expressions."""
    if a.s == b.s:
        return
    if a.op == b.op and len(a.a) == len(b.a) and a.a and depth < 30:
        ca, cb = list(a.a), list(b.a)
        for x in list(ca):
            for y in cb:
                if x.s == y.s:
                    ca.remove(x)
                    cb.remove(y)
                    break
        if len(ca) == len(cb):
            for x, y in zip(ca, cb):
                tdiff(x, y, out, depth + 1)
            return
    out.append((a, b))


def short(e, n=200):
    s = e.s if e is not None else '-'
    return s if len(s) <= n else s[:n] + '...'


_objs = {}

def getobj(p):
    o = _objs.get(p)
    if o is None:
        o = Obj(p)
        _objs[p] = o
    return o


_IDX = None

def retindex():
    global _IDX
    if _IDX is None:
        import pickle
        cache = os.path.join(B, 'GMSE01', 'expr-diff-ret.pickle')
        paths = sorted(os.path.join(r, f) for r, _, fs in os.walk(B + '/GMSE01/obj') for f in fs
                       if f.endswith('.o'))
        stamp = max((os.path.getmtime(p) for p in paths), default=0)
        try:
            with open(cache, 'rb') as fh:
                st, sets = pickle.load(fh)
            if st == stamp:
                idx = RetIndex()
                idx.r3, idx.f1, idx.called, idx.livein, idx.vt, idx.leaf, idx.slotregs = sets
                _IDX = idx
                return idx
        except Exception:
            pass
        idx = RetIndex()
        for p in paths:
            try:
                out = subprocess.run([OD, '-dr', '--no-show-raw-insn', '-M', 'raw,gekko', p],
                                     capture_output=True, text=True, errors='replace').stdout
            except Exception:
                continue
            cur = []
            name = None
            for l in out.split('\n'):
                m = FUNC_RE.match(l)
                if m:
                    idx.scan(cur, name)
                    cur = []
                    name = m.group(2)
                    continue
                m = REL_RE.match(l)
                if m and cur:
                    cur[-1][3] = (m.group(1), m.group(2))
                    continue
                m = INS_RE.match(l)
                if m:
                    cur.append([int(m.group(1), 16), m.group(2), re.sub(r' <.*>$', '', m.group(3)).strip(), None])
            idx.scan(cur, name)
            try:
                idx.scan_vtables(p)
            except Exception:
                pass
        # retail's split objects do not keep weak binding: take it from ours
        for r, _, fs in os.walk(B + '/GMSE01/src'):
            for f in fs:
                if f.endswith('.o'):
                    try:
                        idx.weak |= read_elf(os.path.join(r, f))[1].get('#weak', set())
                    except Exception:
                        pass
        idx.compute_leaves()
        idx.compute_livein()
        idx.compute_slots()
        idx.bodies = {}
        try:
            with open(cache, 'wb') as fh:
                pickle.dump((stamp, (idx.r3, idx.f1, idx.called, idx.livein, idx.vt, idx.leaf, idx.slotregs)), fh)
        except Exception:
            pass
        _IDX = idx
    return _IDX


def report(unit, name, oa, ob, verbose, out):
    idx = retindex()
    try:
        ea, eb, ra, rb = compare(oa, ob, name, idx)
    except Exception as ex:
        out.append((unit, name, 'ERROR', 0, 0, repr(ex)[:200]))
        return 'ERROR'
    na, nb = sum(ra.values()), sum(rb.values())
    if not na and not nb:
        out.append((unit, name, 'SAME', len(ea), 0, ''))
        return 'SAME'
    pairs = pair(ea, eb, ra, rb)
    lines = []
    for x, y in pairs:
        if x is not None and y is not None:
            d = []
            tdiff(x, y, d)
            lines.append('~ %s %s :: %s' % (x.op, short(x.a[0], 60) if x.a else '',
                                             ' | '.join('%s => %s' % (short(p, 150), short(q, 150)) for p, q in d[:3])))
        elif x is not None:
            lines.append('- ' + short(x, 300))
        else:
            lines.append('+ ' + short(y, 300))
    out.append((unit, name, 'DIFF', na, nb, ' || '.join(lines)))
    if verbose:
        print('DIFF %s %s  -%d +%d' % (unit, name, na, nb))
        for l in lines:
            print('    ' + l)
    return 'DIFF'


def exact_funcs(rel):
    oa = getobj(B + '/GMSE01/obj/' + rel)
    ob = getobj(B + '/GMSE01/src/' + rel)
    res = []
    for name, ia in oa.funcs.items():
        ib = ob.funcs.get(name)
        if ib is None or len(ia) != len(ib):
            continue
        if all(x[1] == y[1] and x[2] == y[2] for x, y in zip(ia, ib)):
            res.append(name)
    return oa, ob, res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--unit', help='Dir/Unit')
    ap.add_argument('--func', help='only this mangled symbol')
    ap.add_argument('--a', help='retail object (with --b)')
    ap.add_argument('--b', help='our object')
    ap.add_argument('--tsv', help='semantic-diff --tsv output: run over its functions')
    ap.add_argument('--cls', help='with --tsv: only this semantic-diff class (CLEAN, ARITH, ...)')
    ap.add_argument('--exact', type=int, help='self-check on up to N byte-identical functions')
    ap.add_argument('--out', help='write unit, symbol, verdict, counts, details as TSV')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()
    out = []
    cnt = collections.Counter()
    if args.a:
        oa, ob = Obj(args.a), Obj(args.b)
        names = [args.func] if args.func else [n for n in oa.funcs if n in ob.funcs]
        for n in names:
            cnt[report(args.a, n, oa, ob, True, out)] += 1
    elif args.exact:
        rels = sorted(os.path.relpath(os.path.join(r, f), B + '/GMSE01/obj')
                      for r, _, fs in os.walk(B + '/GMSE01/obj') for f in fs if f.endswith('.o'))
        rels = [r for r in rels if os.path.exists(B + '/GMSE01/src/' + r)]
        # spread the sample over the tree
        step = max(1, len(rels) // 150)
        done = 0
        for r in rels[::step]:
            oa, ob, names = exact_funcs(r)
            for n in names[:6]:
                cnt[report(r[:-2], n, oa, ob, args.verbose, out)] += 1
                done += 1
            if done >= args.exact:
                break
    else:
        todo = []
        if args.tsv:
            for l in open(args.tsv):
                p = l.rstrip('\n').split('\t')
                if args.cls and p[3] != args.cls:
                    continue
                if args.unit and p[0] != args.unit:
                    continue
                todo.append((p[0], p[1]))
        else:
            oa = getobj(B + '/GMSE01/obj/' + args.unit + '.o')
            ob = getobj(B + '/GMSE01/src/' + args.unit + '.o')
            for n, ia in oa.funcs.items():
                if args.func and n != args.func:
                    continue
                ib = ob.funcs.get(n)
                if ib is None:
                    continue
                if not args.func and all(x[1] == y[1] and x[2] == y[2] for x, y in zip(ia, ib)) \
                        and len(ia) == len(ib):
                    continue
                todo.append((args.unit, n))
        for u, n in todo:
            oa = getobj(B + '/GMSE01/obj/' + u + '.o')
            ob = getobj(B + '/GMSE01/src/' + u + '.o')
            if n not in oa.funcs or n not in ob.funcs:
                continue
            cnt[report(u, n, oa, ob, args.verbose, out)] += 1
    if args.out:
        with open(args.out, 'w') as f:
            for r in out:
                f.write('\t'.join(str(x) for x in r) + '\n')
    print(dict(cnt), file=sys.stderr)


if __name__ == '__main__':
    main()
