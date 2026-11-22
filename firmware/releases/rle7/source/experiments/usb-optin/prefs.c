/* SPDX-License-Identifier: GPL-3.0-only */
#include "optin.h"
extern uint32_t ds100_crc32(const uint8_t *, size_t);
#define FILE_OBJECT ((void *)(uintptr_t)0x2002bea4u)
#define FILE_BUSY (*(void * volatile *)(uintptr_t)0x200203b4u)
#define F_OPEN ((int (*)(void *, const char *, unsigned))(uintptr_t)0x08048393u)
#define F_READ ((int (*)(void *, void *, unsigned, unsigned *))(uintptr_t)0x080484f7u)
#define F_WRITE ((int (*)(void *, const void *, unsigned, unsigned *))(uintptr_t)0x08048697u)
#define F_CLOSE ((int (*)(void *))(uintptr_t)0x08048925u)
static const char path[] = "PCREMOTE.CFG";

int ds100_optin_pref_load(void) {
    uint8_t bytes[13]; unsigned got=0, i; uint32_t crc=0; int result, closed;
    if (FILE_BUSY) return 0;
    FILE_BUSY=FILE_OBJECT;
    result=F_OPEN(FILE_OBJECT,path,1u);
    if (result) { FILE_BUSY=0; return 0; }
    result=F_READ(FILE_OBJECT,bytes,sizeof(bytes),&got);
    closed=F_CLOSE(FILE_OBJECT); FILE_BUSY=0;
    if (result || closed || got!=12u || bytes[0]!='R' || bytes[1]!='2' || bytes[2]!='1' || bytes[3]!='P' ||
            bytes[4]!=1u || bytes[5]>1u || bytes[6] || bytes[7]) return 0;
    for (i=0;i<4u;++i) crc |= (uint32_t)bytes[8u+i]<<(8u*i);
    return crc==ds100_crc32(bytes,8u) ? bytes[5] : 0;
}

int ds100_optin_pref_save(unsigned enabled) {
    uint8_t bytes[12]={'R','2','1','P',1,0,0,0,0,0,0,0};
    unsigned written=0,i; uint32_t crc; int result,closed;
    if (enabled>1u || FILE_BUSY) return -1;
    bytes[5]=(uint8_t)enabled; crc=ds100_crc32(bytes,8u);
    for (i=0;i<4u;++i) bytes[8u+i]=(uint8_t)(crc>>(8u*i));
    FILE_BUSY=FILE_OBJECT;
    result=F_OPEN(FILE_OBJECT,path,10u);
    if (result) { FILE_BUSY=0; return -1; }
    result=F_WRITE(FILE_OBJECT,bytes,sizeof(bytes),&written);
    closed=F_CLOSE(FILE_OBJECT); FILE_BUSY=0;
    return result || closed || written!=sizeof(bytes) ? -1 : 0;
}
