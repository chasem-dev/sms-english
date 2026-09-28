# Syncing with upstream

This fork tracks `doldecomp/sms` (remote `upstream`, https://github.com/doldecomp/sms) through real merges, so the shared history keeps each later sync small.
The last sync merged `upstream/main` at `a7a99b4` on 2026-09-28 (merge base before it: `9d330f4`).

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
