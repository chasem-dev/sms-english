# Behaviour audit c-au1

Functions that `tools/semantic-diff.py` flagged, compared retail vs ours by disassembly.
A = harmless (same behaviour), B = real and fixed, C = real and not fixed.

| unit | function | verdict | reason / fix |
| --- | --- | --- | --- |
| Animal/BeeHive | `TBeeHive::controlCollision` | A | retail's extra `cmpw` is a dead wrap test of `index` with no branch; the stores and flag updates are identical |
| Animal/BeeHive | `TBeeHive::doWait` | A | the slerp's `1.0f - 0.01f` is folded to 0.99f in retail and computed at run time here; in single precision it rounds to 0x3f7d70a4, the same value. `TVec3::set` is called out of line in retail and inlined here, storing the same zeros to the same `unk150+56..68` fields |
| Animal/BeeHive | `TNerveBeeHiveWait::execute` | A | the TPathNode copy writes `unk150+56..68` through a different base register (`+56` then 4/8/12); same fields, same values |
| Animal/BeeHive | `TNerveBeeHiveBreak::execute` | A | same as Wait: out-of-line vs inlined `TVec3::set` of zeros and a rebased copy into `unk150+56..68` |
| Animal/BeeHive | `TNerveBeeHiveAttack::execute` | A | same as Wait |
| Animal/BeeHive | `TNerveBeeHiveMarioWaterIn::execute` | A | same as Wait |
| Animal/BeeHive | `TBeeHive::getCenterOfGravity` | A | the unrolled boid loop addresses `boid[i].mPosition` through per-element pointers in retail and through one base plus 80*k here; the same sums run in the same order |
| Enemy/fireWanwan | `TNerveFireWanwanRecoverGraph::execute` | A | `this+264` is formed as `(this+260)+4` in retail and directly here; same address |
| Enemy/fireWanwan | `TNerveFireWanwanFly::execute` | A | retail fuses `x*x + y*y` of `vel.squared()` into one fmadds, ours does not. It is the same source expression and the contraction is the compiler's choice; at most 1 ULP in a squared-speed threshold test |
| MSound/MSoundScene | `MSSceneSE::frameLoop` | A | retail recomputes `&entry[0]` as `base + 0*4 + 1064` from a literal 0; ours reuses the already-formed pointer, which is the same address |
| MSound/MSoundScene | `MSSceneSE::sortMaxTrans` | A | `lwzx base,idx*4` vs `lwz 0(base+idx*4)`; same element |
| System/Application | `TApplication::gameLoop` | A | ours relocates `gSetupThread` against `.bss+0`, where it lives; retail reloads `gpMSound` after the null test, ours keeps the loaded value (same pointer, single-threaded here) |
| System/Application | `TApplication::mountStageArchive` | A | retail reloads the vector's begin pointer (`+12+4`) for the size, ours reuses the one loaded for the empty test; same value |
| Camera/cameralib | `CLBCalcNearNinePos` | A | retail keeps 1.0f live in f25 and reuses it; ours reloads the same constant |
| Enemy/Kumokun | `TNerveKumokunSearch::execute` | A | retail keeps 0.5f in a register, ours loads it twice; the angle `0.5f * (PI * (0.5f + r/32768))` is computed in the same order |
| Enemy/enemy | `TSpineEnemy::zigzagToCurPathNode` | B | ours fused `dVar13 * cycle + getPhaseShift()` into an fmadds; retail rounds the product first, and the sum feeds a sine-table index. Fixed by naming the product (95.96% -> 97.96%, now semantically CLEAN) |
| Enemy/limitkoopa | `TLimitKoopa::startHipDrop` | A | retail's normalize() reuses the squared length from the `len > 200` test; ours recomputes it with the same operations and order, so the same value |
| Map/BathWaterManager | `TBathWaterManager::perform` | A | retail's extra `fcmpo f1, 0.0f` is a dead compare (no branch reads it, from an inlined sqrt guard); its extra 1.0f load is a register-allocation difference |
| MoveBG/MapObjItem2 | `TJumpBase::control` | A | retail computes the sin/cos table index twice from the same angle; ours once |
| NPC/NpcBase | `TBaseNPC::perform` | A | the camera position is copied through `lfs/stfs` in retail and `lwz/stw` here; the same 12 bytes |
| NPC/NpcParts | `TNpcParts::TNpcParts` | A | the same `cNpcPartsNameRootJoint` pointer is loaded; ours defines the symbol in this TU (a known ODR duplicate, documented at its definition) where retail imports it from NpcAnm |
| Player/ModelWaterManager | `TModelWaterManager::move` | A | a position copy into a static at `+28..36` addressed through different base registers (`base+28` vs `base`, `+2068` pre-added vs not); same fields |
| TRK_MINNOW_DOLPHIN/__exception | `lbl_80004600` | A | retail hard-codes `0x803404b8` with `lis/ori`; ours uses `TRKInterruptHandler@h/@l`, which the map places at `0x803404b8` |
