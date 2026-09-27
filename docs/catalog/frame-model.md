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
