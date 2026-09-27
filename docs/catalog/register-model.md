# The MWCC register-colouring model

Read this when a function's instructions match but register numbers differ: an `r24`/`r25` swap, a whole-function rotation of the callee-saved block, or two FPRs exchanged.
The model was read from the allocator itself (the mwcc-debugger dumps, `tools/mwcc-stack/`) and replays every dumped function exactly, including the 1459-web `TCardSave::execMovement_`.
GC/1.1, which the debugger runs, emits code identical to our GC/1.2.5 for every function compared (emitEffects, execMovement_, requestNpcAnm_).

## The model

Given the interference graph, colouring is deterministic and has only two inputs you can move from source: the order webs are coloured in, and each web's degree.

1. **Virtual register numbers.** Arguments come first, in declaration order (`this` lowest).
   Named locals of the function follow in reverse declaration order (the last declared gets the lowest number), nested scopes included.
   Then compiler objects (`@NNN`: inline argument bindings, inlined callees' locals, IRO temporaries) in reverse creation order.
   Then pcode temporaries (values with no object: CSE'd loads, expression results) in increasing generation order.
   Some destructor-related `@` objects are numbered before the arguments (`execMovement_`'s JSU stream bindings); check `variables.txt` rather than assuming.
2. **Object creation order is breadth-first by inline depth.** Every depth-1 object is created, in source order, before any depth-2 object, and so on (fireWanwan emitEffects: its depth-1 `thing` bindings at line 1484 are `@4162`, calcRipplePos' depth-2 `set` bindings at line 1504 are `@4223`, depth-3 objects at line 1474 are `@4227`).
   IRO temporaries (split webs, CSE temporaries, switch values) are created after all of them, so they are numbered lowest of the `@` objects.
3. **Simplify.** Sweep the webs in ascending virtual-register order, pushing every web whose remaining degree is below K (29 GPRs: r0, r3-r12, r14-r31; 32 FPRs) and decrementing its neighbours; repeat sweeps until all are pushed.
   The degree counts physical-register neighbours and **coalesced webs, which are never pushed and so never leave their neighbours' degree**.
   If a sweep pushes nothing, the web with the lowest cost per remaining degree is pushed (seen only in execMovement_).
4. **Colour in reverse push order** (the dump's `regalloc-*-assigned.txt` order). Each web takes the lowest volatile register no coloured neighbour holds (r0 then r3-r12; f0-f13); failing that, the lowest callee-saved register already in use that no neighbour holds; failing that, a new callee-saved register just below the lowest in use (r31, r30, ...).

So for webs of ordinary degree the colouring order is: pcode temporaries (latest generated first), then `@` objects (earliest created first: shallow before deep, source order within a depth), then named locals in declaration order, then arguments last-declared first.
A web whose degree is still at least K when its sweep reaches it is pushed in a later sweep and so coloured *before* everything pushed in the first one: long-lived `this`, loop counters and hoisted constants in big functions.

## Reading a residue

1. Dump the function: `tools/mwcc-stack/dbg.sh SRC SYMBOL OUT` (Shift-JIS or PCH units: prepend `#include <SMS.pch>` and convert with iconv first).
2. `python3 tools/mwcc-stack/regalloc.py OUT mario/<Unit> SYMBOL [gpr|fpr]` lists every web whose register differs from retail with its colouring position; `--search N` tries moving each such web, `--move V:bW` tests a hand-written order, `--drop V:U,...` removes interference edges and re-runs simplify.
3. Translate the order retail needs into source with the rules above.
   A web that must be coloured *later* needs a lower number: declare it later, make it a named local instead of an inline binding, or push it one inline level deeper.
   A web that must be coloured *earlier* needs a higher number or a higher degree.

`regalloc.py` recovers retail's register per web by aligning the dump's pcode with decomp-diff; commutative-operand swaps (`fadds f1, f2, f1` against `f1, f1, f2`) show up as colour differences but are not colouring and are not fixable here.

## Levers

- **Declaration order of named locals** (C-style declarations at the top count as declared there).
  `TBossHanachan::changeAnmRateAndFrameUpdate_` (closed): retail colours the tumble-loop `i` and the head `MActor*` after the last loop's `i` and `actor`; declaring `int i; MActor* actor;` at the top for the last loop, with the head actor a separate later local, reproduces it.
  The earlier trial that moved the *head* actor to the top moved the wrong web.
- **Naming the values instead of binding them.** Named locals are coloured after every `@` object.
  `TFireWanwan::emitEffects` (closed): the slip-smoke `thing(pos[0][2], pos[1][2], pos[2][2])` ctor bindings (depth 1) were coloured before calcRipplePos' depth-2 `z`; `f32 x = pos[0][2]; ...; thing(x, y, z)` moves them behind it and `z` takes f31.
  The earlier trial named the ripple's components, the wrong side.
- **An implicit conversion instead of an explicit compare moves a pcode temporary.** `TNpcKeepAnm::keep`'s `mBlendOn = blend != OFF` generates the `neg` temporary after the pointer load, so it is coloured first and takes r3; `mBlendOn = blend;` (or a `bool` parameter) closes `TBaseNPC::requestNpcAnm_`.
  That is a shared-header edit (NpcBase.hpp, only caller NpcAnm.cpp), parked for the header owner.
- **Ghost degree.** Argument copies coalesced into r3-r6 stay in every long-lived web's degree.
  `TCardSave::waitForStop` (open): `this` and `result` have remaining degree 31 after the first sweep, so they are pushed last and take r31/r30 where retail has r26/r27; removing any three of the nine ghost edges (setMessage's getStringPtr results, the u16 message-ID bindings, the gpMSound copy) replays retail exactly (`--drop "32:64,77,79;36:64,77,79"`).
  Retail therefore has at least three fewer argument copies live across them; naming the setMessage pieces was inert.
  `TCardSave::perform` is the mirror case: `graphics` has degree 30 and two lower neighbours, so it is pushed in the first sweep after the scissor reference `@5011`; one more ghost neighbour would defer it and give retail's r31.

## Open residues read with the model

- `TGuide::appearGuidePane`: setPaneOffset's bindings are created `this`, `initial_x`, `initial_y` and retail colours them `initial_y`, `initial_x`, `this`; named offsets and a named pane change other webs (worse).
- `TGCConsole2::pauseOut`: the inlined startDisappearStar's unk160 `target_y` binding must be created after the unk108 bindings (one level deeper); accessor spellings cost 8 bytes of frame.
- `TFerrisWheel::control`: the named `sound` must be coloured before `@2539`, i.e. become an `@` object; a TU-local level or the unnamed accessor moves the frame by 8.
- `TMerrygoround::control`: the warp block's `joint` is an IRO split web (`@2050`) created after the loops' `this` copies; retail creates it before them.
- `TNerveAnimalBirdWaitOnGround::execute`: isWantToAction's `over` (depth-1 `@1911`) must be coloured after the IRO temporaries, which only a named local of the nerve or an IRO temporary achieves; both spellings tried change the frame.
- `TBaseNPC::npcWetting`: the switch value is an IRO CSE temporary (`@2278`) created after the first case's `mActorType` temporary; retail colours it first.
- `TNerveTabePukuFound::execute`: not colouring; retail coalesces getZDir's `_x` with the scaled component.
- `TMapObjTurn::touchWater`, `J3DSkinDeform::initMtxIndexArray`: commutative operand order, not colouring.

## Additions (c-g4, list B)

- **Extend a call argument's register across a block by naming it early** (closed `TAmiNoko::isHitValid`).
  The volatile block that retail starts two registers higher (f5/f4/f3 against our f3/f1/f2) needs f1/f2 as neighbours: `f32 upZ = mUp.z; f32 upX = mUp.x;` before or right after the dx/dz subtractions, then `matan(upZ, upX)`, coalesces the named copies into f1/f2 from their loads to the call.
  A web that needs a *physical* argument register as a neighbour ("as if f1/f2 were reserved for the pending call") is this lever, not colouring order: `--search` finds nothing for it.
- **The scheduler runs before colouring** (`backend-*-after-scheduling` precedes `before-regalloc`), so interference is taken from the scheduled order.
  `MSHandle::setSeDistanceVolume`: get_thing's `>> 30` takes r3 because the pre-regalloc schedule already consumed getSwBit's r3; retail's r5 needs r3 live across it in that first schedule.
- **Blocked pushes rank by cost/degree.** When no web is below K, the lowest `adjusted cost` (cost / remaining degree in `regalloc-*-assigned.txt`) is pushed first and coloured last.
  `TRKSuppAccessFile` (dumped with GC/1.1p1 and the unit's own C flags): done 91/28 against replyBuffer 88/28 colours done first; declaration order does not reach it.
- **Deferral by one degree.** `JPAVortexField::affect`: thing3.z (IRO temp) reaches the first sweep with remaining degree 31 and is pushed there, so fVar2 (deferred) is coloured before it; retail defers both. Count remaining degree at the sweep, not the total.
- **Mapping limits.** `regalloc.py` needs the pcode row count to equal the diff's; matan, Hxs2_Circle, xFadeBgm, walkAnmRateChange_ and several big functions differ by one or two rows and their "retail" registers are misaligned. C units need the unit's own flags without `-lang=c++` (`hx_wiper.c`: game flags plus `-inline noauto`).
- Readings recorded as TODOs: `TGraphTracer::traceSpline` (rail/web IRO CSE temps created in the wrong order), `TMapObjTree::initMapObj` (the new[] count is the first-created parse-time object), `evSetGraffitoMultiplied` (liveness, not order), `TGorogoro::behaveToWater` (depth-2 locals created in declaration order, retail reverse).
## Refinements (unit agent c-g3, list A)

- **IRO split temporaries.** A named local initialised from a load is usually replaced by an IRO temporary (`@N`, `x = @N`, then every use reads `@N` and `x` dies to a stack slot).
  These temporaries are created in statement order after all inline objects, so the local is coloured as an IRO temporary, and its declaration order is inert.
  `SMS_UnifyMaterial`: `unifier` is `@815` and `mat` is `@817`, and retail needs them the other way round; moving the declarations cannot change that.
  A local with two definitions (`f32 d = y; d -= ty;`) is not split and stays a named web, which is coloured after every `@`.
- **Constant operands come first.** In `k * (f32)dir` the pool load of `k` is generated before the conversion's pcode for every spelling (`dir * k`, `angle *= k` after `angle = dir`, and a TU-local helper with an `f32` parameter).
  So `k` gets the lowest pcode number and is coloured last (`TBathtub::getNearGrip`, closed by none of 7 spellings).
- **IRO reassociation.** `(m00 + m11) + m22 + 1.0f` becomes `1 + (m22 + (m00 + m11))`, because the leaf operand goes first.
  Naming the partial sum (`f32 t = m00 + m11;`) gives retail's order but costs a dead 4-byte slot (`MtxToQuat`, frame 0x28 to 0x30).
- **Measured, not landed:**
  - `TSpineEnemy::calcTurnSpeedToReach`: `double guess = __frsqrte(x); volatile f32 f = x * guess;` fixes both FPRs.
    `f32 result = 90.0f - tmp; return result;` then puts `f` at retail's 0x1c.
    Still missing: one 4-byte dead object above `f` (0x28 against 0x30).
  - `CPolarSubCamera::isNeedGroundCheck_`: `f32 a = JMASSin(min); a = mDistMin * a;` fixes the loads.
    Then `distY` (named, two definitions) must be coloured before `a`'s IRO temporary `@866`.
  - `TLiveActor::calcRideMomentum`: a named `diff`, then a named `vel` with `vel += diff`, fixes the diff and member registers.
    It costs 8 bytes of frame and moves the sum into `vel`'s register.
  - `TBGBeakHit::moveRequest`: `mag` is the IRO temporary `@6868` and `stretch` is a pcode temporary, so `stretch` is coloured first.
    A named `stretch` is forward-substituted and changes nothing.
- **List A triage.** Most `stack+reg` entries have frame gaps of 0x20 or more, or JGadget iterator slot strides.
  Their register residue cannot close before the frame does.
  With an equal frame and a single-move replay fix, only `startAppearStar`, `TShellCup::perform` (Mtx 8 low), `calcEmitterGlobalParams` (the `ref()` fix is -8 frame) and `TBossHanachan::init` (slots) remain.
- Batch triage: running `dbg.sh` and `regalloc.py --search` over a whole list in one sequential background job (about 1 min per big TU) ranks the targets before any edit.
