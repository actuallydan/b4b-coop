// Host: the listen server has no profile for remote players, so the card-ownership check fails for every card of
// theirs and GrantLoadoutCardsForSlot turns the whole deck into "pick a card" draws that then also fail.
// Treat remote humans as owning their deck (their own client UI already limits decks to cards they own).
// Interim until per-player profile sync; see docs/investigations/card-draft.md.
#include <string.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"

#define ADDR_GCM_OWNSCARD 0x14176DDA0ull  // bool (UGameplayCardManager*, AGobiPlayerState*, const FDataTableRowHandle*, uint8 deck)
static const uint8_t SIG_OWNSCARD[] = {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x6c,0x24,0x18,0x56,0x57,0x41,0x56,0x48,0x83,
                                       0xec,0x20,0x41,0x0f,0xb6,0xd9,0x49,0x8b,0xf8,0x4c,0x8b,0xf2,0x48,0x8b,0xe9,0x48};

typedef uint8_t (*OwnsCardFn)(UObject *gcm, UObject *ps, void *row, uint8_t deck);
static OwnsCardFn orig_owns;
static UClass *cls_pc, *cls_lp;
static int overrides;

static int is_remote_human(UObject *ps) {
    if (!cls_pc) cls_pc = ue_find_class("PlayerController");  // resolved lazily: engine classes load after our init
    if (!cls_lp) cls_lp = ue_find_class("LocalPlayer");
    if (!cls_pc || !cls_lp) return 0;
    UObject *pc = ps ? ue_get_ptr(ps, "Owner") : NULL;
    if (!pc || !ue_is_a(pc, cls_pc)) return 0;   // bots (AI controllers) already pass the original check
    UObject *player = ue_get_ptr(pc, "Player");
    return player && !ue_is_a(player, cls_lp);    // a NetConnection => remote client
}

static uint8_t owns_detour(UObject *gcm, UObject *ps, void *row, uint8_t deck) {
    uint8_t r = orig_owns(gcm, ps, row, deck);
    if (!r && is_remote_human(ps)) {
        r = 1;  // TODO(profile sync): check the card against this client's own unlocks
        if (overrides++ < 20) LOG("cards: ownership override for remote player");
    }
    return r;
}

int cards_init(void) {
    if (memcmp((void *)ADDR_GCM_OWNSCARD, SIG_OWNSCARD, sizeof SIG_OWNSCARD)) { LOG("cards: signature mismatch"); return -1; }
    if (MH_CreateHook((void *)ADDR_GCM_OWNSCARD, (void *)owns_detour, (void **)&orig_owns) != MH_OK ||
        MH_EnableHook((void *)ADDR_GCM_OWNSCARD) != MH_OK) { LOG("cards: hook failed"); return -1; }
    LOG("cards: OwnsCard hooked");
    return 0;
}
