// WebAssembly platform layer for doomgeneric.
//
// The engine is driven one doomgeneric_Tick() per browser frame from
// emscripten_set_main_loop (requestAnimationFrame); the short engine waits in
// TryRunTics and the screen wipe go through DG_SleepMs, which yields (Asyncify).

#include <stdint.h>
#include <emscripten.h>

#include "doomgeneric.h"

void DG_Init()
{
}

// Completed DG_DrawFrame calls; read by DG_TEST_GetFrameCount.
int dg_frame_count = 0;

// Copy the engine's B,G,R,x pixels into the canvas as opaque RGBA.
EM_JS(void, dg_blit, (const uint32_t *src, int w, int h), {
    var canvas = Module['canvas'];
    if (!canvas) return;
    var ctx = Module.dgCtx;
    if (!ctx || Module.dgCtxCanvas !== canvas) {
        ctx = canvas.getContext('2d');
        Module.dgCtx = ctx;
        Module.dgCtxCanvas = canvas;
        Module.dgImage = ctx.createImageData(w, h);
    }
    var img = Module.dgImage;
    var out = new Uint32Array(img.data.buffer);
    var inp = HEAPU32.subarray(src >> 2, (src >> 2) + w * h);
    for (var i = 0; i < out.length; i++) {
        var p = inp[i];
        // little-endian RGBA word: A<<24 | B<<16 | G<<8 | R
        out[i] = 0xff000000 | ((p & 0xff) << 16) | (p & 0xff00) | ((p >> 16) & 0xff);
    }
    ctx.putImageData(img, 0, 0);
});

void DG_DrawFrame()
{
    dg_blit((const uint32_t *)DG_ScreenBuffer, DOOMGENERIC_RESX, DOOMGENERIC_RESY);
    dg_frame_count++;
}

// Engine waits (tic sync, screen wipe) yield to the browser via Asyncify
// instead of spinning, so the page keeps painting and answering events.
void DG_SleepMs(uint32_t ms)
{
    if (ms > 100)
        ms = 100;
    emscripten_sleep(ms);
}

uint32_t DG_GetTicksMs()
{
    return (uint32_t)emscripten_get_now();
}

// Pops one queued key event from Module.dgKeys (filled by web/doom-boot.js).
// Returns (pressed << 8) | doomkey, or -1 when the queue is empty.
EM_JS(int, dg_pop_key, (void), {
    var q = Module.dgKeys;
    if (!q || !q.length) return -1;
    var e = q.shift();
    return (e[0] << 8) | e[1];
});

int DG_GetKey(int *pressed, unsigned char *key)
{
    int v = dg_pop_key();
    if (v < 0)
        return 0;
    *pressed = (v >> 8) & 1;
    *key = (unsigned char)(v & 0xff);
    return 1;
}

EM_JS(void, dg_set_title, (const char *title), {
    document.title = UTF8ToString(title);
});

void DG_SetWindowTitle(const char *title)
{
    dg_set_title(title);
}

// A frame can suspend inside DG_SleepMs; ignore rAF callbacks meanwhile.
static int in_frame;

static void frame(void)
{
    if (in_frame)
        return;
    in_frame = 1;
    doomgeneric_Tick();
    in_frame = 0;
}

int main(int argc, char **argv)
{
    doomgeneric_Create(argc, argv);
    emscripten_set_main_loop(frame, 0, 0);
    return 0;
}
