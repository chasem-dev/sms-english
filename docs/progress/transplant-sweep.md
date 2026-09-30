# Transplant sweep (c-tp1)

Every function below 100% in the GMSE01 report (885 functions in 196 units) was checked against a second decompilation of the game that targets the Japanese build with the same compiler.
For each function its definition in that tree was spliced over ours in a shadow copy of the unit, the unit was compiled with our own ninja command and our headers only, and the function was scored against US retail with objdiff.
The tree is never written; the tools are `tools/transplant/transplant.py` (per-function splice and score) and `tools/transplant/context.py` (their whole unit compiled under our flags, a ceiling for what a port could reach).

Automatic fixes while scoring: a member missing from our headers was renamed to ours declared at the same `/* 0xNN */` offset of the same class, a file-scope helper of theirs was copied in, and stack padding was deleted before scoring.
Their macros, pragmas and per-TU inline switches were never imported.

## Counts

| status | functions | meaning |
| --- | ---: | --- |
| better | 33 | compiles here and scores above ours |
| same | 67 | compiles here, same score |
| worse | 325 | compiles here, scores below ours |
| ident | 48 | the two bodies are the same text once comments and spacing go |
| needs-port | 370 | does not compile against our headers |
| missing | 36 | no definition in the other tree's copy of the unit (or no such unit) |
| header | 6 | ours is defined in a header or template, not in the unit |

## The "better" functions

Every one was reviewed by hand before porting; the port is written in our names and constants and often keeps only the one lever that carries the gain.

| unit | function | ours | spliced | decision | commit |
| --- | --- | ---: | ---: | --- | --- |
| Animal/BeeHive | `controlCollision__8TBeeHiveFv` | 89.583 | 96.042 | ported | 2734b348 |
| Animal/BeeHive | `getCenterOfGravity__8TBeeHiveCFv` | 85.897 | 86.143 | ported | d2db4aa4 |
| MSound/MSModBgm | `modBgm__8MSModBgmFUcUc` | 96.508 | 98.095 | ported | fe466094 |
| Enemy/effectObj | `moveObject__14TEffectObjBaseFv` | 98.836 | 99.836 | skip-claimed |  |
| Enemy/graph | `getPosAndRot__11TSplineRailFfPQ29JGeometry8TVec3<f>PQ29JGeometry8TVec3<f>` | 99.603 | 99.915 | rejected-fake |  |
| Enemy/bossgesso | `init__10TBossGessoFP12TLiveManager` | 99.866 | 99.886 | rejected-behaviour |  |
| Enemy/bossgesso | `lenFromToeToMario__10TBossGessoFv` | 99.864 | 99.932 | rejected-regress |  |
| Enemy/bossgesso | `changeAllTentacleState__10TBossGessoFi` | 94.122 | 99.927 | rejected-regress |  |
| Enemy/tobiPuku | `hitWall__9TTobiPukuFv` | 97.057 | 97.621 | ported-adapted | f21a5712 |
| Enemy/igaiga | `reset__10TRollEnemyFv` | 95.690 | 99.762 | ported | c5ee5537 |
| Enemy/igaiga | `walkBehavior__10TRollEnemyFif` | 97.931 | 99.916 | ported-adapted | ed8088c2 |
| Enemy/bgtentacle | `calcAttackGuideAnm__11TBGTentacleFv` | 98.944 | 99.653 | ported | 32a7cdfb |
| Enemy/rocket | `execute__15TNerveRocketFlyCFP24TSpineBase<10TLiveActor>` | 98.939 | 99.750 | rejected-fake |  |
| Enemy/Kukku | `calcMomentum__6TKukkuFf` | 95.775 | 98.873 | rejected-regress |  |
| Enemy/Koopa | `updateAnmSound__6TKoopaFv` | 99.500 | 99.900 | ported-adapted | afbe0982 |
| GC2D/GCConsole2 | `startDisappearCoin__11TGCConsole2Fv` | 99.356 | 99.772 | skip-claimed |  |
| GC2D/GCConsole2 | `startDisappearStar__11TGCConsole2Fv` | 99.950 | 100.00 | skip-claimed |  |
| GC2D/GCConsole2 | `startAppearStar__11TGCConsole2Fv` | 99.877 | 100.00 | skip-claimed |  |
| Map/MapCheck | `checkRoofList__17TMapCollisionDataFfffUcPC12TBGCheckListPPC12TBGCheckData` | 98.451 | 98.890 | ported | 6f19026f |
| Map/MapCheck | `checkGroundList__17TMapCollisionDataFfffUcPC12TBGCheckListPPC12TBGCheckData` | 98.353 | 98.765 | ported | 6f19026f |
| Map/MapMakeData | `setCheckData__17TMapCollisionBaseFPCfPCsP12TBGCheckDatai` | 92.111 | 99.824 | rejected-regress |  |
| Map/MapMakeList | `getGridArea__17TMapCollisionDataFPC12TBGCheckDataiPiPiPiPi` | 99.500 | 99.750 | ported-adapted | 8642b6c8 |
| MoveBG/MapObjBase | `initAndRegister__11TMapObjBaseFPCc` | 99.788 | 100.00 | skip-claimed |  |
| MoveBG/MapObjLib | `emitAndSRT__11TMapObjBaseFlUcPCQ29JGeometry8TVec3<f>RCQ29JGeometry8TVec3<f>RCQ29JGeometry8TVec3<f>` | 95.929 | 99.732 | ported-adapted | cb4c3f12 |
| MoveBG/MapObjLib | `emitAndRotateScale__11TMapObjBaseCFlUcPCQ29JGeometry8TVec3<f>` | 96.350 | 99.783 | ported-adapted | cb4c3f12 |
| MoveBG/MapObjMamma | `load__13TShiningStoneFR20JSUMemoryInputStream` | 97.399 | 99.878 | skip-claimed |  |
| MoveBG/MapObjMamma | `perform__23TMammaMirrorMapOperatorFUlPQ26JDrama9TGraphics` | 96.946 | 97.682 | skip-claimed |  |
| MoveBG/MapObjMare | `draw__9TCogwheelCFv` | 96.730 | 96.822 | skipped-noise |  |
| MoveBG/MapObjPlane | `makeMountain__12TMapObjPlaneFv` | 94.517 | 99.862 | ported | d10bdc66 |
| MoveBG/MapObjBall | `touchActor__14TBigWatermelonFP9THitActor` | 97.279 | 98.100 | rejected-fake |  |
| MarioUtil/MtxUtil | `constraintTail__5TRopeFRCQ29JGeometry8TVec3<f>` | 99.583 | 99.799 | rejected-fake |  |
| MarioUtil/MtxUtil | `moveHead__5TRopeFRCQ29JGeometry8TVec3<f>` | 93.596 | 93.636 | ported | 71d9b9d4 |
| System/CardManager | `decideUseSector__12TCardManagerFPQ212TCardManager9TCriteria` | 93.929 | 97.857 | rejected-regress |  |

Rejections:
- `rejected-fake`: the gain needs a stack pad (`TSplineRail::getPosAndRot`), a pass-through wrap helper (`TNerveRocketFly`), or header methods expanded by hand (`TBigWatermelon::touchActor`, `TRope::constraintTail`); honest spellings of the same statements were measured and are inert.
- `rejected-regress`: the function improves but a caller that expands it drops: `setCheckData` (initAllCheckData 100 -> 58.6), `TKukku::calcMomentum` (RecoverGraph 100 -> 76.9), `changeAllTentacleState` and `lenFromToeToMario` (7 and 13 other bossgesso functions), `decideUseSector` (readBlock_ and copyTo).
- `rejected-behaviour`: `TBossGesso::init` swaps the two `setMActors` arguments against retail's relocations for +0.02 of slot noise.
- `skipped-noise`: `TCogwheel::draw` is +0.09 fuzzy but five more mismatched instructions.
- `skip-claimed`: units held by c-r23 at the time (three of them have since matched on main).

Found while measuring, not from the splice:
- `TMapObjBase::emitAndSRT`/`emitAndRotateScale` needed named `s16` locals before `setRotation` (the other tree expanded the header method by hand and padded); `emitAndRotateScale` is now exact.
- Naming the scaled step in `TRope::moveHead` also brings the UNUSED `moveHeadAndTail` to its map size (0x1b4 -> 0x19c).
- `TConeBeam::calcVertices` reaches 98.3% with coneInPlane's origin components named, but that shrinks the UNUSED out-of-line coneInPlane below its 0x15c map size, so it is recorded as a TODO, not applied. The map's mangled name also confirms coneInPlane takes a `TPartition3` (the other tree passes four scalars).

## needs-port

First compile error: 227 undefined identifiers (members, accessors or helpers our headers lack), 29 private-member accesses, 23 type conversions, the rest syntax or overload mismatches from different declarations.
Body shape against ours (token skeleton similarity): far (< 0.70) 142, mid (0.70-0.90) 140, near (>= 0.90) 88.

Top 25 by our distance from 100 times the function size (`value`); `ctx` is the other tree's whole unit compiled under our flags (`-` when it did not compile), `sim` the skeleton similarity:

| unit | function | value | ours | ctx | sim | flags |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Enemy/Kazekun | `doAttackPose__8TKazekunFb` | 189.2 | 85.62 | 78.77 | 0.71 |  |
| Enemy/bossgesso | `doAttackSingle__10TBossGessoFv` | 178.0 | 93.27 | 85.81 | 0.83 | dont_inline |
| Camera/cameragc | `calcPosAndAt___15CPolarSubCameraFv` | 149.3 | 96.27 | 74.64 | 0.75 |  |
| Enemy/BossHanachanMain | `perform__13TBossHanachanFUlPQ26JDrama9TGraphics` | 147.7 | 97.58 | 69.53 | 0.68 |  |
| Map/BathWaterManager | `prerender__22TBathWaterMeshRendererFPQ26JDrama9TGraphicsRC12TBathtubDataPP10TBathWaterPP16TBathWaterParamsi` | 144.8 | 94.48 | 88.17 | 0.75 |  |
| Player/ModelWaterManager | `move__18TModelWaterManagerFv` | 126.9 | 96.96 | 92.16 | 0.93 |  |
| Enemy/Koopa | `KoopaNeckCallBack__9@unnamed@FP7J3DNodei` | 126.6 | 94.96 | 40.78 | 0.65 |  |
| MoveBG/MapObjMare | `bind__10TMuddyBoatFv` | 111.4 | 91.83 | 95.17 | 0.81 |  |
| Animal/boid | `calcBoids__11TBoidLeaderFv` | 85.4 | 96.08 | 94.51 | 0.95 |  |
| Enemy/BossHanachanSub | `moveHead__11TSphereLinkFRCQ29JGeometry8TVec3<f>` | 80.6 | 91.77 | 80.52 | 0.73 |  |
| Enemy/tinkoopa | `init__9TTinKoopaFP12TLiveManager` | 79.5 | 94.72 | 81.10 | 0.52 |  |
| MarioUtil/ShadowUtil | `calcVtx__19TMBindShadowManagerFv` | 77.5 | 97.23 | 94.53 | 0.89 |  |
| Animal/BeeHive | `doWait__8TBeeHiveFv` | 76.0 | 95.46 | 68.13 | 0.68 |  |
| Enemy/beam | `calcVertices__9TConeBeamFi` | 73.5 | 95.62 | 96.83 | 0.91 |  |
| GC2D/SelectShine2 | `perform__19TSelectShineManagerFUlPQ26JDrama9TGraphics` | 70.4 | 94.03 | 74.82 | 0.55 |  |
| MoveBG/MapObjBianco | `control__14TBellWatermillFv` | 68.6 | 96.59 | 76.14 | 0.78 |  |
| Camera/lensflare | `perform__10TLensFlareFUlPQ26JDrama9TGraphics` | 62.8 | 95.84 | 88.89 | 0.18 |  |
| Animal/AnimalBase | `perform__11TAnimalBaseFUlPQ26JDrama9TGraphics` | 61.5 | 92.53 | 89.58 | 0.83 |  |
| GC2D/GCConsole2 | `processAppearStar__11TGCConsole2Fi` | 61.4 | 96.62 | 96.51 | 0.68 |  |
| Enemy/koopajr | `init__17TKoopaJrSubmarineFP12TLiveManager` | 61.3 | 94.40 | 74.97 | 0.75 |  |
| Player/Tongue | `movement__12TYoshiTongueFv` | 61.0 | 97.23 | 74.94 | 0.72 |  |
| Enemy/limitkoopa | `setUpHitActors__11TLimitKoopaFv` | 56.4 | 94.81 | 92.61 | 0.65 |  |
| JSystem/JParticle/JPADrawVisitor | `exec__22JPADrawExecStripeCrossFPC14JPADrawContext` | 54.1 | 97.32 | 91.03 | 0.81 |  |
| Enemy/Kukku | `execute__22TNerveKukkuGraphWanderCFP24TSpineBase<10TLiveActor>` | 53.7 | 94.54 | 97.22 | 0.55 |  |
| Enemy/bgtentacle | `setAttackTarget__11TBGTentacleFv` | 50.8 | 95.51 | 93.34 | 0.97 |  |

Only 32 needs-port functions score above ours even in the other tree's own context; those are the only ones where a port could pay:

| unit | function | value | ours | ctx | sim | flags |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| MoveBG/MapObjMare | `bind__10TMuddyBoatFv` | 111.4 | 91.83 | 95.17 | 0.81 |  |
| Enemy/beam | `calcVertices__9TConeBeamFi` | 73.5 | 95.62 | 96.83 | 0.91 |  |
| Enemy/Kukku | `execute__22TNerveKukkuGraphWanderCFP24TSpineBase<10TLiveActor>` | 53.7 | 94.54 | 97.22 | 0.55 |  |
| Enemy/koopajr | `calcNearerDirection__14TDirectionCalcFf` | 48.8 | 69.50 | 99.45 | 0.66 | dont_inline |
| Player/MarioMove | `setStatusToJumping__6TMarioFUlUl` | 44.1 | 98.48 | 99.19 | 0.78 |  |
| MarioUtil/DrawUtil | `clash__19TTrembleModelEffectFf` | 32.4 | 93.96 | 96.73 | 0.93 |  |
| Enemy/tamaNoko | `perform__15TTamaNokoFlowerFUlPQ26JDrama9TGraphics` | 27.5 | 97.06 | 99.52 | 0.81 |  |
| Enemy/Kukku | `execute__15TNerveKukkuFallCFP24TSpineBase<10TLiveActor>` | 21.2 | 96.81 | 99.70 | 0.85 |  |
| MoveBG/MapObjMare | `initMapObj__9TCogwheelFv` | 21.0 | 96.78 | 99.65 | 0.86 |  |
| Enemy/BathtubKiller | `isAboided__14TBathtubKillerFv` | 10.6 | 98.72 | 99.05 | 0.86 |  |
| MoveBG/MapObjMare | `touchWater__15TMapObjGrowTreeFP9THitActor` | 8.0 | 98.92 | 99.73 | 0.69 |  |
| Enemy/enemyMario | `findRunAwayNearestNode__11TEnemyMarioFv` | 8.0 | 98.82 | 99.50 | 0.81 |  |
| MoveBG/MapObjMare | `control__15TMapObjGrowTreeFv` | 7.8 | 98.92 | 99.75 | 0.70 |  |
| GC2D/Talk2D2 | `moveTalkWindow__8TTalk2D2Fv` | 5.9 | 99.19 | 99.38 | 0.78 | dont_inline |
| Enemy/chuuhana | `moveObject__9TChuuHanaFv` | 5.1 | 99.43 | 99.58 | 0.80 |  |
| Enemy/bosspakkun | `execute__13TNerveBPVomitCFP24TSpineBase<10TLiveActor>` | 4.7 | 99.45 | 99.57 | 0.72 |  |
| Enemy/hinokuri2 | `moveObject__10THinokuri2Fv` | 4.6 | 99.74 | 99.77 | 0.92 |  |
| Enemy/pakkun | `execute__18TNervePakkunAppearCFP24TSpineBase<10TLiveActor>` | 4.0 | 98.55 | 99.89 | 0.97 |  |
| Enemy/popo | `PopoNonScaleCallback__FP7J3DNodei` | 3.2 | 99.21 | 99.65 | 0.86 |  |
| Enemy/hinokuri2 | `perform__10THino2MaskFUlPQ26JDrama9TGraphics` | 2.6 | 99.65 | 99.79 | 0.95 |  |
| MoveBG/MapObjFence | `initMapObj__11TFenceWaterFv` | 2.4 | 99.30 | 99.43 | 0.82 |  |
| MarioUtil/ShadowUtil | `forceRequest__19TMBindShadowManagerFRC20TCircleShadowRequestUl` | 1.5 | 99.50 | 99.69 | 0.45 |  |
| MSound/MSoundSE | `__ct__Q214MSoundSESystem9MSRandVolFUl` | 1.5 | 99.11 | 99.52 | 0.11 | pad |
| MSound/MSoundSE | `getRandVol__Q214MSoundSESystem9MSRandVolFUl` | 1.1 | 99.12 | 100.0 | 0.40 |  |
| MoveBG/MapObjPinna | `control__13TMerrygoroundFv` | 1.0 | 99.77 | 99.80 | 0.91 |  |
| MSound/MSoundSE | `__ct__Q214MSoundSESystem10MSRandPlayFUlllff` | 0.6 | 99.62 | 100.0 | 0.44 | pad |
| Enemy/BossHanachanSub | `__ct__11TSphereLinkFUsRCQ29JGeometry8TVec3<f>ffffff` | 0.5 | 99.89 | 100.0 | 0.62 | pad |
| MoveBG/MapObjInit | `initUnique__11TMapObjBaseFv` | 0.4 | 99.97 | 100.0 | 0.95 | pad |
| Enemy/smallEnemy | `receiveMessage__11TSmallEnemyFP9THitActorUl` | 0.4 | 99.93 | 100.0 | 0.98 | pad |
| Enemy/hamukuri | `receiveMessage__14TDangoHamuKuriFP9THitActorUl` | 0.3 | 99.94 | 100.0 | 0.98 | pad |
| MoveBG/MapObjBianco | `__ct__24TBiancoWatermillVerticalFPCc` | 0.2 | 99.80 | 100.0 | 0.44 | pad |
| Map/MapEventMare | `appear__13TMareWallRockFv` | 0.2 | 99.96 | 100.0 | 0.69 | pad |

Hand ports tried on that list: `TMuddyBoat::bind` (each statement-level difference inert, 91.8 either way), the Kukku GraphWander and Fall nerves (inert; the context gain comes from their helper split and calcMomentum), `TConeBeam::calcVertices` (see above), the pakkun Appear nerve (its gain is an invented assert macro that forces a dead compare).
`calcNearerDirection` and the koopajr submarine constructor rely on `#pragma dont_inline`; the MSoundSE rows and every `pad` row rely on stack padding.

## Header differences that matter

- The other tree switches inline bodies per translation unit with `#define ..._OUT_OF_LINE` / `..._IMPLICIT_COPY_CTOR` / `..._DECL_ONLY` lines before its includes (72 files: TVec3 add/sub/scale, TUtil sqrt, J3DMtxCalc init, getMActor, TVec3's copy constructor, ...). Most of its whole-unit gains over ours come from those switches, dont_inline pragmas or padding, not from the bodies: of 86 functions where its unit context beats ours, 8 sit in units with none of them.
- `TMultiMtxEffect::setup` is 92.3 here and 99.5 there only because that unit compiles TVec3<f32> with an implicit copy constructor; ours has the user `: Vec(other)` one, which is the open TODO already recorded above the function.
- `TKoopaJrSubmarineManager::createEnemyInstance` (3.6 here) is 99.4 there only because the TKoopaJrSubmarine constructor is under `#pragma dont_inline`; the real cause (the constructor's statement budget) is the open TODO in koopajr.cpp.
- Class layouts agree: every field rename the sweep needed was a name at the same `/* 0xNN */` offset (TAnimalBird, TBeeHive, TCogwheel, JPABaseEmitter's 0x124/0x154/0x174, TRealoidActor's 0x74, MSModBgm's 0x0/0x4, ...). No layout disagreement blocked a port.
- The accessors differ: the other tree adds header helpers ours lacks (`getSaveParam2`, `setScale`, `getWireBinderDirect`, per-class `get...Param` casts), which is most of the undefined-identifier failures; none was needed for the ports above.

Raw rows (one per function, with the automatic fixes and first compile error) are in the session scratchpad as `transplant/results.tsv`; rerun with `python3 tools/transplant/transplant.py --other <tree> --out results.tsv -j 2`.

## hsearch sweep c-hs2 (continued)

The resumed `tools/hsearch` batch (200 s per function, `-j 2`) ran rows 124 to 213 of its 350-function list in two 2-hour legs before the session's background time limit stopped it; rows 214 onward are unsearched and the batch resumes from there.
It reported 40 new exact or improved candidates; review accepted 12 and rejected 28 (the whole sweep so far: 22 accepted, 59 rejected, 81 reviewed).
Made exact: `TGorogoroManager::initSetEnemies`, `TNerveTobiPukuBound::execute`, `TRollEnemy::walkBehavior`, `TShiningStone::load`, `TGCConsole2::startDisappearCoin`, `JPADrawExecLine::exec`.
Improved: `TTinKoopaLaunchOrder::checkOrder`, `SetViewFrustumClipCheck`, `TBathtub::control`, `TKumokun::checkOnMovingFloor`, `TLeanMirror::draw`, `TMario::wireRolling`.
Accepted edits were C-style top declarations, a declaration order, one named compare operand and one named bool test, consistent accessor or raw spellings across a whole block, a dropped redundant TVec3 copy, and two inline levels.
The best find was an extract that turned out to be a map UNUSED: `TBathtub::liftMario` was an empty stub, and holding the extracted torque block it now compiles to its map size 0xe0.
Check every machine extract against the unit's UNUSED map entries before rejecting it.
The other accepted level, `TobiPukuStartBound`, wraps a whole `getTime() == 0` branch whose TVec3 the old TODO had already measured as missing from the low region.
Rejected: arbitrary machine-cut extracts (9), results that stack on an existing fabricated binder or fork (5), hoisting one of several parallel locals (5), a lone `!getTime()` that is the only such spelling in its file (2), a local named at one of two identical call sites (2), whole-body wrappers (2), and small trades or mixed spellings across parallel branches (3).
Tool notes: `extract` offers whole-body wrappers and helpers that take every parameter, and the plausibility filter's four-line window misses spellings mixed across parallel `case` arms.

## hsearch sweep c-hs3

The `tools/hsearch` batch (200 s per function, `-j 2`) ran rows 214 to 294 of the 350-function list, one function per foreground call in chunks of under ten minutes, so the background time limit never hit it; rows 295 onward are unsearched.
It reported 53 exact or improved candidates (7 exact, 45 improved, one flagged as regressing another function); review accepted 19 and rejected 34 (one first rejected, `TBossEel::init`, was accepted on review once the same lever turned up in `SMS_MakeJointsToArc`).
Made exact: `TNerveHino2Turn::execute` (getUnkF4(), getPosition() and getCurrentBck() at their only sites, mRotation raw) and `TMario::warpOut` (a computing `IsWarpOutGet(u32)` inline at both identical animation picks plus a named camera-flag bool, rewritten by hand from hsearch's mixed-spelling exact).
Improved to the retail frame: `TMapWire::getPosInWire`, `JPADrawExecDirectionalCross::exec`, `TDirectionCalc::calcTurnDirection`, `TMareEventDepressWall::initCommon`, `TMuddyBoat::moveByWater`, `TTalk2D2::moveTalkWindow`, `SMS_MakeJointsToArc`, `TBossEel::init`, `TSpineEnemy::goToExclusiveNextGraphNode`, `TKumokun::decideTargetAtDir`, `TBathtub::updatePosture_`, `TBossTelesa::moveObject` and `TNerveBWJumpToBath::execute`.
Improved part of the way: `TMario::moveMain`, `TEnemyMario::drawHPMeter`, `initStage` and `TSplineRail::getPosAndRot`.
Accepted edits were accessors at every read of a member in the function, two-argument `startSoundActor` where the file already uses it, named bool or int tests (in both parallel branches where there are two), one named receiver split and one declaration swap.
`getModelData()->mJointNum` read raw lands the frame in both functions that read it once, where `getJointNum()` is 8 long; c-hs2's `TPakkun::init` also lands its frame with it, at a slot cost.
Rejected: machine-cut extracts with no UNUSED of their shape (9), stacks of five or more moves (7), lone spellings among parallel sites or against the file's style (8), trades or slot-only noise (5), results on a fake (3: a dead shadowed declaration from a doubled `decl-hoist`, the recorded `volatile` in calcTurnSpeedToReach, the fabricated MarioCollisionVecScaled), and two regressions: the one hsearch flagged in MapEventSink, and `TMapWire::drawUpper`, where getStartPoint() at all six reads lands the frame and every slot but drops the objdiff score, so it stays a lead.
Leads: `processMiss`, `processShineGet` and `PauseMenu2::drawAppearPane` all want the same rect-centre emitter with a named int width; `TWireTrap::calcRootMatrix` closes its frame and slots with getPosition(), getScaling() and a lone `getHolder() != nullptr`; `TKoopa::updateAnmSound` closes exactly with its anime-loop block as an inline level.
Tool notes: `decl-hoist` applied to an already hoisted name leaves a dead outer declaration shadowed by the inner one, and the lint does not catch it; the plausibility filter misses identical blocks more than four lines apart (CardSave's three close-A4 blocks), and `sound-short` does not check the file's prevailing spelling.

## hsearch sweep c-hs4

The `tools/hsearch` batch (200 s per function, `-j 2`, one function per foreground call) ran the last rows, 295 to 350, so all 350 rows of the list are now searched.
It reported 12 exact or improved candidates (one exact, one flagged as regressing another function); review accepted 3 and rejected 9, and one of c-hs3's two leads was accepted (the whole sweep: 45 accepted, 103 rejected, 148 reviewed).
Improved to the retail frame: `TNerveBPVomit::execute` (getRotation() at all three rotation reads, where hsearch needed a nonsense `TSpineEnemy* body` temporary and two of three sites) and `TObjHitCheck::checkWater` (a named bool `alive` for the particle test, which also lands every slot).
Improved in slots: `TBeeHive::bind` (getLinearVelocity() at the member's only read, 33 slots to 7).
Lead accepted: one `EmitAtRectCentre(rect, id)` inline over ConsoleStr's four identical bounds-centre emitter blocks takes `processMiss` from 99.64 to 99.81 and `processShineGet` from 99.73 to 99.75, but moves no frame.
Named int widths (inside the level or in the caller), the centre passed as two floats, a pane-taking level and a returned vec are all inert there, and the same level in PauseMenu2 is inert or worse, so the named-width lead is closed.
`TMapWire::drawUpper` with getStartPoint() at all six start reads is left with only the first vertex's two `fadds` in the wrong operand order (retail adds point + offset, addPoint's order); addPoint(getStartPoint()) there is 0x50 and a commuted source is inert, recorded in its TODO.
The one exact, `TNerveBGBeakDamage::execute`, is a machine extract of the BTP-frame, resetDL and BGM block; its 0x64 matched UNUSED `TBGCork::crush` only by size, and `setEyeDamageBtp(1)` there costs the recorded 0x18 of frame.
Rejected: machine extracts (3), nonsense temporaries or lone spellings stacked for a frame (3), inverting an existing mixed split (`TLampSeesawMain::loadAfter`, where every consistent spelling misses by 8), and slot-only noise (2).
Tool fixes: moves that rewrite or delete the same text no longer combine, and the lint refuses a new shadowed declaration (the doubled `decl-hoist`).
Spelling moves are refused when an identical line anywhere in the function is left alone, `sound-short` only offers a form that is at least a quarter of the file's other calls and adds an all-sites move, and an extract winner's helpers are compiled out of line and checked against the unit's UNUSED map sizes (skipping UNUSEDs the build already emits at their size), with the result in the log and the patch header.
