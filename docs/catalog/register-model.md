# The MWCC register-colouring model

Read this when a function's instructions match but register numbers differ: an `r24`/`r25` swap, a whole-function rotation of the callee-saved block, or two FPRs exchanged.
The model was read from the allocator itself (the mwcc-debugger dumps, `tools/mwcc-stack/`) and replays every dumped function exactly, including the 1459-web `TCardSave::execMovement_`.
GC/1.1, which the debugger runs, emits code identical to our GC/1.2.5 for every function compared (emitEffects, execMovement_, requestNpcAnm_).

## The model

Given the interference graph, colouring is deterministic and has only two inputs you can move from source: the order webs are coloured in, and each web's degree.

1. **Virtual register numbers.** Arguments come first, in declaration order (`this` lowest).
   Named locals of the function follow in reverse declaration order (the last declared gets the lowest number), nested scopes included.
   Then compiler objects (`@NNN`: inline argument bindings, inlined callees' locals, IRO temporaries) in reverse creation order.
   Then pcode temporaries (values with no object: CSE'd loads, expression results) in increasing generation order.
   Some destructor-related `@` objects are numbered before the arguments (`execMovement_`'s JSU stream bindings); check `variables.txt` rather than assuming.
2. **Object creation order is breadth-first by inline depth.** Every depth-1 object is created, in source order, before any depth-2 object, and so on (fireWanwan emitEffects: its depth-1 `thing` bindings at line 1484 are `@4162`, calcRipplePos' depth-2 `set` bindings at line 1504 are `@4223`, depth-3 objects at line 1474 are `@4227`).
   IRO temporaries (split webs, CSE temporaries, switch values) are created after all of them, so they are numbered lowest of the `@` objects.
3. **Simplify.** Sweep the webs in ascending virtual-register order, pushing every web whose remaining degree is below K (29 GPRs: r0, r3-r12, r14-r31; 32 FPRs) and decrementing its neighbours; repeat sweeps until all are pushed.
   The degree counts physical-register neighbours and **coalesced webs, which are never pushed and so never leave their neighbours' degree**.
   If a sweep pushes nothing, the web with the lowest cost per remaining degree is pushed (seen only in execMovement_).
4. **Colour in reverse push order** (the dump's `regalloc-*-assigned.txt` order). Each web takes the lowest volatile register no coloured neighbour holds (r0 then r3-r12; f0-f13); failing that, the lowest callee-saved register already in use that no neighbour holds; failing that, a new callee-saved register just below the lowest in use (r31, r30, ...).

So for webs of ordinary degree the colouring order is: pcode temporaries (latest generated first), then `@` objects (earliest created first: shallow before deep, source order within a depth), then named locals in declaration order, then arguments last-declared first.
A web whose degree is still at least K when its sweep reaches it is pushed in a later sweep and so coloured *before* everything pushed in the first one: long-lived `this`, loop counters and hoisted constants in big functions.

## Reading a residue

1. Dump the function: `tools/mwcc-stack/dbg.sh SRC SYMBOL OUT` (Shift-JIS or PCH units: prepend `#include <SMS.pch>` and convert with iconv first).
2. `python3 tools/mwcc-stack/regalloc.py OUT mario/<Unit> SYMBOL [gpr|fpr]` lists every web whose register differs from retail with its colouring position; `--search N` tries moving each such web, `--move V:bW` tests a hand-written order, `--drop V:U,...` removes interference edges and re-runs simplify.
3. Translate the order retail needs into source with the rules above.
   A web that must be coloured *later* needs a lower number: declare it later, make it a named local instead of an inline binding, or push it one inline level deeper.
   A web that must be coloured *earlier* needs a higher number or a higher degree.

`regalloc.py` recovers retail's register per web by aligning the dump's pcode with decomp-diff; commutative-operand swaps (`fadds f1, f2, f1` against `f1, f1, f2`) show up as colour differences but are not colouring and are not fixable here.

## Levers

- **Declaration order of named locals** (C-style declarations at the top count as declared there).
  `TBossHanachan::changeAnmRateAndFrameUpdate_` (closed): retail colours the tumble-loop `i` and the head `MActor*` after the last loop's `i` and `actor`; declaring `int i; MActor* actor;` at the top for the last loop, with the head actor a separate later local, reproduces it.
  The earlier trial that moved the *head* actor to the top moved the wrong web.
- **Naming the values instead of binding them.** Named locals are coloured after every `@` object.
  `TFireWanwan::emitEffects` (closed): the slip-smoke `thing(pos[0][2], pos[1][2], pos[2][2])` ctor bindings (depth 1) were coloured before calcRipplePos' depth-2 `z`; `f32 x = pos[0][2]; ...; thing(x, y, z)` moves them behind it and `z` takes f31.
  The earlier trial named the ripple's components, the wrong side.
- **An implicit conversion instead of an explicit compare moves a pcode temporary.** `TNpcKeepAnm::keep`'s `mBlendOn = blend != OFF` generates the `neg` temporary after the pointer load, so it is coloured first and takes r3; `mBlendOn = blend;` (or a `bool` parameter) closes `TBaseNPC::requestNpcAnm_`.
  That is a shared-header edit (NpcBase.hpp, only caller NpcAnm.cpp), parked for the header owner.
- **Ghost degree.** Argument copies coalesced into r3-r6 stay in every long-lived web's degree.
  `TCardSave::waitForStop` (open): `this` and `result` have remaining degree 31 after the first sweep, so they are pushed last and take r31/r30 where retail has r26/r27; removing any three of the nine ghost edges (setMessage's getStringPtr results, the u16 message-ID bindings, the gpMSound copy) replays retail exactly (`--drop "32:64,77,79;36:64,77,79"`).
  Retail therefore has at least three fewer argument copies live across them; naming the setMessage pieces was inert.
  `TCardSave::perform` is the mirror case: `graphics` has degree 30 and two lower neighbours, so it is pushed in the first sweep after the scissor reference `@5011`; one more ghost neighbour would defer it and give retail's r31.

## Open residues read with the model

- `TGuide::appearGuidePane`: setPaneOffset's bindings are created `this`, `initial_x`, `initial_y` and retail colours them `initial_y`, `initial_x`, `this`; named offsets and a named pane change other webs (worse).
- `TGCConsole2::pauseOut`: the inlined startDisappearStar's unk160 `target_y` binding must be created after the unk108 bindings (one level deeper); accessor spellings cost 8 bytes of frame.
- `TFerrisWheel::control`: the named `sound` must be coloured before `@2539`, i.e. become an `@` object; a TU-local level or the unnamed accessor moves the frame by 8.
- `TMerrygoround::control`: the warp block's `joint` is an IRO split web (`@2050`) created after the loops' `this` copies; retail creates it before them.
- `TNerveAnimalBirdWaitOnGround::execute`: isWantToAction's `over` (depth-1 `@1911`) must be coloured after the IRO temporaries, which only a named local of the nerve or an IRO temporary achieves; both spellings tried change the frame.
- `TBaseNPC::npcWetting`: the switch value is an IRO CSE temporary (`@2278`) created after the first case's `mActorType` temporary; retail colours it first.
- `TNerveTabePukuFound::execute`: not colouring; retail coalesces getZDir's `_x` with the scaled component.
- `TMapObjTurn::touchWater`, `J3DSkinDeform::initMtxIndexArray`: commutative operand order, not colouring.
