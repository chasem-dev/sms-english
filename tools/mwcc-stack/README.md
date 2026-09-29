# mwcc-stack: see MWCC's own stack objects

These tools support frame-gap work.
A frame gap is a function whose instructions match but whose `stwu r1` frame or `r1` slot offsets differ.
The model they support is in `docs/catalog/frame-model.md`.

## Native Linux setup

Native GDB can inspect the compiler while Wibo executes its x86 code.
This needs GDB, the existing non-stripped Wibo, GC/1.1, and the public [cadmic debugger](https://github.com/cadmic/mwcc-debugger).
It does not need a separate emulator or Rust runtime.

```sh
export MWCC_DEBUGGER=/path/to/mwcc-debugger/mwcc_debugger.py
python3 tools/mwcc-stack/native.py src/Enemy/gesso.cpp GessoBodyCallback__FP7J3DNodei /tmp/gesso-dump
python3 tools/mwcc-stack/iro.py /tmp/gesso-dump
```

The adapter reads the selected unit's compiler command from Ninja, keeps its optimization flags and include paths, and removes the incompatible precompiled header before using GC/1.1.
It patches a temporary copy of the external debugger to include `names.txt`, and leaves the original debugger untouched.
Choose an empty output directory for each dump.
The debugger stops at the selected function, so its output is inspection evidence rather than a completed build object.
If ptrace is restricted by the execution sandbox, request the required execution permission.

GessoBodyCallback's native dump took about one second on this machine.
Its matrices and conversion spill agree with production GC/1.2.5; moving the clamp into the actual rotation computation closed the production function exactly.
Verify each function with its production compiler before treating a GC/1.1 observation as conclusive.

## Remote emulator setup

```
git clone https://github.com/cadmic/mwcc-debugger
git clone https://github.com/encounter/retrowin32 && cd retrowin32 && git checkout gdb-stub
cargo build -p retrowin32 -F x86-unicorn --profile lto   # needs gdb, cmake, Rust
export MWCC_DEBUGGER=/path/to/mwcc-debugger/mwcc_debugger.py RETROWIN32=/path/to/retrowin32/target/lto/retrowin32
```

The debugger supports GC/1.1.
For every function measured so far, 1.1 gives the same frames as our GC/1.2.5 (jumpingBasic 0x58, TApplication::proc 0x78, moveRequest 0x70).

## Tools

- `dbg.sh SRC MANGLED_SYM OUTDIR` (optionally `OVL=<header overlay dir>`) takes about 35 s for one function.
  In `OUTDIR` it writes these files:
  - `variables.txt`: every argument and local object, with its name (`@NNN` is a compiler-created object) and either its `r1` range or the register it got.
  - `frontend-00-ast-initial-code.txt`: the AST after inlining, which shows which inline expansion created each `@NNN`.
  - `frontend-01-*`: the AST after the IR optimiser.
    An `@NNN` that is absent from `frontend-00` was created by the optimiser.
- `regalloc.py DUMPDIR UNIT SYMBOL [gpr|fpr]` replays MWCC's register colouring from a `dbg.sh` dump and lists the webs whose register differs from retail, with options to test a colouring order or removed interference edges.
  The model it implements is `docs/catalog/register-model.md`.
  `--why V` prints web V's remaining degree when the first simplify sweep reaches it and the lower-numbered neighbours pushed before it.
- `regsweep.py LIST OUTDIR` dumps every function of a TSV (unit, mangled symbol) with the unit's own flags, one debugger at a time, and runs `regalloc.py --search` on both register classes; `--report` ranks the rows with a single-move fix and an equal frame first, each fix annotated with its `--why` count.
  Runs of other debugger users share the gdb stub's port, so a dump that loses the connection is retried; `--resume` re-runs only the failed ones.
- `census.py [out.tsv]`: classifies every function of every source-built unit.
  The classes are `exact`, `frame` (equal modulo `r1` displacements, but the frame differs), `slots` (same frame, but `r1` offsets differ) and `other`.
  It takes about 8 s after a full build.
- `cmpcensus.py BEFORE.tsv AFTER.tsv [N]`: prints the exact counts and the functions that moved up or down between two census runs.
  Use it to price a shared-header lever across the whole tree in about a minute.
- `inv.py`: for every source-built function with the same instruction count as retail, counts the stack-object pairs retail orders the reverse of ours (`reversed/total  unit  symbol`).
  A block whose objects are reversed was an inlined callee in retail; see `docs/catalog/frame-model.md`.
- `ns.py UNIT SYMBOL [-v]`: counts the diff lines that are not pure `r1` offset differences, so register and instruction gains are visible under a frame gap.
- `patch-debugger.py MWCC_DEBUGGER_PY` patches a copy of the debugger so every dump also writes `names.txt` (which compiler pass made each `@NNN`).
- `iro.py DUMPDIR` tags each local of a patched dump as named, inliner object or IR-optimiser temporary (F/P/S/L/C), counts dead words per kind and attributes each F/P temporary to its source line.
  The kinds are described in `docs/catalog/frame-model.md` ("IRO temporaries").
