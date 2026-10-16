# Batch 2 source inputs

Each source archive has its own lock with one archive name, HTTPS URL and
original SHA512 pin. Batch 2 records were copied from the reviewed experimental
`linux24-gentoo/system/sources.lock`; later records follow their batch-specific
source review below. Digests are not generated from compiler outputs or replaced
with hashes of an arbitrary new download. The runtime verifies archives before
extraction and on every resumption, even when a completed build receipt exists.
There are no stage archives, binary packages, GCC `download_prerequisites`,
moving revisions or revision override switches.

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
Batch 3 adds the kernel signature/key verification described below.

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

# Batch 3 source signature and retained public key

`sources/linux-signature.lock` retains the reference SHA512 pin for
`linux-6.12.111.tar.sign`, signed over the decompressed tar stream. The guest
retains the signature beside the source archive; it does not need GnuPG early.
Before a VM cold boot, the host checks both pins and verifies that detached
signature with `keys/kernel-release.gpg` and requires primary fingerprint
`647F28654894E3BD457199BE38DBBDC86092693E`. GPG uses an isolated temporary home,
no default keyring/ownertrust, no keyserver and no private key. Verification is
repeated when extracting a kernel from the workspace. Hardware can run the
same check with `kernel_source.py --build-root MOUNT/gentoo-bootstrap`.

The fingerprint is independently published by
[kernel.org's signature guide](https://www.kernel.org/signature.html) and
[key analysis page](https://www.kernel.org/doc/wot/gregkh.html). The complete
public key was retrieved by that fingerprint from the Ubuntu public keyserver,
then exported with `export-minimal` to retain its self-signed identities and
remove unrelated third-party certifications. The checked-in public key successfully verified the pinned
Linux 6.12.111 archive/signature during batch 3 checks. This establishes source
signature verification, not signed VM boot or hardware acceptance.


# Batch 5 Gentoo and Portage source inputs

Batch 5 adds four independent SHA512 locks. The GitHub archive contains
Gentoo repository commit `5548522e2c2fa10a4c009348e9ba84c44783f47d`; it is
extracted as the immutable package repository for the seed. Python, bzip2 and
Portage sources are checked against the versions selected for that repository.
Their archive digests were matched to the experimental source locks and the
readable local source cache. The extracted repository's 18,202 Manifest records
and complete file/directory/symlink tree are fingerprinted in the workspace.

| Source | Version | Use | Provenance of pin |
| --- | --- | --- | --- |
| Gentoo repository | commit `5548522e2c2fa10a4c009348e9ba84c44783f47d` | Fixed ebuilds, metadata and amd64 desktop profile | Reference seed lock; GitHub commit archive SHA512 |
| CPython | 3.12.15 | Source-built bootstrap-host Portage runtime | Pinned Gentoo `dev-lang/python` Manifest SHA512 |
| bzip2 | 1.0.8 | Source-built static library for Python's `bz2` module | Pinned Gentoo `app-arch/bzip2` Manifest SHA512 |
| Portage | 3.0.82.2 | Source-only package manager | Reference seed lock and matching pinned repository ebuild |

OpenSSL and zlib are development inputs supplied by the original bootstrap,
not fetched as batch-5 archives. Their headers and linker-resolved libraries
are hashed into `state/host-python.inputs`, along with the host triple and
completed host-toolchain inputs. A changed dependency fails the host-Python
stage fingerprint. Portage source integrity also has a disposable digestcheck
probe: valid bytes pass while corrupt bytes and missing or checksum-free entries
fail, without modifying the Manifest fixture.

# Batch 6 native-tool sources

The native x86_64 root is seeded from the already verified GNU sysroot. Bash
and coreutils are separately source-locked using the pins carried by the
reviewed experimental LFS 13.1-systemd native-host sequence; both SHA512 pins
were compared with the matching entries in the locally available pinned
Gentoo repository Manifests. Native binutils
and GCC reuse the batch-2 source locks, so this stage does not create a second
compiler source lineage. The compiler recipe selects the Gentoo multilib
library paths and its output checks cover ELF32 and ELF64.

The batch-6 native tool floor adds independently locked GNU m4, ncurses,
diffutils, File, findutils, gawk, grep, gzip, make, patch, sed, tar, and xz sources.
Their SHA512 values come from the reviewed experimental source locks. GNU
make's lzip-compressed release is handled by a separately pinned host lzip
build; no system package manager is used to unpack or build these tools.
