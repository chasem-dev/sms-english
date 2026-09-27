#!/usr/bin/env python3
# cmpcensus.py BEFORE.tsv AFTER.tsv [N]: exact count and per-function up/down between two census.py runs
import sys
def load(p):
    d={}
    for l in open(p):
        u,s,fa,fb,st,n=l.rstrip('\n').split('\t'); d[(u,s)]=(int(fa),int(fb),st)
    return d
a=load(sys.argv[1]); b=load(sys.argv[2])
rank={'exact':3,'slots':2,'frame':1,'other':0}
up=[];down=[]
for k in a:
    if k not in b: down.append((k,'MISSING')); continue
    ra,rb=a[k],b[k]
    if ra==rb: continue
    # score: exact>slots>frame>other; within frame class, closeness of frame
    sa=(rank[ra[2]], -abs(ra[0]-ra[1]) if ra[2]!='other' else 0)
    sb=(rank[rb[2]], -abs(rb[0]-rb[1]) if rb[2]!='other' else 0)
    if sb>sa: up.append((k,ra,rb))
    elif sb<sa: down.append((k,ra,rb))
from collections import Counter
print('exact: %d -> %d'%(sum(v[2]=='exact' for v in a.values()),sum(v[2]=='exact' for v in b.values())))
print('up',len(up),'down',len(down))
for x in up[:int(sys.argv[3]) if len(sys.argv)>3 else 15]: print('UP  ',x[0][1][:60],x[1],x[2])
for x in down[:int(sys.argv[3]) if len(sys.argv)>3 else 15]: print('DOWN',x[0][1][:60],x[1:])
