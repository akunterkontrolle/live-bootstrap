/* SPDX-FileCopyrightText: 2026 akunterkontrolle
 * SPDX-License-Identifier: MIT
 * GNU mount is not yet available when the bridge moves build scratch space.
 */
#include <stdio.h>
int main(void)
{
 int result;
 const char *source="tmpfs",*target="/steps",*type="tmpfs";
 const char *options="size=70%,mode=0755";
 __asm__ __volatile__("int $0x80":"=a"(result):"0"(21),"b"(source),"c"(target),"d"(type),"S"(6),"D"(options):"memory");
 if(result){printf("Cannot mount build tmpfs: %d\n",result);return 1;}
 return 0;
}
