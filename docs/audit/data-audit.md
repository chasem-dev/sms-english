# Data audit: CameraChange, CardManager, JPAEmitter, ShadowUtil (GMSE01)

Result: no VALUE differences in any of the four units.
Every data symbol the retail code uses has the same bytes in our build.
Relocated words were compared by what they point to.
Jump tables were compared by their case-to-target pattern, which matched in all 6 tables.
Everything left over is LAYOUT.

Method: parsed both ELF files per symbol, masked relocated words and resolved them to their targets, diffed the value multisets per section, checked which symbols .text references, and cross-checked the section layouts in marioUS.MAP.
Scripts and dumps are in this directory: dump.py, refs.py, jt.py, *.obj.txt, *.src.txt.

## Camera/CameraChange — LAYOUT only
- .data 0x218 vs 0x1f0: ours has three extra unreferenced header constants: @165 and @145 (1.0f x3) and @136 ({0,2,1,3}).
  The map lists retail's copies, @1431, @1411 and @1210, as UNUSED (stripped), along with MtxCalcTypeName.
  Jump tables @3715/@2095 (51 entries) and @3891/@2273 (73 entries) have the same shape.
- .sdata: ours has an extra @137 (02000201), an unreferenced header constant.
- .sbss 0xc3: the 15 JALList<...>::smList and __init__smList objects are weak template statics.
  The map shows CameraChange's copies as UNREFERENCED DUPLICATEs, and AnimalBase's copy is kept.
- .bss 0xb8 vs 0xb4: retail's extra gap_08_803EFD64_bss is 4 bytes of alignment padding (the map gives the section as 0xb4).
  The 15 @3184..@3198 12-byte objects line up.
- .sdata2: identical. Retail also had SMS_NO_MEMORY_MESSAGE here, but the map lists it as UNUSED.

## System/CardManager — LAYOUT only
- .rodata 0xa8 vs 0xa5: trailing alignment only (the map gives 0xa5).
  CardFileName and all 6 strings are identical.
- .data 0x88 vs 0x84: retail gap_07_803DF9F0_data (0x10) is the space of the weak duplicate __vt__10JSUIosBase (0xc, UNREFERENCED DUPLICATE; enemyMario.cpp's copy is kept) plus 4 bytes of padding.
  Ours emits the same weak vtable {0,0,__dt__10JSUIosBaseFv}.
  titles[5] and comments[5] point to the same strings.
  The jump table @1818/@517 (10 entries) has the same shape.
- .data linkage: `titles` and `comments` are global in ours and local in retail.
  To match, add `static` at src/System/CardManager.cpp:14 and :19.
  This changes linkage only, not values.
- .sdata: ours has an extra unreferenced @137.
- .sbss: sDetach matches; retail's gap_10_8040E1DC_sbss is padding.

## JSystem/JParticle/JPAEmitter — LAYOUT only
- .data 0x54 vs 0x48: the extra 0xc is the weak __vt__10JSUIosBase.
  The map shows it as an UNREFERENCED DUPLICATE from JPAEmitter.cpp, and retail's in-map .data is also 0x54.
  Jump tables @2833/@1299 (7 entries) and @3104/@1553 (11 entries) have the same shape.
- .sdata2 0x40 vs 0x3c: all 12 constants are identical in order and value.
  Retail's gap_11_804173EC_sdata2 is padding to the next unit at 0x804173f0.
  Retail's UNUSED @1211 is stripped.
- .bss: JPAEmitterInfoObj (0x180) matches.

## MarioUtil/ShadowUtil — LAYOUT only
- .rodata 0x110 vs 0x10f: trailing alignment only.
  All 12 objects are identical, including the Shift-JIS string, calctablex/z, the index tables and the 4 .bmd paths.
- .data 0x174 vs 0x130: ours has these extras:
  - unreferenced header constants @165 and @136;
  - weak __vt__10J3DMtxCalc and __vt__Q26JDrama8TViewObj, which the map shows as UNREFERENCED DUPLICATEs from ShadowUtil.cpp.
  
  @1411/@145 (1.0f x3), vl/fl localstatics, __vt__19TMBindShadowManager, __vt__18J3DMtxCalcBasicAnm and the 6 local TSetupN/TCylinder vtables are identical.
  The local class names differ only in their `$NNN` numbering.
- .sdata 0x18 vs 0x10: mSquareShadowHeight=200.0f, mTreeScale=0.02f and mYScalePlus=20.0f match (src/MarioUtil/ShadowUtil.cpp:354-356).
  Ours has these extras, all unreferenced header statics: @137, dummyMactorStringValue1 and SMS_NO_MEMORY_MESSAGE.
  Retail's copy of SMS_NO_MEMORY_MESSAGE sits in .sdata2 and is UNUSED.
  Retail's gap_09 is padding.
- .sbss and .bss: same objects and sizes (gpBindShadowManager, mJoinDist, mTestSw, mDLSw, init guards, setupN/cylinder statics).
- .sdata2 0x90: all 36 constants are identical in order and value.
- For information, with no effect on behaviour: the map lists retail .data/.sdata that the linker stripped as dead code.
  These are mSquareShadowWidth__19TMBindShadowManager (4 bytes, value unrecoverable), pnmtxTable$2004 and pnmtxTable$2026 (6 bytes each, from the stripped TModelShadow::calc/draw) and __vt__12TModelShadow.
  Ours does not define them, and nothing live reads them.

## VALUE differences
None.
