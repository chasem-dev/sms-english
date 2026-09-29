#include <MSound/MAnmSound.hpp>
#include <MSound/MSHandle.hpp>
#include <math.h>

// rogue includes needed for matching sinit & bss
#include <MSound/MSSetSound.hpp>
#include <MSound/MSoundBGM.hpp>

MAnmSound::MAnmSound(MSound* sound) { mData = nullptr; }

void MAnmSound::initAnmSound(void* interface, u32 param_2, f32 frame)
{
	initActorAnimSound(interface, param_2, frame);
}

void MAnmSound::animeLoop(Vec* position, f32 frame, f32 speed, u32 ground_no,
                          u8 param_5)
{
	if (mData != nullptr)
		setAnimSoundVec(JAIBasic::getInterface(), position, frame, speed,
		                ground_no, param_5);
}

void MAnmSound::startAnimSound(void* interface, u32 id,
                               JAISoundHandle* out_handle, JAIActor* actor,
                               u8 camera_idx)
{
	if (MSGMSound->gateCheck(id))
		MSoundSESystem::MSoundSE::startSoundActorInner(id, out_handle, actor, 0,
		                                               camera_idx);
}

void MAnmSound::setSpeedModifySound(JAISound* sound,
                                    JAIAnimeFrameSoundData* frame_data,
                                    f32 speed)
{
	if (MSound::getSwitch(sound->getID(), MSSeSwBit_AnimeSpeed,
	                      MSSeSwBit_AnimeSpeedShift))
		JAIAnimeSound::setSpeedModifySound(sound, frame_data, speed);
}

// Fabricated name; fully inlined into MAnmSoundMario::startAnimSound, which is
// its only user, so the map has no symbol for it. It is `inline` so no local
// out-of-line copy is emitted either (the map's .text has none).
static inline u32 get_thing(u32 param_1)
{
	u32 uVar1 = param_1 >> 30;
	u32 uVar2 = param_1 >> 12 & 0xF;

	if (uVar1 == 0)
		return uVar2;

	if (uVar1 == 2)
		return 0x10;

	if (uVar1 == 3)
		return 0x11;

	return 0xffffffff;
}

// Fabricated name; a TU-local level over the voice-number field (see case 7).
static inline u32 MarioVoiceNo(u32 groundNo) { return (groundNo >> 24) & 0xF; }

void MAnmSoundMario::startAnimSound(void* interface, u32 sound_id,
                                    JAISound** out_handle, JAIActor* actor,
                                    u8 camera_idx)
{
	if (MSGMSound->gateCheck(sound_id)) {
		u32 category = get_thing(sound_id);
		switch (category) {
		case 0:
			if ((actor->mGroundNumber & 0x1000) == 0x1000)
				return;
			break;

		// The voice number comes out of a returning helper: a named local
		// swaps r0/r5 against groundNo, an unnamed expression drops the
		// `extsh`.
		case 7: {
			u32 groundNo = actor->mGroundNumber;
			MSGMSound->startMarioVoice(sound_id, MarioVoiceNo(groundNo),
			                           groundNo >> 28);
			return;
		}
		}

		MSoundSESystem::MSoundSE::startSoundActorInner(sound_id, out_handle,
		                                               actor, 0, camera_idx);
	}
}

// This operation owns both positions and the computed Euclidean distance.
static inline f32 PositionDistance(const Vec& position, const Vec& reference)
{
	f32 distance = std::sqrtf(std::powf(position.x - reference.x, 2.0f)
	                         + std::powf(position.y - reference.y, 2.0f)
	                         + std::powf(position.z - reference.z, 2.0f));
	return distance;
}

f32 MSMarioPosVolume::getDistFromMario(const Vec& pos)
{
	if (MSGMSound->cameraLooksAtMario()) {
		const Vec* mario = MSGMSound->unkAC[0].mPosition;
		return PositionDistance(pos, *mario);
	}

	return 0.0f;
}

// Fabricated name: a TU-local level over the entry word, read through it only
// for the flag mask. It reserves retail's four low bytes below the random
// draw's float-bits slot (0x44); every further use adds 4-8 of frame.
static inline u32 EntryWord(JAIAnimeSoundData* p, u32 i)
{
	return p->mEntries[i].unk10;
}

void MAnmSoundNPC::startAnimSound(void* interface, u32 sound_id,
                                  JAISound** out_handle, JAIActor* actor,
                                  u8 camera_idx)
{
	if (MSGMSound->gateCheck(sound_id)) {
		JAIAnimeSoundData* ptr = mData;

		// The masked value is a named local in retail: both inner tests
		// read it (`clrrwi. r4, r3, 16` stays live and feeds
		// `clrrwi. r0, r4, 24` and `rlwinm. r0, r4, 0, 8, 15`), which is
		// also what forces the raw word to be re-loaded for the 0xFF0000
		// branch after `lbz r3, 0x98(r26)` clobbers it.
		u32 flags = EntryWord(ptr, mDataCounter) & 0xFFFF0000;
		if (flags) {
			if (flags & 0xFF000000) {
				// The divisor comes from the entry word, not from
				// mLoopCount: retail's `srwi r3, r3, 24` reads the
				// value loaded at 0x54, while mLoopCount (0x84) only
				// feeds the `cmplwi` and the later `add`.
				u32 uVar5 = mLoopCount;
				u32 uVar6
				    = (ptr->mEntries[mDataCounter].unk10 >> 24) + 1;
				if (uVar5 != 0) {
					u32 uVar3 = uVar5 + unk98 % uVar6;
					if (uVar3 % uVar6 != 0)
						return;
				}
			}

			if (flags & 0xFF0000) {
				// `b = b * r`, not `b *= r`: the compound form swaps
				// r3/r0 against r4 over these three statements.
				u8 b = ptr->mEntries[mDataCounter].unk10 >> 16;
				b += 1;
				b = b * JAIConst::random.get_ufloat_1();
				if (b != 0)
					return;
			}
		}

		if (MSoundSESystem::MSoundSE::checkMonoSound(sound_id, actor)) {
			MSoundSESystem::MSoundSE::startSoundActorInner(
			    sound_id, out_handle, actor, 0, camera_idx);

			if (*out_handle != nullptr
			    && !(ptr->mEntries[mDataCounter].unk10 & 0x8000)) {

				// TODO: all floating-point registers and stack slots match;
				// translation/Mario pointers still swap r27 and r29.
				f32 dVar10 = 1.0f;

				f32 fVar11 = MSMarioPosVolume::getDistFromMario(
				    actor->getTranslation());

				if (fVar11 != 0.0f)
					dVar10 = MSHandle::calcVolume(
					    fVar11, 2000.0f, 600.0f,
					    ptr->mEntries[mDataCounter].unk10 >> 12 & 7, 8);

				(*out_handle)->setSeInterVolume(0, dVar10, 0, 0);
			}
		}
	}
}
