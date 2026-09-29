# External sources

Public material checked for anything that could close or improve this clone's non-exact functions (research batch c-r23, 2026-09-29).
The clean-room rule in `CLAUDE.md` applies to everything here: reconstructions, documentation and compiler research are evidence, leaked SDK, JSystem or MSL source is not.
Every body taken from outside is still measured here, on GMSE01, and reviewed against the fakematch rules before it lands.
Most "100%" claims elsewhere are against GMSJ01 and many rely on devices this clone forbids (stack padding arrays, `#pragma dont_inline`, fabricated padding structs).

## How to re-check

- Forks of doldecomp/sms: GitHub repository search `"decompilation of Super Mario Sunshine" fork:only`, sorted by last update, lists them (the forks API is not reachable from this container, the search is).
- Fetch a fork or pull request into a scratch bare repository, never as a remote of this checkout: `git init --bare ext.git`, then `git fetch --shallow-since=2026-01-01 https://github.com/<owner>/sms '+refs/heads/*:refs/fork/<owner>/*'`, and `git fetch --shallow-since=2026-01-01 https://github.com/doldecomp/sms refs/pull/<n>/head:refs/pr/<n>` for pull requests.
- A fetch that shares objects with this shallow checkout through `objects/info/alternates` fails with "unresolved deltas"; keep the scratch repository standalone (about 80 MB for every fork and pull request).
- Batch c-r23 compared each ref against this tree with a transplant sweep: for every non-exact function of `build/GMSE01/report.json`, take the ref's definition of the same function, compile it in a shadow root of our unit with the `tools/hsearch` evaluator, and score it and the whole unit against retail.
  A distinct body is compiled once even when many refs carry it; about 3 in 5 transplants fail to compile because the fork's member names differ.
- decomp.me: the bare `decomp.me` host answers with a Cloudflare challenge, but `https://www.decomp.me/api/...` works with curl and a browser user agent.
  The Super Mario Sunshine preset is 61: `curl -A Mozilla/5.0 'https://www.decomp.me/api/scratch?preset=61&page_size=100'` (follow `next`), and `.../api/scratch?search=<class>` finds scratches made without the preset.
  `.../api/scratch/<slug>` returns `source_code`; most SMS scratches have none (a 45-character placeholder), so only the ones with source are worth fetching.
  Pause a few seconds between requests: bursts get a throttle reply without `results`.

## doldecomp/sms pull requests

- https://github.com/doldecomp/sms/pulls: open #200, #198, #194, #189, #179, #159, #154, #134, #112, #111 and #95, and the closed-unmerged #199, #195, #182, #161, #153, #150 to #138, #135, #123, #121, #116, #115, #104, #91 and #89 were fetched and swept.
- Clean-room status: community reconstructions; nothing read like leaked source.
- #200 (KakarottoCake, "System: audit factories and card state handling") has an exact `TMarDirector::setNextStage` for us and an exact `TCardManager::decideUseSector` whose ternary regresses two callers in the unit; see the candidates below.
- #161 and #189 (fjooord, "harness progress") carry the most honest improvements of any source: exact `TMBindShadowManager::drawShadowVolume` and a dozen instruction-count or frame gains in MapCheck, MapWire, MSound, Kumokun, MarioPhysics and NPC code.
- #196 is this fork's own earlier history and was skipped.
- The rest are either merged in substance at the last upstream sync (`a7a99b4`) or score worse than this tree.
- Re-check: the open list above, plus any pull request newer than #201.

## Forks of doldecomp/sms

- Fetched every branch of Penguinjanator, SomeoneIsWorking, CaptainProton42, KakarottoCake, TheAzack9, Astrr00, 999sian, hyblocker, mattsumi, lingomaniac88, adriadam10, ThePlayerRolo, sagemono, nstearns96, break-core, vinvirile, FranCarnovale, Cuyler36, gitRasheed, MagiHotline, fjooord, VaroTv7, MarkKatze, pablomeendez, EliasXbox, Blogans, WitchInVerdant and bluisblu (all at https://github.com/<owner>/sms); Nickg02, Mrkol, benny-dreamly, Limon93, Gelatart, run-my-job and LMFinish have nothing since 2026-01-01.
- Clean-room status: all reconstructions; the Japanese text in them is retail string data or English comments quoting it, as in this tree.
- Astrr00 (`copilot/update-matching-decomp`, `cursor/*-round71-*`): agent-generated matches; 21 of its 33 gains here are `char trash[]` stack padding and one is a fabricated padding struct, so only a handful are usable (the `MSRandVol` constructor, exact).
- fjooord (`setup/sms-registration`, the branch behind #161 and #189): the richest honest source, 18 gains.
- SomeoneIsWorking (`main`): a PC-port conformance fork of upstream (casts, `uintptr_t`, clang fixes), with six small codegen gains (`TConductor::isBossDefeated` 95.6 -> 98.8).
- mattsumi (`matching-campaign`), adriadam10 (`main`), TheAzack9 (27 `az-*` branches), gitRasheed (29 `pr/*` branches, mostly merged upstream), KakarottoCake (29 branches, mostly merged upstream): a few small gains or nothing.
- QbeRoot/sms (https://github.com/QbeRoot/sms) is the archived assembly-era predecessor; nothing to take.
- Re-check: repeat the search and the fetch, then the sweep, against refs with commits after the last check.

## decomp.me

- https://www.decomp.me/preset/61 (Super Mario Sunshine): 880 scratches on 2026-09-29, 180 of them matched, almost all of functions this tree already matches.
- Only nine scratches name a function that is non-exact here, and only two of those carry source.
- `JKRExpHeap::allocFromHead` (scratch KbQBG, score 70) is our body with declarations hoisted and a `u32 pad` local; without the pad it is 99.6 with a frame 8 short, so it is not an improvement.
- `TNozzleDeform::emit` (XgrEB) scores far below ours.
- A class-name search outside the preset found `AudioDecoderForOnMemory` (64dIT, matched, GC/1.1): skipped for provenance, since its comments read as the original THP sample code.
- The same search found `JPADrawExecStripe::exec` (E7O93, preset 72, GC/2.x compiler, score 140): another game's JParticle build, not comparable.
- Clean-room status: scratches are community work of mixed provenance; review each one's comments before using it.
- Re-check: the preset listing, sorted by `last_updated`, for new scratches of functions in `report.json` below 100.

## Modding headers and symbol projects

- JoshuaMKW/SunshineHeaderInterface (https://github.com/JoshuaMKW/SunshineHeaderInterface, cloned at `/home/user/joshuamkw/`), JoshuaMKW/super-mario-eclipse and DotKuribo/BetterSunshineEngine (`/home/user/dotkuribo/`).
- They declare classes from the map for GCC-built mods: member offsets and method lists, no inline bodies, so they are layout evidence only.
- Clean-room status: public mod headers derived from the map and the binary.
- No function improved; use them to name a field or check a class size.
- The Sunshine wiki's Ghidra page (https://smswiki.shoutwiki.com/wiki/Disassemble_Sunshine_Code) only documents loading the map into Ghidra.

## Compiler-behaviour material

- KakarottoCake/decomp-triage (https://github.com/KakarottoCake/decomp-triage): triage scripts and a ten-entry lever catalogue built on doldecomp/sms; every lever is already in `docs/catalog/` (its inline budget, 15 then 10/7/3 per pass, matches LEVERS.md 25 counted differently).
- decomp.wiki (https://decomp.wiki/compilers/MWCC): mostly PS2 MWCC notes; nothing for GC/1.2.5.
- The Decompedia wiki (wiki.deco.mp) was unreachable from this container.
- Already in use and not re-read: JackPriceBurns/mwcc and zcanann/mwcc-rs (`/home/user/ext`, see HANDOFF.md), cadmic/mwcc-debugger (`tools/mwcc-stack/`), and the sister decomps zeldaret/tww, zeldaret/tp, doldecomp/mkdd and doldecomp/pikmin2 (`/home/user/ext/sister`, frame-gaps.md batch c-r22).
- None of these has a fix for the three open classes (`a = b - c` by-value `operator-`, the `JUTColor` temporary stride, the JGadget iterator slot).
  The reconstructions checked spell `operator-` exactly as this tree does (`friend const TVec3& operator-(TVec3, const TVec3&)`), and their `JUTColor` and JGadget list headers carry no shape this tree has not measured.

## Landed in c-r23

- `TGCConsole2::startAppearStar` exact and `startDisappearCoin` 99.36 -> 99.77 (with `startCameraDemo` 99.44 -> 99.57): the above-screen offset `-(y2 + 1)` behind its own inline, and raw `unk160`.
- `TMapObjBase::initAndRegister` exact: raw `mMapObjData` with the named group.
- `TFenceWater::initMapObj` exact: `TFence::initMapObj` names the object name before `strstr`.

## Candidates not landed (other agents' units)

Measured on GMSE01 in this worktree; each needs its unit claimed, a review of the diff and the usual verification before it lands.

- Exact: `MSoundSESystem::MSRandVol::MSRandVol` (Astrr00, a named `half = 0.5f` shared by two stores), `TMarDirector::setNextStage` (#200), `TMBindShadowManager::drawShadowVolume` (#189, the `height` local moved into the loop).
- Exact but regresses `readBlock_` and `copyTo` in the unit: `TCardManager::decideUseSector` (#200, a ternary join).
- Large gains: `MSSceneSE::frameLoop` 96.8 -> 99.7, `TConductor::isBossDefeated` 95.6 -> 98.8 (drops a `default:` label), `TKumokun::checkOnMovingFloor` 97.9 -> 99.4, `TMapCollisionData::checkRoofList` 98.6 -> 99.9 (open-codes the edge test), `TBaseNPC::setIndividualDifference_` 98.1 -> 98.9, `TMario::keepDistance` 99.55 -> 99.9.
- Rejected on review: stack padding (`warpOut`, `TSmallEnemy::receiveMessage`, `TDangoHamuKuri::receiveMessage`, `updateCheckListNode`, `getSlideStickMult`, `writeBlock_`, `TMenuBase::perform`, `watchToWarp`), a fabricated two-vector struct (`execWallCheck_`), `a - -b` (`MtxToQuat`), and a dead duplicate pointer (`randPlay`).
