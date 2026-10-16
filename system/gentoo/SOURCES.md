# Batch 2 source inputs

Every `sources/*.lock` contains one archive name, HTTPS URL and original SHA512
pin. These records are copied unchanged from the reviewed experimental
`linux24-gentoo/system/sources.lock`; digests are not generated from compiler
outputs or replaced with hashes of an arbitrary new download. The runtime
verifies archives before extraction and on every resumption, even when a
completed build receipt exists. There are no stage archives, binary packages,
GCC `download_prerequisites`, moving revisions or revision override switches.

| Source | Version | Use | Provenance of pin |
| --- | --- | --- | --- |
| GNU binutils | 2.46.1 | Bare-ELF and Linux/GNU cross tools | Pinned Gentoo `sys-devel/binutils/Manifest` SHA512 |
| GCC | 16.2.0 | Bare C compiler and Linux/GNU multilib C compiler/libgcc | Pinned Gentoo `sys-devel/gcc/Manifest` SHA512 |
| glibc | 2.44 | Real ELF32/ELF64 headers, startup objects, libc and loaders | Pinned Gentoo `sys-libs/glibc/Manifest` SHA512 |
| Linux | 6.12.111 | x86 UAPI headers; kernel build follows in batch 3 | Reference kernel.org archive pin |
| argp-standalone | 1.5.0 | Host musl's missing argp implementation | Reference GitHub tag-archive pin |
| elfutils | 0.196 | Static host libelf for kernel objtool | Reference official sourceware release pin |
| rsync | 3.5.1 | Host tool required by Linux headers_install | Reference official rsync release pin |

The Gentoo repository commit used for the first three records is
`5548522e2c2fa10a4c009348e9ba84c44783f47d`. All three pins were compared with
that locally available tree's unchanged Manifests during batch 2 verification.
Those three archives were not present in the readable host cache, so this check
establishes agreement with the pinned repository, not a new compiler build.
The remaining four readable cached archives were hashed and matched their
SHA512 locks. This does not claim fresh upstream signature verification.
Kernel signature/key handling will accompany the actual kernel in batch 3.

Native-host source smoke verification built rsync with the shipped configure
flags and exercised copying. Linux UAPI export uses an out-of-tree directory
and the source-built rsync. The pinned Linux Makefile explicitly makes
`headers`/`headers_%` independent of `.config`; kernel configuration and its
Kconfig build are deferred to the kernel batch.

The release archives' usual generated configure scripts are used, as in the
working reference. Argp's tag archive generates its configure script with the
existing source-built host autotools. Host GMP, MPFR, MPC and zlib are supplied
by the completed original bootstrap and bound to the host-toolchain input
record; they are not downloaded again here.
