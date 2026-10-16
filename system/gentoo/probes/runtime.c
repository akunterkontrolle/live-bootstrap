/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <stdio.h>
#include <stdlib.h>
/* Volatile operands prevent constant folding of the ELF32 libgcc division. */
int main(void) {
    volatile long long numerator = 9876543212345LL, divisor = 97;
    if (numerator / divisor != 101820033116LL) return 1;
    char *end;
    if (strtol("42", &end, 10) != 42 || *end) return 2;
    return puts("gentoo C runtime ok") < 0;
}
