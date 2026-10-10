# Linux 2.4 replacing Fiwix

This package replaces FiWiX with Linux 2.4, because on some systems (e.g. a Lenovo Thinkpad T440p with Libreboot),
only 2GB of RAM are usable in 32bit mode, the rest is marked reserved. This does not leave enough room for the initram file
and compilation. Tinkering with the initram size proofed fruitless. Additonally, when trying to tinker with memory handling of Fiwix,
only more problems surface. Linux was even at 2.4 a major stable operation system unlike Fiwix. Linux 2.4 offers PAE, so more RAM can be used.
Therefore, with Linux 2.4 a large ramdisk + enough RAM for compiling is available.

There were already projects that patched Linux 2.4 so it could be compiled with tcc: https://github.com/seyko2/tccboot and https://bellard.org/tcc/tccboot.html 

An OpenAI GTP6-luna model made the avalaible work fit into live-bootstrap, modified the original kexec-fiwix to kexec into Linux 2.4 
and backported some kexec version to kexec from 2.4 to the Linux 4.14.

Linux 2.4.26 sources are pinned by SHA256. The before/after patches contain
selected i386 adaptations from 8l/tccboot commit
`cf5c632939856262d095320c887796af3cfbea27`, explicit FASTCALL definition attributes
needed by TCC, assembly syntax adaptations, and a 2GiB kernel split (needed because of the T440p memory map layout).
The startup page directory must map PAGE_OFFSET at entry 512, not 768.

Additional target executables are all built by early TCC:

* `linux24-untar`: the existing early tar extractor with a fixed selection
  of i386 sources and headers. Unpacking other architectures would exhaust
  builder-hex0's file table; the original archive is still checksum-verified.
* `linux24-link`: a separate TCC build with a fixed Linux section layout.
  It does not replace or change `/usr/bin/tcc`. It uses TCC object loading,
  archive extraction, symbol resolution and relocations, and supplies the
  init/exception table boundaries from the Linux linker script.
* `linux24-config`: converts the pinned built-in configuration to C defines.
* `linux24-conmakehash` and `linux24-gen-crc32`: Linux's own table generators,
  adapted to accept an output filename because Kaem has no shell redirects.
  The font table uses checked heap allocation: its original BSS array exceeds
  builder-hex0's 128KiB gap between file-backed data and the initial heap.

No Linux BIOS setup/boot sector, compressed bzImage, GNU assembler/linker,
make, GCC, Bash or Python is required by the target recipe. 

The separate kernel handoff package loads this ELF at physical 1MiB and
constructs Linux's legacy boot parameter block. PAE only helps after boot;
all pre-handoff allocations must remain in usable low physical RAM.

A full Hex0 seed run built this kernel and the 1GiB ext2 root image, packed
it into a 168,027,703-byte gzip stream, booted Linux 2.4 PAE with usable RAM
above 4GiB, then compiled GNU make and passed its original output checksum.
The temporary make-only CI jump exposed Linux's refusal to overwrite a
running /init executable. The first boot now runs /usr/bin/kaem directly;
generated later Bash jumps already replace /init by rename.

The bridge kernel includes a restricted UP kexec reboot command (0x45584543),
using the early kexec segment ABI. The trampoline derives from Eric W.
Biederman's [Linux 2.5.44 patch](https://www.xmission.com/~ebiederm/files/kexec/kexec-2.5.44.diff).
The staging allocator avoids all destination ranges and uses a linear page
list; segment bounds, overlaps, user pointers and E820 RAM are checked.
After disabling paging the trampoline clears CR4, including PAE, before
entering the new boot stub.
