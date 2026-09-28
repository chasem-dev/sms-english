#!/usr/bin/env python3
"""Triage non-exact functions by whether their difference from retail can change behaviour.

Run from the repo root (or a worktree) after a full build:

    tools/semantic-diff.py [--unit Dir/Unit] [--func SYMBOL] [--all] [-v] [--tsv OUT]

For every source-built function that is not byte-identical to retail (the
census "other" class by default; --all adds the frame/slots classes), both
versions are disassembled with relocations and reduced to order- and
register-independent feature multisets:

  call   functions called (R_PPC_REL24 targets)
  data   globals referenced by name, and pooled constants by VALUE (the @NNNN
         names differ between builds, so .sdata2/.rodata literals are
         compared as their bytes)
  field  (load/store mnemonic, displacement) for every memory access whose
         base is not r1 (the stack) - struct field offsets
  addr   addi displacements on non-stack bases (&obj->field)
  imm    integer immediates (li/lis/cmpwi/cmplwi/andi./ori/xori/mulli/
         rlwinm/srawi masks and shifts)
  cmp    comparison kinds (cmpw/cmplw/cmpwi/cmplwi/fcmpu/fcmpo)
  op     arithmetic/conversion opcodes (moves, branches and stack traffic
         excluded)

A difference in call/data/field/addr/imm/cmp is reported as SEMANTIC (it can
change what the function does: a wrong constant, member, callee, signedness).
A difference only in op is reported as ARITH (e.g. fmadds contraction, an
extra extsh/clrlwi - usually benign, sometimes a width/sign bug). Equal
multisets are CLEAN: the difference is register choice, scheduling or stack
layout only. INLINE marks call deltas to weak (header/template) functions
only: a body expanded on one side and called on the other. CLEAN is strong evidence of equivalence, not proof; SEMANTIC is
a lead that needs a human/agent look (inlining differences also show up as
call deltas).
"""
import argparse, collections, concurrent.futures as cf, os, re, struct, subprocess, sys

ROOT = os.getcwd()
B = os.path.join(ROOT, 'build')
OD = os.path.join(B, 'binutils', 'powerpc-eabi-objdump')


# ---------------------------------------------------------------- ELF reading
def read_elf(path):
    """Return (sections, symbols) for a big-endian ELF32 relocatable."""
    with open(path, 'rb') as f:
        d = f.read()
    shoff, = struct.unpack_from('>I', d, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from('>HHH', d, 0x2e)
    secs = []
    for i in range(shnum):
        name, typ, flags, addr, off, size, link, info, align, entsize = struct.unpack_from(
            '>IIIIIIIIII', d, shoff + i * shentsize)
        secs.append(dict(name=name, type=typ, off=off, size=size, link=link, entsize=entsize))
    shstr = secs[shstrndx]
    def sname(o):
        s = shstr['off'] + o
        return d[s:d.index(b'\0', s)].decode('latin1')
    for s in secs:
        s['name'] = sname(s['name'])
        s['data'] = d[s['off']:s['off'] + s['size']] if s['type'] != 8 else b''
    syms = {}
    for s in secs:
        if s['type'] != 2:
            continue
        strtab = secs[s['link']]
        for j in range(s['size'] // 16):
            nm, val, size, info, other, shndx = struct.unpack_from('>IIIBBH', d, s['off'] + j * 16)
            o = strtab['off'] + nm
            n = d[o:d.index(b'\0', o)].decode('latin1')
            if n and n not in syms:
                syms[n] = (shndx, val, size)
            if n and (info >> 4) == 2 and shndx != 0:
                syms.setdefault('#weak', set()).add(n)
            if n and (info >> 4) == 0 and shndx != 0:
                syms.setdefault('#local', set()).add(n)
    return secs, syms


def const_value(secs, syms, name, addend=0):
    """Bytes of a pooled literal, or None when it is not a small constant."""
    if name.startswith('.'):
        sec = next((s for s in secs if s['name'] == name), None)
        if sec is None:
            return None
        return sec['data'][addend:addend + 4].hex()
    t = syms.get(name)
    if not t:
        return None
    shndx, val, size = t
    if shndx == 0 or shndx >= len(secs):
        return None
    sec = secs[shndx]
    if sec['name'] not in ('.sdata2', '.rodata', '.sdata') or size not in (4, 8):
        return None
    return sec['data'][val + addend:val + addend + size].hex()


# ---------------------------------------------------------------- disassembly
FUNC_RE = re.compile(r'^[0-9a-f]+ <(.*)>:$')
INS_RE = re.compile(r'^\s*([0-9a-f]+):\t(\S+)\s*(.*)$')
REL_RE = re.compile(r'^\s*[0-9a-f]+: (R_PPC_\S+)\s+(\S+)$')

def disasm(path):
    try:
        out = subprocess.run([OD, '-dr', '--no-show-raw-insn', path],
                             capture_output=True, text=True).stdout
    except Exception:
        return {}
    res = {}
    cur = None
    for l in out.split('\n'):
        m = FUNC_RE.match(l)
        if m:
            cur = m.group(1)
            res[cur] = []
            continue
        if cur is None:
            continue
        m = REL_RE.match(l)
        if m and res[cur]:
            res[cur][-1][2].append((m.group(1), m.group(2)))
            continue
        m = INS_RE.match(l)
        if m:
            ops = re.sub(r'[0-9a-f]+ <[^>]*?(\+0x[0-9a-f]+)?>', lambda g: g.group(1) or '', m.group(3)).strip()
            res[cur].append([m.group(2), ops, []])
    return res


MEM = re.compile(r'^(l|st)(bz|bzu|hz|hzu|ha|hau|wz|wzu|fs|fsu|fd|fdu|mw|b|h|w|bu|hu|wu)$')
DISP = re.compile(r'^(\w+),(-?\d+)\((r\d+)\)$')
IMM_OPS = {'li', 'lis', 'cmpwi', 'cmplwi', 'andi.', 'andis.', 'ori', 'oris', 'xori', 'xoris',
           'mulli', 'rlwinm', 'rlwinm.', 'rlwimi', 'srawi', 'srawi.', 'slwi', 'srwi', 'clrlwi',
           'clrrwi', 'subfic', 'addic', 'addic.', 'extrwi', 'rotlwi', 'clrlslwi', 'insrwi'}
CMP_OPS = {'cmpw', 'cmplw', 'cmpwi', 'cmplwi', 'fcmpu', 'fcmpo'}
SKIP_OPS = {'mr', 'mr.', 'fmr', 'nop', 'mflr', 'mtlr', 'mfcr', 'mtctr', 'mfctr', 'blr', 'bctr',
            'bctrl', 'b', 'bl', 'isync', 'sync'}

def features(insns, secs, syms):
    f = collections.defaultdict(collections.Counter)
    for mn, ops, rels in insns:
        relsym = None
        for rt, target in rels:
            m = re.match(r'^(.*?)(?:\+0x([0-9a-f]+))?$', target)
            name, add = m.group(1), int(m.group(2) or '0', 16)
            if rt == 'R_PPC_REL24':
                f['call'][name] += 1
            elif rt in ('R_PPC_EMB_SDA21', 'R_PPC_ADDR16_LO', 'R_PPC_ADDR16_HA', 'R_PPC_ADDR16_HI',
                        'R_PPC_ADDR16', 'R_PPC_ADDR32'):
                if rt == 'R_PPC_ADDR16_HA':
                    relsym = 'ha'
                    continue
                v = const_value(secs, syms, name, add)
                if v is not None:
                    f['data']['const=' + v] += 1
                elif (name.startswith('@') or name.startswith('...') or name.startswith('.')
                      or name in syms.get('#local', ())):
                    f['data']['local'] += 1          # string/table: compared by count only
                else:
                    name = re.sub(r'\$\d+$', '$N', name)   # init$1143 vs init$2855
                    f['data'][name + ('+%#x' % add if add else '')] += 1
                relsym = 'lo'
        if mn.startswith('b') and mn not in ('bl',):
            if mn not in ('b', 'blr', 'bctr', 'bctrl'):
                f['op']['bcond'] += 1               # count only: polarity flips with layout
            continue
        if mn in SKIP_OPS:
            continue
        m = MEM.match(mn)
        if m:
            dm = DISP.match(ops.replace(' ', ''))
            if dm and dm.group(3) not in ('r1',) and not relsym:
                # r2/r13 small-data accesses carry a relocation and are handled above
                if dm.group(3) not in ('r0', 'r2', 'r13'):
                    f['field']['%s %d' % (mn.rstrip('u'), int(dm.group(2)))] += 1
            if not dm:
                f['op'][mn] += 1                    # indexed forms (lwzx...)
            continue
        if mn in ('addi', 'subi'):
            p = [x.strip() for x in ops.split(',')]
            if len(p) == 3 and p[1] not in ('r1',) and not relsym and p[1] not in ('r2', 'r13'):
                try:
                    v = int(p[2], 0) * (-1 if mn == 'subi' else 1)
                    if p[1] == 'r0':
                        f['imm']['li %d' % v] += 1
                    elif v != 0:                     # addi rD,rS,0 is MWCC's spelling of mr
                        f['addr'][str(v)] += 1
                except ValueError:
                    pass
            continue
        if mn in CMP_OPS:
            f['cmp'][mn.replace('i', '') if mn in ('cmpwi', 'cmplwi') else mn] += 1
        if mn in IMM_OPS:
            p = [x.strip() for x in ops.split(',')]
            nums = []
            for x in p[1:]:
                try:
                    nums.append(int(x, 0))
                except ValueError:
                    pass
            if nums and not relsym:
                f['imm']['%s %s' % (mn.rstrip('.'), ','.join(map(str, nums)))] += 1
            if mn in ('cmpwi', 'cmplwi', 'li', 'lis'):
                continue
        f['op'][mn.rstrip('.')] += 1
    return f


def delta(a, b):
    out = []
    for k in sorted(set(a) | set(b), key=str):
        if a[k] != b[k]:
            out.append('%s %+d' % (k, b[k] - a[k]))
    return out


SEM_KEYS = ('call', 'data', 'field', 'addr', 'imm', 'cmp')

def offsets(f):
    s = set()
    for k in f['field']:
        v = int(k.split()[1])
        if v:
            s.add(v)
    for k in f['addr']:
        s.add(int(k))
    return s

def widths(f):
    return {re.sub(r'x$', '', k.split()[0]) for k in f['field']}

def imm_core(f):
    # bool materialisation (li 0 / li 1) moves freely between arms
    return collections.Counter({k: v for k, v in f['imm'].items() if k not in ('li 0', 'li 1')})

def classify(fa, fb, weak=frozenset()):
    d = {k: delta(fa[k], fb[k]) for k in SEM_KEYS + ('op',)}
    sem = {
        'call': bool(d['call']),
        'data': bool(d['data']),
        'cmp': bool(d['cmp']),
        'offset': offsets(fa) != offsets(fb),
        'width': widths(fa) != widths(fb),
        'imm': imm_core(fa) != imm_core(fb),
    }
    d['why'] = [k for k, v in sem.items() if v]
    if any(sem.values()):
        # A call that appears on one side only, to a weak (header inline /
        # template) function, is an inlining decision; if nothing but calls
        # to such functions, field/addr/imm/op traffic differs, the body was
        # merely expanded or called.
        calls = set(fa['call']) ^ set(fb['call']) | {k for k in fa['call'] if fa['call'][k] != fb['call'][k]}
        if calls and all(c in weak for c in calls) and not sem['data'] and not sem['cmp']:
            return 'INLINE', d
        return 'SEMANTIC', d
    if d['op']:
        return 'ARITH', d
    return 'CLEAN', d


# ---------------------------------------------------------------- driver
def census_rows(want_all):
    rows = []
    for root, _, fs in os.walk(B + '/GMSE01/obj'):
        for fn in fs:
            if fn.endswith('.o'):
                rel = os.path.relpath(os.path.join(root, fn), B + '/GMSE01/obj')
                if os.path.exists(B + '/GMSE01/src/' + rel):
                    rows.append(rel)
    return sorted(rows)


def run_unit(rel, want_all, only):
    a = disasm(B + '/GMSE01/obj/' + rel)
    b = disasm(B + '/GMSE01/src/' + rel)
    try:
        ea = read_elf(B + '/GMSE01/obj/' + rel)
        eb = read_elf(B + '/GMSE01/src/' + rel)
    except Exception:
        return []
    out = []
    for sym, ai in a.items():
        if only and sym != only:
            continue
        bi = b.get(sym)
        if bi is None:
            continue
        ta = [(x[0], x[1]) for x in ai]
        tb = [(x[0], x[1]) for x in bi]
        if ta == tb:
            continue                                  # byte-identical (pool names may differ)
        if not want_all and len(ta) == len(tb) and all(
                re.sub(r'-?\d+\(r1\)', 'X', p[1]) == re.sub(r'-?\d+\(r1\)', 'X', q[1]) and p[0] == q[0]
                for p, q in zip(ta, tb)):
            continue                                  # frame/slots only
        fa = features(ai, *ea)
        fb = features(bi, *eb)
        weak = ea[1].get('#weak', set()) | eb[1].get('#weak', set())
        cls, d = classify(fa, fb, weak)
        out.append((rel[:-2], sym, len(ai), cls, d))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--unit', help='Dir/Unit (default: every source-built unit)')
    ap.add_argument('--func', help='only this mangled symbol')
    ap.add_argument('--all', action='store_true', help='include frame/slots-only functions')
    ap.add_argument('-v', '--verbose', action='store_true', help='print the feature deltas')
    ap.add_argument('--tsv', help='write unit, symbol, size, class, deltas to this file')
    args = ap.parse_args()
    rels = [args.unit + '.o'] if args.unit else census_rows(args.all)
    rows = []
    with cf.ThreadPoolExecutor(2) as ex:
        for r in ex.map(lambda u: run_unit(u, args.all, args.func), rels):
            rows += r
    order = {'SEMANTIC': 0, 'INLINE': 1, 'ARITH': 2, 'CLEAN': 3}
    rows.sort(key=lambda r: (order[r[3]], r[0], r[1]))
    if args.tsv:
        with open(args.tsv, 'w') as f:
            for u, s, n, c, d in rows:
                f.write('\t'.join([u, s, str(n * 4), c,
                                   ' | '.join('%s: %s' % (k, '; '.join(v)) for k, v in d.items() if v)]) + '\n')
    for u, s, n, c, d in rows:
        if args.verbose or args.func or args.unit:
            print('%-8s %s %s' % (c, u, s))
            for k, v in d.items():
                if v:
                    print('    %-5s %s' % (k, '; '.join(v)))
    print(collections.Counter(r[3] for r in rows), file=sys.stderr)


if __name__ == '__main__':
    main()
