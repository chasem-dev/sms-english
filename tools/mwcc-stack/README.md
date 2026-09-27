# mwcc-stack: see MWCC's own stack objects

These tools support frame-gap work.
A frame gap is a function whose instructions match but whose `stwu r1` frame or `r1` slot offsets differ.
The model they support is in `docs/catalog/frame-model.md`.

## Setup (once per machine)

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
- `census.py [out.tsv]`: classifies every function of every source-built unit.
  The classes are `exact`, `frame` (equal modulo `r1` displacements, but the frame differs), `slots` (same frame, but `r1` offsets differ) and `other`.
  It takes about 8 s after a full build.
- `cmpcensus.py BEFORE.tsv AFTER.tsv [N]`: prints the exact counts and the functions that moved up or down between two census runs.
  Use it to price a shared-header lever across the whole tree in about a minute.
- `inv.py`: for every source-built function with the same instruction count as retail, counts the stack-object pairs retail orders the reverse of ours (`reversed/total  unit  symbol`).
  A block whose objects are reversed was an inlined callee in retail; see `docs/catalog/frame-model.md`.
- `ns.py UNIT SYMBOL [-v]`: counts the diff lines that are not pure `r1` offset differences, so register and instruction gains are visible under a frame gap.
- `regalloc.py DUMPDIR UNIT SYMBOL [gpr|fpr]`: replays MWCC's register colouring from a `dbg.sh` dump; see `docs/catalog/register-model.md`.
