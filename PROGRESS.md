# Progress

Target: North American `GMSE01` rev 0 on this fork's `main` branch, forked from upstream `ab00c3c9`.
The decompilation is **not complete**.
Keep this file to the current state only: overwrite the numbers and the in-progress list each batch; do not append a history entry.
Per-batch history through batch 69 is archived in [docs/progress/history.md](docs/progress/history.md); later history is `git log`.

## Current numbers

Measured from `build/GMSE01/report.json` on 2026-09-29.

| | Fuzzy match | Perfect match | Fully linked | Linked units |
| --- | ---: | ---: | ---: | ---: |
| Game | 99.51451% | 67.65962% | 20.26703% | 207 / 385 |
| JSystem | 99.89833% | 93.94745% | 81.21971% | 186 / 198 |
| SDK | 99.99897% | 99.71085% | 99.54403% | 148 / 149 |
| All | 99.60210% | 73.57762% | 34.33548% | 541 / 732 |

12,048 of 12,904 functions match. The rebuilt DOL remains byte-identical to the original.

## What recent batches have done

JPABaseEmitter::deleteAllParticle now matches all 212 bytes and its 0x40 frame. Both particle-list starts use the base first-link accessor and then the typed link; this removes the typed wrapper's extra stack homes without changing an instruction. The exact-function count rises by one, and exact code by 212 bytes. The unit still has other non-exact code and 72 unmatched data bytes, so its source-link status is unchanged.
The earlier ambient-color reconstruction in TLightCommon::loadAfter remains at 99.959595%, with four stack operands open.
Full production build, zero function regressions, unchanged JPAEmitter symbol diagnostics and the original DOL checksum pass. The JPAEmitter symbol validator retains its pre-existing missing UNUSED destructor report.
The four units with unmatched data are JPAEmitter (72 bytes), CardManager (136), ShadowUtil (304) and CameraChange (496).
A genuine SandCastle frame-read/write operation makes expanded exact alone but regresses three previously exact inlined callers; it was restored. BossManta radius/scale operations and MapEventSink building placement operations were also restored without safe gain.

The selected unit rows below are refreshed from the same measured report as the totals.

| Unit | Unit match | Exact functions |
| --- | ---: | ---: |
| `MoveBG/MapObjWave` | 100.00% | 12 / 12 |
| `Enemy/effectEnemy` | 100.00% | 18 / 18 |
| `Enemy/tinkoopa` | 99.37% | 47 / 54 |
| `Enemy/elecNokonoko` | 99.98% | 66 / 69 |
| `Enemy/killer` | 99.87% | 50 / 54 |
| `Enemy/limitkoopa` | 99.52% | 53 / 58 |
| `Enemy/Kazekun` | 98.23% | 38 / 43 |
| `Animal/Bird` | 98.76% | 45 / 52 |
| `Animal/BeeHive` | 97.68% | 39 / 49 |
| `Enemy/limitkoopajr` | 99.93% | 25 / 27 |
| `Enemy/TabePuku` | 99.46% | 40 / 48 |
| `Enemy/Kukku` | 99.01% | 31 / 37 |
| `Enemy/bosswanwan` | 99.50% | 62 / 79 |
| `Enemy/Koopa` | 98.78% | 64 / 79 |
| `Enemy/pakkun` | 99.54% | 70 / 78 |
| `Enemy/bosstelesa` | 99.89% | 108 / 118 |
| `Enemy/bosspakkun` | 99.83% | 121 / 129 |
| `Enemy/bossgesso` | 99.39% | 72 / 88 |
| `Enemy/hamukuri` | 99.99% | 217 / 226 |
| `GC2D/SelectShine2` | 98.31% | 9 / 13 |
| `GC2D/Guide` | 99.48% | 9 / 17 |
| `GC2D/Option` | 99.96% | 36 / 42 |
| `GC2D/CardSave` | 99.56% | 15 / 21 |
| `GC2D/CardLoad` | 99.54% | 13 / 22 |
| `GC2D/GCConsole2` | 99.69% | 55 / 70 |
| `Map/BathWaterManager` | 98.81% | 31 / 41 |
| `System/EventWatcher` | 99.95% | 85 / 99 |
| `System/MSoundMainSide` | 99.69% | 22 / 29 |
| `System/MarDirectorDirect` | 99.68% | 5 / 14 |
| `Camera/CameraChange` | 99.91% | 16 / 18 |
| `Camera/cameragc` | 98.72% | 56 / 61 |
| `Camera/cameralib` | 97.14% | 13 / 17 |
| `System/MarNameRefGen_Enemy` | 100.00% | 5 / 5 |
| `System/MarNameRefGen_BossEnemy` | 100.00% | 3 / 3 |
| `GC2D/Talk2D2` | 99.49% | 19 / 24 |
| `GC2D/ConsoleStr` | 99.50% | 14 / 19 |
| `GC2D/hx_wiper` | 99.85% | 41 / 45 |
| `Enemy/BathtubPeach` | 99.72% | 20 / 21 |
| `Enemy/fruitsboat` | 99.87% | 21 / 24 |
| `Enemy/rocket` | 99.50% | 29 / 33 |
| `Enemy/bombhei` | 100.00% | 48 / 48 |
| `Enemy/yunbo` | 99.50% | 33 / 35 |
| `Enemy/amiNoko` | 99.69% | 31 / 37 |
| `Enemy/feetinv` | 97.91% | 24 / 25 |
| `Enemy/seal` | 100.00% | 18 / 18 |
| `Enemy/hanasambo` | 99.70% | 88 / 96 |
| `Enemy/cannon` | 99.75% | 47 / 55 |
| `Enemy/popo` | 99.80% | 48 / 54 |
| `Enemy/tobiPuku` | 99.96% | 116 / 120 |
| `Enemy/igaiga` | 99.55% | 83 / 95 |
| `Enemy/chuuhana` | 99.80% | 47 / 57 |
| `MoveBG/MapObjMamma` | 99.60% | 93 / 103 |
| `MoveBG/MapObjMonte` | 99.96% | 48 / 54 |
| `MoveBG/MapObjRailBlock` | 99.94% | 44 / 46 |
| `MoveBG/MapObjDolpic` | 100.00% | 40 / 40 |
| `MoveBG/MapObjRicco` | 100.00% | 32 / 32 |
| `MoveBG/MapObjPinna` | 99.96% | 62 / 68 |
| `MoveBG/MapObjBianco` | 99.45% | 67 / 74 |
| `MoveBG/MapObjMare` | 98.97% | 59 / 67 |
| `MoveBG/MapObjCorona` | 99.64% | 42 / 52 |
| `MoveBG/MapObjPollution` | 100.00% | 12 / 12 |
| `Camera/CameraBGCheck` | 98.80% | 7 / 10 |
| `MSound/MSoundSE` | 99.98% | 25 / 30 |
| `Player/MarioAccess` | 100.00% | 33 / 33 |
| `MSound/MAnmSound` | 99.92% | 8 / 9 |
| `MoveBG/MapObjBall` | 99.63% | 63 / 72 |
| `Player/ModelWaterManager` | 98.68% | 17 / 25 |
| `MarioUtil/LightUtil` | 99.99% | 38 / 40 |
| `MarioUtil/ShadowUtil` | 79.77% | 28 / 49 |
| `Enemy/koopajr` | 99.47% | 73 / 81 |
| `Enemy/wireTrap` | 99.65% | 29 / 35 |
| `Enemy/hauntLeg` | 99.93% | 25 / 28 |
| `Enemy/BathtubKiller` | 99.40% | 39 / 45 |
| `Enemy/BathtubBinder` | 97.70% | 5 / 6 |

Most remaining differences in these units are frame gaps and per-call-site inlining; see `docs/catalog/frame-gaps.md` and `docs/catalog/codegen-tells.md`.

## Still open

- Remotes `fork` and `origin` are configured. New commits from this session remain local; no push was performed.
- **`System/MarioGamePad` is linked on top of a fakematch**: `updateMeaning` still carries the
  pre-existing `u32 stackAlloc[83]` padding (336 bytes of dead low region over 26 inlined
  expansions). The linked count of 492 includes it; the unit is not honestly closed until the
  padding is replaced by real locals (`docs/catalog/linking.md`, "Links 279").
- `Camera/CameraInbetween` matches but will not link: `docs/catalog/linking.md`.
- Units one function from linking (`MSoundBGM`, `MarioParticle`): `docs/catalog/frame-gaps.md`.
- 856 functions remain non-exact and 191 units remain unlinked; source counterparts exist throughout the game.
  Most remaining work is compiler stack layout, register allocation, inline structure and matching data layout.
- **The `a = b - c` pool residue is the single largest open lever**: all 102 retail
  `bl TVec3::sub` sites are nonmatching, 40 of them at >= 99.3%, for one shared
  slot-placement difference. Research batch 113 in `docs/catalog/frame-gaps.md`
  narrows it to 12 bytes, 8 of which are the `operator-=` inline level.

## Local artifacts

- Source build manifest: `config/GMSE01/objects.json`. Metadata provenance: `config/GMSE01/README.md`.
- Reports: `build/GMSE01/baseline.json`, `build/GMSE01/report.json`.
- Frame-gap worklist: `docs/progress/GMSE01-frame-gaps.md`. Old per-batch JSON snapshots and closure audits: `docs/progress/`.
- Build artifacts and game data are local only and excluded from Git.
