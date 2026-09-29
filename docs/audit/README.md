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

- The remaining `TMario::pulling` difference was resolved on 2026-09-29; see the symbolic-effect pass below.

## Symbolic-effect pass (`tools/expr-diff.py`, 2026-09-28)

CLEAN only says both builds use the same callees, constants and field offsets; it cannot see swapped operands, a wrong field in the right set, an inverted condition or a changed fusion.
`tools/expr-diff.py` executes retail's and our code symbolically and compares what each function does, not which features it has.
Values are register-independent expression trees built from the entry arguments, loads, globals and call results; stack slots are keyed by offset, so frame layout and spills do not matter.
Effects are every store to memory, every call with its arguments, the values returned, and every conditional branch with the effects each way reaches.
Floating-point fusion is kept: `fmadds(a,b,c)` is not `a*b+c`, since the last bit can differ.
Commutative operators, sums of constants, masks and sign extensions that cannot change the bits used, and straight-line leaf callees are normalised.
A branch that decides nothing observable (both ways reach the same block alike, a repeated test, a materialised bool) is not an effect.
Run it after a full build: `tools/expr-diff.py --tsv sem.tsv` over `tools/semantic-diff.py --all --tsv sem.tsv`, or `--unit Dir/Unit [--func SYM] -v` for one function; `--exact N` is the self-check.

Coverage: all 887 non-exact functions (`semantic-diff.py --all`: 795 CLEAN, 76 SEMANTIC, 13 ARITH, 3 INLINE), and the self-check on all 12,006 byte-identical functions reports no difference.
The first run flagged 33: 2 were real (fixed below) and 7 fell to this pass's tool changes, which remove 8 false-positive classes (section anchors such as `@1431+0x7c` against our named table, file-static data named on one side only, text tables running into different data, `long`/`int` template names, `bge L; b L`, re-tested conditions, materialised bools, leftover registers at virtual calls).
Eight of the earlier behaviour fixes, rebuilt at their parent commits, are still reported by the improved tool.
24 functions are still flagged: 1 real difference left open, 23 equivalent (below).

### Real differences found and fixed

Behaviour (the retail value, condition or field is now computed):

- `TTamaNoko::isCollidMove`: a TamaNoko that is down, not asleep, can be trampled.
- `TPoihanaSaveLoadParams`: the trap-jump MaxSpY/MinSpY members sat at each other's offsets and keys.
- `TTalk2D2::makeBoxLine`: the rotated glyph offset is added to -mid before +mid (float association).
- `MarioHeadCtrl`: Mario turns his head from a wall in front only while he waits.
- `MSBgmXFade::xFadeBgm`: a downward crossfade crossing is `unk0 >= scTiming[i]`.
- `TSelectMenu::perform`: the cursor step leaves without animating the arrows when no shine lies that way.
- `TWalker::bind`: the second ground check probes from the enemy's own height.
- `TFireWanwan::moveObject`: it hangs by the tail only when it can be taken and the tail is held.
- `JPABaseEmitter::createParticle`: the sphere emitter's yaw resets only when a layer is finished.
- `JPABaseEmitter::createParticle`: the point emitter's direction is (cos, 0, sin), z drawn before x.
- `TEnemyMario::initEnemyValues`: the first replay starts whenever any replay was loaded.
- `TMario::checkCurrentPlane`: the ground-pound sit-up flag is cleared every frame.
- `TMarDirector::initECTGft`: the graffito EFB textures are sized width by height.
- `TLeanMirror::controlShake`: the lean test rounds the z term and fuses the x term (`fma(x, m01, z*m21)`), which decides the impact near a zero sum.
- `TMapObjBall::calcCurrentMtx`: the rolling speed rounds z*z and fuses x*x, up to 1 ulp in the roll angle.

Structure only (no behaviour change): `TCardManager::format_` tests for an I/O error after the mount, outside the mounted branch, as retail does.
`unmount_` is a no-op when nothing is mounted, so the outcome was already the same.

### Remaining known difference

- Resolved 2026-09-29: `TMario::pulling` now writes the subtraction through a `Vec&` computational helper, matching `TongueSubTo`'s dataflow.
  Retail's `fma(x, x, y*y) + z*z` and the animation-rate register are reproduced.
  Match improved from 98.57321% to 99.95638%; only two subtraction-temporary stack offsets remain.
  `expr-diff.py --unit Player/MarioSpecial --func pulling__6TMarioFv -v` reports `SAME`.
  The earlier audit counts above are historical; this closes the one genuine behavior difference they left open.

### Flagged but equivalent (verdicts)

- A body one build inlines and the other calls, checked instruction by instruction against the out-of-line retail body: `TNerveKukkuGraphWander::execute` (calcMomentum; retail's inline `TQuat4::rotate` has calcMomentum's exact fma sequence), `TBossGesso::doAttackSingle` and `moveObject` and `TGorogoroManager::perform` (MsGetRotFromZaxisY: same z == 0 / x >= 0 / z >= 0 arms and constants), `TDirectionCalc::calcNearerDirection` (our `std::fmodf` expansion is the weak `fmodf` body), `TKoopaJrSubmarineManager::createEnemyInstance` (the constructor's stores), `TCardManager::cmdLoop` (`setCheckSum` against retail's store plus `CalcCheckSum`).
- The same set of integers tested another way: `TBossGesso::changeAllTentacleState` ({3, 4, 6}), `TPakkunSeed::rebirth` (0x100..0x105), `TConductor::isBossDefeated` (switch pivots).
- `TGorogoroManager::perform` also re-tests the sink after `isBuried` where retail branches straight to the store; same outcome.
- Leftover registers at a call that does not read them (the tool assumes the widest reader of a vtable slot): `TNerveBeeHiveMarioWaterIn::execute`, `TWaterGun::init`, `TFireWanwanTailHit::perform`, `TGuide::placeMario`, `TTalk2D2::makeBoxLine` (after its fix).
- `__exception` `lbl_80004600`: retail's absolute `TRKInterruptHandler` address (0x803404b8) against our relocation; the linked DOL is identical.
- Earlier verdicts, unchanged: `TKumokun::checkOnMovingFloor` (a plane pointer stored and then overwritten before any read), `TMapCollisionData::intersectLine` (conversion scheduling; loop-counter tagging), `TMapCollisionData::getGridArea` (`std::min` operand order: only ±0 or NaN could differ, and both truncate to the same grid cell), `TRope::constraintTail` (induction variable strength-reduced differently), `TBaseNPC::npcWetting` (a jump-table base left in a register), `TMario::wireSWait` (angle difference taken the other way round, only tested against a symmetric range), `TModelWaterManager::calcDrawVtx` (leftover FPRs at a virtual call).

## Notes for the PC build

- Build with floating-point contraction disabled (`-ffp-contract=off` for GCC/Clang, `/fp:precise` for MSVC). Several fixes above exist only because MWCC's choice to fuse multiply-add changes the last bit; a PC compiler that fuses on its own would reintroduce the same class of difference everywhere.
- `cNpcPartsNameRootJoint` is defined in both `NpcParts.cpp` and `NpcAnm.cpp`; a PC linker needs one of them `static` (or weak).
- `gpCurPopo`, `gpCurSamboHead`, CardManager's `titles`/`comments` are file-static in retail but global here; harmless unless another definition collides at link time.
