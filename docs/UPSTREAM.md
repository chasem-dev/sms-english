# Syncing with upstream

This fork tracks `doldecomp/sms` (remote `upstream`, https://github.com/doldecomp/sms) through real merges, so the shared history keeps each later sync small.
The last sync merged `upstream/main` at `a7a99b4` on 2026-09-28 (merge base before it: `9d330f4`).
The sync after it merged `upstream/main` at `7e788d9` on 2026-09-29 (merge base `a7a99b4`): 11 commits, most restating fixes this fork already had, so all eight conflicted files kept ours and no function changed.

## How to sync again

1. `git fetch upstream`, then look at the size of the job: `git log --oneline main..upstream/main` and `git merge-tree --write-tree --name-only main upstream/main` (lists the conflicted files without touching the tree).
2. Make a worktree off `main` (`tools/worktree.sh add up-sync --no-claim`) and take `build/GMSE01/baseline.json` there before merging.
3. `git merge upstream/main` in the worktree.
4. Resolve conflicts with these defaults, then measure:
   - Our side wins a conflict hunk unless upstream's side is a pure conformance edit (casts, `uintptr_t`, include paths) or adds PAL-only code.
   - Where both sides decompiled a unit independently and ours scores better (Guide, hx_wiper, MSound, TimeRec, Application, MovieDirector, FlagManager, most MarDirector files at the last sync), take the whole file from ours with `git checkout --ours`.
   - Region guards combine: `#if defined(VERSION_GMSE01) ... #elif defined(VERSION_GMSP01) ... #else ... #endif`, or `#if defined(VERSION_GMSP01) || defined(VERSION_GMSE01)` where US shares PAL's shape.
5. Reconfigure with the main checkout's Python, the absolute path recorded as `python` in its `build.ninja` (`<main>/build/venv/bin/python3 configure.py --version GMSE01`), because ninja re-downloads every shared tool when the recorded Python path changes.
   Check `build/venv/bin/ninja -n build/tools/dtk build/compilers` prints no `TOOL` step before building.
6. Build with `build/venv/bin/ninja -j2 -k 0` and fix compile errors.
   Most come from upstream renames or API changes auto-merged into files we did not touch; revert those hunks to ours rather than renaming our code.
7. Compare per function against the baseline (`build/venv/bin/ninja changes_all`), and revert every auto-merged upstream hunk behind a drop.
   At the last sync the drops came from upstream's `SMSGetApplication()->` in place of `gpApplication.` (an extra inline level), its TimeRec API, its MarDirector flag accessors, and its CardManager and MapObjSirena rewrites.
8. Check the DOL SHA-1 (`a6782903ef79d4196c8489ecb1b57decb5b3728f`) and `tools/validate-symbol-order.py` for every unit whose source changed, against the same run on `main`.
9. Commit the merge, then look for upstream wins to port.
   The quick way to find them: build `upstream/main` itself under our GMSE01 config (a detached worktree with our `configure.py`, `tools/project.py`, `config/GMSE01/` and `include/version.h` copied in) and diff its report per function against ours.
   Port each winning function body on its own, check its instructions against retail for behaviour changes, and commit it separately.

## Where upstream's conventions differ from ours

- **Target.** Upstream builds GMSJ01 by default and also PAL (GMSP01); it has no GMSE01.
  `Matching`/`MatchingFor` flags in `configure.py` only matter to those regions: GMSE01 links exactly the objects listed in `config/GMSE01/objects.json`.
- **Region selection.** Upstream guards PAL code with `#ifdef VERSION_GMSP01` and picks constants with `VERSION_SELECT(GMSJ01(a), GMSP01(b))` from `include/version.h`.
  Here `VERSION_SELECT` also needs a `GMSE01(...)` entry; without one it expands to `()` and the US build fails to compile, which forces an explicit US decision at every new upstream site.
- **US follows PAL in places.** US shares PAL's shape for the MSound 0x94..0x9A fields, `getDistPowFromCamera`, TProcessMeter, `TJuiceBlock::touchActor`, the movie subtitle flag, the option flag bounds in FlagManager, the THP preroll wait in `thpInit`, the ending string in TMovieDirector and the missing smoke sound in ItemManager; those sites use `defined(VERSION_GMSP01) || defined(VERSION_GMSE01)`.
  LiveActor, NPC and enemy flag values follow JP.
- **Layout.** Upstream keeps middleware under `libs/<name>/src` and `libs/<name>/include` (dolphin, JSystem, THPPlayer, PowerPC_EABI_Support, TRK_MINNOW_DOLPHIN, OdemuExi2); unit names stay `JSystem/...`, `dolphin/...`.
  `types.h` is gone: include `<dolphin/types.h>` and the C headers a file needs, and `nullptr` comes from `-Dnullptr=0`.
- **Names we kept.** Our code keeps `unk4C`/`unk4E`/`unk50`, `unk12C`, `unk24C`/`unk24D` and `unk260` in TMarDirector, `MEANING_0x..`/`PAD_FLAG_0x..` in TMarioGamePad, and ours for TimeRec and CardManager.
  Upstream's TMarDirector flag accessors (`checkFlag`, `onDemoFlag`, ...) and state names exist as aliases over our fields, so ported bodies compile with a field rename (`mFlags` -> `unk4C` and so on).
  MSound adopts upstream's names (`mSeGateMask`, `mTimerSyncValue`, `mWaterFirEnabled`, `unk94` in MSound rather than JAIBasic).
  `SMSGetApplication()` returns a pointer, as upstream's does.
- **Tools and tags.** We pin older tool tags than upstream (dtk v1.3.0, objdiff v3.7.1, compilers 20250520) and a modified `tools/project.py` (archive link of the DOL); keep ours when upstream bumps them.
  `tools/validate-symbol-order.py` keeps ours (GMSE01 map and Linux `nm` defaults) with upstream's config-driven map lookup added.
- **Rules.** Upstream still forbids autonomous library work and tolerates `#pragma dont_inline` (e.g. its `decideUseSector`); this fork allows library work and forbids the pragma.
- **Docs.** `AGENTS.md` (also `CLAUDE.md`) stays ours on conflict; upstream's other doc edits, such as `docs/AGENT_MATCHING_TIPS.md`, merge in normally.
- **Enemy interpreter.** `Enemy/enemyinterp.cpp` keeps our US body (the class in the `.cpp`, an out-of-line destructor) under one `Object` entry in `configure.py`.
  Upstream's PAL version, its `PCHObject` entry and `include/Enemy/EnemyInterp.hpp` are not used here.

## Sync of 2026-09-30

This sync merged `upstream/main` at `2df38b9` (merge base `7e788d9`): 23 commits, 39 conflicted files.
Upstream built alone under our GMSE01 config scored below ours in every conflicted unit (bombhei 37/48 exact against our 48/48, hanasambo 72/96 against 89/96, rocket 25/33 against 29/33, seal, effectEnemy, MapObjWave, generator, question, MSoundScene, Option, PacketUtil, GCConsole2, MarDirectorEvent, MarNameRefGen_*, MenuDir, NpcParts, ModelWaterManager), so every conflict hunk kept ours.
The auto-merged `PacketUtil.cpp` interleaved upstream's FIFO fog writers into our body and was restored to ours whole.
Upstream's `THamuKuriLauncherManager`/`TNameKuriLauncherManager` header classes were dropped because our `MarNameRefGen_Enemy.cpp` defines them TU-locally.
`TPitchYaw` in `CameraMapTool.hpp` and `isUnk700` in `MapData.hpp` were not taken: ours has `mRotation` and `isSeaFloor` for the same bytes.
`HIT_MESSAGE_UNK9` stays our `HIT_MESSAGE_ELECTRIC_SHOCK`; the new particle ids (0xB2, 0xB3, 0xB8, 0xB9, 0xC1, 0xC2, 0xD1, 0xD2, 0x179) were added to our `Particles.hpp`.
Behaviour fixes that came in: the `uintptr_t` DSP/AI buffers, `GXTexCoord2u16` and `GXEnd` in `JUTRomFont::drawChar_scale`, `GXEnd` in `TPollutionLayerWave::draw` and `TGCConsole2::drawJuice`/`drawWater`, the named fields in `CPolarSubCamera::isJetCoaster1stCamera` and `TBoardNpcManager::clipActors`, and `TNpcParts`'s ctor reading `unk4[i]` at each use.
The vtable-slot, `TShimmer::perform`, JPA 8-vertex and `Mtx44` fixes were already in our tree.
Two of upstream's named-field reads were ported onto our bodies: `mInitialRotation.y` in `TMario::receiveMessage` and the unit's holder check in `TGCConsole2::checkChangeTelopArray`, spelled as `->mHolder` because `getHolder()` adds an inline level and 8 bytes of frame.
Upstream's 1-up mushroom test in `TMario::receiveMessage` (`unk13A == 0 && unk13C >= 120`) is not what the ROM does; ours (take unless `unk13A == 0 && unk13C < 120`) is, so it stays.
No function changed score and no unit became newly linkable.

## Upstream function port c-u4

Built under our GMSE01 config, upstream at `2df38b9` scored strictly higher than ours in 22 functions; each was measured on our tree.
Seven were ported, one commit each: `TBossPakkunMtxCalc::calcHeadDir` (96.91 -> 99.73), `TBossMantaAdditionalCollisionSet`'s ctor (99.81 -> 100), `TNerveBPVomit::execute` (99.52 -> 99.56), `TMapWire::release` (99.76 -> 99.84), `MSStageCubeFade::proc` (99.51 -> 99.53), `TMarDirector::initECDisp` (99.78 -> 99.81) and `TMarDirector::initECTGft` (99.65 -> 99.76).
The bossManta ctor needs both halves of upstream's reading: the collision's name as a default argument in `BossManta.hpp` and `getChildren().push_back(this)` in its ctor; either alone is worse.
Upstream's `fromPolar` for the vomit nerve is kept as the TU-local `BosspakkunFromPolar`; the nerve's instructions are exact, 8 bytes of frame short.
`TStayPakkun::load` (and pakkun's weak `set<f>`), the `TWaterEmitInfo` ctor, `TNerveKumokunWait`, `TBossEelTooth::perform` and `TFireWanwan::bindBody` win upstream only through its `TPathNode` and `TVec3` copy-constructor headers, which cost more elsewhere than they gain (see the notes in `PathNode.hpp` and `JGVec3.hpp`); their bodies are inert on our tree.
`changeAllTentacleState` (isThing), `cmdLoop` and `readBlock_` (upstream's `setCheckSum`/`read` bodies) are the trade-offs already recorded at those sites: they cost the bossgesso expansions and five CardManager functions, two of them exact.
The `TWaterEmitInfo` ctor's zero defaults for mSize/mHitRadius/mHitHeight are contradicted by the ROM, which loads three non-zero literals there.
`MSBgmXFade::xFadeBgm` reverses a comparison the ROM has the other way, `isBossDefeated` drops the ROM's default case, and `TSplineRail::getPosAndRot` needs `trash2` padding.
`TMapWire::getPointPosOnWire` hand-expands the UNUSED `getPointPosAtReleased` with extra temporaries, and `TCardLoad::waitForStart` gains only through a fourth TU-local spelling of `setCenteredSize` (98.09 -> 98.34, frame exact) at its four sites, so neither was taken.
`setLookDir` in BathWaterManager compiles to the same instructions as ours when upstream is rebuilt (97.48), so its report figure (97.57) is not a real gain.

## Sync c-u5 (2026-09-30)

This sync merged `upstream/main` at `4a07479` (merge base `2df38b9`): 7 commits, 9 conflicted files.
Upstream built alone under our GMSE01 config scored below ours in every function of the touched units: elecNokonoko (upstream 54 of 69 against our 66 exact), popo (34 of 54 against our 48), smallEnemy, MapObjPlane, Yoshi and MapCheck, so their files kept ours.
Upstream's `decHpByWater`, `behaveToHitOthers` and `TMapObjPlane::draw` score the same as ours, so nothing was combined per function.
To build upstream under GMSE01, its 28 `VERSION_SELECT` sites without a `GMSE01(...)` entry were given the JP value in the measurement tree only.
Yoshi's tongue fix (sound table 22..25, `thinkJumpEnd`, `thinkUpper` on `mTongueAnmSound` with the `unk54` test, `checkFrameMeaning` for B) was already in our tree; `thinkUpper` is exact and `init`'s instructions are exact, so the ROM agrees with it.
Taken: `BG_CHECK_FLAG_X_FACING` (0x8) in `MapData.hpp`, `MapCheck.cpp` and `MapData.cpp`, and `(uintptr_t)-1` for `J3DMatPacket::unk3C`, both byte-identical on GMSE01.
The new particle ids 0xA1, 0x13C..0x13F, 0x177 and 0x1D1..0x1D2 were added to our `Particles.hpp`; upstream's `SCENE_BOSSPAKKUN_*` renames were not taken.
No function changed score and no unit became newly linkable.
Of the 14 upstream functions that still score higher than ours, all are the c-u4 rejections recorded above (their files did not change in this sync), so none was ported.

## UNUSED restoration c-u6 (2026-10-01)

Nine UNUSED bodies were restored, each from an exact-size copy elsewhere in the binary or a sibling class whose body is already verified.
limitkoopa (16 -> 13 mismatches): `setAnimationIndex` is TKoopaJr's 0x70 body, and `getDown` (0x2a0, exact) and `stagger` (0x240) are TKoopa's without the Fall and Provoke tests, as `getShowered` already was.
`stagger`'s Flame test compiles to 0x240 in any position after the Tumble test, so its place is left as a TODO.
CardLoad (2 -> 1) and CardSave (2 -> 1): `changePattern` is TGuide's 0xc8 blink.
bosstelesa (10 -> 9): `TTelesaSlot::entryObjCollision` is TMameGesso's two `setVertexData` triangles over the same collision-plus-four-vertices layout (0x58).
cannon (5 -> 3): the same split lands both of TCannon's helpers at their map sizes: the triangles move from `calcObjCollision` (now 0x12c) into `entryObjCollision` (0x58), `calcRootMatrix` calls both, and `init` allocates the collision itself.
fireWanwan (22 -> 21): `emitTailHitEffect` (0x54) is the Fly nerve's `li r3, 9 ... bl SMS_EasyEmitParticle` tail puff, 21 instructions; calling it from the nerve moves the (1, 1, 1) temporary 4 bytes (97.57 -> 97.55), so the nerve keeps its spelled-out copy.
Rejected: TKoopaParts::set with TLimitKoopaParts' `height <= 0` default reaches the map's 0x84 but adds six instructions to TKoopa::setUpHitActors (98.1 -> 94.1), so retail's has none.
No scored function changed and unit data stayed at 100%.
MarioMove's 13 stubs and fireWanwan's `calcShadowPos`, `clipNodes` and `receiveMessageFromTail` have no call site or inlined copy; they carry TODOs with their map sizes.

## UNUSED restoration c-u7 (2026-10-01)

`tools/unused-siblings.py` lists, for every UNUSED map function that is a stub or mis-sized in our objects, the map functions with the same method name, signature and size whose body is verified (scored exact, or UNUSED at its map size); `--fuzzy` adds same-size names sharing the first and last camel-case words.
Seven UNUSED symbols reached their map sizes, from siblings of that kind or from inlined copies in scored functions.
tamaNoko (2 -> 1): `TTamaNokoFlower::setBckAnm` is TCannonDom's 0xa8 TSharedParts body, which passes the index to `setBckFromIndex` where ours passed 0.
koopajr (5 -> 3): `getBathtubY` is TBathtubKiller's 0x34 read of the same TBathtub's root joint height, and `startKoopaJrMessage` is `TTinKoopa::startTinKoopaMessage` (0x2c, inlined at its exact balloon sites).
limitkoopajr (3 -> 2): `startKoopaJrMessage` is the same balloon call.
DrawUtil (13 -> 12): `TSilhouette::calcSilhouetteBorder` is `TLightWithDBSetManager::calcLightBorder` (both 0x134) over unk24's distances; `loadAfter` keeps its spelled-out copy, since calling the helper leaves the frame 0x10 short (more markers, same 99.7%).
limitkoopa (13 -> 12): the flame nerve's UNUSED 0x6c destructor is the size of the ones that walk TNerveLimitKoopaTurn, as TNerveKoopaFlame derives from TNerveKoopaTurn; with that base `stagger` reaches its 0x240 only with the Flame test first, as in TKoopa, which settles the position c-u6 left open.
Yoshi (3 -> 2): `startVoice` is `gpMSound->startMarioVoice(id, 1, 1)` (0x2c), the call `movement` makes for the dismount voice; calling it there compiles to the same code.
Rejected: `TWatermelon::control` and `TWoodBarrel::control` share 0xa4 but not a base class; `getAnmEnd` (TKoopa and TLimitKoopa, 0x48) stays 0x2c..0x40 in every bool spelling although its inlined copies are exact; `TGraphGroup`'s destructor freeing the ctor's webs is 0xc0..0xc8, not 0x94.
`TExPane(JUTTexture*, GXCullMode)` around the UNUSED `J2DPicture(JUTTexture*)` and `setCullBack` reaches 0xec of 0xf0 and is left as a TODO.
No scored function changed and the DOL stayed byte-identical.

## UNUSED restoration c-u8 (2026-10-01)

c-u6's bosstelesa lead does not hold as stated: calling `isRollDrum` from `slotStop` gives 0xd4 (map 0xa8) and calling `getSlotResult` from `checkSlotResult` gives 0x38 (map 0x4c).
`slotStop` reaches 0xa8 only if the nerve push is a call as well, and no out-of-line push symbol exists in the map, so that reading is rejected; the UNUSED `fanfale` expands `getSlotResult` three times at its map size, so depth alone does not explain `checkSlotResult` either.
Both, and `isInDamage` (the 0x8c of `TBossEel::isValidToothDamage`'s single nerve test, nerve unknown), carry TODOs; bosstelesa stays at 9 mismatches.
`tools/unused-siblings.py` looks for library objects under `build/GMSE01/libs/...`, where none exist, so every JSystem stub reads `ours=-` and verified JSystem siblings never count; mapping `libs/<Lib>/src/` to `src/<Lib>/` cuts the filtered list from 112 to 50 real hits.
Of the nerve destructors, all five UNUSED ones are at their map sizes and every scored one is exact, so no other wrong base was found.
ParamInst (3 missing -> 0): `TParamT<u16>` and the two `TParamT<TFlagT>` loads are explicit instantiations in the map's reversed order.
ToolData (11 missing -> 1): `Hash` (0x54) and `SearchItemInfo` (0x8c) are the header's hash and item search made real members, which the scored GetValues still expand exactly; the u32 read applies the item's mask and shift (0xe0), the bool read the mask alone (0xe4), the float read takes the field whole (0xcc); four FindElement overloads land with one loop, and the float one is 0x5c of 0x80.
JKRAramArchive (9 -> 3): `fixedInit` is JKRMemArchive's with `MOUNT_ARAM`, and the address getters are the fetchResources' `mDataOffset + mBlock->getAddress()` behind a null test over the JKRArchive finds.
JKRDvdArchive (5 -> 4) and JKRCompArchive (4 -> 3): the same `fixedInit` with modes 3 and 4 is 0x44, since only a mode of 1 or 2 shares a register with the other stores.
J2DTextBox (6 -> 1): `setFontSize` is J2DPrint's (0x60), `setLineSpace` its leading half (0x48), `getString` a strcpy (0x2c), and the font-name constructors look the font up with `JKRGetNameResource(name, nullptr)` (exactly three instructions).
JASSystemHeap (9 -> 0): the DRAM and ARAM accessors read the heap or variable their names give.
No scored function changed and the DOL stayed byte-identical.

## UNUSED restoration c-u9 (2026-10-01)

With library objects visible to `tools/unused-siblings.py`, the JSystem hits were worked through; three units gained their UNUSED symbols.
JSUFileStream (10 -> 2): `JSUFileOutputStream` is restored whole, since its map sizes repeat JSUFileInputStream's member for member (ctor 0x44, seekPos 0xe8, dtor 0x74, getLength 0x30, getPosition 0x8) and its 0x28 vtable is JSURandomOutputStream's eight slots.
Its `writeData` is readData without the clamp to the file size (0x6c; dropping the `count > 0` test as well gives 0x64), and both classes' `close` is the single virtual call through mFile that getLength also is (0x30).
Both `open`s (0x4c) carry TODOs: nothing in the binary shows what they do beyond the call they presumably forward.
J3DTevs (11 -> 1, symbol order now passes): the nine UNUSED 0x4 loaders are empty bodies, `J3DLoadCPCmd` is the 0x18 CP write J3DLoadArrayBasePtr spells out, and `J3DGDSetTexLookupMode` moves to the map's place after `J3DTexMtx::load` without moving a byte of the DOL.
`J3DIndTexCoordScale::load` leaves its header for the .cpp, as nothing calls it; J3DGDSetTexLookupMode stays 4 bytes long out of line (reg2 is built in r0 and copied to r31, where loadTexNo's inlined copy uses r31 directly).
JUTNameTab (1 -> 0): `getResNameTable` is defined out of line at the map's place; nothing called the in-class inline.
Rejected: `JKRDvdRipper::doneProcess` matches JKRDvdFile's 0x30 in size only, and the ripper's async path (JKRDMCommand, sync) is unimplemented, so where it posts is unknown.
`J3DDeformer::clear` (0x20) has room for four zeroed pointers and one non-zero flag word, and `J3DDeformData::clear` equals its 0x20 constructor (the constructor calls it), but neither the flag value nor which six of DeformData's twelve fields are cleared is settled by anything in the binary.
`JUTResFont()` (0x60) is, by instruction count, one word longer than the scored constructor's base call, vtable, three null stores and one further call, `TDLTexQuadMulti::createDLBuffer` shares 0xa0 only with TDLTexQuad's unrelated `createBuffer`, and JAIBasic's getters and `stopPlayingCategorySe` name no field or list the binary confirms.
The std-list and singlelinklist hits sit in TUs whose UNUSED members are still largely unwritten, so they are left for a structural pass.
No scored function changed and the DOL stayed byte-identical.
