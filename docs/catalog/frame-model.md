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
