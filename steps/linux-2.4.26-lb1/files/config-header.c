/* SPDX-FileCopyrightText: 2026 akunterkontrolle
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Convert the pinned, built-in-only configuration without a shell/make. */
#include <stdio.h>
#include <string.h>
int main(int argc,char **argv)
{
    char line[512],*value,*end;
    FILE *in,*out;
    if(argc!=3) return 1;
    in=fopen(argv[1],"r"); out=fopen(argv[2],"w");
    if(!in || !out) return 1;
    fprintf(out,"/* Generated from the pinned Linux configuration. */\n#define AUTOCONF_INCLUDED\n");
    while(fgets(line,sizeof(line),in)) {
        if(strncmp(line,"CONFIG_",7)) continue;
        value=strchr(line,'='); if(!value) return 1;
        *value++=0; end=strchr(value,'\n'); if(end) *end=0;
        if(!strcmp(value,"m")) return 1;
        if(!strcmp(value,"y")) value="1";
        fprintf(out,"#define %s %s\n",line,value);
    }
    if(ferror(in) || ferror(out)) return 1;
    fclose(in); return fclose(out)!=0;
}
