# The MWCC dead-stack model

This model comes from reading the compiler's own stack objects with `tools/mwcc-stack` (research agent c-r1, 2026-09-27).
It replaces guesswork about "saturation" and "inline levels" in [frame-gaps.md](frame-gaps.md), where the two disagree.
Diagnose a frame-only or slots-only function with `tools/mwcc-stack/dbg.sh` before laddering levers.

## The model

1. **Allocation.** Low to high: 8-byte link area, outgoing args, *all* of this function's arguments (whether or not they are in registers, used or not), then every object in the frontend's locals list that did not get a virtual register, then spills, then backend temps (int-to-float magic etc.).
   Nothing is ever removed, so a dead object keeps its bytes.
2. **Which locals get no register.** An object gets a register only if it is still referenced in the IR that reaches the backend.
   An object whose every reference is optimised away (by the inliner's substitution, by IRO copy propagation or forward substitution) is never register-allocated, so it gets a stack slot.
   Aggregates (arrays, class objects whose address is taken) always get a slot.
3. **Which objects exist.** Named locals; unused/uninitialised named locals of the function itself; for each inline expansion, a binding object per argument that is not "simple" (simple = constant, local/param variable, `this`, address constant, arithmetic of those; a load such as `ga`, `a->p`, `g` is not simple), the inlined callee's own locals (unused trivial scalars are dropped), a result object; by-value class parameters and temporaries; and IRO temps.
4. **When a binding/local dies (single def, single use).** It is forward-substituted and dies when its use is a store value, a store address/base, a condition, an index, or the RHS of another copy.
   It survives in a register (costs nothing) when its use is directly a call argument or call receiver (`ext(t)`, `ext(t + 1)`, `p->f()`), or when it has two or more uses, or is live across a call.
   The same rule holds for named locals of the caller, which is why `int t = x; g = t;` is +1 slot and `int t = x; ext(t);` is +0.
   So an accessor whose result feeds a call is inert; one whose result is stored or tested costs a slot. That is the "per site" behaviour of every ladder in frame-gaps.md.
5. **Result objects.** Measured (4-byte units, object type distinguished with `double`):
   `g = f(simple)` +1, `ext(f(simple))` 0, `if (f(simple))` 0; with a non-simple argument add its binding: `ext(ga->get())` 2, `g = ga->get()` 3, `if (ga->get())` 1.
   A statement-bodied inline (`{ int r = a; return r; }`) adds its local `r`.
6. **Sizes and packing.** Each object takes its own size and alignment (u8 1, u16 2, pointers/ints 4, float 4/8, double 8), packed in list order; the frame then rounds to 8.
   TFlagT<u16> copies in `TApplication::proc` are 2 bytes each; u8 argument bindings 1 byte.
7. **Order.** List order is reverse creation: the first object created sits highest, later ones lower.
   The inliner expands **breadth-first over the whole function**: all depth-1 call sites (in source order) create their bindings and locals, then the calls inside the inlined bodies are expanded, and so on; IRO temps come last (lowest).
   Proof (v1.cpp): `off = p - pos; ...; ride();` puts operator-'s by-value copy above ride()'s `Mtx`; `off = diff(p, pos)` with `diff` an inline wrapper puts the same copy below the `Mtx`.
8. **Non-additivity ("saturation").** IRO temps are part of the dead set, and a frontend binding that names an expression removes the IRO temp that CSE would otherwise have made for it.
   moveRequest: stock header has 3 dead IRO temps + the `const TVec3&` result pointer; with a by-value `operator-` the IRO temps vanish.
8a. **Named locals first, then temporaries.** The list holds every named local of the function (declaration order) before any compiler object, whatever the source position.
   A parse-time temporary (`TMsRange<f32>(a, b).rand()`, an implicit `TPathNode(pos)`, a by-value parameter copy) therefore sits below the whole named block, and an inline object below every parse-time one.
   A retail temporary at the top of the frame means retail named it and declared it first (c-d2: `TCannon::setKillerGoalPoint`, `bombSet`).
8b. **Inlined callee locals are created last-declared first.** In willFall's `setSafeGoal` expansion the TPathNode `goal` (declared last) sits highest, then `point`, `index`, `range`: the reverse of the caller's own named block.
8c. **Within one expansion, callee locals come before argument bindings.** In a `TVec3 r; r = *fst;` `operator-` the local `r` is created first and the non-simple `fst` binding right after it, so the binding sits directly *below* `r`.
8d. **By-value-returning calls are expanded late, and a by-value parameter copy is created after the return temporary.** With `operator-` returning `TVec3`, its return temporary is a parse-time object (top of the pool), but its body's local sits below every ordinary depth-1 object (`moveRequest`: below `checkRideReCalc`'s Mtx).
   `friend TVec3 operator-(TVec3 a, const TVec3& b)` puts the parameter copy directly under the return temporary, the same slot map as the local-`r` form.
9. **All-or-nothing.** A function with no locals area (no frame beyond the link area) shows none of this; add one saved register or one slot and all dead objects materialise (cc12).

10. **Offsets count what was created later.** Allocation starts at the bottom, so an object's offset is the size of everything created after it (plus the floor).
    An object 4 low in ours means retail created one more word *after* it; removing an object created *before* it (a named local, an earlier site) never moves it.
11. **The frame bounds the count.** frame = align8(align8(top of the locals) + saved GPRs/FPRs), so retail's frame gives the top of its locals to within 8 bytes.
    When the slots below say "+4" but that bound forbids a net addition, the object was *moved*, not added: something created early (usually a named local, which sits at the top) is created later in retail.
12. **Inline bodies and result objects.** A straight-line inline body that ends in `return <constant>;` makes no result object (the call becomes a comma expression).
    A body with control flow makes one, which dies when the caller copies it into a local (+4).
    `BOOL result = 0; ...; return result;` in a straight-line body costs +8 (the local and the result object it forces); in a body that already had a result object, +4.

## Experiments (synthetic TUs)

Marker method: `int mk; extp(&mk);` declared first sits right above the dead region, so `(mk - 8) / 4` counts dead words.
- e3/e5: `ga->get()` +8 frame per expansion; `a->get()` (local receiver) 0; unused parameter of the function +4.
- c1: `s1(ga->m)` 2, `s1(i+k)` 0, `s1(g)` 2, `s2(ga->m, gb->m)` 3, `sr(i+k)` (const& to rvalue) 2, `sr(i)` 0, `ga->set(i)` 1, `ga->setn(i)` (this used twice) 0, `gA.get()` 0.
- c3/c5/c6: contexts listed in rule 4 (named locals and inline bindings identical).
- c9: double-typed returns decode the object kinds of rule 5.
- v1.cpp: breadth-first order (rule 7).

## Applied

- `TMario::moveRequest` (MarioMove, read only): retail = offset 0x5c, 12-byte 0x50, ridingMtx 0x20, operator- copy 0x14, one 4-byte 0x10.
  With the header's `operator-` as `TVec3 operator-(const TVec3& b) const { TVec3 r(*this); r -= b; return r; }` (or a friend with a local, or a by-value helper) the frame is 0x70 exactly and the layout is retail's minus that last 4-byte object (copy 0x10, Mtx 0x1c: every slot 4 low).
  Tree-wide that header is 6 up / 52 down (exact 11866 -> 11859): the `bind` family's instructions change (cc23's copy count), so it is a site property, not a header lever.
- `TApplication::proc`: our dead set is 28 TGameSequence/TFlagT copy objects (1-2 bytes each). A 32-variant grid of `GameSequence.hpp` (u8/int params, TFlagT by value/const&, operator= by value/const&, set/memberwise, assign/set(get())) never exceeds 0x80 at unchanged instructions (retail 0xc8); int params give proc 0x80 and setNextStage 0x48 (retail 0x50).
- `TApplication::checkAdditionalMovie` (0x58 vs 0x20) has **zero** dead objects in ours: all 11 `getInstance()` feed call receivers (rule 4), so no accessor spelling can add frame there; retail's 56-64 bytes are some other construct.
- `jumpingBasic`: ours has 10 dead objects (3 `mWallPlane` bindings for isNoWallJump/isFence/getNormal, a bool and six IRO temps); retail needs 18 more words with no instruction change and no visible slot, so nothing is closable from evidence.
- `TMario::jumpMain` (closed, c-d1): 11 missing words all below pullJumping's `pos` (0x34 vs 0x60) and none above, so only handlers inlined after pullJumping could carry them.
  Named `result` locals in jumpingThrow, the four jump*Down handlers, landSafeDown and fallDead give exactly +0x2c (5 x 4 + 3 x 8); the same spelling on handlers before pullJumping overshoots the frame.
- `TMarNameRefGen::getNameRef` (closed, c-d1): two `this` slots 4 low with the frame already at its bound, so one object had to move from above them to between TSplashManager's depth-1 binding and TSmplFader's TColor.
  Building Mario through a static inline (its `mario` becomes a depth-1 callee local) is that move; the same level around MLight (whose inline ctor then expands one level deeper) moves every later slot and the frame.
- `evSetAttentionTime` (EventWatcher, open): the push slice is 0x10 low and frame 0x18 short. Moving the dead pop one inline level down (`static inline int f(interp) { return TSpcSlice(interp->pop()).getDataInt(); }`) lands the slice and every object below it; the frame is then 8 short (one or two words above the slice), and no spelling found adds only those.
- `evStartMontemanBGM` (open): retail = ours plus one object created after the push slice (the frame bound leaves no room above it); every sound-receiver spelling tried adds that word only together with two above.
- c-d2 closes (all instruction-exact before, one dead object each way):
  `TChorobei::perform`: retail one word short below the Mtx; `mCannon->checkLiveFlag(...)` binds the loaded receiver (rule 5, `if (ga->get())` 1), the raw `mCannon->mLiveFlag & ...` does not.
  `TCannon::setKillerGoalPoint`: the `TMsRange` temporary is retail's topmost object (rule 8a: named `range` declared first), and retail has one more named word below `pos`: a named constant `f32 radius = 500.0f;` used twice is propagated away and keeps a dead 4-byte slot.
- Partial, not committed: `bombSet` with a named `range` and `getSaveParams()->` lands `range` and the low region, leaving one named scalar too many (retail has one of `r`/`rate`, but both named statements are needed for the schedule).
  `wireMove` with a named `f32 ratio = mWirePosRatio;` for the first compare lands `start`/`end`/`dir`; the operator- by-value copy stays 8 high (header class).
  `TNerveBPTumbleOut`: reading the frame directly instead of through `BosspakkunBckFrame` removes the one depth-1 object above `mouth` that retail lacks, but retail then has one more object below it (frame 0x98 vs 0xa0).
- chuuhana `setSafeGoal` family: retail's `reset()` ends in `setSafeGoal()` (its tail is that body verbatim), and with `ChuuHanaGraphNode(ChuuHanaGraphOf(this), index)` / `ChuuHanaGraphNodeNum(ChuuHanaGraphOf(this))` in setSafeGoal instead of the SafeNode/SafeNodeNum binders, `reset` is byte-exact and isCollidMove's frame lands (0x118).
  That spelling has six fewer inner dead words, which ForceJumped (exact today) needs from its own later code: it drops to 0x10 short, so it is not committed.
  Per rule 8b the block stacks goal, point, index, range from the top in every caller; willFall/KeepBalance then need six more depth-1 words before the expansion.
- **The `a = b - c` class (research c-s1, 2026-09-27): retail's `operator-` returns by value; one dead word per site is still unexplained.**
  Retail `moveRequest` has a 12-byte object right under `offset` (a parse-time temporary) and the live copy low (0x14), which only a by-value return produces (rule 8d).
  Tongue's `(tpos - mTipPos) * k` is `bl __ct__(r <- tpos); bl __ami__(r); bl __ct__(ret <- r)` with the last source `addi r4, r1, r`, not `__ami__`'s r3: the body is `TVec3 r(a); r -= b; return r;` or `(TVec3 a, ...) { a -= b; return a; }` (identical slot maps and code), not `return r -= b;`.
  Both need the uncast `operator=` (`*(Vec*)this = other`) so the return copy disappears at `m = a - b` and `set(a - b)` sites (cc23).
  Under that shape every measured site is exactly one 4-byte object short at the very bottom (created after the live copy): `TCoasterEnemy::bind` 0x10/0x28 retail against 0xc/0x24, `moveRequest` all slots 4 low with the frame exact, the whole `bind` family.
  Refuted as the source of that word (all slot-identical to the plain shape): copy constructor base-init, implicit, statement-bodied, out-of-class `inline`; `const` return; `return (r)`; `TVec3 r; r = a;`, `r = (const Vec&)a`, raw `Vec` copies; an extra helper level before `operator-=`; a member `operator-`; a `const Vec&` left operand (a reference-to-base conversion is simple).
  Shapes that add exactly one word at bind sites, both contradicting Tongue: cc23's `operator-(const Vec* fst, ...)` (the conversion makes `fst` a binding; +1 in bind, +2 with an IRO temp in `moveRequest`) and `TVec3 r; (r = a) -= b; return r;` (the `operator=` result becomes `-=`'s `this` binding; +1 in both, but the site's first copy is scheduled differently).
  `TVec3 r(a); return r -= b;` adds two (result binding plus an IRO temp) and closes `attackToMario`, `wireMove`, `TLeanMirror::loadAfter`, `TBeeHive::bind`, whose extra word is likely their own.
  An accessor on the right operand (`nextPos - getPosition()`) supplies the word in `TLiveActor::bind` and `moveRequest` but swaps two instructions in `bind`.
  Tree-wide against 11880 exact (census; uncast `operator=` in every row): plain by-value (either spelling) 11875, 13 up/48 down; `return r -= b` 11882, 19/35; `const Vec*` 11888, 25/33 (`__ami__` MISSING, wire* +6); `(r = a) -= b` 11885, 18/40. `ninja changes_all`: plain 32 up/80 down functions, `const Vec*` 42/58. None committed.
  The losses that need site work whatever the header: `TVec3<f32>(a - b).length()` sites (retail has the return copy: unwrapped under the stock header they are 55 against 61 instructions, and under a by-value header they match but their binders were tuned to the old pool), and the cast-`operator=` group (`TBWLeashNode::calcMatrix` 99.15 -> 87.03, `forceRequest`, `calcNrm`).
- Tool note: GC/1.1 rejects some TUs that 1.2.5 accepts (chuuhana: incomplete `J3DJoint`); prepend the missing `#include` to a scratch copy of the .cpp and pass that to `dbg.sh`.

- c-d7 closes (all instruction-exact before):
  `TCogwheel`/`TWireBell::initDraw`: the file-scope `static const GXColor` lever, as predicted.
  `TAnimalBird::load`: retail one word more below `eventID` with frame slack; naming `J3DModelData* modelData` in the inlined `initTevColor` chain (`getModel()->getModelData()->getMaterialName()`) adds exactly one dead callee word. Splitting a chained accessor inside an inlined callee is a cheap +4 below everything named.
  `TSamboHead::genEventCoin`: a hand-written rotation matrix (`s16 angle; f32 s, c; mtx[..] = ...`) put 12 bytes of dead named scalars above `range`; retail's `MsMtxSetRotY(mtx, deg)` makes them callee objects (angle binding, sin, cos) below it. Grep for open-coded `JMASSin`/`JMASCos` matrix fills before laddering.
  `TSamboFlowerManager::dropLeaf`: retail's dead `params` sat above `angles`; declaring it uninitialised first and assigning it in the loop moves its slot to the top (rule 8a).
- An uninitialised named local declared at the top and assigned from an inline's result (`MtxPtr mtx; ... mtx = f();`) is dead (+4 top), while the same for a plain member read (`f32 speed; speed = mSpeed; if (speed ...)`) gets a register; declared-and-initialised at the use it was dead.

## Refinements (unit agent c-d3, 2026-09-27)

- **Upper/lower trades.** A named single-use value (`f32 searchDist = p->m.get(); if (d < searchDist)`) is one more dead object in the named block, and can remove one IRO temp from the low region (rule 8), so the frame stays put while every object declared after it moves down 4.
  A named base pointer used only as an index base (`TGraphWeb* graph = ...->getGraph(); graph->getGraphNode(8)`) is the same trade against the inline `this` binding it replaces.
  When retail shows extra words *between* two named aggregates and fewer below, look for these two spellings first: `TEnemyMario::emWaitingToInviteMario` closed with one of each.
- **Callee locals are depth-1 objects.** A named local in an inlined callee's body is created with that callee's depth-1 expansion, ahead of later depth-1 call sites in the caller.
  `TMario::walkEnd` needed one dead word above `considerRotateStart`'s `direction`: a nested `f32 speed = mForwardVel;` in `isRunningSlipStart` put it there (retail does not emit `isRunningSlipStart` out of line), and `rate = mForwardVel / 4.0f` dropped `getForwardVel()`'s by-value result object below it (rule 5, `g = f(simple)`).
- **Per-field copy temps.** A by-value `J3DGXColorS10` copy creates four 2-byte IRO temps (one per `s16` field) per distinct source object; copies from one shared source share them (`TYoshi::entry`: sharing one `tevColor` drops 8 of the 12).
- **Where a by-value copy is created is a depth fact.** `TYoshi::doSearch`'s retail `mTipPos - pos` copies sit below all depth-1 objects, i.e. they are an inline callee's local (`TVec3 r(a); r -= b; return r;`), not the friend operator's by-value parameter, which is created at the call site.
- The debugger cannot show which expression an IRO temp named: `@N` created and removed inside IRO appears only in `variables.txt`, not in any frontend or backend dump.

## Refinements (unit agent c-d4, 2026-09-27)

- **Callee locals are reversed.** Inside one inline expansion the callee's own objects are created last-declared first, so its first-declared local sits lowest: `checkDropInWater` (gesso) lists `local_34` over `velocity` over `position`; `decideTarget` (fireWanwan) lists its two `TVec3` temporaries over `dir` over `diff`.
- **A callee local is created after every parse-time object.** Moving a block *with its declaration* into a TU-local inline turns a named local into a callee local, created at expansion; that is the lever when retail has an object declared earlier in the source sitting *below* one declared later (hinokuri2 `GraphWander`: `local_60` under `TStack_3C`; tamaNoko `Attack`: velocity copies under `setGoalPathMario`'s `TPathNode`).
- **Dead forward-substituted named locals sit on top.** `TLiveActor* body = spine->getBody(); THinokuri2* self = (THinokuri2*)body;` puts `body` above every other local; the same two lines inside `Hino2Self()` put the binding under them (`TNerveHino2Die` closed on that alone).
- **An uninitialised named scalar declared first moves its dead slot up.** `f32 diff;` at the top of `TTamaNoko::walkBehavior` puts its slot above `local_34` (closed).
- **A file-scope `static const GXColor` removes the dead named colour.** It folds to the same immediate, so `TColor(kColor)` keeps the copy temporaries and drops the 4-byte local (`TSwingBoard::initDraw` closed); `TCogwheel`/`TWireBell::initDraw` report the same +8 residue.
- **Index spellings price differently.** `a.back()` vs `a[a.mSize - 1]` vs `a[a.size() - 1]`, with the array reached through a binder or raw, span -4 to +0x10; the exact one for `TFireWanwanTailHit::moveRequest` was a binder receiver with a raw `mSize` index.
- **Units built with `mwcc_sjis` and the `SMS.mch` prefix** fail in `dbg.sh` (`undefined identifier 'j3dSys'`). Prepend `#include <SMS.pch>` and convert to Shift-JIS into a scratch file, then run `dbg.sh` on that copy: `{ echo '#include <SMS.pch>'; cat src/X.cpp; } | iconv -f utf-8 -t shift_jis > $SCRATCH/sj_X.cpp`.
- `TBossManta::collidedWithWater` (closed, c-d5): 8 bytes too many below the second hit-count array came from the value binder `{ s32 g = p->mGeneration; return g; }` compared in the caller.
  The predicate `{ s32 g = p->mGeneration; return g >= 4; }` gives retail's frame with the same single `mGeneration` load; `mGeneration >= 4` spelled in the caller reloads it, and a caller-level named local lands at the top and overshoots the frame bound.
- `TBossManta::moveObject` (closed, c-d5): naming the loop's receiver (`THitActor* hit = mCollisions[i]; hit->isActorType(...)`) removes the inline `this` binding from the low region and adds a named slot at the top, which the frame's rounding absorbed.
- `TBossMantaManager::setupEfbAlpha` (closed, c-d5): a `(GXColor){...}` argument's two temporaries belong to depth 0, so they sit above anything an inline creates.
  Retail has them above the `Mtx44`/`Mtx` locals, so those matrices are locals of two small inline helpers (ortho projection, identity load).
  Declaring `GXColor color;` at the top of the body and assigning it later puts it *below* the matrices: named locals are ordered by first reference in the statement list, not by declaration.
- `TBossMantaAdditionalCollisionSet::update` (closed, c-d5): 0x58 missing with every instruction right was two named `TVec3<f32>` built from named scalars (`TVec3 center(cx, cy, cz)`), each worth the vector plus its constructor's argument objects.
  Building them straight from the matrix loads reverses the load order (arguments evaluate right to left); four vectors overshoot by 0x30. A 3^4 per-joint sweep (scalars / ctor / `set`) found the one exact combination.
- `evStartMontemanBGM` (open): retail's layout equals our `MSound* sound = EventWatcherRawMSound();` spelling minus the binder's own local: two dead IRO temps below the push slice and nothing above it.
- `TApplication::initialize_nlogoAfter` (open): the unused `JKRMemArchive* this_00 = new ...` keeps a dead named slot between the two streams that retail does not have (drop the name: instruction-neutral, -4).
  Retail then needs 8 more bytes created at depth 1 between the name-ref stream's ctor binding (line of `stream(bufStageArcBin, ...)`) and the option stream's, plus 4 below; a `ResTIMG` data helper at the two `getResource` sites adds 16, all below.
- `TModelWaterManager::drawSilhouette` (open): every slot 0x1c low, so retail has 7 more words created after all of this body's own objects; the two particle-count binders give 4.
- `TBossManta::control` / `updateAnimBlend` (open): retail has exactly 12 bytes between `local_134` and `curVel`, i.e. `cross1`'s slot, with the store/reload of `turn` at `cross1.z`'s offset; so `b` and `turn` are not dead named locals there.
  Scalarising the cross product drops the frame by 0x20 (the `cross` expansion's `_x/_y/_z` are part of retail's set).
## Refinements (unit agent c-d6, 2026-09-27)

- **Default-argument temporaries.** `JDrama::TFlagT<u16>()` (the defaulted `T v = T()`) makes one more dead object than `TFlagT<u16>(0)`; with `startStateTimer(mExplodeWaitTime)` for the store it closed `TSandCastle::waitBeforeExplode` (the unnamed temp is then the top object, retail's layout).
- **Moving a result object one depth down.** When retail has one word fewer above an object and one more just below it, and the word is a binder's result, wrap the binder in a direct-return inline: `KumokunIsTaken(p) { return TakeActorIsTaken(p); }` creates the binder's local with the depth-2 expansions (`TKumokun::calcRootMatrix`).
- **A named local's slot moves with its declaration.** A dead named pointer (`mouthMtx`, forward-substituted) declared before the aggregates sits at the top instead of between them (`TBossTelesa::genAttacker`, with a named `roll`).
- **Sharing one aggregate across switch arms** (function-scope `TVec3 trans`) removes a 12-byte named object; the freed words then come back from named single-use values (`f32 height = ...getMax().y - ...getMin().y;`) (`TMammaBlockRotate::control`).
- **FPR order via argument bindings (rule 22).** A caller's named `f32 speed = m;` fed into `speed + getFrame()` gets f30 after the inline getFrame temp; passing `m` as an argument of an inline helper (`SandBombAddFrame(actor, ctrl, speed)`) makes it a depth-1 binding, allocated first (f31), which is retail's order in all five sand-bomb functions.
- **Single-site binders price 1-3 words.** A binder over a raw global/member (`TMarDirector* d = gpMarDirector; return d;`) at one site gave +8 low region, over an inline accessor (`SMSGetMarDirector()`) +0xc; `SMS_GetMarioGroundPlane()` and `mChangeStage` binders +4 each. Use them only where the frame bound leaves exactly that gap.
- **Auto-inlining guards the out-of-line body.** Shrinking a small member's statement count (TVec3 `add`/`scale` for x/y/z arithmetic) got `TKoopaJrSubmarine::makeCollisionPositions` inlined into `perform`; the component-wise `+=`/`*=` spelling kept the frame and the out-of-line call.
- `TApplication::setupThreadFuncLogo` (closed, c-d8): retail's `heap` word sat below the two `SMSLoadArchiveARAM` path buffers instead of between them and the `SMSLoadArchive` ones.
  Calling through the real JSystem wrapper `JKRDvdToMainRam(...)` instead of `JKRDvdRipper::loadToMainRAM(...)` pushes the heap accessor to depth 2, so its object is created after every depth-1 callee local; the accessor must then be a direct-return fork (the binder overshoots by 8).
  When a word is missing below the last expansions and extra above them, look for a header inline wrapper (`JKRDvdToMainRam`, `JKRGetNameResource`, ...) around the call first.
- `TApplication::initialize_nlogoAfter` (open, c-d8): without the `this_00` name and with the stage-archive search behind a binder over `search2`, frame and every slot land except the option stream's ctor binding (4 high): one more depth-1 object belongs between the two stream constructions and one fewer below.
- `TBaseNPC::npcMadding` (open, c-d8): retail ranks the vectors named result (top), parse-time temporary, depth-1 callee local (lowest), i.e. `TVec3 v = a - b` through a by-value-returning `operator-`; a TU-local by-value helper reproduces that order but not the registers. Header class (JGVec3.hpp cc23 shape).
- The `new JUTTexture` storeTIMG `this` copy (CardSave initData, Guide load, GCConsole2, SelectMenu, Talk2D2) appears in retail at sites whose source is identical to copy-free ones (CardLoad, GCConsole2 vs CardLoad coin loops); a TU-local texture loader helper does not produce it.

## Refinements (unit agent c-d9, 2026-09-27)

- **A this-taking inline moves a whole statement's bindings one depth down.** `TSpineEnemy::goToRandomNextGraphNode`/`goToRandomEscapeGraphNode` (closed): the if/else node choice's five `getTracer()->...` receiver bindings sat above `setGoalPathFromGraph`'s TVec3/TPathNode block; retail has the block on top.
  Wrapping the if/else in `static inline void f(TSpineEnemy* enemy)` (argument `this` is simple, so no depth-1 binding) creates the bindings at depth 2, after the block, with no instruction change.
  A helper taking the tracer (`f(getTracer())`) or per-read helpers returning values do not work: the argument binding or the result objects stay at depth 1.
- **Chained accessors expand by nesting, not statement depth.** In `jumpToNextGraphNode` the receiver chain inside `getGraphNode(getTracer()->getCurGraphIndex()).checkFlag()` is created after `setGoalPathFromGraph`'s block although both are caller-level code; the idx statement's three bindings sit above it.
- **Moving a loop into an inline changes its induction setup.** The exclusive variant's loop moved into a helper gets `li r29, 0; addi r30, r29, 0` instead of retail's two `li`; a helper holding named `currIdx`/`idx` also drops the `mr` copies retail keeps (goToDirectedNextGraphNode), so those locals are caller-level in retail.

## Refinements (unit agent c-d10, 2026-09-27)

- **A converting argument binding moves up when named.** An inline setter taking `u32` fed an `s32` local makes a typecon binding (non-simple, rule 3) created with the depth-1 expansion, below every named local.
  `u32 id = eventId; setEventId(id);` turns it into a dead named word at the declaration point: `TShine::loadBeforeInit` closed (retail had one word between `eventId` and `v` and one fewer below `v`).
- `TMap::isTouchedOneWall` (open): the inlined `center` reference of `isTouchedOneWallAndMoveXZ` is part of retail's set (dropping it is -8); the one missing word sits below `record`. Inert or code-breaking: `int r;` declared before the record, a named `bool`, `if (r != 0)` (loses the ternary's bool), default-ctor plus `set()` (stops the inlining).
- `PopoRollCallback`: `MsMtxSetRotY(rollPtr, 180.0f)` is instruction-identical to the open-coded `JMASSin(0x8000)` block but frame-inert there; retail's six extra words are all low region (`gpCurPopo->isRollJump()` is +8).
  `PopoNonScaleCallback` needs 15 more low words than ours with the same two inlined nerve compares; retail's `isUseScaleCallBack`/`isRollJump` expansions carry about twice our dead objects.
- `TShine::control`: `MsAngleWrap(mRotation.y)` for the discarded `MsWrap(...)` dead-strips the wrap loops (-9 instructions per site), so retail calls `MsWrap` directly there.
- `decideRandomLoveFruit` (open): every random spelling (`f32 rnd` named, `MsRandI`, a named product) reaches +8 at most; retail needs +0x10.
- The 0xa0-0xc8 dead regions with no stack access (`TPoiHanaManager`/`TGessoManager`/`TIgaigaManager`/`TTamaNokoManager::initSetEnemies`, `TNerveBWRoll`) are one size family; look for one shared construct rather than per-function levers.
