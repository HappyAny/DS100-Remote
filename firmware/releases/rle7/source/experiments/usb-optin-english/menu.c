/* SPDX-License-Identifier: GPL-3.0-only */
#include "../usb-optin/optin.h"
typedef struct {
    uint8_t native[0x78];
    void *buttons[2], *status;
    uint32_t selected;
    int32_t error;
} optin_menu;
_Static_assert(sizeof(optin_menu)<=0xa0u,"The native USB allocation is too small.");
#define NEW ((void *(*)(uint32_t))(uintptr_t)0x0801032fu)
#define FREE ((void (*)(void *))(uintptr_t)0x08010333u)
#define AVAILABLE ((uint32_t (*)(void))(uintptr_t)0x08010515u)
#define LABEL ((void *(*)(void *,void *,const int16_t *,const char *))(uintptr_t)0x08015a3bu)
#define DIRTY ((void (*)(void *))(uintptr_t)0x080168adu)
#define BASE_DTOR ((void *(*)(void *))(uintptr_t)0x08015acbu)

static void text(void *p,const char *value) {
    unsigned i=0;
    char *buffer=*(char **)((uint8_t *)p+0x64);
    if (!buffer) return;
    while (i<95u && value[i]) { buffer[i]=value[i]; ++i; }
    buffer[i]=0; DIRTY(p);
}

static void color(void *p,uint16_t background,uint16_t foreground) {
    if (!p) return;
    *(uint16_t *)((uint8_t *)p+0x62)=background;
    *(uint16_t *)((uint8_t *)p+0x68)=foreground;
    DIRTY(p);
}

static void *label(void *parent,int16_t y0,int16_t y1,const char *text) {
    const int16_t rectangle[4]={20,y0,459,y1};
    void *p=NEW(0x78u);
    if (!p) return 0;
    p=LABEL(p,parent,rectangle,0);
    *(void **)((uint8_t *)p+0x64)=NEW(96u);
    if (!*(void **)((uint8_t *)p+0x64)) return 0;
    /* The original destructor owns this allocation. Later refreshes reuse it. */
    {
        unsigned i=0;char *buffer=*(char **)((uint8_t *)p+0x64);
        while (i<95u && text[i]) { buffer[i]=text[i]; ++i; }
        buffer[i]=0;
    }
    color(p,0xffffu,0x001fu);
    return p;
}

static void refresh(optin_menu *m) {
    uint32_t flags=ds100_optin_flags(); unsigned i;
    text(m->buttons[0],flags & OPT_ENABLED ? "PC Remote: ON" : "PC Remote: OFF");
    text(m->buttons[1],flags & OPT_FILES ? "USB Files (read-only): ON" : "USB Files (read-only): OFF");
    text(m->status,m->error==-1 ? "Save failed; state unchanged" :
         m->error ? "USB initialization failed" : flags & OPT_ENABLED ? "PC Remote ON; setting saved" :
         flags & OPT_FILES ? "Files only; PC Remote OFF" : "PC Remote OFF");
    for (i=0;i<2u;++i) color(m->buttons[i],i==m->selected ? 0x001fu : 0xe71cu,
                            i==m->selected ? 0xffffu : 0u);
}

static int menu_key(void *p,const uint8_t *event) {
    optin_menu *m=p;
    if (!event || event[1]!=1u || event[0]>3u) return 0;
    if (event[0]==3u) {
        ds100_optin_menu_close(); m->native[0x48]=1u;
    } else {
        m->error=m->selected ? ds100_optin_files() : ds100_optin_toggle();
        refresh(m);
    }
    return 1;
}

static int menu_wheel(void *p,const uint8_t *event) {
    optin_menu *m=p;
    if (!event || (!event[0] && !event[4])) return 0;
    m->selected^=1u; refresh(m);
    return 1;
}

static void *menu_destruct(void *p) {
    ds100_optin_menu_close();
    return BASE_DTOR(p);
}

static void menu_delete(void *p) {
    (void)menu_destruct(p); FREE(p);
}

static const uintptr_t menu_vtable[10]={
    (uintptr_t)menu_key,(uintptr_t)menu_wheel,(uintptr_t)menu_destruct,(uintptr_t)menu_delete,
    0x0804c65bu,0x0804c657u,0x08015cfbu,0x0804c659u,0x0804c64fu,0x0804c653u
};

void *ds100_usb_menu_create(void *memory,void *parent,const int16_t *rectangle,const char *unused) {
    optin_menu *m; void *title,*hint; (void)unused;
    if (!memory || !rectangle) return 0;
    if (AVAILABLE()<2048u) { FREE(memory); return 0; }
    m=LABEL(memory,parent,rectangle,0);
    *(const uintptr_t **)m=menu_vtable;
    m->selected=0; m->error=0;
    color(m,0xffffu,0x001fu);
    title=label(m,12,49,"USB");
    m->buttons[0]=label(m,66,107,"");
    m->buttons[1]=label(m,118,159,"");
    m->status=label(m,174,215,"");
    hint=label(m,234,295,"Wheel: Select   P: Toggle   E: Back");
    if (!title || !m->buttons[0] || !m->buttons[1] || !m->status || !hint) {
        (void)menu_destruct(m); FREE(m); return 0;
    }
    refresh(m);
    return m;
}
