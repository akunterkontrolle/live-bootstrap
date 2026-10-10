/* TCC runtime library. 
   Parts of this code are (c) 2002 Fabrice Bellard 

   Copyright (C) 1987, 1988, 1992, 1994, 1995 Free Software Foundation, Inc.

This file is free software; you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the
Free Software Foundation; either version 2, or (at your option) any
later version.

In addition to the permissions in the GNU General Public License, the
Free Software Foundation gives you unlimited permission to link the
compiled version of this file into combinations with other programs,
and to distribute those combinations without any restriction coming
from the use of this file.  (The General Public License restrictions
do apply in other respects; for example, they cover modification of
the file, and distribution when not linked into a combine
executable.)

This file is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; see the file COPYING.  If not, write to
the Free Software Foundation, 59 Temple Place - Suite 330,
Boston, MA 02111-1307, USA.  
*/
/* Exploratory TCC helpers; shift implementations from TCC lib/libtcc1.c (GPL-2+). */
#include <linux/kernel.h>
typedef union { struct { int low, high; } s; long long ll; } DWunion;
long long __ashrdi3(long long a, int b)
{
#ifdef __TINYC__
    DWunion u;
    u.ll = a;
    if (b >= 32) {
        u.s.low = u.s.high >> (b - 32);
        u.s.high = u.s.high >> 31;
    } else if (b != 0) {
        u.s.low = ((unsigned)u.s.low >> b) | (u.s.high << (32 - b));
        u.s.high = u.s.high >> b;
    }
    return u.ll;
#else
    return a >> b;
#endif
}

/* XXX: fix tcc's code generator to do this instead */
unsigned long long __lshrdi3(unsigned long long a, int b)
{
#ifdef __TINYC__
    DWunion u;
    u.ll = a;
    if (b >= 32) {
        u.s.low = (unsigned)u.s.high >> (b - 32);
        u.s.high = 0;
    } else if (b != 0) {
        u.s.low = ((unsigned)u.s.low >> b) | (u.s.high << (32 - b));
        u.s.high = (unsigned)u.s.high >> b;
    }
    return u.ll;
#else
    return a >> b;
#endif
}

/* XXX: fix tcc's code generator to do this instead */
long long __ashldi3(long long a, int b)
{
#ifdef __TINYC__
    DWunion u;
    u.ll = a;
    if (b >= 32) {
        u.s.high = (unsigned)u.s.low << (b - 32);
        u.s.low = 0;
    } else if (b != 0) {
        u.s.high = ((unsigned)u.s.high << b) | ((unsigned)u.s.low >> (32 - b));
        u.s.low = (unsigned)u.s.low << b;
    }
    return u.ll;
#else
    return a << b;
#endif
}


int abs(int x) { return x < 0 ? -x : x; }
void __put_user_bad(void) { panic("TCC unsupported put_user size"); }
long __get_user_bad(void) { panic("TCC unsupported get_user size"); return 0; }
void __this_fixmap_does_not_exist(void) { panic("TCC invalid fixmap index"); }
__asm__(".globl __get_user_X\n__get_user_X: movl $-14,%eax; xorl %edx,%edx; ret\n");
