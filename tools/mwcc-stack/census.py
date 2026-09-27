#!/usr/bin/env python3
# Run from the repo root (or a worktree) after a full build.
# census.py [out.tsv] : per function: unit, sym, retail frame, our frame, status
# status: exact | frame (insns equal modulo r1 displacements, frame differs) | slots (equal frame, r1 offsets differ) | other
import subprocess, sys, os, re, concurrent.futures as cf
B=os.path.join(os.getcwd(), 'build')
OD=B+'/binutils/powerpc-eabi-objdump'
def funcs(path):
    try: out=subprocess.run([OD,'-d','--no-show-raw-insn',path],capture_output=True,text=True).stdout
    except Exception: return {}
    res={}; cur=None
    for l in out.split('\n'):
        m=re.match(r'^[0-9a-f]+ <(.*)>:$',l)
        if m: cur=m.group(1); res[cur]=[]; continue
        if cur and '\t' in l:
            ins=l.split('\t',1)[1].strip()
            ins=re.sub(r'\s+[0-9a-f]+ <.*>$','',ins)
            ins=re.sub(r'<.*>','',ins)
            res[cur].append(ins)
    return res
def norm(ins):
    return re.sub(r'-?\d+\(r1\)','X(r1)',re.sub(r'(r1,)-?\d+$',r'\1X',ins))
def frame(lst):
    for i in lst[:8]:
        m=re.match(r'stwu\s+r1,-(\d+)\(r1\)',i)
        if m: return int(m.group(1))
    return 0
def unit(rel):
    a=funcs(B+'/GMSE01/obj/'+rel); b=funcs(B+'/GMSE01/src/'+rel)
    rows=[]
    for s,ai in a.items():
        bi=b.get(s)
        if bi is None: continue
        fa,fb=frame(ai),frame(bi)
        if ai==bi: st='exact'
        elif len(ai)==len(bi) and all(norm(x)==norm(y) for x,y in zip(ai,bi)):
            st='frame' if fa!=fb else 'slots'
        else: st='other'
        rows.append((rel,s,fa,fb,st,len(ai)))
    return rows
rels=[]
for root,_,fs in os.walk(B+'/GMSE01/obj'):
    for f in fs:
        if f.endswith('.o'):
            rel=os.path.relpath(os.path.join(root,f),B+'/GMSE01/obj')
            if os.path.exists(B+'/GMSE01/src/'+rel): rels.append(rel)
rows=[]
with cf.ThreadPoolExecutor(2) as ex:
    for r in ex.map(unit,sorted(rels)): rows+=r
out=sys.argv[1] if len(sys.argv)>1 else '/dev/stdout'
with open(out,'w') as f:
    for r in rows: f.write('\t'.join(map(str,r))+'\n')
from collections import Counter
print(Counter(r[4] for r in rows))
