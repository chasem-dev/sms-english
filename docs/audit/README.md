# Behaviour audit (2026-09-28)

Goal: the source is used to build a PC replica, so every function must *behave* like retail even where it is not byte-identical.

## Method

`tools/semantic-diff.py` compares every non-exact function with retail after reducing both to order- and register-independent features: callees, globals and pooled constants (by value), struct-field offsets, immediates, comparison kinds, and arithmetic opcodes.
Functions whose features are equal are CLEAN (only register choice, scheduling or stack layout differ); the rest were reviewed by hand, function by function, against both disassemblies.
Re-run it after any change: `python3 tools/semantic-diff.py --tsv out.tsv`, or `--unit Dir/Unit -v` for one unit.

| Class | Functions | Meaning |
| --- | ---: | --- |
| exact | 11,991 | byte-identical to retail |
| CLEAN | 753 | same features; register/order/stack only |
| INLINE | 3 | a weak helper called on one side, expanded on the other |
| SEMANTIC + ARITH | 89 | reviewed by hand (tables `c-au1.md`..`c-au4.md`) |

Coverage: every retail object has a source counterpart, and only two retail functions are absent from our objects (an assembly padding block in `__exception` and one weak `TVec3::set<f>` copy that we expand inline).
Data: 99.84% byte-identical; the four units that differ were checked symbol by symbol and hold the same values (`data-audit.md`).

## Real differences found and fixed

- `TConductor::isBossDefeated`: maps other than 2 and 3 fell off the switch and returned an undefined value; retail sends them to the hinokuri arm (`case 2: default:`).
- `TGorogoroManager::inArea` (UNUSED, inlined): returned TRUE with no area manager; retail returns FALSE.
- Float rounding (a product fused into `fmadds`, or not, where retail did the opposite; results can differ in the last bit near a threshold or table index):
  `TSpineEnemy::zigzagToCurPathNode`, `TMammaMirrorMapOperator::perform`, `TBossHanachan::perform`, `TYoshiTongue::movement`.
- Earlier in the same session (matching work): the Kumokun roof/floor plane checks (wrong subtraction), mameGesso `getGravityY` (wrong nerve), `TNpcParts` constructor (wrong table layout), `calcNoticeTargetYrot_` (thresholds swapped), `TCameraMapTool` (rotation layout).

## Known remaining difference

- `TMario::pulling`: retail's `delta.length()` fuses `x*x` into the sum (`fma(x,x,y*y)+z*z`); ours does not. At most 1 ulp before the `len < 1.0f` animation test; no source spelling found.

## Notes for the PC build

- Build with floating-point contraction disabled (`-ffp-contract=off` for GCC/Clang, `/fp:precise` for MSVC). Several fixes above exist only because MWCC's choice to fuse multiply-add changes the last bit; a PC compiler that fuses on its own would reintroduce the same class of difference everywhere.
- `cNpcPartsNameRootJoint` is defined in both `NpcParts.cpp` and `NpcAnm.cpp`; a PC linker needs one of them `static` (or weak).
- `gpCurPopo`, `gpCurSamboHead`, CardManager's `titles`/`comments` are file-static in retail but global here; harmless unless another definition collides at link time.
