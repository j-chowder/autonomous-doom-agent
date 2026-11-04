// WebAssembly platform layer for doomgeneric.
//
// The engine is driven one doomgeneric_Tick() per browser frame from
// emscripten_set_main_loop (requestAnimationFrame); the short engine waits in
// TryRunTics and the screen wipe go through DG_SleepMs, a bounded spin.

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

// Short bounded spin; never blocks the page for long.
void DG_SleepMs(uint32_t ms)
{
    if (ms > 5)
        ms = 5;
    double end = emscripten_get_now() + ms;
    while (emscripten_get_now() < end)
        ;
}

uint32_t DG_GetTicksMs()
{
    return (uint32_t)emscripten_get_now();
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    (void)pressed;
    (void)key;
    return 0;
}

EM_JS(void, dg_set_title, (const char *title), {
    document.title = UTF8ToString(title);
});

void DG_SetWindowTitle(const char *title)
{
    dg_set_title(title);
}

static void frame(void)
{
    doomgeneric_Tick();
}

int main(int argc, char **argv)
{
    doomgeneric_Create(argc, argv);
    emscripten_set_main_loop(frame, 0, 0);
    return 0;
}
