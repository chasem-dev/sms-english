# Behaviour audit c-au2

Verdicts for the non-exact functions `tools/semantic-diff.py` flagged in this batch.
A = same behaviour, B = real difference fixed, C = real difference left open.

| unit | function | verdict | reason / fix |
| --- | --- | --- | --- |
| Player/WaterGun | emit__11TNozzleBaseFi | A | Retail shifts the sin/cos table index twice where ours CSEs one `sraw`; `lwzx` vs `add`+`lwz 7272` is the same array element. |
| Player/WaterGun | emit__13TNozzleDeformFi | A | Same CSE of the sin/cos index and the same indexed load; the float ops compute the same products. |
| Player/WaterGun | emit__14TNozzleTriggerFi | A | Same CSE of the sin/cos index and the same indexed load. |
| Player/WaterGun | init__9TWaterGunFv | A | Both reference nozzleBmdData twice; the only delta is its binding (local in retail, global here). |
| Player/WaterGun | perform__9TWaterGunFUlPQ26JDrama9TGraphics | A | Same nozzleBmdData binding-only delta. |
| Enemy/tinkoopa | execute__19TNerveTinKoopaBreakCFP24TSpineBase<10TLiveActor> | A | Retail's extra `cmpwi r0,2` is the empty third damage-stage arm; its result is never branched on. |
| Enemy/tinkoopa | init__9TTinKoopaFP12TLiveManager | A | The joint name/index tables are the same data under different section symbols; `stwx` vs `add`+`stw` stores to the same slot. |
| Enemy/tinkoopa | reset__9TTinKoopaFv | A | The same dead `cmpwi r0,2` from the inlined makeHitCollision. |
| GC2D/SelectShine2 | initData__19TSelectShineManagerFPUcUcUcP17JPAEmitterManager | A | cCenter is reached as `...bss.0`+0 here, which is cCenter itself. |
| GC2D/SelectShine2 | perform__19TSelectShineManagerFUlPQ26JDrama9TGraphics | A | Same cCenter alias; the 0/1 pool registers are swapped but cross and dot evaluate to the same x and y. |
| Player/MarioMove | setStatusToJumping__6TMarioFUlUl | A | Retail multiplies by a pooled 1.0f (identity) and reloads field 872 twice with no store between. |
| Player/MarioMove | gunExec__6TMarioFv | A | Retail copies the onYoshi bool into a second register before testing it. |
| Camera/CameraNormal | ctrlNormalOrTowerCamera___15CPolarSubCameraFv | A | Redundant `extsh` of an already sign-extended value, and one on an angle the `rlwinm` masks to 16 bits anyway. |
| Enemy/BossHanachanMain | perform__13TBossHanachanFUlPQ26JDrama9TGraphics | B | The sand-body `x*x + z*z <= 50^2` test was unfused where retail uses `fmadds`; the components are now scalar locals so fp_contract fuses it. The 0.001 double-vs-float pool constant has the same value (A). |
| Enemy/TabePuku | updateTerrainCollsion__11TTPHitActorFv | A | BOOL (`cmpwi`) vs bool (`clrlwi.`) on a nonzero test. |
| Enemy/gesso | GessoBodyCallback__FP7J3DNodei | A | gpCurGesso binding only (local in retail); the relocations and matrix stores are the same. |
| Enemy/rocket | setDeadAnm__7TRocketFv | A | The discarded mDir read is dead on both sides; ours only lands it in an unused local. |
| Map/MapMirror | perform__19TMirrorModelManagerFUlPQ26JDrama9TGraphics | A | Extra calls to the empty TVec3 default constructor, gpCamera reloaded where retail caches it (no writes between), and a commuted multiply. |
| MoveBG/MapObjPlane | calcNrm__12TMapObjPlaneFii | A | Ours CSEs `0 - w` / `0 - nw` across faces where retail re-subtracts; all four cross products check out term by term. |
| NPC/NpcEvent | ev__ForceStartTalkExceptNpc__FP32TSpcTypedInterp<13TEventWatcher>Ul | A | The popped slice is dead on both sides; retail reads only its value word. |
| Player/MarioDraw | initModel__6TMarioFv | A | `addic.` on a stack-object address; only the frame offset differs. |
| Player/Tongue | movement__12TYoshiTongueFv | B | The TongueDist grab test (< 200) was fused (`fmadds`) where retail squares all three components unfused; it now squares through a TU-local `TongueSquare`. The out-of-line TVec3 copy-ctor calls (a plain 3-word copy) remain (A). |
