#!/usr/bin/env python3
# udbg.py UNIT SYM OUT : dump with the unit's own flags (run from repo root)
import os, sys, tempfile, time
sys.path.insert(0, "tools/mwcc-stack")
os.environ.setdefault("MWCC_DEBUGGER", "/tmp/claude-0/-home-user/5237deed-2ca6-5250-aaf6-949252dd8e4b/scratchpad/k2/mwcc_debugger.py")
os.environ.setdefault("RETROWIN32", "/tmp/claude-0/-home-user/5237deed-2ca6-5250-aaf6-949252dd8e4b/scratchpad/retrowin32/target/lto/retrowin32")
import regsweep
unit, sym, out = sys.argv[1:4]
if os.environ.get("VSRC"):
    _uf = regsweep.unit_flags
    def uf(u):
        src, fl, pre = _uf(u)
        return os.environ["VSRC"], fl, pre
    regsweep.unit_flags = uf
work = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(out)))
os.makedirs(out, exist_ok=True)
t = time.time()
ok = regsweep.dump(unit, sym, out, work, sys.argv[4] if len(sys.argv) > 4 else "1.1")
print("ok" if ok else "FAIL", round(time.time() - t, 1), "s")
if not ok:
    print(open(out + ".log").read()[-1500:])
