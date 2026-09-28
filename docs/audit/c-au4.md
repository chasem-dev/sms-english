# Behaviour audit c-au4

Verdicts for the non-exact functions `tools/semantic-diff.py` flagged in this batch.
A = same behaviour as retail, B = behaviour differed and is fixed, C = behaviour differs and is not fixed.

| unit | function | verdict A/B/C | one-line reason / fix |
| --- | --- | --- | --- |
| Enemy/bossgesso | TBossGesso::changeAllTentacleState | A | Retail tests 4, 6, 3 separately, ours folds 3..4 into one unsigned range test; the state set is the same. |
| Enemy/bossgesso | TBossGesso::doAttackSingle | A | Retail expands MsGetRotFromZaxisY at one site (matan, 90/-90/180, 360/65536); ours calls it; the body is identical. |
| Enemy/bossgesso | TBossGesso::moveObject | A | Retail calls the weak SMS_GetMarioPos(), ours reads *gpMarioPos inline; same value. |
| Enemy/koopajr | TDirectionCalc::calcNearerDirection | A | Ours expands std::fmodf, retail calls it; retail's weak fmodf (wireTrap.o) is the same body instruction for instruction. |
| Enemy/koopajr | TKoopaJrSubmarineManager::createEnemyInstance | A | Ours inlines TKoopaJrSubmarine's constructor; the stores are the same as retail's out-of-line constructor. |
| Enemy/koopajr | TKoopaJrSubmarine::init | A | Retail addresses the joint table and the dtor-chain object from one .bss base register; same stores, layout only. |
| MoveBG/MapObjMamma | TLeanMirror::controlShake | A | Retail calls SMatrix34C's constructor, which is a 4-byte `blr`. |
| MoveBG/MapObjMamma | TSandCastle::expanded | A | Retail calls getMActor() (`lwz r3,116(r3)`), ours reads the member directly. |
| MoveBG/MapObjMamma | TMammaMirrorMapOperator::perform | B | Retail sums three separate squares (no fmadds) for both distances, ours contracted them into fmadds; now uses three named squares (90.9 -> 96.9%). Field offsets were only a different base register. |
| MoveBG/MapObjMare | TMapObjGrowTree::control | A | Retail calls getMActor(), ours reads the member directly; the rest is FPR allocation. |
| MoveBG/MapObjMare | TMapObjGrowTree::touchWater | A | Same as control: getMActor() call vs member read. |
| Camera/CameraBGCheck | CPolarSubCamera::calcInHouseNo_ | A | The nine-point offset loop writes the same stack array (base+108) through a different pointer register. |
| Camera/cameragc | CPolarSubCamera::calcPosAndAt_ | A | Extra extsh are redundant: re-extending s16 values already sign-extended, or ahead of a mask that keeps only the low 15 bits. |
| Enemy/Kukku | TNerveKukkuGraphWander::execute | A | Retail expands calcMomentum and calls TQuat4::rotate, ours calls calcMomentum; the fused-op sequence of both rotates is identical. getTime()==0 test is equality only. |
| Enemy/conductor | TConductor::isBossDefeated | B | Retail sends every map but 3 to the hinokuri arm; ours fell off the end for maps other than 2 and 3 (undefined return). Fixed with `case 2: default:` (98.8 -> 95.6%). |
| Enemy/igaiga | TGorogoroManager::perform | A | Area test is guarded twice, so inArea's null arm is unreachable here; the UNUSED inArea's null result was also corrected to FALSE to match its retail expansion (96.4 -> 96.6%). |
| JSystem/JParticle/JPAEmitter | JPABaseEmitter::createParticle | A | Same emitter fields (364..380) reached through precomputed address registers; JPAEmitterInfoObj is the same object. |
| MoveBG/MapObjInit | TMapObjBase::makeMActors | A | Retail loads anim entry unkC into a dead register (initMActor ignores it); ours drops the load. |
| NPC/NpcAnm | TBaseNPC::setNpcAnm_ | A | `cmplwi r3,0` vs `mr. r27,r3` on the same null checks. |
| NPC/NpcNerve | TNerveNPCGraphWander::execute | A | Retail calls getCurGraphIndex() (`lwz 4`) and getGraph() (`lwz 0`), ours reads the members. |
| Player/MarioSpecial | TMario::pulling | C | Retail computes `delta.length()` as fma(x,x,y*y)+z*z, ours as (x*x+y*y)+z*z (no contraction), which can differ by 1 ulp before the `len < 1.0f` animation threshold. MWCC's contraction here depends on scheduling: explicit sums, reordered operands and a named z*z stay uncontracted (same as wireMove's open TODO). |
| System/EventWatcher | evGetTalkNPCName | A | Both push slice {type 2, ""}; retail's `if (!v)` stores the same value on either arm. |

Counts: A 19, B 2, C 1.
