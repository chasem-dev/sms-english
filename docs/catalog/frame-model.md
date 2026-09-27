# The MWCC dead-stack model

This model comes from reading the compiler's own stack objects with `tools/mwcc-stack` (research agent c-r1, 2026-09-27).
It replaces guesswork about "saturation" and "inline levels" in [frame-gaps.md](frame-gaps.md), where the two disagree.
Diagnose a frame-only or slots-only function with `tools/mwcc-stack/dbg.sh` before laddering levers.

## The model

1. **Allocation.** Low to high: 8-byte link area, outgoing args, *all* of this function's arguments (whether or not they are in registers, used or not), then every object in the frontend's locals list that did not get a virtual register, then spills, then backend temps (int-to-float magic etc.).
   Nothing is ever removed, so a dead object keeps its bytes.
2. **Which locals get no register.** An object gets a register only if it is still referenced in the IR that reaches the backend.
   An object whose every reference is optimised away (by the inliner's substitution, by IRO copy propagation or forward substitution) is never register-allocated, so it gets a stack slot.
   Aggregates (arrays, class objects whose address is taken) always get a slot.
3. **Which objects exist.** Named locals; unused/uninitialised named locals of the function itself; for each inline expansion, a binding object per argument that is not "simple" (simple = constant, local/param variable, `this`, address constant, arithmetic of those; a load such as `ga`, `a->p`, `g` is not simple), the inlined callee's own locals (unused trivial scalars are dropped), a result object; by-value class parameters and temporaries; and IRO temps.
4. **When a binding/local dies (single def, single use).** It is forward-substituted and dies when its use is a store value, a store address/base, a condition, an index, or the RHS of another copy.
   It survives in a register (costs nothing) when its use is directly a call argument or call receiver (`ext(t)`, `ext(t + 1)`, `p->f()`), or when it has two or more uses, or is live across a call.
   The same rule holds for named locals of the caller, which is why `int t = x; g = t;` is +1 slot and `int t = x; ext(t);` is +0.
   So an accessor whose result feeds a call is inert; one whose result is stored or tested costs a slot. That is the "per site" behaviour of every ladder in frame-gaps.md.
5. **Result objects.** Measured (4-byte units, object type distinguished with `double`):
   `g = f(simple)` +1, `ext(f(simple))` 0, `if (f(simple))` 0; with a non-simple argument add its binding: `ext(ga->get())` 2, `g = ga->get()` 3, `if (ga->get())` 1.
   A statement-bodied inline (`{ int r = a; return r; }`) adds its local `r`.
6. **Sizes and packing.** Each object takes its own size and alignment (u8 1, u16 2, pointers/ints 4, float 4/8, double 8), packed in list order; the frame then rounds to 8.
   TFlagT<u16> copies in `TApplication::proc` are 2 bytes each; u8 argument bindings 1 byte.
7. **Order.** List order is reverse creation: the first object created sits highest, later ones lower.
   The inliner expands **breadth-first over the whole function**: all depth-1 call sites (in source order) create their bindings and locals, then the calls inside the inlined bodies are expanded, and so on; IRO temps come last (lowest).
   Proof (v1.cpp): `off = p - pos; ...; ride();` puts operator-'s by-value copy above ride()'s `Mtx`; `off = diff(p, pos)` with `diff` an inline wrapper puts the same copy below the `Mtx`.
8. **Non-additivity ("saturation").** IRO temps are part of the dead set, and a frontend binding that names an expression removes the IRO temp that CSE would otherwise have made for it.
   moveRequest: stock header has 3 dead IRO temps + the `const TVec3&` result pointer; with a by-value `operator-` the IRO temps vanish.
8a. **Named locals first, then temporaries.** The list holds every named local of the function (declaration order) before any compiler object, whatever the source position.
   A parse-time temporary (`TMsRange<f32>(a, b).rand()`, an implicit `TPathNode(pos)`, a by-value parameter copy) therefore sits below the whole named block, and an inline object below every parse-time one.
   A retail temporary at the top of the frame means retail named it and declared it first (c-d2: `TCannon::setKillerGoalPoint`, `bombSet`).
8b. **Inlined callee locals are created last-declared first.** In willFall's `setSafeGoal` expansion the TPathNode `goal` (declared last) sits highest, then `point`, `index`, `range`: the reverse of the caller's own named block.
8c. **Within one expansion, callee locals come before argument bindings.** In a `TVec3 r; r = *fst;` `operator-` the local `r` is created first and the non-simple `fst` binding right after it, so the binding sits directly *below* `r`.
8d. **By-value-returning calls are expanded late, and a by-value parameter copy is created after the return temporary.** With `operator-` returning `TVec3`, its return temporary is a parse-time object (top of the pool), but its body's local sits below every ordinary depth-1 object (`moveRequest`: below `checkRideReCalc`'s Mtx).
   `friend TVec3 operator-(TVec3 a, const TVec3& b)` puts the parameter copy directly under the return temporary, the same slot map as the local-`r` form.
9. **All-or-nothing.** A function with no locals area (no frame beyond the link area) shows none of this; add one saved register or one slot and all dead objects materialise (cc12).

10. **Offsets count what was created later.** Allocation starts at the bottom, so an object's offset is the size of everything created after it (plus the floor).
    An object 4 low in ours means retail created one more word *after* it; removing an object created *before* it (a named local, an earlier site) never moves it.
11. **The frame bounds the count.** frame = align8(align8(top of the locals) + saved GPRs/FPRs), so retail's frame gives the top of its locals to within 8 bytes.
    When the slots below say "+4" but that bound forbids a net addition, the object was *moved*, not added: something created early (usually a named local, which sits at the top) is created later in retail.
12. **Inline bodies and result objects.** A straight-line inline body that ends in `return <constant>;` makes no result object (the call becomes a comma expression).
    A body with control flow makes one, which dies when the caller copies it into a local (+4).
    `BOOL result = 0; ...; return result;` in a straight-line body costs +8 (the local and the result object it forces); in a body that already had a result object, +4.

## Experiments (synthetic TUs)

Marker method: `int mk; extp(&mk);` declared first sits right above the dead region, so `(mk - 8) / 4` counts dead words.
- e3/e5: `ga->get()` +8 frame per expansion; `a->get()` (local receiver) 0; unused parameter of the function +4.
- c1: `s1(ga->m)` 2, `s1(i+k)` 0, `s1(g)` 2, `s2(ga->m, gb->m)` 3, `sr(i+k)` (const& to rvalue) 2, `sr(i)` 0, `ga->set(i)` 1, `ga->setn(i)` (this used twice) 0, `gA.get()` 0.
- c3/c5/c6: contexts listed in rule 4 (named locals and inline bindings identical).
- c9: double-typed returns decode the object kinds of rule 5.
- v1.cpp: breadth-first order (rule 7).

## Applied

- `TMario::moveRequest` (MarioMove, read only): retail = offset 0x5c, 12-byte 0x50, ridingMtx 0x20, operator- copy 0x14, one 4-byte 0x10.
  With the header's `operator-` as `TVec3 operator-(const TVec3& b) const { TVec3 r(*this); r -= b; return r; }` (or a friend with a local, or a by-value helper) the frame is 0x70 exactly and the layout is retail's minus that last 4-byte object (copy 0x10, Mtx 0x1c: every slot 4 low).
  Tree-wide that header is 6 up / 52 down (exact 11866 -> 11859): the `bind` family's instructions change (cc23's copy count), so it is a site property, not a header lever.
- `TApplication::proc`: our dead set is 28 TGameSequence/TFlagT copy objects (1-2 bytes each). A 32-variant grid of `GameSequence.hpp` (u8/int params, TFlagT by value/const&, operator= by value/const&, set/memberwise, assign/set(get())) never exceeds 0x80 at unchanged instructions (retail 0xc8); int params give proc 0x80 and setNextStage 0x48 (retail 0x50).
- `TApplication::checkAdditionalMovie` (0x58 vs 0x20) has **zero** dead objects in ours: all 11 `getInstance()` feed call receivers (rule 4), so no accessor spelling can add frame there; retail's 56-64 bytes are some other construct.
- `jumpingBasic`: ours has 10 dead objects (3 `mWallPlane` bindings for isNoWallJump/isFence/getNormal, a bool and six IRO temps); retail needs 18 more words with no instruction change and no visible slot, so nothing is closable from evidence.
- `TMario::jumpMain` (closed, c-d1): 11 missing words all below pullJumping's `pos` (0x34 vs 0x60) and none above, so only handlers inlined after pullJumping could carry them.
  Named `result` locals in jumpingThrow, the four jump*Down handlers, landSafeDown and fallDead give exactly +0x2c (5 x 4 + 3 x 8); the same spelling on handlers before pullJumping overshoots the frame.
- `TMarNameRefGen::getNameRef` (closed, c-d1): two `this` slots 4 low with the frame already at its bound, so one object had to move from above them to between TSplashManager's depth-1 binding and TSmplFader's TColor.
  Building Mario through a static inline (its `mario` becomes a depth-1 callee local) is that move; the same level around MLight (whose inline ctor then expands one level deeper) moves every later slot and the frame.
- `evSetAttentionTime` (EventWatcher, open): the push slice is 0x10 low and frame 0x18 short. Moving the dead pop one inline level down (`static inline int f(interp) { return TSpcSlice(interp->pop()).getDataInt(); }`) lands the slice and every object below it; the frame is then 8 short (one or two words above the slice), and no spelling found adds only those.
- `evStartMontemanBGM` (open): retail = ours plus one object created after the push slice (the frame bound leaves no room above it); every sound-receiver spelling tried adds that word only together with two above.
- c-d2 closes (all instruction-exact before, one dead object each way):
  `TChorobei::perform`: retail one word short below the Mtx; `mCannon->checkLiveFlag(...)` binds the loaded receiver (rule 5, `if (ga->get())` 1), the raw `mCannon->mLiveFlag & ...` does not.
  `TCannon::setKillerGoalPoint`: the `TMsRange` temporary is retail's topmost object (rule 8a: named `range` declared first), and retail has one more named word below `pos`: a named constant `f32 radius = 500.0f;` used twice is propagated away and keeps a dead 4-byte slot.
- Partial, not committed: `bombSet` with a named `range` and `getSaveParams()->` lands `range` and the low region, leaving one named scalar too many (retail has one of `r`/`rate`, but both named statements are needed for the schedule).
  `wireMove` with a named `f32 ratio = mWirePosRatio;` for the first compare lands `start`/`end`/`dir`; the operator- by-value copy stays 8 high (header class).
  `TNerveBPTumbleOut`: reading the frame directly instead of through `BosspakkunBckFrame` removes the one depth-1 object above `mouth` that retail lacks, but retail then has one more object below it (frame 0x98 vs 0xa0).
- chuuhana `setSafeGoal` family: retail's `reset()` ends in `setSafeGoal()` (its tail is that body verbatim), and with `ChuuHanaGraphNode(ChuuHanaGraphOf(this), index)` / `ChuuHanaGraphNodeNum(ChuuHanaGraphOf(this))` in setSafeGoal instead of the SafeNode/SafeNodeNum binders, `reset` is byte-exact and isCollidMove's frame lands (0x118).
  That spelling has six fewer inner dead words, which ForceJumped (exact today) needs from its own later code: it drops to 0x10 short, so it is not committed.
  Per rule 8b the block stacks goal, point, index, range from the top in every caller; willFall/KeepBalance then need six more depth-1 words before the expansion.
- **The `a = b - c` class (research c-s1, 2026-09-27): retail's `operator-` returns by value; one dead word per site is still unexplained.**
  Retail `moveRequest` has a 12-byte object right under `offset` (a parse-time temporary) and the live copy low (0x14), which only a by-value return produces (rule 8d).
  Tongue's `(tpos - mTipPos) * k` is `bl __ct__(r <- tpos); bl __ami__(r); bl __ct__(ret <- r)` with the last source `addi r4, r1, r`, not `__ami__`'s r3: the body is `TVec3 r(a); r -= b; return r;` or `(TVec3 a, ...) { a -= b; return a; }` (identical slot maps and code), not `return r -= b;`.
  Both need the uncast `operator=` (`*(Vec*)this = other`) so the return copy disappears at `m = a - b` and `set(a - b)` sites (cc23).
  Under that shape every measured site is exactly one 4-byte object short at the very bottom (created after the live copy): `TCoasterEnemy::bind` 0x10/0x28 retail against 0xc/0x24, `moveRequest` all slots 4 low with the frame exact, the whole `bind` family.
  Refuted as the source of that word (all slot-identical to the plain shape): copy constructor base-init, implicit, statement-bodied, out-of-class `inline`; `const` return; `return (r)`; `TVec3 r; r = a;`, `r = (const Vec&)a`, raw `Vec` copies; an extra helper level before `operator-=`; a member `operator-`; a `const Vec&` left operand (a reference-to-base conversion is simple).
  Shapes that add exactly one word at bind sites, both contradicting Tongue: cc23's `operator-(const Vec* fst, ...)` (the conversion makes `fst` a binding; +1 in bind, +2 with an IRO temp in `moveRequest`) and `TVec3 r; (r = a) -= b; return r;` (the `operator=` result becomes `-=`'s `this` binding; +1 in both, but the site's first copy is scheduled differently).
  `TVec3 r(a); return r -= b;` adds two (result binding plus an IRO temp) and closes `attackToMario`, `wireMove`, `TLeanMirror::loadAfter`, `TBeeHive::bind`, whose extra word is likely their own.
  An accessor on the right operand (`nextPos - getPosition()`) supplies the word in `TLiveActor::bind` and `moveRequest` but swaps two instructions in `bind`.
  Tree-wide against 11880 exact (census; uncast `operator=` in every row): plain by-value (either spelling) 11875, 13 up/48 down; `return r -= b` 11882, 19/35; `const Vec*` 11888, 25/33 (`__ami__` MISSING, wire* +6); `(r = a) -= b` 11885, 18/40. `ninja changes_all`: plain 32 up/80 down functions, `const Vec*` 42/58. None committed.
  The losses that need site work whatever the header: `TVec3<f32>(a - b).length()` sites (retail has the return copy: unwrapped under the stock header they are 55 against 61 instructions, and under a by-value header they match but their binders were tuned to the old pool), and the cast-`operator=` group (`TBWLeashNode::calcMatrix` 99.15 -> 87.03, `forceRequest`, `calcNrm`).
- **Migration attempt (c-m1, 2026-09-27): not landed; work is on branch `wt/c-m1-wip`.**
  Header: `friend TVec3 operator-(const TVec3& a, const TVec3& b) { TVec3 r(a); r -= b; return r; }` plus the uncast `operator=`.
  Site fixes that are exact under it (all were exact before, so they only avoid losses): `soundTorocco` (`(mPosition - mToroccoPos).length()` and the `SMSGetMSound()->startSoundActorWithInfo` wrapper instead of the binder), `toroccoEffect` (`mPosition`, `mToroccoWind.get()`, `mToroccoSpark.value`), `isTakeSituation` (`object->mPosition - mPosition`, raw `object->mDamageRadius`), `moveRoof` (`mPosition - getPrevPosition()`, named `mult = ...mAnmMult.value`).
  Tongue: a `TongueScaledDiff(a, b, k) { TVec3 d = a - b; return d * k; }` level gives retail's `ctor ; __ami__ ; ctor` plus the inline by-value copy and keeps `__ami__`; plain `TVec3 diff = mTipPos - mHeadPos;` and `(tpos - mTipPos).length()` replace `TongueSubTo`/`TongueDist`, which makes `movement` instruction-exact (frame 0x28 short, 96.70 -> 99.7).
  With those, the full build keeps the DOL and exact stays 11920 (+`TEffectColumWater::generate`, `TMapCollisionBase::setCheckData`; -`TNerveMameGessoJitabata::execute`, -`TBaseNPC::execUTurn`), but `changes_all` still has 75 function regressions, about 35 of them the `bind` family.
  Blockers: the unexplained word (below), and units held by c-d16 at the time (`Enemy/enemyMario` `findRunAwayNearestNode` 98.82 -> 88.10, `Player/Yoshi` `doSearch` 99.74 -> 96.25 and `movement`).
- **The missing word belongs to the copy-out, not to `operator-`.** Under the by-value shape, `.length()` sites need no extra word (the four above close without one), while `m = a - b`, `T v = a - b` and `setX(a - b)` all lack one below `r`.
  A dead callee local in `operator=` supplies it: `{ Vec& v = *this; v = other; return *this; }` closes `TCoasterEnemy::bind` and `THaneHamuKuri::bind`; moving that local one level down (`operator=` calling a helper holding it) closes `TLiveActor`, `TAmiNoko`, `TCoasterEnemy`, `THamuKuri` and `TLimitKoopa::bind`.
  But on every `operator=` it costs 11920 -> 11675 exact tree-wide: retail's plain `a = b` assignments have no such word, so the object comes with the temporary being consumed, not with `operator=` itself.
  A dead local in `operator-` declared before `r` (`const TVec3& bb = b; TVec3 r(a); ...`) makes `moveRequest` exact and puts every `bind` slot right, but evaluates `b` early (one scheduling swap), which is also why `nextPos - getPosition()` is not the answer (`TLiveActor::bind` 99.87 against 99.95 today).
  Inert: `operator=` spelled void, implicit, out of class, with `static_cast` or a `Vec*` local; `operator-` as a member template, `r.sub(b)`, `return TVec3(r)`, `TVec3 r = a`; a trivial destructor breaks the code.
- Tool note: GC/1.1 rejects some TUs that 1.2.5 accepts (chuuhana: incomplete `J3DJoint`); prepend the missing `#include` to a scratch copy of the .cpp and pass that to `dbg.sh`.
- **Why ours has no copy-out word (research c-r2, 2026-09-27): hoisting.** Not closed; no header committed.
  A by-value call that is an argument of a *statement-level* inline call (`m = a - b`, `setX(a - b)`, `T v = a - b`) is hoisted: `operator-` is expanded as its own statements and the consumer gets `&temp`, which is simple, so nothing is bound.
  In expression context (`f = (a - b).length()`) `operator-` is expanded in place (an ECOMMA) and the consumer binds it; the arguments are expanded first, so that binding sits directly below `r`.
  At statement level a binding is made before the argument is expanded (above `r`).
  Retail's copy-out word is not a consumer binding: every spelling that binds the temporary at a copy-out site (a `const Vec&` setter, an identity inline around `a - b`, `(void)(m = a - b)`) stops the copy propagation (+6 instructions, frame +0x18 to +0x20).
  So the word is an object that stays dead off the copy chain (an IRO temp or a deeper callee local).
- The only spellings found that add exactly that word with identical instructions return the assignment expression from `operator=`: `return (TVec3&)(*(Vec*)this = other);` (also `static_cast<TVec3&>`, `*(TVec3*)&(...)`, `(Vec&)*this = other` inside).
  In a real-unit compile they make `TCoasterEnemy::bind` and `TLiveActor::bind` byte-exact.
  The mechanism is a destination-pointer temp plus one IRO temp, and it is not specific to temporaries: at a plain `m = n` or `setX(n)` the pointer keeps a register (+1 `addi`, frame +8).
  That hits every plain assignment, so it was not priced tree-wide.
- Refuted in synthetic TUs, with instructions and slots compared at `setX(a - b)`, `m = a - b`, `T v = a - b`, `d; d = a - b`, `(a - b).length()` and five plain-assignment shapes:
  a 150-cell grid of copy constructor (base-init, implicit, `Vec((const Vec&)o)`, cast statement, `(Vec&)*this = o`), `operator=` (uncast, `(Vec&)*this = o`, cast, implicit, `(const Vec&)o`, `Vec*` local) and `operator-` (local `r(a)`, `r = a`, by-value parameter, member, `r; r = a`).
  None adds the word at copy-out without adding it at plain assignment; the implicit, cast and `(const Vec&)` `operator=` lose the propagation (+6).
  Also inert or worse: `const` return, out-of-class and after-use definitions, a helper level, `return TVec3(r)`, `return *&r`, `return (const TVec3&)r`, `TVec3 s(r); return s;` (+16), a non-const `operator=(TVec3&)` overload (the setter's `const` parameter still takes the other one), a by-value `operator=`, a comma-expression body, a trivial destructor (stops `operator-` inlining).
  Compilers GC/1.0, 1.1, 1.2.5 and 1.2.5n give identical layouts here; 1.3.x differs.
- **The `.length()` sites are one word over, not exact, under the local-`r` shape.** With only the unwrap (`(a - b).length()`, no binder respelling), `soundTorocco`, `toroccoEffect` and `isTakeSituation` have `r` 4 high: the receiver binding.
  With `friend TVec3 operator-(TVec3 a, const TVec3& b) { a -= b; return a; }` (the stock operator with a by-value return) all three are byte-exact with no other change.
  c-m1's exact closes of those sites under the local-`r` shape came from retuning their binders, so they are not evidence that retail has no word there.
  But `moveRequest`'s live copy is low in retail (0x14), where the by-value parameter puts it at 0x40; each shape is right at one class of site. `TPinnaCoaster::control` stays 8 over under both.
- Tool note: on a synthetic TU `dbg.sh` takes about 3 s, and the source path must be absolute.

- c-d7 closes (all instruction-exact before):
  `TCogwheel`/`TWireBell::initDraw`: the file-scope `static const GXColor` lever, as predicted.
  `TAnimalBird::load`: retail one word more below `eventID` with frame slack; naming `J3DModelData* modelData` in the inlined `initTevColor` chain (`getModel()->getModelData()->getMaterialName()`) adds exactly one dead callee word. Splitting a chained accessor inside an inlined callee is a cheap +4 below everything named.
  `TSamboHead::genEventCoin`: a hand-written rotation matrix (`s16 angle; f32 s, c; mtx[..] = ...`) put 12 bytes of dead named scalars above `range`; retail's `MsMtxSetRotY(mtx, deg)` makes them callee objects (angle binding, sin, cos) below it. Grep for open-coded `JMASSin`/`JMASCos` matrix fills before laddering.
  `TSamboFlowerManager::dropLeaf`: retail's dead `params` sat above `angles`; declaring it uninitialised first and assigning it in the loop moves its slot to the top (rule 8a).
- An uninitialised named local declared at the top and assigned from an inline's result (`MtxPtr mtx; ... mtx = f();`) is dead (+4 top), while the same for a plain member read (`f32 speed; speed = mSpeed; if (speed ...)`) gets a register; declared-and-initialised at the use it was dead.

## Refinements (unit agent c-d3, 2026-09-27)

- **Upper/lower trades.** A named single-use value (`f32 searchDist = p->m.get(); if (d < searchDist)`) is one more dead object in the named block, and can remove one IRO temp from the low region (rule 8), so the frame stays put while every object declared after it moves down 4.
  A named base pointer used only as an index base (`TGraphWeb* graph = ...->getGraph(); graph->getGraphNode(8)`) is the same trade against the inline `this` binding it replaces.
  When retail shows extra words *between* two named aggregates and fewer below, look for these two spellings first: `TEnemyMario::emWaitingToInviteMario` closed with one of each.
- **Callee locals are depth-1 objects.** A named local in an inlined callee's body is created with that callee's depth-1 expansion, ahead of later depth-1 call sites in the caller.
  `TMario::walkEnd` needed one dead word above `considerRotateStart`'s `direction`: a nested `f32 speed = mForwardVel;` in `isRunningSlipStart` put it there (retail does not emit `isRunningSlipStart` out of line), and `rate = mForwardVel / 4.0f` dropped `getForwardVel()`'s by-value result object below it (rule 5, `g = f(simple)`).
- **Per-field copy temps.** A by-value `J3DGXColorS10` copy creates four 2-byte IRO temps (one per `s16` field) per distinct source object; copies from one shared source share them (`TYoshi::entry`: sharing one `tevColor` drops 8 of the 12).
- **Where a by-value copy is created is a depth fact.** `TYoshi::doSearch`'s retail `mTipPos - pos` copies sit below all depth-1 objects, i.e. they are an inline callee's local (`TVec3 r(a); r -= b; return r;`), not the friend operator's by-value parameter, which is created at the call site.
- The debugger cannot show which expression an IRO temp named: `@N` created and removed inside IRO appears only in `variables.txt`, not in any frontend or backend dump.

## Refinements (unit agent c-d4, 2026-09-27)

- **Callee locals are reversed.** Inside one inline expansion the callee's own objects are created last-declared first, so its first-declared local sits lowest: `checkDropInWater` (gesso) lists `local_34` over `velocity` over `position`; `decideTarget` (fireWanwan) lists its two `TVec3` temporaries over `dir` over `diff`.
- **A callee local is created after every parse-time object.** Moving a block *with its declaration* into a TU-local inline turns a named local into a callee local, created at expansion; that is the lever when retail has an object declared earlier in the source sitting *below* one declared later (hinokuri2 `GraphWander`: `local_60` under `TStack_3C`; tamaNoko `Attack`: velocity copies under `setGoalPathMario`'s `TPathNode`).
- **Dead forward-substituted named locals sit on top.** `TLiveActor* body = spine->getBody(); THinokuri2* self = (THinokuri2*)body;` puts `body` above every other local; the same two lines inside `Hino2Self()` put the binding under them (`TNerveHino2Die` closed on that alone).
- **An uninitialised named scalar declared first moves its dead slot up.** `f32 diff;` at the top of `TTamaNoko::walkBehavior` puts its slot above `local_34` (closed).
- **A file-scope `static const GXColor` removes the dead named colour.** It folds to the same immediate, so `TColor(kColor)` keeps the copy temporaries and drops the 4-byte local (`TSwingBoard::initDraw` closed); `TCogwheel`/`TWireBell::initDraw` report the same +8 residue.
- **Index spellings price differently.** `a.back()` vs `a[a.mSize - 1]` vs `a[a.size() - 1]`, with the array reached through a binder or raw, span -4 to +0x10; the exact one for `TFireWanwanTailHit::moveRequest` was a binder receiver with a raw `mSize` index.
- **Units built with `mwcc_sjis` and the `SMS.mch` prefix** fail in `dbg.sh` (`undefined identifier 'j3dSys'`). Prepend `#include <SMS.pch>` and convert to Shift-JIS into a scratch file, then run `dbg.sh` on that copy: `{ echo '#include <SMS.pch>'; cat src/X.cpp; } | iconv -f utf-8 -t shift_jis > $SCRATCH/sj_X.cpp`.
- `TBossManta::collidedWithWater` (closed, c-d5): 8 bytes too many below the second hit-count array came from the value binder `{ s32 g = p->mGeneration; return g; }` compared in the caller.
  The predicate `{ s32 g = p->mGeneration; return g >= 4; }` gives retail's frame with the same single `mGeneration` load; `mGeneration >= 4` spelled in the caller reloads it, and a caller-level named local lands at the top and overshoots the frame bound.
- `TBossManta::moveObject` (closed, c-d5): naming the loop's receiver (`THitActor* hit = mCollisions[i]; hit->isActorType(...)`) removes the inline `this` binding from the low region and adds a named slot at the top, which the frame's rounding absorbed.
- `TBossMantaManager::setupEfbAlpha` (closed, c-d5): a `(GXColor){...}` argument's two temporaries belong to depth 0, so they sit above anything an inline creates.
  Retail has them above the `Mtx44`/`Mtx` locals, so those matrices are locals of two small inline helpers (ortho projection, identity load).
  Declaring `GXColor color;` at the top of the body and assigning it later puts it *below* the matrices: named locals are ordered by first reference in the statement list, not by declaration.
- `TBossMantaAdditionalCollisionSet::update` (closed, c-d5): 0x58 missing with every instruction right was two named `TVec3<f32>` built from named scalars (`TVec3 center(cx, cy, cz)`), each worth the vector plus its constructor's argument objects.
  Building them straight from the matrix loads reverses the load order (arguments evaluate right to left); four vectors overshoot by 0x30. A 3^4 per-joint sweep (scalars / ctor / `set`) found the one exact combination.
- `evStartMontemanBGM` (open): retail's layout equals our `MSound* sound = EventWatcherRawMSound();` spelling minus the binder's own local: two dead IRO temps below the push slice and nothing above it.
- `TApplication::initialize_nlogoAfter` (open): the unused `JKRMemArchive* this_00 = new ...` keeps a dead named slot between the two streams that retail does not have (drop the name: instruction-neutral, -4).
  Retail then needs 8 more bytes created at depth 1 between the name-ref stream's ctor binding (line of `stream(bufStageArcBin, ...)`) and the option stream's, plus 4 below; a `ResTIMG` data helper at the two `getResource` sites adds 16, all below.
- `TModelWaterManager::drawSilhouette` (open): every slot 0x1c low, so retail has 7 more words created after all of this body's own objects; the two particle-count binders give 4.
- `TBossManta::control` / `updateAnimBlend` (open): retail has exactly 12 bytes between `local_134` and `curVel`, i.e. `cross1`'s slot, with the store/reload of `turn` at `cross1.z`'s offset; so `b` and `turn` are not dead named locals there.
  Scalarising the cross product drops the frame by 0x20 (the `cross` expansion's `_x/_y/_z` are part of retail's set).
## Refinements (unit agent c-d6, 2026-09-27)

- **Default-argument temporaries.** `JDrama::TFlagT<u16>()` (the defaulted `T v = T()`) makes one more dead object than `TFlagT<u16>(0)`; with `startStateTimer(mExplodeWaitTime)` for the store it closed `TSandCastle::waitBeforeExplode` (the unnamed temp is then the top object, retail's layout).
- **Moving a result object one depth down.** When retail has one word fewer above an object and one more just below it, and the word is a binder's result, wrap the binder in a direct-return inline: `KumokunIsTaken(p) { return TakeActorIsTaken(p); }` creates the binder's local with the depth-2 expansions (`TKumokun::calcRootMatrix`).
- **A named local's slot moves with its declaration.** A dead named pointer (`mouthMtx`, forward-substituted) declared before the aggregates sits at the top instead of between them (`TBossTelesa::genAttacker`, with a named `roll`).
- **Sharing one aggregate across switch arms** (function-scope `TVec3 trans`) removes a 12-byte named object; the freed words then come back from named single-use values (`f32 height = ...getMax().y - ...getMin().y;`) (`TMammaBlockRotate::control`).
- **FPR order via argument bindings (rule 22).** A caller's named `f32 speed = m;` fed into `speed + getFrame()` gets f30 after the inline getFrame temp; passing `m` as an argument of an inline helper (`SandBombAddFrame(actor, ctrl, speed)`) makes it a depth-1 binding, allocated first (f31), which is retail's order in all five sand-bomb functions.
- **Single-site binders price 1-3 words.** A binder over a raw global/member (`TMarDirector* d = gpMarDirector; return d;`) at one site gave +8 low region, over an inline accessor (`SMSGetMarDirector()`) +0xc; `SMS_GetMarioGroundPlane()` and `mChangeStage` binders +4 each. Use them only where the frame bound leaves exactly that gap.
- **Auto-inlining guards the out-of-line body.** Shrinking a small member's statement count (TVec3 `add`/`scale` for x/y/z arithmetic) got `TKoopaJrSubmarine::makeCollisionPositions` inlined into `perform`; the component-wise `+=`/`*=` spelling kept the frame and the out-of-line call.
- `TApplication::setupThreadFuncLogo` (closed, c-d8): retail's `heap` word sat below the two `SMSLoadArchiveARAM` path buffers instead of between them and the `SMSLoadArchive` ones.
  Calling through the real JSystem wrapper `JKRDvdToMainRam(...)` instead of `JKRDvdRipper::loadToMainRAM(...)` pushes the heap accessor to depth 2, so its object is created after every depth-1 callee local; the accessor must then be a direct-return fork (the binder overshoots by 8).
  When a word is missing below the last expansions and extra above them, look for a header inline wrapper (`JKRDvdToMainRam`, `JKRGetNameResource`, ...) around the call first.
- `TApplication::initialize_nlogoAfter` (open, c-d8): without the `this_00` name and with the stage-archive search behind a binder over `search2`, frame and every slot land except the option stream's ctor binding (4 high): one more depth-1 object belongs between the two stream constructions and one fewer below.
- `TBaseNPC::npcMadding` (open, c-d8): retail ranks the vectors named result (top), parse-time temporary, depth-1 callee local (lowest), i.e. `TVec3 v = a - b` through a by-value-returning `operator-`; a TU-local by-value helper reproduces that order but not the registers. Header class (JGVec3.hpp cc23 shape).
- The `new JUTTexture` storeTIMG `this` copy (CardSave initData, Guide load, GCConsole2, SelectMenu, Talk2D2) appears in retail at sites whose source is identical to copy-free ones (CardLoad, GCConsole2 vs CardLoad coin loops); a TU-local texture loader helper does not produce it.

## Refinements (unit agent c-d9, 2026-09-27)

- **A this-taking inline moves a whole statement's bindings one depth down.** `TSpineEnemy::goToRandomNextGraphNode`/`goToRandomEscapeGraphNode` (closed): the if/else node choice's five `getTracer()->...` receiver bindings sat above `setGoalPathFromGraph`'s TVec3/TPathNode block; retail has the block on top.
  Wrapping the if/else in `static inline void f(TSpineEnemy* enemy)` (argument `this` is simple, so no depth-1 binding) creates the bindings at depth 2, after the block, with no instruction change.
  A helper taking the tracer (`f(getTracer())`) or per-read helpers returning values do not work: the argument binding or the result objects stay at depth 1.
- **Chained accessors expand by nesting, not statement depth.** In `jumpToNextGraphNode` the receiver chain inside `getGraphNode(getTracer()->getCurGraphIndex()).checkFlag()` is created after `setGoalPathFromGraph`'s block although both are caller-level code; the idx statement's three bindings sit above it.
- **Moving a loop into an inline changes its induction setup.** The exclusive variant's loop moved into a helper gets `li r29, 0; addi r30, r29, 0` instead of retail's two `li`; a helper holding named `currIdx`/`idx` also drops the `mr` copies retail keeps (goToDirectedNextGraphNode), so those locals are caller-level in retail.

## Refinements (unit agent c-d10, 2026-09-27)

- **A converting argument binding moves up when named.** An inline setter taking `u32` fed an `s32` local makes a typecon binding (non-simple, rule 3) created with the depth-1 expansion, below every named local.
  `u32 id = eventId; setEventId(id);` turns it into a dead named word at the declaration point: `TShine::loadBeforeInit` closed (retail had one word between `eventId` and `v` and one fewer below `v`).
- `TMap::isTouchedOneWall` (open): the inlined `center` reference of `isTouchedOneWallAndMoveXZ` is part of retail's set (dropping it is -8); the one missing word sits below `record`. Inert or code-breaking: `int r;` declared before the record, a named `bool`, `if (r != 0)` (loses the ternary's bool), default-ctor plus `set()` (stops the inlining).
- `PopoRollCallback`: `MsMtxSetRotY(rollPtr, 180.0f)` is instruction-identical to the open-coded `JMASSin(0x8000)` block but frame-inert there; retail's six extra words are all low region (`gpCurPopo->isRollJump()` is +8).
  `PopoNonScaleCallback` needs 15 more low words than ours with the same two inlined nerve compares; retail's `isUseScaleCallBack`/`isRollJump` expansions carry about twice our dead objects.
- `TShine::control`: `MsAngleWrap(mRotation.y)` for the discarded `MsWrap(...)` dead-strips the wrap loops (-9 instructions per site), so retail calls `MsWrap` directly there.
- `decideRandomLoveFruit` (open): every random spelling (`f32 rnd` named, `MsRandI`, a named product) reaches +8 at most; retail needs +0x10.
- The 0xa0-0xc8 dead regions with no stack access (`TPoiHanaManager`/`TGessoManager`/`TIgaigaManager`/`TTamaNokoManager::initSetEnemies`, `TNerveBWRoll`) are one size family; look for one shared construct rather than per-function levers.
## Refinements (unit agent c-d12, 2026-09-27)

- **The two-argument sound wrapper costs two words.** `SMSGetMSound()->startSoundActor(id, &pos)` (the fabricated overload with a named `sound`) adds its local and forces the six-argument body's result object; spelling the six arguments at the site closed `TPictureTelesa::touchActor` (0x30 -> 0x28), whose old TODO blamed a helper's named products.
- **One reused local instead of one per loop.** `TMario::addDamageFog` had three named `J3DModelData*` (one per fog loop) between `fogColor` and the compound-literal temporaries where retail has one: reassigning a single `modelData` for the body, cap and hand loops put the temporaries on retail's slots, and one more low word (a named `J3DPEBlock* peBlock` in the first loop, found by lever-search once the named block was right) closed it.
  Fix the named block first (rules 8a, 10), then let lever-search price the low region.
- **Header inlines that hide a depth.** `TGraphTracer::getCurrent()` (fabricated) expands `getGraph()->getGraphNode(mCurrIdx)` at depth 2, so its bindings come after every depth-1 callee local (here `resetStep`'s vectors). Retail's `TNormalLift::control` had them before: `TGraphNode& node = unk138->getGraph()->getGraphNode(unk138->getCurGraphIndex());` (the spelling the rest of the file uses) closed it.
  When retail has a few more words above an inline's aggregates and as many fewer below, look for a fabricated accessor that pushes a sibling's objects one level down.
- **Callee-local reversal is a diagnostic.** In `TBossHanachan::execWalk` retail has an unnamed `TVec3(...)` *above* the named `target` it is built from, i.e. both are locals of one expansion (created last-declared first), and `isCanWalk`'s pair sits lowest, i.e. one level deeper. Open: wrapping the isCanWalk site stops it inlining.
- `TBossHanachan::execSlip` (open): moving a hand-written Y rotation into a static inline lands `goal` and the TPathNode; retail then materialises the `isZero()` bool (`mfcr; extrwi.`) with no named slot, and no unnamed spelling found does both.
- `MarioWaistCtrl` (open): retail uses one `Mtx` (0x88, the top object) at all three sites; a function-scope `Mtx` lands it but the frame drops 0xe0 -> 0x80, so 0x60 of dead objects created after it are missing (MarioHeadCtrl is 0x60 short too).
## Refinements (unit agent c-d11, 2026-09-27)

- **A named local below compiler objects is a callee local.** Rule 8a puts every named local of the function on top, so when retail's copy of one of our named locals sits under inline temporaries it was a local of an inlined callee in retail.
  `TGCConsole2::perform`: retail stacks the 4720 and 4663 helper objects, then 4739 over 4737, then `bounds`, then balloon/counter objects, the setScissor TRect and `graph` last; `bounds` and `graph` are callee locals there, and each reversed pair of adjacent statements is one callee's locals (rule 8b).
- **Refused inline calls still leave their bindings.** `movementCommon`'s three unit `update()`s are refused at depth 2 and `bl`'d, yet each non-simple `this` binding keeps a dead slot at the depth-2 point (the three words `movementOption` has above its JUTRect temp that retail lacks).
- **Moving a block into a TU-local inline reorders it with the temporaries.** `linGetSRT`'s nine named slices become depth-1 callee locals in source order, level with the pop and default-push temporaries, once each arm is `f(interp, const f32& v)`; a reference argument like `owner->m.x` is simple (no binding word), a by-value one binds and hoists the load above the slice's zero stores.
- **`std::sqrtf`'s `volatile float y` is the lone sqrt slot** (`TMario::readBillboard`: retail keeps 15 more words created after it).
## Refinements (unit agent c-d13, 2026-09-27)

- **A dead named scalar's slot follows its declaring scope.** A retail "4-byte hole" between two named aggregates was a function-scope `f32 cos;` declared between them and assigned in a later loop (`getRandomButDirLimited`, `getEscapeDirLimited` closed).
  The same scalar declared in an inner block (an else arm) sits below every aggregate of the enclosing loop body; declaring it uninitialised at the top of that body moves it above them.
- **A named reference to an inlined accessor's result** (`const TGraphNode& node = getGraphNode(i); node.getPoint(&p);`) takes a register and removes the accessor's index binding and result object from the low region (the other half of the `getEscapeDirLimited` close).
- **A helper holding a statement and its local moves both one depth down.** `TNerveLimitKoopaJrWait` (closed): retail had moveWait's `TDirectionCalc target(dir)` and its by-value ctor copy below canRun's depth-2 objects; `static inline void LimitKoopaJrTurnBody(TLimitKoopaJr*, const TVec3&)` holding the declaration and the turn statement creates them at depth 2, after canRun's.
  Tell: a callee local of expansion A sits below the depth-2 objects of an earlier expansion B.
- **Unrolled constant loops leave dead objects.** `for (int i = 0; i < 4; i++) a[i] = i * 0.25f;` unrolls to the same folded stores and keeps `i` plus two IRO temps dead (+12 per loop).
  It is not the `MSRandVol` ctor's answer (retail +8, and `MSRandPlay`'s loop-free ctor has the same +8); the debugger shows zero dead objects in our MSoundSE ctors and `__sinit` (retail +2/+2/+10 words), so those gaps are not inline bindings.
- **spcinterp exec{add,sub,mul,div} (open):** the word above the else arm's temp is setDataFloat's argument binding; retail has none there and one more below the temp.
  Direct field writes drop it but schedule the mType store early; a named `f32` keeps the schedule but lands above the pop temps.
  Moving the float arm into a helper puts `getDataFloat` at depth 2, where it is called out of line.
## Refinements (unit agent c-d14, 2026-09-27)

- **A callee reference declared before a parse-time temporary adds one word below it.** `setDeadBathtubKillerAnm`'s `TVec3(0, 0, 0)` temporary sat 4 high in both nerves that inline it; `JGeometry::TVec3<f32>& accel = mAcceleration; accel.set(...)` declared before the temporary is created after it (rule 8b) and dies, closing TNerveBathtubKillerBreak/Explosion (a `TQuat4&` for the quat line does the same).
- **Fix the named block before the low region.** `TBathtub::allowsTumble` was 5 low words short with `pos` and `angle` swapped; declaring `f32 angle;` first made the gap a uniform shift, and lever-search then closed it with the real accessors `getBathtubData()` and `gpMarioOriginal->getFludd()` (+8 each).
- **Naming a nerve argument moves its word up.** `TTabePuku::init`'s SMS_Eular2Quat return slot was 4 high: `TNerveBase<TLiveActor>* nerve = &...::theNerve(); mSpine->initWith(nerve);` closed it.
- **A discarded call is an unused local's remnant.** TNerveKoopaTumble's bare `getMActor()->getFrameCtrl(ANM_TYPE_BCK);` was `f32 frame = ...->getFrame();` never read: the read is dropped, the call and 0x10 of dead objects stay (0x68 -> 0x78). Not kept: an unused named local breaks the no-dead-locals rule, so the function stays open with this as the known cause. The other Koopa frames (Wait, Flame, Turn, perform 18 low words, getTargetDir 10) are inert to search<T>, an unnamed matan, and a `Mtx&`.
- `TBathtub::quake` (open): raw `getInitialPosition()` (no binder) lands SMS_ThrowMario's temporary at 0x74; retail then has 12 bytes more *above* it with the frame bound fixed, the size of a by-value `pos - init` return temporary (the header class). Named koopa, a named `up` vector and an uninitialised top declaration are inert.
- `TBGTentacle` ctor (open): the tree's usual `TIdxGroupObj* group = search<TIdxGroupObj>(...); group->getChildren().push_back(this);` in both inlined hit-actor constructors makes every slot uniformly 8 low (frame 0x198 vs 0x1a0), so retail has two more words below the push_back iterator chain; `group->push_back`, `.value`/`mParams` params and `mHitFlags &=` are worse.
  Spelling the `getInstance()->getRootNameRef()->search()` chain in the constructor moves the TNameRefGen binding up to the getChildren binding's level.
- `TBGAttackHit::perform` (open): retail has one more word before each throwMario expansion's copy and 15 below; an attack-mode helper stops throwMario inlining (49%).

## Refinements (unit agent c-e2, 2026-09-27)

- **Pairing slots across the whole body.** When instructions match, pair every `(r1)`/`addi rX, r1` operand of ours with retail's on the same row and tag ours with the debugger's object names: sorted by our offset, `retail - ours` is constant inside a run and each step between consecutive objects is exactly the extra bytes retail created between them (a negative step is an object retail created elsewhere). `setupObjects` read that way: no dead `cam` word, one more word beside `measurementGroup`, the `GXTexObj` copy below the PerformLists streams (a temporary, not a named local), and 0xf8 below every object we have.
- **Unnaming a local removes its slot but can add an IRO temp.** Dropping `setupObjects`' dead `cam` (a store base) freed its word and shifted every object below it by 4, frame unchanged (rule 8).
- **An accessor instead of a raw member can remove a result copy.** `TWaterGun::init`'s `mMario->mModel->unk8->getAnmMtx(i)` made a copy (`add r5; mr r24, r5`); `->getModel()->getAnmMtx(i)`, the spelling the rest of the body uses, computes straight into the local and is +0x10 frame toward retail.
- **A `new` result passed straight to a call is copied with `addi rD, rS, 0`; through a named local it is `mr`** (`setModel(new J3DModel(...), 0)` in `TWaterGun::init`).
## Refinements (unit agent c-d15, 2026-09-27)

- **A qualified base call is auto-inlined.** `void TGorogoro::flagJump() { TRollEnemy::flagJump(); }` expands the earlier-defined base body (`-inline auto`), so its named locals become depth-1 callee locals below the by-value return temporary; spelled out in the derived body they sat on top (closed, retail's layout exactly).
  Tell: a derived override instruction-identical to its base, with the base's named objects below the parse-time temporaries.
- **A temporary argument is the object just below earlier temporaries.** `IgaigaPushGoal(this, TPathNode(pos))` with a `const TPathNode&` parameter creates the node as a parse-time temporary, after the two `calcVelocityToJumpToY` return temporaries and above every inline object (`TGorogoro::generateByGateKeeper` closed, with `MsMtxSetRotY` replacing a hand-written rotation's three dead named scalars).
  A named local in the helper is a depth-1 callee local (below earlier statements' depth-1 objects); a by-value parameter and `const T& x = T(...)` both land elsewhere (the latter ranks with the named locals, above the temporaries).
- `TBWPicket::moveRequest` (closed): the c-d6 depth move again: a direct-return fork over the existing latest-nerve binder puts the binder's locals below pullTail's copy; raw `mLeash`/`mHitPoints` then give retail's low region.
- **Which depth a callee's dead word has.** In `TBossEel::setBckAnm`, a dead named local of the callee (`f32 ratio = 1.0f;`, one initialised from a nested inline's result, or a named single-use value) is created with the depth-1 expansion; `ctrl->setRate(rate)` with named `ctrl`/`rate` leaves both named locals in registers and kills setRate's two parameter copies at depth 2.
  Open (bosseel): one dead word per setBckAnm closes OutWait (and Appear with two binders), but FirstSpin/SecondSpin want it created after ExecSpinNerve_Sub's `spinSpeed`, i.e. at depth 2, and no spelling of the body found does that; `docs/progress/lever-search/bosseel_setbckanm_248.patch` is the base.
## Refinements (structural agent c-s2, 2026-09-27)

- **An inlined callee's temporaries are laid out in reverse source order.** With `-inline deferred` the temporaries in an inline body are created last-first, like its named locals (rule 8b): for `setPanePosition(t, JUTPoint(..), JUTPoint(..), JUTPoint(..))` the first argument's temporary sits lowest, and of several calls in one body the last call's temporaries sit highest.
  At depth 0 the order is source order (first argument highest).
  So a retail site whose argument temporaries run the other way from ours was written inside an inline callee.
- `TGCConsole2::startCameraDemo` showed that tell for its nine `JUTPoint(0, 0)`: they belong to the UNUSED `resetMoveTank()` (map 0xe0), which is `unk48 = 0;` plus three `setPanePosition(1, 0-points)`/`update()` pairs (exactly 56 instructions) and is inlined there.
  With that inline every JUTPoint slot is in retail's order (uniformly 0x2c low, the rest is low region); instructions unchanged.
- Tree scan for that tell (`addi rX, r1, N` offsets ranked in retail against ours, same instruction count): `TSelectMenu::perform` has 1208 of 1225 object pairs reversed (all twelve `setPanePosition` sites: the whole block is callee code in retail).
  Smaller signals: `TLensFlare::perform` 31/55, `wireHanging` 17/66, `TBossHanachan::perform` 14/55, `TPauseMenu2::perform` 14/28, `wireWait`/`wireSWait` 13/45. GCConsole2 has none left.
- **The GCConsole2 low-region deficit is not a header spelling.** Priced on every unit that includes the header (compile-only, against retail objects):
  JUTPoint `set(const int&, const int&)` 0 up/16 down, `JUTPoint(const int&, const int&)` 0/18, both 0/21, ctor assigning directly or by init list 0/1 (`processAppearLife`'s converting `JUTPoint(unk1C8, unk1CA)` needs today's `{ set(x, y); }`);
  JUTRect `getWidth`/`getHeight` with a named local 0/37; statement-bodied `TBoundPane::getPane` 0/22; non-const `getPane`, `isVisible() != 0`, raw `J2DPane::getHeight` inert.
  The constant-argument JUTPoint sites that are exact today (`startAppearTank`, `processDownCoin`, `processAppearTank`, TTalk2D2's board windows) pin the JUTPoint header: any spelling that adds an object per temporary breaks them.
- `processMoveNozzle` (no inlinable call besides the JUTPoint ctor) needs 5-12 bytes created before its nine temporaries and 16 words after them.
  A named per-case `int y = -30;` passed to `JUTPoint(0, y)` gives exactly the upper part (three dead named words); named `bool`/switch locals are inert (they take registers), a statement-bodied pane binder is +2 words per site, below.
  The 16 low words are unexplained: our function has no IRO temp in the stack at all, so the retail low region is objects created after inlining that our spelling never makes.

## The MarDirector deficit is not a name-search level (structural agent c-s3, 2026-09-27)

- **Every binding level on the search chain loses tree-wide.** Census against 11920 exact, one level at a time (`JDRNameRefGen.hpp`/`JDRNameRef.hpp` overlays, full build):
  a named local in `getInstance()` 11773 (19 up / 167 down), in `getRootNameRef()` 11803 (15/128), `TNameRef::search` binding its result 11798 (15/133) or its key 11865 (2/64), `search<T>` binding the call result or the cast result 11812 (15/125), binding the root or root-then-result 11811 (14/128).
  The direct-return chain in the tree is right for the great majority of the ~250 sites; do not re-test these.
- **The levels cannot reach retail's frame anyway.** A 48-variant grid (the four levels crossed, `-opt`/`-inline`/compiler-version flags included separately) moves `TMarDirector::currentStateFinalize` (four search sites) from 0x90 to at most 0x110; retail is 0x120.
- **The deficit is in functions with no search at all, and it sits at the bottom.** `direct()` (0x48 short, no search, no flag reads) has exactly three stack objects in ours (`local_40`, the 0x100-byte `TGraphics`, one dead `unk18[i]` binding under it); pairing its slots with retail's shifts every one of them, the lowest included, by exactly 0x48, so retail has 18 more words created *after* the `TGraphics` (inline bindings or IRO temps), not a header-level word per search.
  `changeState` and `updateGameMode` pair the same way: 0x50 below their lowest object, plus words between; `decideNextScenario` (only `smInstance->getBool` calls and the inlined shadow-Mario loop) has no dead object in ours and 7 words in retail.
- Reading: retail's director code reached other classes' fields and non-simple receivers through inline accessors whose bindings and result objects die (rule 4), where ours reads fields raw (`gpMSound->unkA8`, `gpSilhouetteManager->unk48`, `gpCamera->unk2C8`, pad button fields).
  `TSelectDir::direct` and `TMovieDirector::direct` have the same bottom-region deficit class. The fix is per-site real accessors, not a shared header; none was committed here.

## Accessor pricing on the director deficits (unit agent c-d17, 2026-09-27)

- **Real accessors supply at most 0x38 of `currentStateFinalize`'s 0x90** (no stack access, so the frame is the whole score).
  `unk18[0]->offFlag(f)` for the five raw `mFlags &= ~f` is +0x18, `getGamePad()->offFlag(f)` +0x28; `mCurrArea.getStage()`/`getScenario()` +8 each; best combination 0xc8 against 0x120.
  Inert: `search2`, the spelled-out `getInstance()->getRootNameRef()->search`, named search receivers, `getConsole()`, `SMSGetCamera()`, `!unk124`.
- `TMarDirector::direct`: only `gpCamera->getUnk2C8()` moves the frame (+8); `isUnk48Positive()`, `SMSGetMSound()`, `getGamePad(i)` and unnaming `pad` are inert or break code.
- `TSelectDir::direct` pairs as: 1-2 words above `res` (created before it), one word between `res` and the `setColor` by-value copy, 29 words below every object; `isFullyFadedOut()` for the last compare supplies one of the low words, and no colour spelling adds the middle one without reordering.
- The class covers every director, not only MarDirector: `TGCLogoDir::direct` (0x30: one word between its JUTRect and TColor temporaries, 14 below), `direct_nlogo` 0xa8, `TMenuDirector::direct` 0xb0, and the three `setup`s (0x18, the JDRDStageGroup.hpp TODO). Look for one shared construct, not per-site accessors.
- `decideNextScenario` is instruction-exact with `int scenario = 0;` and an else-if chain ending `else scenario = 0;` (retail hoists the first `li r3, 0`); its 5-7 dead words are still unexplained.
## Refinements (unit agent c-g2, 2026-09-27)

- **Reversed-pair targets closed structurally (order only).** `TSelectMenu::perform` is `if (flags & 1) update(); if (flags & 8) { if (bad state) return; draw(gfx); }`, both inline members: all 6670 slot pairs in retail order.
  The early `return` stays at depth 0; inside the inline it becomes `blt` where retail has `bge; b`.
  `TPauseMenu2::perform`: the move switch is an inline `move()` and `draw()` *is* the draw switch with an orthograph per case (the pair per site is reversed); `TLensFlare::perform`: move/calcAnim/entry inline members (107 -> 42 instruction diffs).
- **Helpers reached at depth 2 must be written out.** With the switch one level down, `inline` helpers it called (selectPrev/selectNext/animateArrows, the letterbox fade) are refused; retail's calls one level down confirm their bodies sit directly in the switch callee.
  An extra level also pushes a fabricated depth ladder one step down: drop one wrapper (lensflare's `CalcLensNearNinePos`, `LensDirTo`) to keep the out-of-line call set.
- **A callee local passed into a deeper inline gets a binding.** `s32 time` (an `update()` local) fed to TExPane's inline setters was re-converted from the float (two `fctiwz` stores) and rescheduled; `int time` keeps one conversion and retail's schedule.
- **Callee-saved order follows adjusted cost, then vreg number; vregs are numbered in reverse object creation.** An IRO-split web of a reused variable gets a fresh object created last (lowest vreg), so sharing `u32 var1, var2` across the two letterbox blocks gave the second block retail's r21/r22 order; the first block, which keeps the original object, stays swapped.
  Reusing one variable for a load and its update (`u16 a = pane->getAlpha(); a += k;`) put the value in the load's callee-saved register (SelectMenu APPEAR case).

## Refinements (unit agent c-d18, 2026-09-27)

- **An UNUSED member holding a nerve's tail moves a by-value return temporary to depth-1 rank.** `TNerveKukkuRecoverGraph` spelled `updateRotation(); mLinearVelocity = calcMomentum(...)` itself, so calcMomentum's return temporary was a parse-time object (sat 8 high); calling the UNUSED `doRecoverToCurPathNode()`, whose body is exactly those two statements, makes it a callee temporary created after the depth-1 habataki read (closed with `getSaveParams()->getHabatakiTimer()` at the site and `mMarchSpeed.get()` in the helper).
  Tell: a by-value call result 1-2 words too high with the parse-time temporaries above it exact; look for an UNUSED stub whose body is the site's statements.
- `TMapWire::drawUpper` (open): every `getStartPoint()`/`getEndPoint()` passed to the TU's `addPoint`/`subPoint` helpers is one dead word; all four accessor sites give retail's 0x58 but move the `r31` restore after `mtlr`.
## Refinements (unit agent c-d19, 2026-09-27)

- **A reversed callee-saved order means the locals were argument bindings one level down.** `TNerveNPCGraphWait` (closed): retail ranks the `unk22C` timer (the inline's non-simple `this` binding) above minFrame/maxFrame, ours below, because callee locals are created before the argument bindings (8c).
  Passing the two reads as arguments of a further level (`resetRandom(min.value, max.get())`) creates the `this` binding first and the reads in argument order, and keeps both loads ahead of the `unk0 = 0` store as retail schedules them; the `.value`/`.get()` mix supplies the frame. So the "this-versus-pool-base swap" is not always closed: look for a deeper level with parameters.
- **A fabricated `getCurrent()` hides a CSE.** `TRailFence::goOnRail`: `getGraph()->getGraphNode(tracer->mCurrIdx)` lets the inlined `moveToShortestNext` reuse the loaded index as retail does; the fabricated accessor and `getCurGraphIndex()` both reload it. A named single-use `f32 dist` then gave retail's 4-byte word between `toNode` and the first by-value temporary (frame still 0x20 short, all below).
- **An accessor receiver fixes a float schedule.** `TCoasterKiller::perform`: `getPosition().distance(...)` instead of `mPosition.distance(...)` gives retail's load/subtract order and the missing 8 bytes; left open is that its `this + 0x10` is then CSE'd with the `&mPosition` argument (`lfsu`).
- `TFenceWaterH::control` (open): each inlined `setEular(f32...)` has 15 dead words (the six sin/cos locals and nine setXDir/YDir/ZDir argument bindings), the out-of-line MapObjLib copy has the same set and is exact, and retail's fence needs six fewer per expansion; `TFenceWater::initMapObj`'s `addi r31, r3, 0` is made by the peephole pass (GC/1.1 dumps `mr` up to prologue insertion).
## Refinements (unit agent c-d16, 2026-09-27)

- **Bind the end of an accessor chain, not its head.** `{ TMarioGamePad* pad = gpMarDirector->getGamePad(); return pad; }` creates the inner accessor's `this` binding one level down, below a later depth-1 temporary; the head binder chained in the caller (`EventWatcherGetMarDirector()->getGamePad()`) keeps both its words at depth 1.
  Tell: retail has one word fewer between two parse-time or depth-1 objects and one more below the later one, frame unchanged.
  Closed evInvalidatePad (pad binder) and evAppear8RedCoinsAndTimer (console binder at two sites: the nil-push slice 8 lower).
- **Inside a helper, which value is named decides the depth of its word.** `BosspakkunBckFrame`'s `{ J3DFrameCtrl* ctrl = a->getFrameCtrl(0); return ctrl->getFrame(); }` puts the word below resetWaterMark's `mouth` block, where `{ f32 frame = a->getFrameCtrl(0)->getFrame(); return frame; }` put it above (TNerveBPTumbleOut closed).
- **A shared inline plus one sibling.** `changeEMWalkGraph`'s raw `unk124->reset()` (no getTracer binding) closed emWaiting and left emWalkAround 8 short; `getPosition()` in emWalkAround's pollute call (emJumping's spelling) closed it. Sweep the shared body and the siblings' own accessor sites together.
- Inert here: spcinterp.hpp `push()`, `push(int)` and `TSpcStack::push` spellings (no EventWatcher/NpcEvent slot moves); sound-call helpers for evStartSE/evStartEventSE/evStartMontemanBGM.
- evInsertTimer (open): retail has no dead named `p2` above the pops (the pop temp is the top object); a switch changes code, a TU helper taking `p2` fixes the pops but pushes the director words below the push slice.
- bossgesso changeBck (open): per expansion ours costs about 10 words in every nerve, while retail's Roll (8 sites, 0xa8) allows about 4 and Tug/Eye/Pollute/PolDrop match at 10, so no single changeBck/joinAnm spelling fits; `setEyeDamageBtp` calls are +0x10 per site.
- MSStage::init (open): our frame has no dead objects at all, retail 24 words; inlining MSStageDistFade's ctor (dropping its `(void)0` guard) adds none.
## Refinements (unit agent c-d20, 2026-09-27)

- **A fabricated `void f(axis, &result.x)` helper pushes a nested inline one depth down.** In `MsGetRotFromZaxis` MsSqrtf's volatile sat under normalize()'s depth-2 setLength binding; spelling the pitch in the body makes MsSqrtf depth 1, so its volatile is created before every depth-2 object (directly under `axis`, as in retail) and the load/FPR swap disappears.
- **A converting argument binding is a 3-word carrier at depth 2.** The remaining 12 bytes below the volatile were `static inline f32 MsShortAngleToDegree(f32 angle) { angle = k * angle; return angle; }` fed matan's `s16` result at one site in the yaw helper (closed); at two or three sites it overshoots. `return k * angle;` or `angle *= k` give the same frame with a swapped FPR or operand order.
- `TMapStaticObj::init`: calling `setUpUnk8TRS` directly (no `setUpCollision` level) puts its Mtx above the push_back iterator pool as in retail (all slots uniformly 4 high), but setMtx then inlines, so the weak-plus-`bl` class still blocks it.
- `genEnemyFromPollution`: declaring `maxR` before `minR` makes MsRandF's result reuse `minR`'s register as retail does, but ranks `maxR` into f31; the residue is web coalescing, not declaration order.
## Refinements (unit agent c-d22, 2026-09-27)

- **Price combinations, not ladders.** `TMario::incHP` (closed) needed 0x20 of low region; the `SMSGetMSound()` binder's rungs (+0x10/+0x18/+0x28/+0x30) and `getHealth()` at the clamp (+8) each miss it, but together they land it exactly (non-additive: binder at both sites alone is +0x18, with `getHealth()` +0x20).
  A 72-cell grid (six receiver spellings per sound site x accessor on/off) in one `score-variant.sh` call found seven exact cells; pick the one with a single helper.
- `TNerveYumboDancing` (open): the one object between `toMario` and isFindOutMario's `pos` is the SMS_GetMarioPos() result (the copy constructor's argument binding under `*gpMarioPos`); retail has none there and two more words after `pos`.

## Refinements (unit agent c-d21, 2026-09-27)

- **An explicit `TVec3<f32>(m)` initialiser is a 12-byte parse-time temporary right under the named block.** `TNerveKazekunAttack` had retail 12 bytes between `vel` (top) and the first doAttack's callee locals, nothing else moved: `TVec3<f32> vel = TVec3<f32>(kazekun->mVelocity);` lands it and the frame (0x1e8 -> 0x1f0) at unchanged instructions. The TU spells velocity copies that way elsewhere (doAttackPose, MapObjBall).
- **A named local one word too high with every temporary one word too low is a callee local.** `TMapObjBall::boundByActor` closed by moving `minSpeed` and the statement it guards into a `this`-taking helper (c-d13's lever): the word leaves the named block and lands below the unnamed `TVec3(mVelocity)` copies. A helper returning the condition (`return a || b;`) adds the bool materialisation (+4 instructions); hold the whole statement instead.
- **Callee-saved GPR choice, read from `regalloc-gpr-pass-1-assigned.txt`.** Nodes are coloured in the file's order (the highest-degree webs first, then descending vreg number), and each takes the lowest callee-saved register already in use that no coloured neighbour holds, else a new one below the lowest in use. So a one-register rotation (`waitForStop`, `TCardSave::perform`, execMovement_'s r24/r25) means retail coloured some web earlier or later, not that a declaration moved; `perform`'s scissor reference (@5011, the highest vreg) is coloured before `graphics`, which retail colours first.
## Refinements (unit agent c-d24, 2026-09-27)

- **A fabricated load-order local can hide a header float overload.** `TDirectionCalc::makeDirection`'s `TVec3* pDir = &dir; f32 z = pDir->z; f32 x = pDir->x;` was retail's `mDirection = atan2(dir.x, dir.z);`, the C++ `atan2(float, float)` wrapper in math.h: its argument bindings load z first and its result object is the one dead word (out of line exact).
  In an inlining caller that word is created one depth below the by-value copy instead of above it, which landed `TKoopaJr::perform`'s copy slot and frame (0x138 -> 0x150, with the inlined `TDirectionCalc(TVec3)` ctor, which propagates to a single copy, and a named `angle`).
- **A float compare that differs only in f0/f1 is a reversed relation.** `TKumokun::checkOnMovingRoof`'s `mHeadHeight < d` was retail's `d < mHeadHeight` (branch sense read from `fcmpo f1(d), f0(head); bge else`): same bytes except the two registers, and the old spelling was the opposite test.
- `TKoopaJr::perform` (open): toMario 4 high and the copy exact; one depth-2 object between them is created after the copy in retail. Inert: search2 at either search, a named `*gpMarioPos` reference, `getSpine()` at one compare (adds below instead of moving).
## Refinements (unit agent c-d23, 2026-09-27)

- **A named reference to a pointer member gives `lwzu` plus a reload through the kept address.** `TModelWaterManager::move`: `const TLiveActor* const& actor = plane->mActor;` for the null test and the virtual call; the raw `plane->mActor` twice reloads the address-taken `plane` after the mStaticHitActor stores, and a helper taking the plane by value keeps the pointer but loses the `lwzu`.
- **A reference to a class with a conversion operator makes each use `addi rD, rS, 0`.** `TBathWaterMeshRenderer::render`: `TPosition3f& view = cam->unk1EC;` passed to setViewMtx/PSMTXConcat; an `MtxPtr` local gives `mr` at the inline setter.
- **A loop in a TU-local inline starts `li rA, 0; mr rB, rA`** (render's drop billboards; written in the caller, two `li`), but its named `f32` locals then number upwards from f20 (callee locals reversed).
- **A TU-local class's in-class member for a repeated statement group** (`TDrop::move()`: the add and four zero() calls) turns the following `extend()`'s `this` from `mr` into `addi rD, rS, 0` (BathWaterManager perform, initializeIfYet_).
- **A matched out-of-line member can be the inlined source of a caller block.** `TGuide::load`'s archive mount is `setup(gpMarDirector->unkD8)`, which loads the member straight into the saved register.
- `TExPane::setPaneAlpha` (shared ExPane.hpp, not committed): `a = a > 255 ? s16(255) : a;` makes Guide's weak out-of-line copy byte-exact but breaks the inlined sites `TCardLoad::titleDraw` and `TConsoleStr::startOpenWipe`.
- `calcPosAndAt_`: spelling both `CLBAbs<int>` calls as in-place ternaries reproduces retail's per-arm `extsh`, but reranks `this` out of r31 (net worse).
## Refinements (unit agent c-e3, 2026-09-27)

- **A ternary is cheaper to inline than the same if/else.** `TBossGesso::showMessage` with `idx`/`flag` as ternaries inlined at depth 2 (checkTakeMsg inside perform), where retail `bl`s it; as if/else assignments plus a named `console` receiver it inlines at depth 1 (perform's own showMessage(3), TBGBeakHit::receiveMessage) and is called at depth 2, with the out-of-line body still exact (perform 98.3 -> 99.7).
  Tell: a small method called at depth 2 and expanded at depth 1; respell its conditional values as statements.
- **A by-value inline result passed straight to a `const T&` parameter is built in place.** `T v = f(); g(v);` copies the result; `g(f())` has none. In bosswanwan's GraphWander the direct argument alone inlined `TVec3::set`; with the UNUSED `rollNextGraphNode` level called, `set` stayed out of line and JMASSin inline as in retail (96.8 -> 99.1). A pasted UNUSED body is worth re-calling after the helper's own spelling is fixed.
- **`f32 p = a / k; p *= 0.75;` puts the variable first in the `fmul` against a double literal** (then `frsp`), where `(f32)((f64)(a / k) * 0.75)` puts the constant first.
- **An unnamed repeated accessor can fix volatile colouring.** TBWBinder::bind: `if (tracer->getGraph())` and `tracer->getGraph()->unk0` later (CSE'd, no reload) gave retail's registers where a named `graph` did not; in the other block a named `graph` with `graph->getGraphNode(i)` gave retail's load order where a named `nodes` array did not.
## Refinements (unit agent c-e4, 2026-09-27)

- **A saved GPR that retail never initialises is an uninitialised local.** `emReplayJumpToNearestNode`'s `int selected;` (assigned only on the loop's break) lives from entry, so it takes a callee-saved register (retail r24) with no `li`; `int selected = 0;` put it in r0 and cost one saved register (r20-r31 vs r21-r31).
- **Index a two-level array directly to keep base and row apart.** `replayLinks[nodeIndex][i]` instead of a `TReplayLink* links` row local gave retail's separate saved registers for the base and the hoisted row (98.1 -> 99.7, instruction-exact with named `dx`/`dz` for `matan`).
- Commutative `fadds` operand order is not a source knob: `y + 10.0f` / `10.0f + y` and `a * k` / `k * a` compile identically (drawHPMeter); the order follows vreg numbering.
- `TChorobei::checkHit`: declaring `TCannon* cannon;` at the top gives retail's r26 (named-local vregs follow declaration order), but the theNerve instance temp stays our lowest; retail ranks it second.
## Refinements (unit agent c-g5, 2026-09-27)

- **An UNUSED member whose rebuilt body hits the map size is the inlined tail.** `TBathtubKiller::moveParabolic` (UNUSED 0x78) is exactly 0x78 as `mAcceleration.set(0, -getGravityY(), 0); makeQuat(mVelocity, 1.0f, 0.1f);`, the Wander nerve's last two statements; calling it moves makeQuat's by-value copy from the parse-time top to depth-1 rank (like c-d18's `doRecoverToCurPathNode`).
- **Calling the deeper function directly flips a pair.** `moveStraight` calling `makeQuat` itself (not through `makeVelocityQuat`) puts the argument copy above `dir`, as in retail's Straight/ChaseStraight nerves.
- **Two named single-use results above an inline's vectors are its own last-declared locals.** `isAttackable` closed the Chase nerve with `f32 marioDist = ...; f32 myDist = ...;` (Mario's distance evaluated first, as retail's registers show), plus `SMS_GetMarioPos()` and a raw `unk1CC->mPosition` to drop two low bindings; in Wander a named `params` in `canChase` supplied the depth-1 word.
- **Reuse the TU's own helpers before inventing one.** `drawMantaShadow` spelled out the ortho and identity blocks that `setupEfbAlpha` already calls as static inlines; calling them puts texObj, the colour copy and proj in retail's order (the 0x78 deficit is then all created after proj).
- **Not every reversed pair is a callee.** In `TBossHanachan::perform` the offsetX/offsetZ and ground/direction pairs were depth-0 declaration order (`f32 offsetZ, offsetX;`, `ground` declared at the top of the probe loop).
- `TKumokun::bind` (open): retail's mVelocity copy and `local_f8` are parse-time temporaries under the named block (`+= TVec3<f32>(mVelocity)`, `+= a - b`), which lands the frame and every upper slot but loses the copy out of the difference under the current `operator-` (the open class).
