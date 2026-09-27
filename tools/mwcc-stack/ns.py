#!/usr/bin/env python3
# usage: ns.py UNIT SYM [-v]   -> counts non-stack diff lines (and prints them with -v)
import subprocess,sys,re
unit,sym=sys.argv[1],sys.argv[2]
verbose='-v' in sys.argv
out=subprocess.run(['python3','tools/decomp-diff.py','-u','mario/'+unit,'-d',sym,'--no-collapse'],capture_output=True,text=True).stdout
lines=out.splitlines()
n=0;stack=0;res=[]
for l in lines:
    m=re.match(r'^([~|<>])\s+(\w+) \| (.*?)\s*\| (.*)$',l)
    if not m: continue
    k,off,L,R=m.groups()
    if k=='~':
        a=re.sub(r'\{[^}]*\}','X',L).strip(); b=re.sub(r'\{[^}]*\}','X',R).strip()
        if a==b and 'r1' in L:
            stack+=1; continue
    n+=1; res.append(l)
print(f"{lines[0] if lines else ''}  nonstack={n} stack={stack}")
if verbose:
    print('\n'.join(res))
