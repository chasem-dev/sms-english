#!/usr/bin/env python3
# inv.py [out.txt]: per function, how many stack-object pairs retail orders the reverse of ours (same instruction count).
# A block whose pairs are reversed was an inlined callee in retail (see docs/catalog/frame-model.md). Run from the repo root after a full build.
import sys,subprocess,re,os,concurrent.futures as cf
R=os.getcwd();OD=R+'/build/binutils/powerpc-eabi-objdump'
def funcs(p):
    out=subprocess.run([OD,'-d','--no-show-raw-insn',p],capture_output=True,text=True).stdout
    res={};cur=None
    for l in out.split('\n'):
        m=re.match(r'^[0-9a-f]+ <(.*)>:$',l)
        if m: cur=m.group(1);res[cur]=[];continue
        if cur and '\t' in l: res[cur].append(l.split('\t',1)[1].strip())
    return res
def unit(rel):
    a=funcs(R+'/build/GMSE01/obj/'+rel); b=funcs(R+'/build/GMSE01/src/'+rel); out=[]
    for s,ai in a.items():
        bi=b.get(s)
        if not bi or len(ai)!=len(bi) or ai==bi: continue
        pa=[];pb=[]
        for x,y in zip(ai,bi):
            mx=re.match(r'addi\s+r(\d+),r1,(\d+)$',x); my=re.match(r'addi\s+r(\d+),r1,(\d+)$',y)
            if mx and my and mx.group(1)!='1': pa.append(int(mx.group(2))); pb.append(int(my.group(2)))
        # distinct objects: dedupe by retail offset
        d={}
        for x,y in zip(pa,pb): d.setdefault(x,y)
        ks=sorted(d); inv=0;tot=0
        for i in range(len(ks)):
            for j in range(i+1,len(ks)):
                tot+=1
                if d[ks[i]]>d[ks[j]]: inv+=1
        if inv: out.append((rel,s,inv,tot))
    return out
rels=[]
for root,_,fs in os.walk(R+'/build/GMSE01/obj'):
    for f in fs:
        if f.endswith('.o'):
            rel=os.path.relpath(os.path.join(root,f),R+'/build/GMSE01/obj')
            if os.path.exists(R+'/build/GMSE01/src/'+rel): rels.append(rel)
with cf.ThreadPoolExecutor(2) as ex:
    for r in ex.map(unit,sorted(rels)):
        for x in r: print(f'{x[2]:4}/{x[3]:<5} {x[0]:30} {x[1][:70]}')
