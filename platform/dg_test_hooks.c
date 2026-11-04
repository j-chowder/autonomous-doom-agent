// Read-only test hooks. Getters only; do not add other DG_TEST_ symbols.

#include <emscripten.h>

#include "doomstat.h"
#include "d_items.h"
#include "d_player.h"
#include "p_mobj.h"

extern int dg_frame_count;

EMSCRIPTEN_KEEPALIVE int DG_TEST_GetGameTic(void)
{
    return gametic;
}

EMSCRIPTEN_KEEPALIVE int DG_TEST_GetFrameCount(void)
{
    return dg_frame_count;
}

EMSCRIPTEN_KEEPALIVE int DG_TEST_GetPlayerX(void)
{
    mobj_t *mo = players[consoleplayer].mo;
    return mo ? mo->x : 0;
}

EMSCRIPTEN_KEEPALIVE int DG_TEST_GetPlayerY(void)
{
    mobj_t *mo = players[consoleplayer].mo;
    return mo ? mo->y : 0;
}

EMSCRIPTEN_KEEPALIVE int DG_TEST_GetViewAngle(void)
{
    mobj_t *mo = players[consoleplayer].mo;
    return mo ? (int)mo->angle : 0;
}

EMSCRIPTEN_KEEPALIVE int DG_TEST_GetAmmo(void)
{
    player_t *p = &players[consoleplayer];
    ammotype_t a = weaponinfo[p->readyweapon].ammo;
    return a == am_noammo ? 0 : p->ammo[a];
}
