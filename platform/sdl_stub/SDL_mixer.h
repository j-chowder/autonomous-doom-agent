// Empty stand-in: i_sound.c includes SDL_mixer.h under FEATURE_SOUND, but the
// web port plays sound through Web Audio (platform/i_sound_web.c), not SDL.
#ifndef DG_SDL_MIXER_STUB_H
#define DG_SDL_MIXER_STUB_H
#endif
