/* SPDX-FileCopyrightText: 2026 akunterkontrolle
 * SPDX-License-Identifier: GPL-3.0-or-later
 * First kernel handoff from pinned builder-hex0, NOT a Linux userspace kexec.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
#define LOW_END 0x7f617000U
#define PAGE_OFFSET 0x80000000U
struct file { u8 *data; u32 size; };
struct elf_header {
    u8 ident[16]; u16 type,machine; u32 version,entry,phoff,shoff,flags;
    u16 ehsize,phentsize,phnum,shentsize,shnum,shstrndx;
} __attribute__((packed));
struct elf_program { u32 type,offset,vaddr,paddr,filesz,memsz,flags,align; };

static int find_file(const char *name, struct file *f)
{
    int n;
    for (n=14335; n>=3; n--) {
        const char *path=(const char *)(0x00fffc00U-(14335-n)*1024U);
        u32 *entry=(u32 *)(0x01000000U+16U*n);
        if (!strcmp(path,name)) {
            f->data=(u8 *)entry[1]; f->size=entry[2];
            return f->size && (u32)f->data<LOW_END && f->size<=LOW_END-(u32)f->data;
        }
    }
    return 0;
}
static void word(u8 *p,u32 v) { memcpy(p,&v,4); }
static void half(u8 *p,u16 v) { memcpy(p,&v,2); }
static int overlap(u32 a,u32 size,u32 b,u32 len)
{ return a<b+len && b<a+size; }

/* A first handoff must never touch a firmware-reserved physical range. */
static int usable(struct file *map,u32 address,u32 length)
{
    u32 off=0; int covered=0;
    u64 start=address,end=start+length;
    while(off<map->size) {
        u32 size,type; u64 base,count,limit;
        if(map->size-off<24) return 0;
        memcpy(&size,map->data+off,4);
        if(size<20 || size>map->size-off-4) return 0;
        memcpy(&base,map->data+off+4,8);
        memcpy(&count,map->data+off+12,8);
        memcpy(&type,map->data+off+20,4);
        limit=base+count;
        if(limit<base) return 0;
        if(type==1 && start>=base && end<=limit) covered=1;
        if(type!=1 && start<limit && base<end) return 0;
        off+=size+4;
    }
    return length && covered;
}

int main(int argc,char **argv)
{
    struct file kernel,root,map;
    struct elf_header *eh;
    struct elf_program *ph;
    u8 *params=(u8 *)0x90000, *code=(u8 *)0x4000;
    u32 i,j,off=0,nmap=0,entry,n=0;
    int entry_valid=0;
    u64 *gdt=(u64 *)0x7000;
    if (argc<5 || argc>6) {
        puts("Usage: kexec-linux24 kernel-elf root-ext2 e820 cmdline [t440p]"); return 1;
    }
    if (!find_file(argv[1],&kernel) || !find_file(argv[2],&root) || !find_file(argv[3],&map)) {
        puts("Linux handoff: missing file or out-of-range source"); return 1;
    }
    if (!usable(&map,(u32)root.data,root.size) ||
        !usable(&map,(u32)kernel.data,kernel.size) ||
        !usable(&map,0x4000,0x1000) || !usable(&map,0x7000,0x1000) ||
        !usable(&map,0x90000,0x3000)) {
        puts("Linux handoff: allocation crosses an E820 reservation"); return 1;
    }
    if (kernel.size<sizeof(*eh)) return 1;
    eh=(struct elf_header *)kernel.data;
    if (memcmp(eh->ident,"\177ELF\1\1",6) || eh->machine!=3 || eh->type!=2 ||
        eh->phentsize!=sizeof(*ph) || !eh->phnum || eh->phnum>8 ||
        eh->phoff>kernel.size || eh->phnum*sizeof(*ph)>kernel.size-eh->phoff ||
        eh->entry<PAGE_OFFSET) { puts("Linux handoff: invalid ELF"); return 1; }
    entry=eh->entry-PAGE_OFFSET;
    ph=(struct elf_program *)(kernel.data+eh->phoff);
    for(i=0;i<eh->phnum;i++) {
        if(ph[i].type!=1) continue;
        if(ph[i].vaddr<PAGE_OFFSET || ph[i].paddr!=ph[i].vaddr-PAGE_OFFSET ||
           ph[i].paddr<0x100000 || ph[i].paddr>=0x08048000 ||
           ph[i].memsz>0x08048000-ph[i].paddr || ph[i].filesz>ph[i].memsz ||
           !usable(&map,ph[i].paddr,ph[i].memsz) ||
           ph[i].offset>kernel.size || ph[i].filesz>kernel.size-ph[i].offset ||
           overlap(ph[i].paddr,ph[i].memsz,(u32)root.data,root.size) ||
           overlap(ph[i].paddr,ph[i].memsz,(u32)kernel.data,kernel.size)) {
            puts("Linux handoff: unsafe kernel segment"); return 1;
        }
        if((ph[i].flags&1) && entry>=ph[i].paddr &&
           entry-ph[i].paddr<ph[i].filesz) entry_valid=1;
        for(j=0;j<i;j++) if(ph[j].type==1 &&
            overlap(ph[i].paddr,ph[i].memsz,ph[j].paddr,ph[j].memsz)) {
            puts("Linux handoff: overlapping ELF segments"); return 1;
        }
    }
    if(!entry_valid) { puts("Linux handoff: entry outside executable segment"); return 1; }
    /* Linux's initial direct map ends at 1920MiB with this configuration.
       Leave initrd in place; do not duplicate the gigabyte-sized image. */
    if ((u32)root.data<0x01000000 || (u32)root.data>=0x78000000 ||
        root.size>0x78000000-(u32)root.data) {
        puts("Linux handoff: initrd exceeds initial low-memory mapping"); return 1;
    }
    memset(params,0,0x1000);
    while(off<map.size && nmap<32) {
        u32 size,type;
        if(map.size-off<24) return 1;
        memcpy(&size,map.data+off,4);
        if(size<20 || size>map.size-off-4) return 1;
        memcpy(&type,map.data+off+20,4);
        if(type<1 || type>4) type=2;
        memcpy(params+0x2d0+20*nmap,map.data+off+4,16);
        word(params+0x2d0+20*nmap+16,type);
        nmap++; off+=size+4;
    }
    if(!nmap || off!=map.size || strlen(argv[4])>255) return 1;
    params[0x1e8]=nmap; params[0x210]=0xff;
    word(params+0x218,(u32)root.data); word(params+0x21c,root.size);
    strcpy((char *)0x92000,argv[4]); word(params+0x228,0x92000);
    params[6]=3; params[7]=80; params[0xe]=25; params[0xf]=1;
    half(params+0x10,16);
    if(argc==6) {
        if(strcmp(argv[5],"t440p")) return 1;
        params[0xf]=0x23;
        half(params+0x12,1920); half(params+0x14,1080); half(params+0x16,32);
        word(params+0x18,0xe0000000); word(params+0x1c,127);
        half(params+0x24,7680);
        params[0x26]=8; params[0x27]=16; params[0x28]=8; params[0x29]=8;
        params[0x2a]=8; params[0x2b]=0; params[0x2c]=8; params[0x2d]=24;
    }
    for(i=0;i<eh->phnum;i++) {
        if(ph[i].type!=1) continue;
        memset((void *)ph[i].paddr,0,ph[i].memsz);
        memcpy((void *)ph[i].paddr,kernel.data+ph[i].offset,ph[i].filesz);
    }
    gdt[0]=gdt[1]=0; gdt[2]=0x00cf9a000000ffffULL; gdt[3]=0x00cf92000000ffffULL;
    half((u8 *)0x7f00,31); word((u8 *)0x7f02,0x7000);
    /* CLI; LGDT absolute; far JMP to kernel selector; reload data selectors. */
    code[n++]=0xfa; code[n++]=0x0f; code[n++]=0x01; code[n++]=0x15;
    word(code+n,0x7f00); n+=4;
    code[n++]=0xea; word(code+n,0x400f); n+=4; half(code+n,0x10); n+=2;
    code[n++]=0x66; code[n++]=0xb8; half(code+n,0x18); n+=2;
    code[n++]=0x8e; code[n++]=0xd8; code[n++]=0x8e; code[n++]=0xc0;
    code[n++]=0x8e; code[n++]=0xe0; code[n++]=0x8e; code[n++]=0xe8;
    code[n++]=0x8e; code[n++]=0xd0;
    code[n++]=0xbe; word(code+n,0x90000); n+=4;
    code[n++]=0xb8; word(code+n,entry); n+=4;
    code[n++]=0xff; code[n++]=0xe0;
    printf("Starting Linux 2.4 at %08x, root %08x+%08x, E820 %u\n",entry,(u32)root.data,root.size,nmap);
    ((void (*)(void))code)();
    return 1;
}
