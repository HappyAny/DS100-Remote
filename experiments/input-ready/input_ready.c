/* Feed virtual input through the stock key and wheel event consumers. */
#include "../../firmware/ds100_remote.h"

#define INPUT_MAGIC UINT32_C(0x364e5049)
#define LEASE_MS UINT32_C(600)

typedef struct {
    uint32_t magic, last_touch, remote_keys, physical_keys, reported_keys;
    int32_t wheel1, wheel2;
    uint32_t auto_started;
} input_state;

input_state ds100_v48_input_state;
extern void ds100_v48_profile_init(void);
extern void ds100_v48_original_usb_start(void);

static uint32_t now(void) { return ((uint32_t (*)(void))(uintptr_t)0x08015385u)(); }

void ds100_v48_controls_init(void) {
    unsigned i;
    uint32_t *words = (uint32_t *)(void *)&ds100_v48_input_state;
    ds100_v48_profile_init();
    for (i = 0; i < sizeof(input_state) / sizeof(uint32_t); ++i) words[i] = 0;
    ds100_v48_input_state.magic = INPUT_MAGIC;
}

static void expire(void) {
    input_state *s = &ds100_v48_input_state;
    if (s->magic == INPUT_MAGIC && (uint32_t)(now() - s->last_touch) >= LEASE_MS) {
        s->remote_keys = 0; s->wheel1 = s->wheel2 = 0;
    }
}

void ds100_v48_usb_start(void) {
    if (!*(const volatile uint8_t *)(uintptr_t)0x200213f0u)
        ds100_v48_original_usb_start();
}

void ds100_v48_auto_usb(void) {
    input_state *s = &ds100_v48_input_state;
    if (s->magic != INPUT_MAGIC || s->auto_started) return;
    /* This call point is in the GUI loop, after normal board/LCD startup.
     * Reuse stock USB initialization; do not open the modal USB screen.
     */
    s->auto_started = 1;
    if (!*(const volatile uint8_t *)(uintptr_t)0x200213f0u) {
        ds100_v48_usb_start();
        /* The stock clock change stops acquisition peripherals. Reapply the
         * current native configuration after USB initialization. Do not run
         * AUTO, change trigger mode, or replace the user's scale and offset.
         */
        ((void (*)(void))(uintptr_t)0x0802d457u)();
    }
}

void ds100_v48_read_keys(uint8_t *out) {
    input_state *s = &ds100_v48_input_state;
    uint32_t desired, changed, bit;
    unsigned key;
    ((void (*)(uint8_t *))(uintptr_t)0x08036bc7u)(out);
    if (s->magic != INPUT_MAGIC) return;
    expire();
    if ((out[0] == 1u || out[0] == 2u) && out[1] < 4u) {
        bit = UINT32_C(1) << out[1];
        if (out[0] == 1u) s->physical_keys |= bit;
        else s->physical_keys &= ~bit;
    }
    desired = s->physical_keys | s->remote_keys;
    changed = desired ^ s->reported_keys;
    out[0] = 0;
    if (!changed) return;
    for (key = 0; key < 4u; ++key) {
        bit = UINT32_C(1) << key;
        if (changed & bit) {
            out[0] = (desired & bit) ? 1u : 2u;
            out[1] = (uint8_t)key;
            s->reported_keys ^= bit;
            return;
        }
    }
}

static void write16(uint8_t *out, uint16_t value) {
    out[0] = (uint8_t)value; out[1] = (uint8_t)(value >> 8);
}

void ds100_v48_read_wheels(uint8_t *out) {
    input_state *s = &ds100_v48_input_state;
    int32_t delta;
    unsigned i, offset;
    for (i = 0; i < 8u; ++i) out[i] = 0;
    ((void (*)(uint8_t *))(uintptr_t)0x08036b53u)(out);
    if (s->magic != INPUT_MAGIC) return;
    expire();
    /* The stock consumer handles one wheel per loop pass. Keep physical
     * events intact; retain virtual wheel 2 until wheel 1 has been consumed.
     */
    if (out[0] || out[4]) return;
    /* A provider ack is not a GUI ack. The stock widget discards wheels
     * while dirty/busy (0x080295aa..0x080295ba). Retain virtual events until
     * that consumer can accept them. Physical events above remain intact.
     */
    if (s->wheel1 || s->wheel2) {
        uintptr_t model, widget;
        if ((s->remote_keys & 12u) != (s->reported_keys & 12u)) return;
        model = *(const volatile uint32_t *)(uintptr_t)0x200200acu;
        if ((model & 3u) || !((model >= 0x10000008u && model <= 0x1000fe60u) ||
                             (model >= 0x20021648u && model <= 0x20027ca0u))) return;
        widget = *(const volatile uint32_t *)model;
        if ((widget & 3u) || !((widget >= 0x10000008u && widget <= 0x1000fdd0u) ||
                              (widget >= 0x20021648u && widget <= 0x20027c10u))) return;
        if (*(const volatile uint32_t *)widget != 0x08072840u) return;
        /* A modal menu consumes its own wheel events. Do not gate it on the
         * suspended parent widget's dirty flag.
         */
        if (!*(const volatile uint32_t *)(widget + 0x160u) &&
            (*(const volatile uint8_t *)(widget + 0xe8u) ||
             *(const volatile uint8_t *)(widget + 0x229u))) return;
    }
    if (s->wheel1) { delta = s->wheel1; s->wheel1 = 0; offset = 0; }
    else { delta = s->wheel2; s->wheel2 = 0; offset = 4; }
    if (!delta) return;
    out[offset] = (delta > 0) ? 1u : 2u;
    write16(out + offset + 2u, (uint16_t)((delta > 0) ? delta : -delta));
}

int ds100_v48_input(void *context, uint16_t keys, int16_t wheel1, int16_t wheel2,
                    uint8_t *out, size_t capacity, size_t *length) {
    input_state *s = &ds100_v48_input_state;
    int32_t a, b;
    (void)context;
    if (!out || !length || capacity < 8u || keys > 15u || s->magic != INPUT_MAGIC) return -1;
    expire();
    a = s->wheel1 + wheel1; b = s->wheel2 + wheel2;
    if (a < -32767 || a > 32767 || b < -32767 || b > 32767) return -1;
    s->remote_keys = keys; s->wheel1 = a; s->wheel2 = b;
    s->last_touch = now();
    write16(out, keys); write16(out + 2u, (uint16_t)LEASE_MS);
    write16(out + 4u, (uint16_t)(int16_t)a); write16(out + 6u, (uint16_t)(int16_t)b);
    *length = 8u;
    return 0;
}
