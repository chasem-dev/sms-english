# hsearch: honest source search

`hsearch` searches source spellings of one near-exact function by machine.
It compiles each variant with the unit's own MWCC command, scores it in-process against the retail object, and keeps every trial in one SQLite database so nothing is compiled twice.
It only generates honest moves: value-preserving rewrites that read like code a person wrote.
It never writes to the source tree while searching; winners come out as patch files.

Run it from the repo or worktree root, after a full build (it needs `build.ninja`, `objdiff.json` and the retail objects under `build/GMSE01/obj`).

```
python3 -m tools.hsearch run -u Enemy/hanasambo -f attackToMario__10TSamboHeadFv
python3 -m tools.hsearch batch --list targets.tsv --out DIR --db DIR/trials.sqlite
python3 -m tools.hsearch moves -u Enemy/hanasambo -f attackToMario__10TSamboHeadFv -v
python3 -m tools.hsearch score -u Enemy/hanasambo -f attackToMario__10TSamboHeadFv
python3 -m tools.hsearch apply DIR/attackToMario__10TSamboHeadFv.patch --revert
python3 -m tools.hsearch stats --db DIR/trials.sqlite
```

`batch` takes a TSV of `unit<TAB>mangled symbol` rows, is resumable (a function with a recorded run is skipped unless `--rerun`), and stops cleanly before the next function when the `--stop-file` exists.
Defaults: `--budget 900` seconds per function, `-j 2` parallel compiles, database `$HSEARCH_DB` or `$TMPDIR/hsearch/trials.sqlite`.
The machine has four cores shared with other agents: keep `-j` at 2 or 3.

## Files

- `elfscore.py`: the scorer. It parses both ELF objects, extracts the function, masks every relocated field, and compares relocation targets by name (or, for `@NNN` pool symbols and section symbols, by the bytes they point at).
- `moves.py`: the honest move set. It imports lever-search's generators and keeps a whitelist of them, and adds this package's levers.
- `search.py`: shadow-root compiles, the trial database and the search.
- `dbgobj.py`: the debugger-guided layout objective.
- `__main__.py`: the command line, including the `apply` verification.

## The score

The score is lexicographic, lower is better:

1. byte-exact or not;
2. objdiff's fuzzy match percent for the function, higher is better: the value `report.json` records and `ninja changes_all` compares (`objdiff-cli diff -c functionRelocDiffs=none`, the option `report generate` uses), so a candidate ranked better is never one changes_all reads as a regression;
3. differing instructions (opcode, immediate or relocation target differ, plus the length difference);
4. the frame-size delta;
5. register-operand mismatches;
6. `r1` slot-offset mismatches.

The counts below the fuzzy match only break its ties: before it led, an edit that landed the frame but lost registers ranked as a gain and then read as a regression in changes_all.
The unit profile behind the exact-variant check ranks every other function the same way.
Trials recorded before the fuzzy match was kept are rescored on first use.

On the full tree the scorer agrees with `tools/mwcc-stack/census.py` on 11992 of 11998 exact functions (the rest are relocation-name artefacts of the split, and a few functions census calls `other` that are byte-exact).
Scoring a function takes milliseconds; the compile dominates.

An exact variant is accepted only when the honesty lint passes and a whole-unit profile shows no other function and no data section getting worse than the base source.

## The move set

Kept from lever-search: accessor vs raw member (`acc->raw`, `raw->acc`, `set-assign`, only accessors that exist in a header), null tests, `int`/`s32` only, naming and unnaming single-use values (`unname`, `name-call`, `name-read`, `name-conv`), declaration order and scope (`decl-order`, `decl-split`, `decl-hoist`), compound assignments (`compound`, `split-sum`), `TVec3` constructor vs `set`, UNUSED stubs called instead of pasted code, rotation helpers.

Left out: its TU-local forks and binders, chain helpers, parameter-alias `bind` names and file-scope colours.

Added here:

- `commute`: commutative operand order for `+` and `*`, when at most one operand has a call with side effects; literal operands are skipped because the frontend canonicalises them anyway (docs/catalog/register-model.md, c-r11).
- `ternary`: if/return and if/else assignment against `?:`, both ways.
- `cond-zero`: `if (f())` against `if (f() != 0)` for non-pointer call results.
- `stmt-swap`: adjacent statements with no data dependency (read/write sets per local and per member name; calls with side effects never cross memory accesses).
- `for-scope`: a `for` counter declared in the header, at the loop or at the block top.
- `accumulate`: `T x = a OP b;` as `T x = a; x OP= b;` (this also gives the u8 two-step mask), and a sum in a comparison named and accumulated.
- `merge-assign`: the reverse of `accumulate`.
- `addr-assign`: `(p = &G)->m = ...` at the first store through a pointer initialised with a global's address.
- `pred-level`: `return E;` against a `bool result` local set in an `if`, both ways.
- `extract`: a run of one to four statements moved into a TU-local `static inline void` level; a single statement qualifies only with a computation or a control statement, so pass-through helpers are never generated.
  Members are rewritten as `self->m`; the compiler is the type check, so a wrong guess just fails to compile.
  A winner with an extract gets an UNUSED check: each generated helper is compiled out of line and its size set against the unit's UNUSED entries in `marioUS.MAP` that the built object does not already emit at their map size, and the result goes in the log and the patch header (`# unused check: controlPart56 is 0xe0, the size of UNUSED liftMario__8TBathtub...`).
  A same-size UNUSED is the lead to try first: move the block into that function instead of the helper; with none, the helper is a machine cut and is rejected in review.
- `conv-raw`: a matrix argument through its conversion operator against the raw `.mMtx` array.
- `sound-short`: `startSoundActor(id, pos, 0, nullptr, 0, 4)` against the two-argument `startSoundActor(id, pos)` overload (and the handle form against the three-argument one), per site (codegen-tells.md, batch 82) and at every site of the function at once.
  A form is offered only when it is at least a quarter of the file's other calls, so a file that spells its sounds with six arguments is not given a lone short one.

Plausibility filters drop generated moves that compile but read wrong:

- any spelling move (accessor/raw, naming, null and zero tests, `commute`, `sound-short`, compound and split sums, `int`/`s32`) that edits a line or statement while an identical one anywhere in the function is left alone (three identical `if (unkA4->isVisible())` blocks spelled two ways, however far apart);
- a single-site or subset accessor/raw flip that leaves the other spelling of the same member on the same receiver anywhere in the function (`raw->acc sites 3+4 of 4`, `unk18.x; unk18.y; getUnk18().z`); writes such as `mRotation.y = a` do not count as raw reads;
- a `raw->acc` whose accessor is not the member's accessor in the function's class (the header index is keyed by name, so a namesake would change the value), or whose "member" is a local;
- naming a value (`name-call`, `name-read`, `name-conv`) while an identical read stays anywhere in the function (a lone named value at one of several identical call sites), or naming an accessor's result next to a raw read of its member;
- `name-read` of a `stream >> m` operand (the copy would not be written back) and `name-call` of a constructor declaration;
- `== 0` to `== nullptr` on a non-pointer.

`stmt-swap` treats any operator on a class-typed local (`stream >> x`) as a call that changes it, so such statements never swap.

The lint refuses a winner that gained `volatile`, a pragma, `reinterpret_cast`, a padding array, a `(void)0` filler, an empty `if` body, a pass-through helper or a binder helper.
It also refuses a winner with more local declarations shadowing one in an enclosing block than the base has (a dead outer local is padding).
Moves that rewrite or delete the same text never combine, so two `decl-hoist`s of one declaration cannot leave it declared twice.
Review every winner by eye anyway: generated helper names (`bindPart3`) must be renamed, and a helper must read like a real inline level.

## The search

Within the budget:

1. singles: every move on the base source (up to 60% of the budget);
2. beam: pairs, triples and quadruples of the useful singles (better, or a different object at the same score);
3. hill-climbing with restarts: regenerate the moves on the current text, score a batch of neighbours, take the best, allow sideways moves across plateaus, and kick with two or three random moves at once; it stops early when forty rounds in a row compile nothing new.

Moves are weighted by how often their kind has improved a score in this function.

## The database

`trials(fn, unit, hash, score, sig, moves, secs, at)` holds every compiled variant, keyed by the SHA-1 of the whole source text, so a re-run, a resumed batch or a different move order never compiles the same text twice.
`runs` holds one row per searched function; `results.tsv` in `--out` mirrors it with the patch path and `best_at`, the second at which the final best score was first reached (a measure of whether a longer budget would help).

## The debugger-guided objective

`--dbg K` adds a layout distance from the MWCC debugger (`dbgobj.py`, needs `MWCC_DEBUGGER` and `RETROWIN32` as for `tools/mwcc-stack/dbg.sh`).
A variant is dumped with GC/1.1 and the unit's own flags, every `r1` access of our compiled function is mapped to the dumped object that holds it, and retail's offset for the same access comes from the aligned retail instruction.
That names, per object, where retail keeps it, so the order and the gaps retail implies can be compared with ours.
The distance is lexicographic: objects out of retail's top-down order (the count outside a longest common order), then words missing or extra between consecutive mapped objects (plus above the first and below the last), then objects whose offset differs, then register webs (GPR and FPR) that `tools/mwcc-stack/regalloc.py` finds coloured differently from retail in the same dump.
It is a tie-breaker below exactness, instruction differences and the frame delta, and above the register and slot counts.
A dump takes 7-20 s and one debugger runs at a time (the gdb stub's port is fixed), so the search dumps the base, the best 2K singles, the best K combinations of each beam level and at most one climb neighbour per step, and outside the singles dumps may take at most half the time spent.
Layout distances are cached in the database's `dbg2` table.
A run whose best has the same in-process score as the base but a closer layout reports `improved-layout`.

`dbg -u UNIT -f SYMBOL [--file VARIANT]` prints the mapping for one text: our objects top-down with their kind (named, inline, argument or the IR optimiser's F/P/S temporaries), retail's offset for each mapped one, and the regions where retail has words we lack or lacks words we have.

### The per-statement slot map

`dbg ... --by-line` adds, per object, retail's displacement from ours in words and the source line and type of the first statement that references it (from the front end's initial code in the same dump, so the `@N` numbers always agree).
An object an inlined body creates carries the line of the caller's statement that expanded it, so a stray word between two mapped objects names the statement whose inline depth is off (docs/catalog/frame-model.md, "Iterator groups as depth markers").
Back-end temporaries (int/float conversions) and spills have no statement and print `(back-end temp)` / `(back-end spill)`.
With a debugger patched by `tools/mwcc-stack/patch-debugger.py` (patch a copy in your own scratch directory and point `MWCC_DEBUGGER` at it, never the shared one), the kind column also names the IR optimiser's temporaries (F, P, S, ...) and the F/P ones get `tools/mwcc-stack/iro.py`'s statement line; a `~` before their type marks iro.py's approximate pairing.
Dead objects the inliner made and never referenced (an unpatched dump's unlabelled `@N` rows at the bottom) have no line either way.

```sh
source <scratchpad>/env.sh   # MWCC_DEBUGGER, RETROWIN32
python3 -m tools.hsearch dbg -u System/GCLogoDir -f 'setup__10TGCLogoDirFPQ26JDrama8TDisplayP13TMarioGamePad' \
    --by-line --keep <scratch>/gl [--file variant.cpp] [--timeout 900] --work <scratch>
python3 -m tools.hsearch dbg -u System/GCLogoDir -f 'setup__...' --by-line --load <scratch>/gl
```

`--keep DIR` keeps the dump (all front-end and back-end passes, `variables.txt`, the register-allocator logs), our compiled object, the variant text and `meta.json` in `DIR`; `--load DIR` re-prints a kept dump without compiling or running the debugger, so a slow dump is read as often as needed.
`--timeout` is the debugger's time limit per run (default 300 s); `MarDirector::setupObjects` needs about 7 minutes, so give it 900.
The symbol is the mangled name as `powerpc-eabi-nm` prints it for our object.

## Applying a winner

`apply PATCH` applies the patch to this checkout, then runs `build/venv/bin/ninja -k 0`, checks the DOL SHA-1, runs `ninja changes_all` and fails on any lowered value, and compares `tools/validate-symbol-order.py` before and after.
With `--revert` a failed patch is undone.
Commit only after reviewing the diff for honesty; rename generated helper names first.

## Measured (pilot, 2026-09-28)

Forty instruction-exact functions with register, slot or frame residues, most with long recorded hand searches, at 15 then 8 minutes each and `-j 3`: 172k variants in 6.5 hours (441 per minute; 100 to 1300 per minute depending on the unit's compile time).
Two exact variants came out and both were refused in review (a value named for one of two identical calls a line apart, and interleaved split declarations); thirteen functions improved, of which two read as honest source (`TBossManta::updateAttractor`: raw `mPosition` at two of three sites, frame and every slot exact; `TEnemyManager::performShared`: `getObjNum()` bound with raw `unk18[i]`, frame exact).
Seven of the twelve improvements were found within 50 s, both honest ones among them; the later finds were more machine-cut helpers or the two refused exacts, so a bigger budget does not help on these residues.
Rerunning the same 38 functions with `--dbg 3` at 8 minutes and `-j 2` gave no exact result either: 413 dumps took 59% of 4.3 hours (about 22 s each, over a minute each in `bossgesso` or `GCConsole2`), the layout distance ranked the same improvements, and it found two same-score variants with a closer layout.
The residues these functions carry are not reachable by the honest move set; a lever the search lacks, not the objective, is the limit.

