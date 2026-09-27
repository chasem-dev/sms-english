#!/usr/bin/env python3
# patch-debugger.py MWCC_DEBUGGER_PY : make mwcc_debugger.py (GC/1.1) also write
# names.txt, one line per compiler-generated @NNN name created while the dumped
# function is compiled: "@NNN phase return-addresses...", phase being inline
# (before the IR optimiser), iro (inside it) or post. iro.py reads it.
# Patch a copy, not a debugger another agent is running. Idempotent.
import sys

p = sys.argv[1]
s = open(p).read()
if 'names.txt' in s:
    print('already patched')
    sys.exit(0)
old = '    gdb.execute(f"break *{MWCC_VERSION.codegen_end_addr:#x}")\n'
new = old + '''    # IRO_Optimizer call and return in CodeGen, and CParser_GetUniqueName
    gdb.execute("break *0x43538e")
    gdb.execute("break *0x435393")
    gdb.execute("break *0x4904c0")
    phase = "inline"
    names_f = open(Path(OUTPUT_DIR) / "names.txt", "w")
'''
old2 = '        if current_addr == MWCC_VERSION.codegen_end_addr:\n'
new2 = '''        if current_addr == 0x43538E:
            phase = "iro"
        if current_addr == 0x435393:
            phase = "post"
        if current_addr == 0x4904C0:
            sp = int(gdb.parse_and_eval("$esp"))
            num = read_u32(0x581DC0)  # the unique-name counter
            mem = gdb.selected_inferior().read_memory(sp, 4 * 64)
            chain = []
            for i in range(64):
                v = parse_u32(mem, 4 * i)
                if 0x401000 <= v < 0x535000:
                    chain.append(f"{v:x}")
            print(f"@{num} {phase} {' '.join(chain)}", file=names_f)
            names_f.flush()
''' + old2
assert old in s and old2 in s, 'unexpected mwcc_debugger.py'
s = s.replace(old, new, 1).replace(old2, new2, 1)
open(p, 'w').write(s)
print('patched')
