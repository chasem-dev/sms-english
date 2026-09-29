#ifndef JDR_DSTAGE_GROUP_HPP
#define JDR_DSTAGE_GROUP_HPP

#include <JSystem/JDrama/JDRViewObjPtrList.hpp>
#include <JSystem/JDrama/JDRFrmGXSet.hpp>

namespace JDrama {

// The `TFlagT<u16>` default argument follows the JDrama creatable-object
// convention (TDStageDisp, TEfbCtrlDisp, TEfbCtrlTex all take
// `(const char* = "<...>", TFlagT<u16> = 0)`); nothing in the inlined body
// uses it. Its temporary is the dead top word of every `new TDStageGroup`
// site (MenuDir/MovieDirector setup 0x3c, GCLogoDir setup's parse-time block,
// SelectDir rsetup) and its per-field IRO copy is one of the bottom words
// (research c-r28: +8 frame on all four sites, nothing else moves).
// TODO: the setups are still two dead words short above (retail MenuDir
// setup: flag temp 0x3c, this 0x38, X 0x34, list this 0x30, Y 0x2c, list
// TViewObj 0x28, allocator 0x24, FrmGXSet TViewObj 0x20) and 8 bytes short
// below the FrmGXSet TViewObj. X is created after this ctor's `this`
// binding at depth 1 (GCLogoDir: between it and the next `new`'s binding),
// and is absent in SelectDir, whose non-simple `unk1C` display binding is
// live in r28: X is the `display` binding, which our simple `param_1`
// argument never creates. Y is a depth-2 word (after the list's `this`,
// before the depth-3 TViewObj bindings): the FrmGXSet's own `display`
// binding fits. Measured forcing spellings (synthetic, c-r28):
// `TFrmGXSet(TDisplay* const&)` gives X only, `TDStageGroup(TDisplay*
// const&)` gives Y only; const/void*/cast/body-assignment spellings of the
// parameter are inert. See docs/catalog/frame-gaps.md, research c-r28.
class TDStageGroup : public TViewObjPtrListT<TViewObj> {
public:
	TDStageGroup(TDisplay* display, const char* name = "<TDStageGroup>",
	             TFlagT<u16> flag = 0)
	    : TViewObjPtrListT<TViewObj>(name)
	    , unk20(display)
	{
	}

	virtual void perform(u32 cue, TGraphics* graphics);

public:
	/* 0x20 */ TFrmGXSet unk20;
};

} // namespace JDrama

#endif
