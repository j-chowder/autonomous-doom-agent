// Your WebAssembly platform layer for doomgeneric.
//
// These are the functions doomgeneric expects a platform to provide
// (see third_party/doomgeneric/doomgeneric.h). Right now they do nothing,
// so the engine links but shows nothing and takes no input.
//
// main() is a minimal placeholder so the link succeeds; the real loop
// comes later.
//
// Making this behave like Doom in a browser is the project.

#include <stdint.h>

#include "doomgeneric.h"

void DG_Init()
{
}

// Completed DG_DrawFrame calls; read by DG_TEST_GetFrameCount.
int dg_frame_count = 0;

void DG_DrawFrame()
{
    dg_frame_count++;
}

void DG_SleepMs(uint32_t ms)
{
    (void)ms;
}

uint32_t DG_GetTicksMs()
{
    return 0;
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    (void)pressed;
    (void)key;
    return 0;
}

void DG_SetWindowTitle(const char *title)
{
    (void)title;
}

int main(int argc, char **argv)
{
    doomgeneric_Create(argc, argv);
    return 0;
}
