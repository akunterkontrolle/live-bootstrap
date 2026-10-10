/* SPDX-FileCopyrightText: 2026 akunterkontrolle
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Fixed i386 Linux 2.4 section layout for a dedicated early TCC linker.
 * Mirrors arch/i386/vmlinux.lds; not a general linker-script interpreter.
 */
static void linux24_symbol(const char *name, addr_t value)
{
    int n = find_elf_sym(symtab_section, name);
    ElfW(Sym) *sym;
    if (!n) n = put_elf_sym(symtab_section, value, 0,
        ELFW(ST_INFO)(STB_GLOBAL, STT_NOTYPE), 0, SHN_ABS, name);
    sym = &((ElfW(Sym) *)symtab_section->data)[n];
    sym->st_value = value;
    sym->st_shndx = SHN_ABS;
}

static int linux24_layout(TCCState *s1, ElfW(Phdr) *phdr, int *order)
{
    static const char *names[] = {
        ".text", ".fixup", ".gnu.warning", ".rodata", ".kstrtab",
        "__ex_table", "__ksymtab", ".data", ".data.init_task",
        ".text.init", ".data.init", ".setup.init", ".initcall.init",
        ".data.idt", ".data.cacheline_aligned", ".text.exit",
        ".data.exit", ".exitcall.exit", ".bss", NULL
    };
    addr_t addr = 0x80100000;
    int off = 0x1000, n = 1, i, j, index;
    Section *s;
    linux24_symbol("_text", addr);
    for (j = 0; names[j]; j++) {
        if (j == 3) linux24_symbol("_etext", addr);
        if (j == 5) {
            addr = (addr + 15) & ~15;
            linux24_symbol("__start___ex_table", addr);
        }
        if (j == 6) {
            linux24_symbol("__stop___ex_table", addr);
            linux24_symbol("__start___ksymtab", addr);
        }
        if (j == 7) linux24_symbol("__stop___ksymtab", addr);
        if (j == 8) {
            linux24_symbol("_edata", addr);
            addr = (addr + 8191) & ~8191;
        }
        if (j == 9) {
            addr = (addr + 4095) & ~4095;
            linux24_symbol("__init_begin", addr);
        }
        if (j == 11) {
            addr = (addr + 15) & ~15;
            linux24_symbol("__setup_start", addr);
        }
        if (j == 12) {
            linux24_symbol("__setup_end", addr);
            linux24_symbol("__initcall_start", addr);
        }
        if (j == 13) {
            linux24_symbol("__initcall_end", addr);
            addr = (addr + 4095) & ~4095;
            linux24_symbol("__init_end", addr);
        }
        if (j == 14) addr = (addr + 31) & ~31;
        if (j == 18) linux24_symbol("__bss_start", addr);
        for (i = 1; i < s1->nb_sections; i++) {
            s = s1->sections[i];
            if (strcmp(s->name, names[j]) &&
                !(j == 3 && !strncmp(s->name, ".rodata.", 8)) &&
                !(j == 0 && !strcmp(s->name, ".text.lock")) &&
                !(j == 7 && !strcmp(s->name, ".got"))) continue;
            if (!(s->sh_flags & SHF_ALLOC)) continue;
            addr = (addr + s->sh_addralign - 1) & ~(s->sh_addralign - 1);
            s->sh_addr = addr;
            s->sh_offset = addr - 0x80100000 + 0x1000;
            order[n++] = i;
            addr += s->sh_size;
            if (s->sh_type != SHT_NOBITS) off = s->sh_offset + s->sh_size;
        }
    }
    linux24_symbol("_end", addr);
    phdr[0].p_type = PT_LOAD;
    phdr[0].p_offset = 0x1000;
    phdr[0].p_vaddr = 0x80100000;
    phdr[0].p_paddr = 0x00100000;
    phdr[0].p_filesz = off - 0x1000;
    phdr[0].p_memsz = addr - 0x80100000;
    phdr[0].p_flags = PF_R | PF_W | PF_X;
    phdr[0].p_align = 0x1000;
    phdr[1].p_type = PT_NULL;
    for (i = 1; i < s1->nb_sections; i++) {
        s = s1->sections[i];
        for (index = 1; index < n; index++) if (order[index] == i) break;
        if (index < n) continue;
        if ((s->sh_flags & SHF_ALLOC) && !s->sh_size) {
            s->sh_flags &= ~SHF_ALLOC;
            s->sh_addr = addr;
        }
        if (s->sh_flags & SHF_ALLOC)
            tcc_error("Linux layout: unknown allocated section %s", s->name);
        off = (off + s->sh_addralign - 1) & ~(s->sh_addralign - 1);
        s->sh_offset = off;
        if (s->sh_type != SHT_NOBITS) off += s->sh_size;
        order[n++] = i;
    }
    return off;
}
