#!/usr/bin/env python3
# iro.py DUMPDIR : classify every local object of a dbg.sh dump (needs names.txt,
# written by a debugger patched with patch-debugger.py) and attribute the IR
# optimiser's forced-load (F) and comma (P) temporaries to their source lines.
#
# Kinds: named = a local of the function; inline = created before IRO (inliner
# bindings, callee locals, parse-time temporaries); F/P/S/L/C/T/B = created by
# IRO (see docs/catalog/frame-model.md, "IRO temporaries").
import glob, re, sys

# GC/1.1 IRO functions that create temporaries (entry, end), keyed by the
# return address found second on the stack when GetUniqueName is entered.
CREATORS = [
    (0x430C10, 0x430D30, 'F'),  # forced load of an inline's return value
    (0x431330, 0x4314F0, 'P'),  # value of a comma expression
    (0x430E50, 0x430F70, 'T'),  # ?: value
    (0x4311F0, 0x431330, 'B'),  # && / || value
    (0x44AAF0, 0x44ADD0, 'S'),  # scalar replacement of a small class object
    (0x45BED0, 0x45C020, 'L'),  # loop unrolling
    (0x45C160, 0x45C510, 'L'),
    (0x44E170, 0x44E460, 'C'),  # common subexpressions
    (0x459620, 0x4597A0, 'X'),  # struct copy
]
TEMP_OBJECT_RET = 0x47C263  # GetUniqueName called from the temp-object constructor


def kind_of(chain):
    if len(chain) < 2 or chain[0] != TEMP_OBJECT_RET:
        return None  # a label or other non-object name
    for lo, hi, k in CREATORS:
        if lo <= chain[1] < hi:
            return k
    return '?%x' % chain[1]


def main(o):
    kinds = {}
    iro_order = []
    for line in open(o + '/names.txt'):
        p = line.split()
        if len(p) < 3:
            continue
        chain = [int(x, 16) for x in p[2:]]
        if p[1] == 'iro':
            k = kind_of(chain)
            if k:
                kinds[p[0]] = k
                iro_order.append((p[0], k))
        elif chain and chain[0] == TEMP_OBJECT_RET:
            kinds[p[0]] = 'inline'
    rows = []
    sec = None
    for line in open(o + '/variables.txt'):
        s = line.rstrip()
        if s.strip().endswith(':'):
            sec = s.strip()
            continue
        if sec != 'locals:':
            continue
        nm = s.split()[-1]
        m = re.search(r'r1\+0x([0-9a-f]+)-0x([0-9a-f]+)', s)
        where = 'r1+0x%s' % m.group(1) if (m and '->' not in s) else 'reg'
        k = kinds.get(nm, 'inline' if nm.startswith('@') else 'named')
        rows.append((nm, k, where))
    dead = [r for r in rows if r[2] != 'reg']
    print('locals (top of the list is created first):')
    for nm, k, where in rows:
        print('  %-8s %-6s %s' % (nm, k, where))
    count = {}
    for nm, k, where in dead:
        count[k] = count.get(k, 0) + 1
    print('dead words by kind:', ' '.join('%s=%d' % kv for kv in sorted(count.items())))

    # F/P attribution: IRO linearises each statement's tree bottom-up, making an
    # F for every EFORCELOAD and a P for every ECOMMA whose value is used.
    fps = [(nm, k) for nm, k in iro_order if k in 'FP']
    f00 = glob.glob(o + '/frontend-00*')[0]
    seq = []
    stack = []
    stmt = None
    for line in open(f00):
        if not line.strip():
            continue
        ind = len(line) - len(line.lstrip(' '))
        t = line.strip()
        m = re.match(r'(\d+) ST_', t)
        if m or t.startswith('ST_'):
            stmt = m.group(1) if m else '?'
            stack = [(ind, [t, [], stmt])]
            seq.append(stack[0][1])
            continue
        node = [t, [], stmt]
        while stack and stack[-1][0] >= ind:
            stack.pop()
        stack[-1][1][1].append(node)
        stack.append((ind, node))
    out = []

    def post(n, used):
        t, kids, st = n
        if t.startswith('ECOMMA'):
            post(kids[0], False)
            for k in kids[1:]:
                post(k, used)
            if used:
                out.append(('P', st, t))
            return
        for k in kids:
            post(k, not t.startswith('ST_EXPRESSION'))
        if t.startswith('EFORCELOAD'):
            out.append(('F', st, t))
    for root in seq:
        post(root, False)
    where = {nm: w for nm, k, w in rows}
    ok = [k for _, k in fps] == [k for k, _, _ in out]
    print('F/P temporaries by line%s:' % ('' if ok else ' (approximate: tree and IRO counts differ)'))
    for (nm, k), (_, st, t) in zip(fps, out):
        print('  %-8s %s line %-5s %-10s %s' % (nm, k, st, where.get(nm, '?'), t))


if __name__ == '__main__':
    main(sys.argv[1])
