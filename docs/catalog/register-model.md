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

## Additions (c-e5)

- **A header wrapper overload adds ghost degree** (closed `TTinKoopaPartsBase::perform`).
  `curAnmEndsNext()` (which forwards `ANM_TYPE_BCK, nullptr`) instead of spelling the constants makes argument-copy webs coalesced into r4/r5; they lift `this`/`cue`/`graphics` to degree K, so the arguments are pushed in a later sweep and the joint index, live across `getModel()`, is coloured after them (r28 instead of r31).
  Tell: a callee-saved temporary numbered *below* the arguments in retail, same instructions.
- **Non-simple arguments into an UNUSED inline keep the value in a callee-saved binding** (closed `TTinKoopa::checkTinKoopaKillerApproachingMessage`).
  `calcCoasterDistanceInOrder(killer->getPathIdx(), graph->findNearestNodeIndex(pos, -1))` instead of two named node locals: the `to` binding is compared from r28 (retail) rather than from the call's r3, and the two bindings are the dead words retail has under the by-value `pos`.
- **`mr rX, rY` between two registers both holding `1`** is a nested `||` result copied from the outer one: `return a || b || c;` (closed `TOptionControl::isChangedSetting`), not a `bool r = result` local.
- **A constant multiplicand coloured first** (open: `TPaneScalingControl::update` in the three Option `update()`s, `TBathtub::getNearGrip`): our pcode generates the constant's `lfs` before the value's loads, retail after them; `f32 a = v; a *= K;` flips the `fmuls` operand order but recolours the block.
- **A named local cannot be coloured before an `@` object** (`TTinKoopaLaunchOrder::checkOrder`, partial): with the receiver named, mDirection's CSE temp (or makeKillerQueue's s8 binding) takes r4 first; retail's receiver must be an `@` object created earlier.

## Additions (c-e6)

- **A shared helper for two actors colours the second actor first** (partial, `TBEelTears::init`): `--search` wants `waterHitActor` coloured at position 2, i.e. an `@` binding, not a named local.
  Passing `getMActor("tears_waterhit.bmd")` straight into a TU-local `SetScreenTex(MActor*, const ResTIMG&)` (ChangeTextureAll + setLightType), used for both actors, fixes every register and two of the push_back iterator slots; three words of the iterator block stay 0xc low, so it is not landed.
- **Reuse picks the lowest in-use callee-saved FPR** (`JPADrawExecRotDirectional[Cross]::exec`): pt's components are IRO temps created in store order, and the first coloured takes f29 (lowest in use), not f31.
  Retail's x-first loads with x in f31 therefore need x's temp created last but loaded first; `set()`, scalars, and `getGlobalPosition` all create it first.
- **A two-definition named local below K is coloured last** (`TFireWanwan::updatePollute`): `radius` (degree 30) needs two more neighbours or an `@` home; an uninitialised top declaration is inert and a TU-local radius helper changes instructions.

## Additions (c-e7)

- **Declare the loop's other named scalars ahead of a long-lived one to colour it first** (closed `TBigWindmill::control`).
  `angle` (degree exactly K) was numbered above the loop's `rad` and `offset`, so their first-sweep pushes lowered its remaining degree and it was pushed early, coloured after the three hoisted loop literals (f28; retail f31).
  `f32 rad; f32 offset;` declared before `f32 angle = ...` numbers `angle` lowest of the three: it meets the first sweep at full degree, is deferred, and is coloured first.
  Tell: `--search` fixes everything by moving one named web to position 0, and that web's degree is at or just above K.
- **A named call result can restore retail's call order** (`TBaseNPC::perform`, +0.4): `f32 dist = getAnmOffDist_();` before `NpcSquaredDist(...) > CLBSquared(dist)` puts the out-of-line `getAnmOffDist_` call first and the square sum (into f31) between the two calls, where the nested `CLBSquared(getAnmOffDist_())` ran both calls before the sum.
- Declaring three named squares last-to-first (`sqZ`, `sqY`, `sqX`) gives retail's f2/f0/f1 in `ModelGateLength`, but not its x-first load order.

## Additions (c-g9, regsweep)

- **Sweep.** `tools/mwcc-stack/regsweep.py` over the 222 register-only functions: 17 dumps or replays fail, 16 show no differing mapped web (commutative swaps or unmapped rows), and 65 are fixed by one web move (31 of them with the frame equal).
  Most of the 31 are slot-offset or already-recorded residues; the actionable tell is the `[left/degree]` from `regalloc.py --why`.
- **Deferral by numbering** (closed `TBossHanachan::setHeadAndBodyAnm`, `M3UMtxCalcBlendAux`).
  When `--search` moves a long-lived web to position 0-1 and `--why` shows it one short of K at its first-sweep turn, remove one *lower-numbered* neighbour that is pushed before it in that sweep; it is then deferred and coloured before everything pushed there.
  Named locals are always numbered below `@` objects, so: for a named web, declare the blocking named local ahead of it (`int frame;` before `body`); for an IRO temporary (`&j3dSys.mModel`), turn the blocking named locals into an inline level's parameters (the `p`/`q` scaled copy as `M3UScaleMtxCopy`), which numbers them above it.
- **An inline predicate's parameter is coloured before an IRO temporary** (`TDirectionCalc::sub` 98.4 to 99.8, `calcTurnDirection` 98.2 to 99.3): `IsLongWayRound(dir - mDirection)` in place of a named `diff`.
  The rest of `sub` is a pre-regalloc schedule difference (the wrap's load placed before the `fmr` that saves the incoming f1), not colouring.

## Additions (c-g10, regsweep apply)

- **An inline accessor turns a named local into an IRO temporary** (closed `TNerveTamaNokoThrown`, `TNerveMameGessoThrown`, `TNerveBombHeiThrown`).
  `f32 power = *gpMarioThrowPower;` stays a named web, coloured after every `@` object; `f32 power = SMS_GetMarioThrowPower();` (a TU-local `{ return *gpMarioThrowPower; }`, like the other SMS_GetMario* accessors) makes `power` an IRO split temporary created in statement order, ahead of the cosine's, so it takes f2.
  The named local's slot goes dead, and with `rate`'s it gives the 8 bytes retail has above `vel`.
  Tell: a named web that `--search` wants coloured just before an `@` object, with our frame 4-8 bytes short of retail.
  Inert on `TMapObjGeneral::thrown`, whose frame is already equal.
- **Declaration order closes a loop counter swap** (`TBossHanachan::init`, registers only; its slots stay open): `--why` showed `group` pushed after `i`, its only lower neighbour, and `int i;` declared ahead of `group` for the last loop reverses the two.
- Still open after this pass: `TBossManta::calcRootMatrix` needs cross2's y component generated first (its 1.0f literal must be numbered above the `0*v.z` product); `TRocket::calcRootMatrix` (lenZ's IRO temporary sits at exactly K) and `TCardSave::perform` need one more or one fewer neighbour, and no spelling found supplies it.

## Operand order and generation order (research c-r11)

Two source rules decide which operand of a binary node comes first, in the instruction and in the vreg numbering.
Both were read from `dbg.sh` dumps (`frontend-00-ast-initial-code.txt`, `backend-00-initial-code.txt`) and a scratch TU compiled with GC/1.2.5 (`-O4,p`, the game flags).

- **The frontend canonicalises `+` and `*` at parse time; nothing later re-sorts them.**
  In `x + 200.0f`, `200.0f + x`, `x * k`, `(f32)i * k`, `call() * k` and `(a - b) * k` the literal is the left child already in `frontend-00` (touchWater's `EADD(EFLOATCONST 200, throwY)`), so the `fadds`/`fmuls` reads the constant first.
  Otherwise the right operand moves left only when it is simple and the left one is not.
  Simple: a leaf (variable, parameter, `p->m`), a leaf plus an integer constant (`p->n + 2`), a unary `-x`, a conversion.
  Not simple: a binary node of two values (`a - b`), an indexed load `q[i]`, a call, inline accessors included (they are still calls at parse time).
  So `(p->n + 2) * d` keeps its order while `(num() + 2) * d` becomes `d * (...)` (the MapObjMonte board-number fork), and two simple or two complex operands keep source order.
  The PCode keeps AST order (`fadds rD, left, right`; jpb-mwcc `docs/SCHEDULER.md`: "FADDS operand order = materialization order"), and mwcc-rs records the same leaf-first canonicalisation empirically (`expressions/arithmetic.rs`, a scaled subscript "canonicalizes it to the SECOND operand of a commutative op").
- **So retail's value-first `x * K` or `x + K` means the constant was not a literal when the expression was built.**
  Three spellings keep source order: a compound assignment (`t += K`, `t *= K`: the result lands in `t`'s own web), an inline parameter bound to the literal (`throwObjToFront(obj, 200.0f, ...)` makes touchWater's `mPosition.y + y_offset` read `y` first), or a non-`const` named local holding the literal (`f32 k = K; x * k`: IRO propagates it, but the dead local keeps a 4-byte home in a non-leaf function).
  `const` locals, `static const`, `(f32)` casts and double literals are folded at parse and canonicalised like literals.
  Measured on 13 retail operand orders in 8 functions, each flipped by the spelling the rule predicts: SelectShine2 `move` (4, a named `rate`), enemyMario `drawHPMeter` (2, `+=`), pakkun Stay (2, the getter named so both operands are leaves), hinokuri2 `moveObject` (`+=`), TabePuku Drag (`*=`), getPos (named step), touchWater (the inlined helper), J3DSkinDeform `initMtxIndexArray` (`base += n`, research 215).
  Most of these functions still carry a frame gap, so the flip alone closes none of them; take it together with the frame work, and prefer the spelling with no dead home.
- **Operands are generated left first, except that a subtree holding a real (not inlined) call is generated first.**
  `K * (f32)s` generates the literal's `lfs` before the conversion, so it gets the lower vreg and is coloured after the conversion's temporaries; `f() * K` (an out-of-line `f`) generates the call and its conversion first and the literal last, so the literal is coloured first while the `fmuls` still reads it first.
  An inlined callee's result is a temporary, a leaf, so `inl() * K` behaves like `K * s` and cannot give the late literal.
  Closed `TBathtub::getNearGrip`: retail's late literal meant `matan(...) * (360.0f / 65536.0f)` sits inside the UNUSED `getDir`, whose map size (0x9c) that body now matches exactly; the call sites read `f32 angle = getDir(...)`.
  Tell: a constant `lfs` that retail colours first (lowest volatile) right after a `bl` whose result is converted and scaled; look for an UNUSED helper 4-10 instructions short of its map size.
- **An inline-budget side effect.** Shortening a body by moving work into its helper can make a caller inline it: getNearGrip dropped under allowsTumble's budget until a named `MtxPtr mtx = *getRootJointMtx();` restored one statement.
  After shortening any body, check `changes_all` for a sibling that now inlines it (allowsTumble fell to 68%).
- **Not source order, per the dumps.** `li r8,0; addi r7,r8,0` (TSelectGrad::perform) is two copies of `nextCycle`'s zero into `i` and the strength-reduced `i*4`; our three `li` are separate object webs that CSE and constant propagation never merge (`s32`/`BOOL`/`u8` flags and hoisted or outer-scope `i` are all inert).
  The `new JUTTexture(res())` extra copy (Talk2D2, CardSave, SelectMenu, GCConsole2) is a second web for storeTIMG's `this` that the scheduler hoists above `getGlbResource`; after inlining, our AST substitutes `@new` directly (no `this` binding survives to `frontend-00`), and the class shapes tried in a scratch TU (base class, init list, virtual dtor, out-of-class inline ctor) never create one.
  `addi rD, rS, 0` in the final code is only MWCC's spelling of a PCode `mr`, not an add of zero.

## Additions (c-k2)

- An inline body's shape decides its temporary's colouring rank.
  A ternary return is lowered into an optimiser temporary created after the caller's named locals' split temporaries; an if/return keeps the inliner's own return object, which is coloured earlier.
  Proved by `TAfterEffect::checkFlag` inside `TAfterEffect::perform` (ScreenUtil closed).
- Splitting `a >> s & m` into two statements (`u8 x = a >> s; x &= m;`) keeps the source register live one instruction longer in the pre-allocation schedule.
  `u32`/`int` two-step spellings are inert; the `u8` truncation was needed.
  Proved by `MSHandle::setSeDistanceVolume` (MSHandle closed).
- Comparing retail's slot offsets with ours in the debugger's `variables.txt` can pin a missing object and a creation-order swap exactly (see the MapObjBase `initAndRegister` entry in `units/other-units.md`).
- The debugger (`tools/mwcc-stack/dbg.sh`) takes about 7 s per function once retrowin32 is built; build it first, it closed both units.
