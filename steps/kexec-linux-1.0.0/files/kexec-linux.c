/* SPDX-FileCopyrightText: 2026 akunterkontrolle
 * SPDX-License-Identifier: MIT
 * Userspace loader for the Linux 2.4 bridge's restricted kexec reboot ABI.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
struct segment {void *buf;u32 bufsz;void *mem;u32 memsz;};
struct arguments {u32 entry;int count;struct segment *segments;};
struct image {u8 *data;u32 size;};
static u32 get32(const u8*p){u32 v;memcpy(&v,p,4);return v;}
static void put32(u8*p,u32 v){memcpy(p,&v,4);}
static void put16(u8*p,u16 v){memcpy(p,&v,2);}
static u32 page_size(u32 n){return(n+4095)&~4095u;}
static int read_image(const char *name,struct image *image,u32 maximum)
{
 int fd=open(name,O_RDONLY),count;long length;u32 done=0;
 if(fd<0)return 0;
 length=lseek(fd,0,SEEK_END);
 if(length<=0||(unsigned long)length>maximum){close(fd);return 0;}
 image->size=length;image->data=malloc(image->size);
 if(!image->data||lseek(fd,0,SEEK_SET)<0){close(fd);return 0;}
 while(done<image->size){count=read(fd,image->data+done,image->size-done);if(count<=0){close(fd);return 0;}done+=count;}
 close(fd);return 1;
}
int main(int argc,char **argv)
{
 struct image kernel,root,map;
 struct segment segments[6];struct arguments args;
 u8 params[4096],command[4096],gdt[4096],stub[4096];
 u32 setup,payload,offset=0,nmap=0;int result,n=0;
 static const u8 descriptors[32]={0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,255,255,0,0,0,154,207,0,255,255,0,0,0,146,207,0};
 static const u8 entry_code[]={0xfa,0x0f,0x01,0x15,0,0x7f,0,0,0xea,0x0f,0x80,0,0,0x10,0,0x66,0xb8,0x18,0,0x8e,0xd8,0x8e,0xc0,0x8e,0xe0,0x8e,0xe8,0x8e,0xd0,0xbe,0,0,9,0,0xb8,0,0,0x10,0,0xff,0xe0};
 if(argc<5||argc>6||(argc==6&&strcmp(argv[5],"t440p"))||strlen(argv[4])>255){puts("Usage: kexec-linux bzImage initrd e820 command [t440p]");return 1;}
 if(!read_image(argv[1],&kernel,64*1024*1024)||!read_image(argv[2],&root,512*1024*1024)||!read_image(argv[3],&map,4096)){puts("Cannot read handoff inputs");return 1;}
 if(kernel.size<4096||get32(kernel.data+0x202)!=0x53726448||((unsigned int)kernel.data[0x206]+256u*kernel.data[0x207])<0x202||!(kernel.data[0x211]&1)){puts("Expected a Linux high-loaded bzImage, protocol >=2.02");return 1;}
 setup=(kernel.data[0x1f1]?kernel.data[0x1f1]:4)+1;setup*=512;
 if(setup>=kernel.size){puts("Invalid bzImage setup length");return 1;}
 payload=kernel.size-setup;
 if(root.size>0x37ffffffu-0x10000000u+1u){puts("Initrd exceeds conservative bzImage low-memory limit");return 1;}
 memset(params,0,sizeof(params));memcpy(params+0x1f1,kernel.data+0x1f1,0x7f);
 while(offset<map.size){
  u32 size;
  if(map.size-offset<24||nmap==32){puts("Invalid E820 record");return 1;}
  size=get32(map.data+offset);
  if(size<20||size>map.size-offset-4){puts("Invalid E820 size");return 1;}
  memcpy(params+0x2d0+20*nmap,map.data+offset+4,20);nmap++;offset+=size+4;
 }
 if(!nmap)return 1;
 params[0x1e8]=nmap;params[0x210]=0xff;params[0x211]=1;
 put32(params+0x214,0x100000);put32(params+0x218,0x10000000);put32(params+0x21c,root.size);
 memset(command,0,sizeof(command));strcpy((char*)command,argv[4]);put32(params+0x228,0x92000);
 params[6]=3;params[7]=80;params[14]=25;params[15]=1;put16(params+16,16);
 if(argc==6){
  params[15]=0x23;put16(params+0x12,1920);put16(params+0x14,1080);put16(params+0x16,32);
  put32(params+0x18,0xe0000000);put32(params+0x1c,127);put16(params+0x24,7680);
  params[0x26]=8;params[0x27]=16;params[0x28]=8;params[0x29]=8;
  params[0x2a]=8;params[0x2b]=0;params[0x2c]=8;params[0x2d]=24;
 }
 memset(gdt,0,sizeof(gdt));memcpy(gdt,descriptors,sizeof(descriptors));put16(gdt+0xf00,31);put32(gdt+0xf02,0x7000);
 memset(stub,0,sizeof(stub));memcpy(stub,entry_code,sizeof(entry_code));
 segments[n++]=(struct segment){kernel.data+setup,payload,(void*)0x100000,page_size(payload)};
 segments[n++]=(struct segment){root.data,root.size,(void*)0x10000000,page_size(root.size)};
 segments[n++]=(struct segment){gdt,4096,(void*)0x7000,4096};
 segments[n++]=(struct segment){stub,4096,(void*)0x8000,4096};
 segments[n++]=(struct segment){params,4096,(void*)0x90000,4096};
 segments[n++]=(struct segment){command,4096,(void*)0x92000,4096};
 args.entry=0x8000;args.count=n;args.segments=segments;
 puts("Starting the next Linux kernel");fflush(stdout);
 __asm__ __volatile__("int $0x80"::"a"(36):"memory");
 __asm__ __volatile__("int $0x80":"=a"(result):"0"(88),"b"(0xfee1dead),"c"(672274793),"d"(0x45584543),"S"(&args):"memory");
 printf("Linux kexec failed: %d\n",result);return 1;
}
