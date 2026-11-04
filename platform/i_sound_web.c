// Web Audio sound effects for doomgeneric.
//
// Provides DG_sound_module / DG_music_module (selected by i_sound.c when built
// with -DFEATURE_SOUND). Sound lumps are DMX: u16 format(3), u16 rate, u32
// length, 16 pad bytes, 8-bit unsigned samples, 16 pad bytes. Decoding to a
// Float32 AudioBuffer happens lazily in JS and is cached per lump.

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <emscripten.h>

#include "doomtype.h"
#include "i_sound.h"
#include "w_wad.h"
#include "z_zone.h"

// Referenced by i_sound.c / m_config.c when FEATURE_SOUND is defined.
int use_libsamplerate = 0;
float libsamplerate_scale = 0.65f;

#define NUM_CHANNELS 16

// Wall-clock ms at which each channel's sound ends (0 = idle).
static double channel_end[NUM_CHANNELS];

// The browser context is created by web/doom-boot.js on the first key or
// pointer press (autoplay policy); until then sounds are silently dropped.
EM_JS(void, dg_audio_play, (int channel, int lump, const uint8_t *data, int len,
                            int vol, int sep), {
    try {
        var A = Module.dgAudio || (Module.dgAudio = { cache: {}, ch: {} });
        var ctx = window.dgAudioContext && window.dgAudioContext();
        if (!ctx) return;
        if (ctx.state === 'suspended') ctx.resume().catch(function () {});
        var buf = A.cache[lump];
        if (!buf) {
            var rate = HEAPU8[data + 2] | (HEAPU8[data + 3] << 8);
            var n = HEAPU8[data + 4] | (HEAPU8[data + 5] << 8) |
                    (HEAPU8[data + 6] << 16) | (HEAPU8[data + 7] << 24);
            if (n > len - 8) n = len - 8;
            // length counts the 16 pad bytes on each end
            var start = 24, count = n - 32;
            if (count <= 0 || rate < 3000 || rate > 96000) return;
            buf = ctx.createBuffer(1, count, rate);
            var f = buf.getChannelData(0);
            for (var i = 0; i < count; i++) f[i] = (HEAPU8[data + start + i] - 128) / 128;
            A.cache[lump] = buf;
        }
        var old = A.ch[channel];
        if (old) { try { old.src.stop(); } catch (e) {} }
        var src = ctx.createBufferSource();
        src.buffer = buf;
        var gain = ctx.createGain();
        gain.gain.value = vol / 127;
        var node = gain;
        src.connect(gain);
        if (ctx.createStereoPanner) {
            var pan = ctx.createStereoPanner();
            pan.pan.value = Math.max(-1, Math.min(1, (sep - 128) / 128));
            gain.connect(pan);
            node = pan;
        }
        node.connect(ctx.destination);
        A.ch[channel] = { src: src, gain: gain };
        src.onended = function () { if (A.ch[channel] && A.ch[channel].src === src) delete A.ch[channel]; };
        src.start();
    } catch (e) { console.warn('audio play failed', e); }
});

EM_JS(void, dg_audio_params, (int channel, int vol, int sep), {
    try {
        var A = Module.dgAudio;
        var c = A && A.ch[channel];
        if (c) c.gain.gain.value = vol / 127;
    } catch (e) {}
});

EM_JS(void, dg_audio_stop, (int channel), {
    try {
        var A = Module.dgAudio;
        var c = A && A.ch[channel];
        if (c) { delete A.ch[channel]; c.src.stop(); }
    } catch (e) {}
});

static snddevice_t sfx_devices[] = {
    SNDDEVICE_SB, SNDDEVICE_PAS, SNDDEVICE_GUS, SNDDEVICE_WAVEBLASTER,
    SNDDEVICE_SOUNDCANVAS, SNDDEVICE_AWE32,
};

static boolean use_prefix;

static boolean SndInit(boolean use_sfx_prefix)
{
    use_prefix = use_sfx_prefix;
    memset(channel_end, 0, sizeof(channel_end));
    return true;
}

static void SndShutdown(void)
{
}

static int SndGetSfxLumpNum(sfxinfo_t *sfxinfo)
{
    char namebuf[16];

    if (use_prefix)
        snprintf(namebuf, sizeof(namebuf), "ds%s", sfxinfo->name);
    else
        snprintf(namebuf, sizeof(namebuf), "%s", sfxinfo->name);
    return W_CheckNumForName(namebuf);
}

static void SndUpdate(void)
{
}

static void SndUpdateSoundParams(int channel, int vol, int sep)
{
    if (channel < 0 || channel >= NUM_CHANNELS)
        return;
    dg_audio_params(channel, vol, sep);
}

static int SndStartSound(sfxinfo_t *sfxinfo, int channel, int vol, int sep)
{
    int lump = sfxinfo->lumpnum;
    int len;
    uint8_t *data;

    if (channel < 0 || channel >= NUM_CHANNELS || lump < 0)
        return -1;
    len = W_LumpLength(lump);
    if (len < 48)
        return -1;
    data = W_CacheLumpNum(lump, PU_STATIC);
    if (data[0] != 3 || data[1] != 0)
        return -1;
    dg_audio_play(channel, lump, data, len, vol, sep);

    {
        unsigned rate = data[2] | (data[3] << 8);
        unsigned n = data[4] | (data[5] << 8) | (data[6] << 16) | ((unsigned)data[7] << 24);
        double ms = rate ? (n > 32 ? (n - 32) : 0) * 1000.0 / rate : 0;
        channel_end[channel] = emscripten_get_now() + ms;
    }
    return channel;
}

static void SndStopSound(int channel)
{
    if (channel < 0 || channel >= NUM_CHANNELS)
        return;
    channel_end[channel] = 0;
    dg_audio_stop(channel);
}

static boolean SndSoundIsPlaying(int channel)
{
    if (channel < 0 || channel >= NUM_CHANNELS)
        return false;
    return channel_end[channel] > emscripten_get_now();
}

static void SndCacheSounds(sfxinfo_t *sounds, int num_sounds)
{
    // Decoding is lazy (first play) so startup and the tic rate are unaffected.
}

sound_module_t DG_sound_module = {
    sfx_devices,
    sizeof(sfx_devices) / sizeof(*sfx_devices),
    SndInit,
    SndShutdown,
    SndGetSfxLumpNum,
    SndUpdate,
    SndUpdateSoundParams,
    SndStartSound,
    SndStopSound,
    SndSoundIsPlaying,
    SndCacheSounds,
};

// Music is out of scope: a silent module that accepts every call.
static snddevice_t music_devices[] = { SNDDEVICE_GENMIDI, SNDDEVICE_SB, SNDDEVICE_ADLIB };

static boolean MusInit(void) { return true; }
static void MusShutdown(void) { }
static void MusSetVolume(int volume) { }
static void MusPause(void) { }
static void MusResume(void) { }
static void *MusRegister(void *data, int len) { return data; }
static void MusUnRegister(void *handle) { }
static void MusPlay(void *handle, boolean looping) { }
static void MusStop(void) { }
static boolean MusIsPlaying(void) { return false; }
static void MusPoll(void) { }

music_module_t DG_music_module = {
    music_devices,
    sizeof(music_devices) / sizeof(*music_devices),
    MusInit,
    MusShutdown,
    MusSetVolume,
    MusPause,
    MusResume,
    MusRegister,
    MusUnRegister,
    MusPlay,
    MusStop,
    MusIsPlaying,
    MusPoll,
};
