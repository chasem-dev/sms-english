# Behaviour audit c-au3

Retail and rebuilt objects compared instruction by instruction for each function `tools/semantic-diff.py` flagged.
Verdicts: A = same behaviour, B = real difference fixed, C = real difference left open.
All 21 functions in this batch are A; no source was changed.

| unit | function | verdict | reason / fix |
| --- | --- | --- | --- |
| Enemy/popo | PopoNonScaleCallback__FP7J3DNodei | A | `gpCurPopo` is `(object,local)` in the map but global here (linkage only, the extern lives in the shared `Popo.hpp`); the code is otherwise identical apart from stack slots. |
| Enemy/popo | PopoPossessedCallback__FP7J3DNodei | A | Same `gpCurPopo` linkage difference; the rest is register swaps (`r3`/`r4`, `r3`/`r5`) that compute the same `this+464` arguments. |
| Enemy/popo | PopoRollCallback__FP7J3DNodei | A | Same `gpCurPopo` linkage difference; the rest is stack offsets only. |
| Enemy/popo | calcRootMatrix__5TPopoFv | A | Same `gpCurPopo` linkage difference; the float block around `frsqrte` is a consistent register renaming with identical operands and compare order. |
| Enemy/pakkun | execute__18TNervePakkunAppearCFP24TSpineBase<10TLiveActor> | A | Retail keeps a dead `cmpwi r3,0` on the discarded `checkPass(100.0f)` result; both call it and neither uses the result. |
| Enemy/pakkun | load__11TStayPakkunFR20JSUMemoryInputStream | A | Retail calls `TVec3<f>::set(c,c,c)` out of line, and ours inlines three `stfs` of the same pooled constant. |
| Enemy/pakkun | rebirth__11TPakkunSeedFv | A | Retail tests `(u16)(x-257) <= 4`, and ours tests `x==257 \|\| (u16)(x-258) <= 3`: both accept the same set, 257..261. |
| GC2D/ConsoleStr | perform__11TConsoleStrFUlPQ26JDrama9TGraphics | A | Retail forms `&p[k]` as `(base+8k)+52` and reads it at `+52`/`+56`; ours reads the same addresses at `+0`/`+4`. Retail's redundant `bne`/`beq` pair has the same effect as our single `beq`. |
| GC2D/ConsoleStr | startCloseWipe__11TConsoleStrFb | A | Retail computes `(465 - x) + 224`, and ours computes `689 - x`: the same value. |
| MarioUtil/ShadowUtil | drawShadowGD__19TMBindShadowManagerFUlPQ26JDrama9TGraphics | A | Only the local-class mangling numbers differ (`TSetup1$908` vs `$2172` and the like), plus one register and one scheduling difference. |
| MarioUtil/ShadowUtil | calcVtx__19TMBindShadowManagerFv | A | Retail indexes with `lwzx base,i*20+12` and ours with `lwz 12(base+i*20)`: the same address. The float block is the same `fnmsubs`/`fadds` tree with renamed registers. |
| Animal/Bird | moveObject__11TAnimalBirdFv | A | Inlined `isChangeToItem` evaluates `nerve != ChangeToCoin && !byte316` through a bool temporary instead of direct branches; the timer decrement re-reads the field. Same effect. |
| Camera/CameraNotice | calcNoticeTargetYrot___15CPolarSubCameraFRC3Vec | A | Retail re-sign-extends the `s16` result of `matan` before subtracting; the difference is `extsh`'d afterwards, so the low 16 bits and the `abs` result match. |
| Enemy/Kazekun | doAttackPose__8TKazekunFb | A | Retail reads `mQuat` through a base pointer (`r30=this+416`, offsets 0 to 12) and ours reads it directly. The quaternion products are identical once registers are mapped: x, y and z of `spinAxis` were checked term by term. |
| Enemy/bosseel | perform__13TBossEelToothFUlPQ26JDrama9TGraphics | A | A 0/1 flag is tested with `clrlwi.` (bool) in retail and `cmpwi` (int) here; the rest is register renaming. |
| Enemy/hanasambo | SamboHeadRollCallback__FP7J3DNodei | A | `gpCurSamboHead` is `(object,local)` in the map but global here (linkage only, header extern). The three zero-guarded normalisations keep the same fall-back value in renamed registers. |
| GC2D/Talk2D2 | perform__8TTalk2D2FUlPQ26JDrama9TGraphics | A | Retail's switch has an extra empty range test (`cmpwi 2`) that jumps to the same exit as our default. |
| MoveBG/MapObjBianco | control__14TBellWatermillFv | A | Retail reloads `jmaSinShift`/`jmaSinTable`/`jmaCosTable` before each lookup, and ours hoists them; the matrices built are element-for-element identical. |
| MoveBG/ModelGate | perform__10TModelGateFUlPQ26JDrama9TGraphics | A | Retail stores to `(p+32)+28` and `(p+64)+28` where ours stores to `p+60` and `p+92`: the same addresses. The switch's pre-computed byte pointers (65/105/125..128/177) map one to one onto ours. |
| NPC/NpcInitPrg | setIndividualDifference___8TBaseNPCFR20JSUMemoryInputStream | A | Retail loads the same word twice (`lwzx` at `base+i*4+j*8+52`, once for the null test and once for the argument), and ours reuses the first load. |
| Player/MarioPhysics | checkGroundAtJumping__6TMarioFRC3Veci | A | A `cmpwi` vs `cmplwi` of a 0/1 value against 1 feeds `bne` only (equality). |
| System/CardManager | cmdLoop__12TCardManagerFv | A | Retail zeroes `mWriteCount` and calls `CalcCheckSum(this, 0x1FFC)` to set `mCheckSum`, and ours calls `setCheckSum(0)`, which does the same stores and the same loop. |
