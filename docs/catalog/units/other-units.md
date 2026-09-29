# Other units: exceptions and rejected trials

Per-unit notes that do not generalise.
Search this file for the unit name before retrying a stalled function.
Scores are as of the batch noted and may be stale.
Longer evidence for batches 25-39 is in `docs/progress/GMSE01-closure-audit-batch<N>.md`.

## Camera

- **CameraDemo** (c-k2, debugger-read): `updateDemoCamera_`'s two `origin + offset` return buffers are parse-time objects @616/@617 (`pointer(void)`), allocated directly under the named block; retail has 36 bytes of objects created before them. Not closed.
- **cameragc / CameraChange / cameralib sweep (2026-09-17):** chained assignment puts the inner `operator=` one level deeper (`mPreviousTarget = mCurrentTarget = mTargetBeforeFixedMode`: the ROM inlines the outer off the returned reference and calls the inner, weak `__as__13TTargetCamera`; one wrapper level above the statement flips it, `changeCamModeSub_` 89 -> 98). The depth-2 allowance measured **10** statements here (`checkStatusType` expands at 10, calls at 11). Per-call-site inlining is not `if`-nesting (a copy of `calcExternalData_()` outside all `if`s expands the same helpers). `inline` is *not* how retail got weak-and-not-inlined: adding it to `ctrlGameCamera_` (0x468) expands it into `perform` (95 -> 70); open. Factoring `CLBCalcNearNinePos`'s duplicated up/right transform into one helper: 37 -> 62. `mult33` writes over its argument (the ROM copies the input after `setRotate`); `identity33()` (9 stores) not `identity()`. MWCC extends `s16` returns at the caller: an `s16` accumulator gives the ROM's `mr r3, r5`. A named `const TCameraMapTool* prev = unk70;` was worth 8 bytes (qualifies "pointer locals are worth zero"). `getThing`'s `return unk8[unk4 - 1]` needs an explicit `const int* p;` if/else for the ROM's `subi` + shared `lwz 0(p)`. Fabricated `execLButtonCameraOnProc_` adds the level that makes `changeCamModeSpecifyFrame_` a `bl` at depth 4 while keeping it expanded at depth 2. Two missing behaviours restored in `execCameraModeChangeProc_` (`MSD_SE_SY_NOT_COLLECT` behind `checkFrameMeaning(0x4000)`; `CAMERA_MODE_FOLLOW_D` short-circuit). Open header items: `TMario::checkStatusType(s32)` must be callable from cameragc (five sites; forcing it gives unit 95 -> 97 and `isMarioReadyGun_` size-exact; the one-statement in-class body at depth 2 expands; three extra levels flip it); one-arg `TRotation3::mult33(TVec3&)` should forward as `TVec3 tmp(v); mult33(tmp, v);` (`CLBRotatePosAndUp` 73 -> 84); `MsClamp<f32>` needs to be out of line from `perform`/`changeCamModeSub_` (local 0x20 in both TUs). `CLBIsPointInCube`/`CLBCalcPointInCubeRatio`/`CLBCalc2DFPos` are pure 64/64/24-byte frame gaps.

- **CameraMode** (linked): current-mode check is an out-of-line call, previous-mode check an inline switch. Added a fabricated current-mode predicate analogous to `isLButtonCamera`, used only for the current-mode branch.
- **CameraTalk** (linked): existing Mario-angle accessor restores talk setup.
- **Sun bounds** (`sunmgr`, `lensglow`, `lensflare`): `isInBounds` via a const reference to the first position recovers `lfsu`/offset-4 accesses. Exception: the `sunmodel` self-call addresses members directly, with a named bound.
- **CameraSecureView, sunmgr**: accessor/ABS/predicate and stream-chain/array/loop trials failed (batch 32 audit).
- CameraWarp, area-cylinder, multiplayer-camera frame trials: batch 28 audit.

## Enemy

- **BossEel::init**: tooth-model stores use indices 0, 1, 2; eye/heart loader flag is 0x10240000; post-init collision uses virtual slot 0x1c (`setUpTrans`). A named skin-deformer allocation local and one shared resource pointer help. Open: frame 0x300 vs 0x310, eye/heart copy registers. Rule: inspect differing immediates, stack stores and virtual slots even at 99%.
- **SleepBossHanachan** (linked): fall nerve declares position before a named `BOOL` animation result via `getMActor`, assigning inside the success branch.
- **egggen** (linked). A vector `squared` method removes a required SDK call.
- **seal / effectEnemy**: see `../codegen-tells.md` bool rules.
- **hanasambo**: `TSamboFlower(const char* name = "サンボフラワー")` with `new TSamboFlower` in `createEnemyInstance`. The map lists the constructor as a weak out-of-line symbol (duplicated in `MarNameRefGen_Enemy`); spelling the name at the call site inlines it. Open: UNUSED `hide` is 0xb8 vs map 0x50 and `isUseCallBack` 0x150 vs 0x140.
- **Spider, Beam**: see vectors in `../codegen-tells.md`. Beam's `coneInPlane` UNUSED body must stay 348 bytes.
- **AnimalNerve**: two `MsRandI(hi, lo)` calls corrected to `(lo, hi)`. Open: frame 0x118 vs 0xE8.
- **popo / rocket**: the emit-matrix normalisation has a real bug that is in the ROM: the three zero guards are rotated one step against the divisors (`if (lenZ != 0) col0 /= lenX;` ...). Both objects' disassembly agrees; do not "fix" it. The two units share the nozzle-possession protocol and statics.

## GC2D

- **CardSave::waitForChoice** and siblings: `updateCenteredSize` sites use 0x18/0x14 (`mOffsetInterpolator`), not 0x30/0x2C. Naming width/height or expanding to `setPaneSize`/`setPaneOffset` regresses; `pos.set` and reversed additions don't fix particle scheduling. `CardLoad::selectFunction` accesses members 0x20 above the current layout; check the constructor first.
- **GCConsole2::load**: health-pane pairs use `i*2`, `i*2+1`. At 0xec04 `stwu` updates `unk2AC`, but the following blend calls use `unk2A0` (r25); only hide uses `unk2AC`. Default-constructed colour local and named texture allocation rejected.
- **SelectDir::rsetup**: explicit `TDStageDisp("<DStageDisp>", 0)` places the flag temporary better. Naming the three camera vectors before allocation regresses (98.4%). Open: frame 0x610 vs 0x648.
- **ProgSelect**: removed old selector padding (explained small regression). Hoisted controller booleans add normalisation; `u8` selection local no effect.
- **MessageLoader**: tag/length declarations before the outer stream, typed advancing INF1 cursor, named discarded entry-size read. Chained extractions and extra locals regress.
- **MessageUtil** (linked): tag `s32` (matches `readS32`), `u16 entrySize` declared with parser locals before the payload stream.

## Map

- **MapCollisionEntry**: name the translation-only flag in `move` and warp `setUp`; warp also declares its vector before the predicate and uses `set`. Remaining: see `../frame-gaps.md`.
- **MapCollisionPlane** (linked): fabricated `worldToGridIndex` computes `mOneOverScale * (v + mExtent)` into a named `int`. `MapObjPlane::depress` keeps the fractional API.
- **MarNameRefGen_Map** (linked): `TPollutionTest` inline ctor initialises only `TViewObj` (size 0x10); call with no name argument.
- **PollutionObj** (linked): ground-query pointer at slot 0x54 declared first; named `is_near` and centre-height results.
- **Water-filter / Shimmer**: reuse `isDemoCamera()`/`getUnk124()`; declare inverse-view, translation and scale matrices before transform info. Shimmer needs model `calc`, `viewCalc`, `entry` (vtable 0x10, 0x14, 0x0C).
- **SplashManager** (linked): colour at 0x54, by-value copy at 0x58 via a compound literal at `requestCol`; alpha named before vertex writes.
- **MapStaticObject** (closed, c-k1): `init`'s push_back iterator pair was 4 low with the frame equal.
  The debugger's slot list showed one named word (`ref`) too many above the pair and one object too few between the pair and insert's iterators.
  `search<TScreenTexture>(...)->getTexture()->getTexInfo()` unnamed moves the word (getTexture's `this` binding is created after the pair), but adds one IRO temporary low; `setUpUnk8TRS(mPosition, ...)` in initMapCollision (raw member, not `getPosition()`) removes one.
  Neither alone helps; together exact.
- **MapModel, MapXlu, PollutionPos, PollutionManager, MapObjWater, MapObjFloat, MapEventSirena**: trials in batch 30/31/33/37 audits. Sirena: named flag-manager result only partially shifts the slot.

## MSound

- **MSHandle** (closed c-k2): `setSeDistanceVolume`'s curve index is `u8 curve = getSwBit() >> 16; curve &= 7;`. The folded `>> 16 & 7` is one pcode op that consumes getSwBit's r3 before get_thing's `>> 30`, which then takes r3; the split mask keeps r3 live past it in the first schedule, giving retail's r5.
- **MAnmSound** (c-k2, open, debugger-read): `MAnmSoundNPC::startAnimSound`'s volume `dVar10` has FPR degree 31 (K 32), one short of being deferred and coloured first (retail f31); the translation pointer is the IRO temp @417 coloured after the inlined `mario` @404, retail colours it among pcode temps. Inert: named 2000/600 locals, a named calcVolume result, an else arm, `(this, ptr, actor)` volume helpers (frame +-8).
- **MSModBgm**: repeated zero-load mismatch survives bool/u8, integer-zero, assignment-order and early-return trials. `getTiming` optional-output behaviour lacks evidence.
- **MovieRumble**: `init`/`checkRumbleOff` share a missing pointer move inside `readCurInfo`; getter placement, validity locals, signed group, const pointer all fail.

## NPC

- **NpcAnm::npcWetting**: naming the sunflower predicate or final switch value doesn't fix registers and can grow the frame (0x178 vs 0x160).
- **NpcCallback**: `checkLiveFlag` inside the conditional bool; named Mario Y across the range call.
- **NpcColor**: direct two-colour arguments and named material lookup don't fix the frame.
- **NpcInbetween**: ratio accessor recovers float registers but grows the frame by 8.
- **isCanWalk / execWalk**: horizontal `(dx, 0, dz)` against `CLBSquared(10.0f)` (not 3D with 2.5625). `fabsf(mRotation.y - angle)` before `MsWrap`. Direction copied by vector assignment. `unkF4.getPoint() - mPosition` adds an out-of-line `sub`.
- `TNerveNPCGraphWander::execute` has an existing `(void)&local_58` workaround; not investigated.

## System

- **TTimeRec::startTimer/endTimer**: existing `JUtility::TColor` gives four-byte colour storage; convert before the null check and `OSGetTick`. Four-component overload loads the instance before constructing colour. Fixed livemanager/objmanager `perform` (both linked). TimeRec has three UNUSED stubs.
- **objmanager**: `stream >> capacity` then `initObjArray(capacity)`; direct `readS32` as the argument grows the frame.
- **SnapTimeObj**: packed-colour overload; open colour slot 0x34 vs 0x38.
- **Strategy / ObjHitCheck**: `s32` counter keeps the initial branch; ObjHitCheck already matches.
- **PerformList**: `load` differs by one stream-read slot; by-value iterator in `perform`.
- **MarioGamePad**: see padding list in `../frame-gaps.md`.
- **MarDirectorEvent::setNextStage** (c-k3, open, debugger-read): named `next` 0x30 (retail 0x38), then `curr` and the `MDEApp()` result `@1062`, both register-held, then the ctor's `TFlagT(0)` temporary `@1069` at 0x2c (retail's too).
  Retail's 8 extra bytes are created after `next` and before `@1069`, i.e. two dead 4-byte named or parse-time objects; `curr` and `@1062` homed would be exactly that.
  Inert: a named `TApplication& app = gpApplication` for setNextArea and `curr` (a reference to a global is an alias with no object); `app.setMovie`/a `TApplication*` local change code (93.8).
- **liveinterp::linGetSRT** (c-k3, open): a TU-local `pushFloat(interp, const f32& value)` (default slice, direct writes, push) at the nine float sites is instruction-exact at frame 0xc0 (retail 0xe0); the by-value `f32 value` form and one calling `setDataFloat` change code (93.7).

## TRK

- **support::TRKSuppAccessFile** (c-k3, open, debugger-read with GC/1.1p1 and the unit's C flags): MWCC's spill cost is `2 x uses + defs`, each weighted by the block's loop weight (8 in the loop, 1 outside): `done` 2x41 + 9 = 91, `replyBuffer` 2x40 + 8 = 88, `length` 120, `read` 97, `data` 35, all exact.
  Both reach the blocked phase with 28 remaining neighbours (12 physical registers plus 16 coalesced call-result copies, `mr rN, r3; mr error, rN`, which never leave the degree), so `done` (3.25) is pushed after `replyBuffer` (3.14) and coloured first.
  One more ghost neighbour on `done` flips it (91/29 = 3.138 < 3.143); every ghost inside the loop also neighbours `replyBuffer`, so the extra copy must sit between `done = 0` and the loop or after the loop before `*count = done`.
  Inert: `done = error = DS_NoError`, the init as a `for` clause, `*io_result` first, `exit == FALSE`, `&data[done]`; the `length = *count - done` clamp and the release after `done += length` change code.

## JSystem

- **JDREfbSetting::IssueGXCopyDisp** (c-k2, open, debugger-read): the six webs are pcode temps coloured latest-first; the `flags & 0x20` test is generated after the inlined antialias conversion, so it takes r0 where retail has r4. Named copies of the test are forwarded back (+8 frame, registers unchanged).
- **JAIGFrameSe::checkNextFrameSe** (c-k4, 98.3 to 99.3, debugger-read): the candidate counter and the category's `mMaxPlaying` are one `u8` local (`num`).
  A separate `maxPlaying` initialised from one load is split by the IR optimiser (`maxPlaying = @250 = load`, the hoisted `+ 1` reads `@250`), which costs a copy and rotates every callee-saved register; with two definitions the local is never split and the load lands in its register.
  Tell: retail's load writes straight into a callee-saved register that an earlier, unrelated counter also used.
  Open: `it`/`pi`/`(u8)camEnd` (r18/r20/r27 in retail). The replay closes with top declarations `it; cam; pi; num` after `bVar18` plus one extra coalesced web neighbouring the four hoisted pool temps but not `it`; the declarations alone are 85 markers (the pool temps fall in the second sweep).
- **JKRExpHeap::allocFromHead** (c-k4, open, debugger-read): the `nor` placement is colouring: the loop-invariant mask (`@167`) is created after getContent()'s forced-load result (`@166`, which `content` becomes) and so coloured after it; retail colours the mask first. Raw `(void*)(block + 1)` fixes the mask but recolours the rest (97.1).

## MoveBG and Animal

- **MapObjBase::initAndRegister** (c-k2, open, debugger-read): retail's slots are ours plus one 4-byte object created first (above the end() return buffer) and the TNameRefGen binder created before insert's argument pair. Named group binds the group, not the list (`addi r31, r3, 0`), `getChildren()` spellings are +8.
- **MapObjAirport**: `0x484D` clear-sign sound via `MSound::startSoundSystemSE`; use the director accessor and pollution global. Open: passed camera flag 0x34 vs 0x3C; pool ctor and `appear` UNUSED bodies undersized.
- **MapObjBall**: per-call-site inlining, see `../codegen-tells.md`. Scores: `hold` 48%, `touchWall` 75%, `TBigWatermelon::touchActor` 61%, `TResetFruit::control` 52%, `receiveMessage` 69%.
- **MapObjPollution**: accessor/loop trial reached the right frame with wrong registers (batch 39 audit).
- **AnimalManager**: `loadSaveParams_` restored; named near-plane input for clipping.
- **EffectUtil**: missing one UNUSED definition; two `cross2` calls regress.
- **enemyMario / bgtentacle (batch 58):** `TEnemyMario::getStickPower()` (UNUSED 0x8) returns `0.0f` and `setStickToAngle` is `power * (JMASSin(angle) * getStickPower())`: a literal `0.0f` folds away, the inlined accessor keeps the product (the retail enemy Mario's stick is always zero). `initEnemyValues`' three if/else-if chains are `switch`es with grouped cases (`case 0: case 1:` first builds retail's `beq`/`bge` tree and puts `"/../map/pad/Setting.prm"` ahead of the param names in the pool). `matan(dz, dx)` with `dx` named first. `TBGTentacleMtxCalc::calc` subtracts `col3(i-1) - col3(i-2)`; the old `char trash[0x10]` is gone. `unk84 += v * (f32)i * 80.0f` by-value chain is worth 128 bytes of frame and two `scale()` calls (retail elides the first result into the named vector; open). Own-class accessor reads (`getState()`/`getOwner()`) are the frame lever for the 88-136-byte-short functions. Ruled out: by-value `TNode::getPosition()`, `char` replay letter (right `extsb`, permutes every register), `.sub()` for `-=`, `SMS_GetMarioPos()`. Open header items: `MAnmSound` dtor in-class (UNUSED copy in enemyMario), `M3UMtxCalcSIAnmBlendQuat` default ctor in-class (weak, `__construct_new_array`), `TVec3::cross()` store order (x, y, reload, z), and retail's `consider()` calling const `TGraphTracer::getGraph()` and `TPathNode::getPoint()` out of line (frame 0x250 vs 0x220; not the caller-size family). `calcAttackGuideAnm` and `MtxCalc::calc` share a residue: retail promotes the subtrahend's `.y`/`.z` into f30/f31 and re-reads only `.x`.
- **bossManta / chuuhana (batch 59):** `AttackMario(this)`, not `AttackMario(mCollisions[i])`, in both collision loops (tell: retail reads `0x10(rN)` through the register holding `this`, one fewer saved GPR). GC2D hint ids `0xC/0xD/0xE` (small ids, third unit). `setupEfbAlpha`'s quad z is `-10.0f` (shared `.sdata2` slot with `drawMantaShadow`). `setGoal`: `f32 swingAngle = swing.rand();` and `TVec3 dir(0, 0, 1)` declared before the matrix (its stores sit after `bl rand`). `ChuuHanaBodyCallback` projects onto matrix *columns* as three `TVec3` locals (zDir, xDir, yDir), three named `f32` results, the vector built once at the end, hand-built identity as in popo. `behaveToWater`'s guard is one positive `||` with `(mNewSw && nerve == Stick)` plus `else if (!mNewSw && nerve == KeepBalance)`. `TChuuHana::margeVelocity` (UNUSED 0xc4) leaves `onLiveFlag(AIRBORNE)` and the position bump to its call sites (ordered oppositely in `behaveToWater` and `moveObject`). `initSetEnemies`' `graphlist` has six entries (kohana0, kohana1 x2, kohana2 x3). `updateMantaEscape` deliberately dropped 90 -> 84: retail keeps one 12-byte vector, zeroes `y` in place and hoists only `y`/`z` out of both loops; the old two-vector spelling is disproved by the frame extent. Open: `margeVelocity`'s discarded `length()` guard over two adjacent `mVelocity` copies (only residue of `moveObject`/`behaveToWater`); `updateAttractor` CSEs `pusher.squared()` into a saved FPR; `setSafeGoal()`'s `xoris` one slot late at four sites; bossManta's whole-TU non-weak ORDER failure (29+ symbols, a reordering job). Ruled out: including `MapCollisionEntry.hpp` for the missing zero/one `.rodata` pair (numbering offset is non-constant; data 42 -> 18). Both units' data scores are depressed by dead-stripped objects the target lacks and are not work items.

- **MapObjItem2 TJumpBase::control** (c-k4, 97.2 to 98.0): case 5's two `sraw` (retail derives the sine and cosine index twice from one `lha`/`clrlwi`) come from `JMASSin(SMS_GetMarioAngleY())`/`JMASCos(SMS_GetMarioAngleY())`: two inline results are not merged by the IR optimiser, and the backend CSE merges only the loads and the `clrlwi`.
  A named `int angle` (or `*gpMarioAngleY` twice) is merged whole.
  The `this`/pool-base swap (98.0 to 99.9) was ghost degree: `this` and `prevState` both reach the second sweep at remaining degree 30, 15 of it coalesced getMActor() result webs; raw `mMActor` at the thirteen sites removes them, both are pushed before the pool base, and it takes r31.
  Open: only the frame (0x90 against 0x88): retail's mVelocity copy is an unnamed temporary under the vector, not a named `v2`, and its low region has three fewer dead words than SMS_GetMarioAngleY's inlined trig leaves.

- **MapObjDolpic TMonumentShine::hitByWater** (c-k4, 98.4 to 99.7, debugger-read): the header `cross()`'s `_x`/`_y` are depth-1 inline objects and are coloured before the IR CSE temporaries of the subtractions, stealing f4/f5; writing the three components out lets IRO scalar-replace `cross` into temporaries coloured last, retail's order.
  Open: one 4-byte dead object below the named vectors (the inline's `_z` supplied it before); no cross spelling or accessor lever tried gives it back.

## MapObjOption (closure batch 136)

- 100/100 with `validate-symbol-order` PASS and still 234 bytes of `.rodata` out of order at link time until `Map/MapCollisionEntry.hpp` was moved after `M3DUtil/InfectiousStrings.hpp` (second instance of the DebuTelesa finding, this time with a DOL consequence). Linked.

## Closure batch c-k5 (2026-09-28)

- **enemymanager `copyAnmMtx`** closed (99.71 -> 100). Three changes, each read from the debugger: the scratch matrix is a `TPosition3f` (as `unk48` is, per the dead-stripped weak TPosition3 ctor), whose conversion operator keeps its address in r28 across the loop where a fabricated `MtxPtr wtf = afStack;` had imitated it; the scale is read from raw `mScaling` (the named `const TVec3& v = getScaling()` reference was a dead word under the matrix, 0x68 for 0x64); and the frame index comes straight from `getCurAnmFrameNo` (the direct-return fork split the raw index from its `slwi`).
  The `EnemymanagerGetMActor` identity binder is still needed for 0x10 of frame (0xb0 without it); raw, named, reversed and `int` spellings of the first test and every `getModel()` subset of the other sites are worse.
  `performShared` (99.72 -> 99.94): its alive-count loop is the inlined `countLivingEnemy()` (global in the map). As inliner objects the count and the loop's byte offset share one zero, which gives retail's `li r5, 0; addi r3, r5, 0`; named locals never did (the same shape as TSelectGrad/TEMario, worth trying there). countLivingEnemy reads `(const TSpineEnemy*)TObjManager::getObj(i)` so the read still expands at depth 2 and its own copy stays exact.
  Left: every instruction exact, frame 0xf8 for 0xf0; the TTimeRec colour temps sit at 0xc4/0xbc for 0xbc/0xb8 (countLivingEnemy's dead int result object between them, one more dead word below).
- **enemyAttachment `generatePolluteModel`**: the ground check belongs in the UNUSED `TEnemyPolluteModel::generate`. Inlined callee locals are created last-declared first (frame-model 8b), so only a body declaring `check` before the matrix puts `check` (0x3c) directly under the matrix (0x40); and with the check inside, the out-of-line `generate` is the map's 0x178 exactly (it was 0xf8, and the old TODO blamed `identity33` depth).
  Left: both slots 4 low, one dead word short after `check`. `isIllegalData()`/`checkFlag()` without the flag binder give frame 0x90; `SMS_IsWaterSurface()` drops `generate` to 0x140; `!isLegal()` adds six instructions. `bind` is the known-open `a = b - c` class, so the unit cannot close yet.
- **JPAMath `JPAVecToRotaMtx`** (62%): checked term by term against retail (cross, sum of squares, dot, guarded sqrt, zero-or-scale, the nine entries): same behaviour, the gap is purely retail's memory-resident `axis`. Whole body in a TU-local inline (62.7) and an asm `fres` helper (42.6) do not make it memory-resident. `JPAGetRMtxTVecElement`: `regalloc.py` does not replay this function (22 misses with the current order).
- **CameraChange `changeCamModeSub_`**: the `bgt; b join` pair survives six more TU-local pop bodies and two call-site shapes.
- **MapWarp `init`**: the swapped registers are the IRO CSE temporaries of `local_180[i]`/`local_1d0[i]` (`regalloc.py`: one move fixes two of three webs); named copies are worse.
- **smallEnemy `genEventCoin`**: our PCode keeps the stores in source order until the scheduler, which puts `local_d0.x` before `mtx[2][3]`; a priority difference, not a spelling of the stores.
- **MarioPhysics `checkGroundAtJumping`**: retail's `pos` has one named 4-byte local above it and one more dead word below; an uninitialised `f32` carrying `checkRoofPlane`'s result takes a register instead.

## Closure batch c-k6 (2026-09-28)

- **MapObjFlag `TMapObjFlagManager::perform`** closed (94.67 -> 100). The inlined UNUSED `update()` passes the matrix as the raw array `mMtx.mMtx`: through `TMatrix34`'s conversion operator the argument is a binding web (`addi rA, r28, 0x8c; mr r3, rA`), and that one extra pre-RA instruction reorders the scheduler's three rotation conversions (ours y first, retail z, y, x; the IR order and temp slots were identical all along).
  The 0x14 of dead low region below the JUTTexture is `getPosition()` for the three position arguments plus `SMSGetMarDirector()->getCurrentMap()`; `getRotation()` in place of `getPosition()` is the same price, and all three accessors together are +8 too many.
  `TMapObjFlag::draw` stays open: 80 bytes of low region (20 dead words: six GXPosition3f32 sites spill only `x` in ours, retail may spill all three) and the y*4/z*12 r5/r6 swap.
- **GC2D/Menu `TMenuPlane` ctor** 93.07 -> 96.27, frame exact: iterating with `unk14->getFirstChild()`/`getEndChild()` and the iterator declared in the `for` keeps `this` out of the loop test, so the post-loop `lwz r31, 8(r1)` no longer lands inside the loop and the textbox block reloads `this` as retail does.
  Left: retail re-reads `unk28` after the `stwx` into `local_420` (its IR optimiser treats the array store as possibly aliasing `this->unk28`); only a store through a separate `J2DTextBox**` reproduces that, and it is an alias pointer, so not committed. The colour temporaries' 8-byte stride stays.
  `TMenuBase::perform`: with raw `unk10`, explicit `x2 - x1` widths give frame 0x118 but put orthoGraph at 0x1c.
- **MapMirror `TMirrorModelManager::perform`** (91.3): the residue is structural and sits in the deep `entry -> makeMirrorViewMtx` copy. Retail keeps each camera vector's address in r28 for both the `dot` argument and the called `scaleAdd`'s `c` (one binding used twice, i.e. an inline parameter level), and it has no `bl TVec3::TVec3()` for the two reflected vectors. A `reflect(out, point)` level alone moves scaleAdd/dot across the depth budgets in both copies (71.7/53.3); it probably has to replace the fabricated `MapMirrorSetPlaneFromGround`/`MapMirrorMakeViewMtx` levels at the same time.
- **yunbo `TNerveYumboDancing::execute`**: five more `toMario` spellings recorded in its TODO, all inert or worse.
- Not attempted beyond reading (deep searches recorded in their TODOs): MapObjPlane `calcNrm`/`makeMountain`, MapMakeData `setCheckData`/`updateTrans`, MSModBgm `modBgm`/`xFadeBgm`, MSoundScene `frameLoop`/`sortMaxTrans`, MarioCollision `damageExec`/`calcDamagePos`, LightUtil `loadAfter`/`perform`, yunbo `shotSeeds`.
  MapObjPlane `calcNrm` was checked face by face against retail: same edges, crosses and normalisation, so no behaviour difference.

## Closure batch c-k7 (2026-09-28)

- **MSoundStruct `startSoundSetDyna`** (both instantiations 99.37 -> 99.52, 21 -> 9 markers): the restart test is a TU-local inline predicate (`MSSetSoundCanRestart`) holding the interval, random shift and frame counter; that gives retail's r23/r24 and the first JAIActor's slot.
  The member lookup must sit in a ternary arm: a callee with a loop is not expanded in a conditionally evaluated sub-expression of a value context (scratch TU: `c ? (n = g->search(id)) ... : true` calls `search`; the same test in `if` statements or in an `if (a && (n = ...))` condition expands it), which is what keeps retail's searchD out of line.
  The `if`-statement form gives retail's exact block shape (one `li r0, 1` shared by the null-group test and the last compare) but expands searchD (+10 instructions).
  Left: that shared `li r0, 1`; local_AC 4 high (the caller's named `bVar2` is one of retail's two words); the volume/pitch receiver in r23 for r24.
- **MapObjGeneral `thrown`**: `regalloc.py --move 36:b37` replays retail exactly: set()'s z binding must be coloured before its y binding. Named y (any accessor), a named physical-data pointer and a raw y are worse.
- **elecNokonoko `TNerveElecCarapaceMove`**: the TPathNode argument is a parse-time object; a by-value class argument is created when the body holding the call is expanded, so a depth-1 predicate around the distance test lands the TPathNode (24 -> 12 markers) but not the copy, which retail created after the later depth-1 ElecIsNerve bindings. Not landed (fabricated level).
  `TNerveElecCarapaceReturn` and `shoot`: the `a - b` temporary sits low in retail and the by-value `v` high; the `a = b - c` family.
- **MathUtil `matan`**: `a` (IRO @425) has neighbours only in f0-f2; retail's f4 needs an f3 neighbour, and the only candidate (the 1024.0f load in the `0x8000 -` block) is always scheduled after `fres` (backend-08 read for both `inv` spellings). `regalloc.py` misaligns here (161 pcode rows for 169).
- **PollutionLayer `stampModel`**: a pre-regalloc schedule tie; x's chain has the extra `addi` from getBaseTRMtx's array address, so x issues before mMinX where retail issues mMinX first. Six spellings inert. `initTexImage` is 0x70 of frame short (structural). The unit also still carries `#pragma dont_inline` on `appearItem`, so it cannot link as is.
- Known-open classes, not attempted beyond triage: telesa `bind`/`behaveToWater` and emario `perform` (`a = b - c`), PauseMenu2 `setDrawStart` (JUTColor stride), MenuDir/MovieDirector `setup`/`rsetup`/`direct` (TDStageGroup/ViewObj inlined-ctor pool, 0x18-0xb0 short), NpcParts (0x28 and 0x60 short), DrawSyncManager (header TVector insert depth surcharge; setCallback's two-word object), emario `init`/`load` and MSSetSoundTL ctor (dead low regions 0x28-0x68).

## Closure batch c-k8 (2026-09-28)

- **SelectMenu `TSelectGrad::perform`** 98.36 -> 99.54, frame and instructions exact.
  The two cue blocks are inline levels (TU-local `SelectGradAnimate`/`SelectGradDraw`; the map names neither).
  As inliner objects the colour loop's `nextCycle`, `i` and byte offset share one zero, which gives retail's `li r8,0; addi r7,r8,0; addi r3,r8,0` (the c-k5 countLivingEnemy shape, confirmed on a second unit); `s32 i;` declared ahead of `nextCycle` gives retail's r7 for `i`.
  The draw block as a level puts the Mtx and the AmbColor temporary on retail's slots, which removed the dead `midA` that used to fill the frame and let the mid channels be declared R, G, B.
  Left: `nextCycle`/`value` coloured r6/r8 (retail r8/r6) and the top-left cycle loaded into r0 (retail r5). Inert: u8/BOOL flag, `value` at the top, a second pointer, a channel-select inline (96.8).
- **MapObjInit `initMActor`/`makeMActors`**: `if (param_2 != nullptr) {}` after `viewCalc()` reproduces retail's dead r29 and both `unkC` loads (initMActor 87.1 -> 98.6, makeMActors 98.65 -> 99.8 and instruction-exact); `if (param_2) {}` is folded by the frontend.
  Not landed (empty body). `initUnique` is frame-only, 0x1d8 short.
- **Empty bodies** (scratch TU, game flags, relevant to effectObj `perform`, coasterkiller `TCoasterEnemy::perform`, NpcCollision `bind`): `{}`, `;`, `do {} while (0)` and `if (0) f();` as the body of a test after a call keep the tested value in a callee-saved register and are deleted after register allocation, exactly retail's shape; `(void)0;` (the release `JUT_ASSERT`), an empty inline call, a dead `int x = 0;` and a bare expression statement are folded before it; `if (p) {}` on a bare pointer is folded, `if (p != nullptr) {}` survives.
  So each of these retail sites had a body the preprocessor removed (the release JUT_WARNING/JUT_LOG_F/JUT_CONFIRM_MESSAGE expand to nothing); whether to spell that is a policy question.
- **`mr` against `addi rD,rS,0`** (MapObjFence `TFenceWater::initMapObj`, MapObjSirena `partsRollCallback`, both one instruction from closure): the post-RA peephole respells a `mr` as `addi` when the next instruction in the pre-RA schedule is an integer add (add, addi, lis, mr) and keeps `mr` before a load, compare, call or `fmr` (every case in five dumps). The fix is a pre-RA schedule change next to the copy, not a spelling of the copy.
- **NpcNerve** `getCurGraphIndex` at 0% is the weak copy never emitted because both GraphWander tracer sites expand it; retail calls it (and the const `getGraph`) at the first site only. Name and body are right. `TNerveNPCTurnToMario` is the `a = b - c` operator- class (axis, then two by-value copies above it).
- Read only, earlier TODOs confirmed (deep searches recorded): effectObj `moveObject` (conversion temp x copy colours last) and `TEffectColumWater::generate` (the operator* return), coasterkiller `moveCoaster`/`TCoasterKiller::perform` (raw `mPosition` for the distance is 95.5), CameraNotice (frame gaps 0x18-0x20, matan `extsh` pair), MapObjSirena `generateItem`/`moveObject`, JASChannel (0x10 frame in both `__UpdateJcToDSP*`; FPR clamp temps), MarioAutodemo (frame-only: warpOut 0x10, warpIn 8, readBillboard 0x40; `getStatusState()`/`getStatusTimer()`/`(u8)` arg spellings inert in warpOut).

## Closure batch c-k9 (2026-09-28)

- **NpcAnm `walkAnmRateChange_`: closed.**
  The debugger put MsSqrtf's `volatile` at 0xb8 (first depth-1 object) where retail has 0x7c, below every depth-1 parameter binding: the square root is one inline level down.
  `static inline f32 NpcAnmSpeedXZ(const TVec3<f32>& v) { return MsSqrtf(v.x * v.x + v.z * v.z); }` moved it to 0x78.
  The blend flag written as a depth-1 predicate (`NpcAnmIsBlending(const TBaseNPC*)`, the `bool result = true; if (!a && !b) result = false;` body) gives retail's shared `1` (`mr r0, r3`) and moves two receiver bindings below the volatile (0x80).
  `if (getColNum())` in place of `!= 0` drops the u16 forced load, the last word: byte-exact.
  NpcAnm is left with `npcMadding` (the `a - b` class), `npcWetting` and `setNpcAnm_`.
- **spcinterp** (4 arithmetic ops, open): retail's int-arm temporary is the first object after the second pop temporary, so setDataFloat's binding is created after it (depth 2) or not at all.
  The int arm cannot be a parse-time temporary: `mProcessStack.push(TSpcSlice(sum))` and the implicit `push(sum)` leave both getDataInt calls out of line (78.6%).
- **CameraBGCheck**: `isNeedGroundCheck_` with `f32 a = mDistMin * JMASSin(min);` (single definition) is left with a distY/a swap that `regalloc.py --move` closes by colouring distY before a's product temporary; no spelling found.
  `execGroundCheck_` has one dead word too many *below* `ground` (not above); a raw `.value` (-2) plus `getCamMode()` (+1) lands every slot but swaps the CLBLinearInbetween argument loads.
  `execWallCheck_`: `posCam = posArg` reads retail's slot but schedules the copy differently (98.4).
- **MapEventMare `appear`**: one dead word too many below `trans`; the four `(void)0` fillers stand for real statements (without them movement inlines appear, 8%).
- **liveactor, GCLogoDir, MSound `exitStage`, ObjHitCheck `checkWater`**: readings recorded in the TODOs (object sequences and the colouring moves that replay retail); none closed.

## Closure batch c-k10 (2026-09-28)

- **poihana `walkBehavior`: closed.**
  Retail's `add r4, r4, r3` writes the sum into the wake frame's own register, so the two are one web: `int wakeFrame = ...mSLWakeFrame.get(); wakeFrame += getInstanceIndex() * 100; if (mGoToSleepTimer > wakeFrame)`.
  `regalloc.py --move` had shown the timer web alone could not replay retail (one miss left), which pointed at the coalescing rather than the order.
  Left: `genEventCoin` and `TNervePoihanaTrapped` (the `a = b - c` class), `init` (iterator/insert pair, studied), `initSetEnemies` (the family's dead region).
- **hauntLeg `THauntLegManager::initSetEnemies`: closed.**
  The 4-byte hole between `range` and `point` is a dead named `int index = range.rand();`, and the missing word below `point` is the reference taken through the header accessor, `TGraphNode& node = graph->getGraphNode(index); node.getPoint(point);`; the old `HauntLegGetMActor` binder (it only padded the frame) is gone.
  `calcRootMatrix`: a named `f32 angle` for the lean makes the named block exact (every named slot then sits 0x40 low), so retail has 16 more inline/IRO words; the four `TVec3::cross` and two `MsVECNormalize` sites are the suspects.
  `HauntLegCallback` is the `MsMtxSetRot*` class (MathUtil.hpp note); `init` the iterator class; the Haunt nerve the `a = b - c` class.
- **cameragc `calcSlopeAngleX_`**: 98.07 -> 98.39. The ground test as a predicate level with its own flag (`CameragcIsOnThing`) gives retail's `mr r0, r4` false arm, the c-k9 shared-constant rule.
  Retail creates `p3`, `ground`, `p2`, five words, then `sample`: the sample-point copies are callee locals of nested inline levels, not named locals (frame 0xd0 vs 0x110).
  The constructor's residue is one word created after the `TPlacement` ctor's `this` binding (depth 3), i.e. a deeper inline object or an IRO temporary.
- **Yoshi `thinkAnimation`**: the named `sliding` flag was a byte retail lacks; without it the named block is retail's and every slot is exactly 0xdc (55 words) low. `entry`: a loop-local colour plus one shared mirror colour gives the 0x148 frame with every object 8 low (retail needs three S-replaced copy sources in 12 bytes of named space).
- **gesso `TGessoPolluteObj::set` / tamaNoko `TNerveTamaNokoAttack`**: both velocity test copies are created at depth 1, after the earlier sites' inline words, in source order (gesso: the 12-byte hole under `local_54` is exactly the three GessoUnk160/getAnmMtx words). A TU-local predicate reaches the depth but reverses the copies and materialises a bool; a by-value velocity getter is inert.
- **tamaNoko `landEffect`**: retail creates the four `operator*` return copies first and the four `scale()` operands after them, the rule-8d order of a by-value return with a body local (`TVec3 r(a); r *= k; return r;`); JGVec3.hpp item.
- **killer**: `calcChaseParam`'s `params` has only r0/r1/`this` as neighbours, so r3 must be held by something live across the two param loads in retail; `KillerBodyCallback` and gesso's `GessoBodyCallback` are the `MsMtxSetRot*` literal-order class; `fly`, `TNerveGessoFall`/`Freeze`, Tongue's `canGo` the `a = b - c` class.
- **MapEventMare `rising` and `depressing`: closed (c-k11).**
  `rising` was one word short below both finishEvent vectors: the two-argument `startSoundActor(id, pos)` form supplies it.
  `depressing` needed the camera-shake and rumble words created after the second branch's setJointPosX vector: both branches' rumble, camera shake and (two-argument) sound are one TU-local inline level with real content (`DepressWallQuake`), which retires the two binders there; the end block's sound reads `gpMSound` raw.
  `loadAfter`: retail's buffer is the top object and nine more words are created after it; `initCommon` keeps its recorded search.
- **NpcAnm `setNpcAnm_` (c-k11 tells)**: the 0x4000015 case's two zeroed indices share one zero (an inline level in retail), and the other parts sites test `getPartsMActor`'s result in r3 and copy it only after the index switch (an inline temporary, not a named local); a pass-through level there gives retail's `this` in r31 but not the frame.
  `npcWetting`: `regalloc.py --search` closes the switch-value swap by colouring `@2277` first, i.e. its two per-case `mActorType` CSE neighbours must be created before it (or it needs two more degree).
- **c-k11 frame readings, not closed**: cameragc's constructor word is not `mFlag()` (inert; the rest is the shared JDRCamera/JDRPlacement headers); JPADrawVisitor Directional is eight bottom words and two top words short, DirectionalCross two bottom words; MarioAutodemo `warpOut` has no dead object at all while retail has four or five, `readBillboard` sixteen below its sqrt temporary; Yoshi `init` is eight words short with only backend temporaries visible, `doSearch` is the `operator-` class.

## Closure batch c-k12 (2026-09-29)

- **rocket `TRocketManager::initSetEnemies`: closed.**
  The debugger put retail's missing word between the `TMsRange` and `point`: a named `int index = node.rand();` (the THauntLegManager shape, c-k10).
  Naming it alone costs two low words (the `getGraphNode` argument binding and one IRO temporary go away, frame 0x88); the header accessors `getObjNum()` for the loop bound and `web->getNodeNum()` for the range put exactly those two words back below `point`.
  Tell: a named word missing between two named objects, and the frame short by 8 once it is named: look for header accessors on the same statement or loop, each one EFORCELOAD word at the bottom (c-f1).
- **koopajr `TKoopaJr::perform`: closed.**
  hsearch's triage moved the lazy bathtub lookup (`if (mBathtub == nullptr) mBathtub = search<TBathtub>(...)`) one inline level down (`KoopaJrFindBathtub`), which creates the search's objects after checkNerve's depth-1 objects.
  The submarine and koopa lookups must stay at depth 1 (a shared level for two or three of them is 12 slot markers).
  No out-of-line copy is in the map, so retail's level was class-inline or TU-local.
- **bossManta `updateAttractor`**: raw `mPosition` at the first two of three sites lands the frame (0x168) and every slot; left is an f24/f25 swap between pusher's squared length and the second `length()` result.
- **enemymanager `performShared`**: `getObjNum()` as the no-collision loop bound with raw `unk18[i]` lands the frame (0xf0); left is one slot pair: countLivingEnemy's int result object is created between the two TTimeRec colour temporaries, where retail creates it after the second.
  A predicate level around the call stops countLivingEnemy expanding (frame 0xc0).
- **Application `drawDVDErr`**: `SMS_DrawInit()` plus the full-screen viewport as one inline level (`DrawInitFullViewport`) lands the J2DPrint/Mtx44 block; the viewport alone, or a level taking the TVideo, is worse (95.3). Left is the known colour-pair stride (one 8-byte copy at 0x2c).
- **conductor `genEnemyFromPollution`**: debugger reading only (TODO); without the two radius binders the frame drops 0x10-0x18.
- **MapCheck `checkRoof`**: retail has one more named word above `local_4c` and one fewer low word; a named roof list, named extent sums and checkGround's own shape are inert or -8.
- **MSoundSE `construct`**: retail stores `new MSoundSE` straight to `mObj` (no `se` slot); that spelling is frame 0x98 with every slot 8 low, so retail's twelfth low word is still missing.
- **MSoundSE `randPlay`**: the two dead words below `actor` are the JAIActor constructor's `a`/`b` argument bindings of `vec->mTrans`; retail has one of them and one named word above `actor`; a named switch operand is +8 frame, a named `trans` removes both bindings.
- **Guide `resetScore`**: retail's `remaining` holds the untruncated difference; the compound `u8 remaining = allShines; remaining -= total;` reaches that web but truncates the wrong operand.
- **GCConsole2 `startDisappearStar`** (read only, unit held by c-tp2): `unk26A + offset` with the raw member (both operands simple, so source order survives, c-r11) gives retail's `add` operand order but one instruction changes (99.2, zero `~`).
- Refused or inert this batch: named radius/normal locals in MarioRun `slopeProcess` (90.3), accessor forms of TLiveActor::init's binders (each binder is +8 and the accessors are +0), named x/y/z in `TBiancoMiniWindmill::touchWater`, `getPosition()` in `evIsWaterMelonIsReached`, a named `z` or collision receiver in `TMareWallRock::appear`.
