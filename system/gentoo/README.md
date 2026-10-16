# Gentoo continuation — batches 1–2

## Prerequisites and ownership

Host commands run from the repository root, using Python 3.11 or newer (stdlib
`tomllib`). Guest commands run as root in the completed live-bootstrap Linux
build environment. The guest needs Bash, GNU coreutils/findutils, awk, sed,
util-linux (`blkid`, `mount`, `flock`, `blockdev`, and `lsblk` for hardware
identification), a mounted `/proc`, and, for hardware, `/sys` and persistent
`/dev/disk/by-id` links. Downloads use wget and pinned SHA512 digests for this
batch's sources; the helper also accepts SHA256 pins.
TOML parsing remains host-only. Source builds now use the completed bootstrap's
GCC/G++, GMP/MPFR/MPC and zlib static libraries, make, tar and decompressors,
Bison, Flex, Perl, Python 3, and autotools (including autoreconf for argp).
The preflight compiles and runs host C/C++ prerequisite probes before any
cross build. It records compiler/component and prerequisite header/archive
checksums, and rejects an unexpected host-toolchain change on resumption.

The workspace must already be writable ext4 with the configured label and must
be visible to the guest. This batch does not create disks or filesystems. It
mounts the selected existing workspace at `/build` and builds only under
`/build/gentoo-bootstrap`. It checks an existing `/build` mount before writing
build data and refuses a different device or a read-only/non-ext4 mount.
Provision the workspace separately for these standalone checks. Automatic VM
image preparation arrives in batch 3; original-bootstrap integration is batch
14. No new `rootfs.py` arguments are available yet.

The host owns TOML parsing, execution-mode selection, input copying, and future
VM resources. `--mode vm|hardware` is an explicit input to the standalone bundle
preparer. Final integration will derive it from `--qemu` or `--bare-metal`.
The existing `--cores`, `--qemu-ram`, and `--target-size` remain responsible for
VM CPU/RAM and original bootstrap capacity. TOML cannot override those switches.
The launcher will record the actual QEMU invocation and image paths in batch 3.

Passwords, passphrases, private keys, and tokens do not belong in this file or
package policies. Account passwords will be prompted during installation.
`allow_weak_passwords` defaults to false. This batch has no partitioning,
formatting, LUKS initialization, firmware writes, or installation commands.

## VM command sequence (current batch)

1. **Host:** copy `system/gentoo/config.example.toml` to your own TOML file and
   edit the account, locale and resource choices. Prepare a fresh output path:

   ```sh
   python3 system/gentoo/prepare.py --config my-gentoo.toml \
     --mode vm --output /tmp/gentoo-inputs
   ```

2. **Host/operator:** copy the complete `/tmp/gentoo-inputs` directory to the
   running bootstrap guest, for example as `/tmp/gentoo-inputs`. Attach the
   prepared workspace. Transport and disk preparation are currently manual.

3. **Guest:** establish `/proc` if needed, then run:

   ```sh
   bash /tmp/gentoo-inputs/gentoo.sh build
   ```

   The driver validates/mounts the workspace, takes a process-bound lock, saves
   inputs and logs, then builds the numbered cross-toolchain and GNU sysroot
   stages. It stops after verifying both ABI link probes. No reboot is
   required yet; the continuation kernel and shutdown boundary arrive in batch 3.

4. **Guest, including after a reboot:** make the same workspace available and
   mount `/proc` if needed. Read the selected durable input version and resume:

   ```sh
   mkdir -p /build
   mount -t ext4 -L GENTOO_BUILD /build  # omit if already mounted
   version=$(cat /build/gentoo-bootstrap/current-inputs)
   d=/build/gentoo-bootstrap/inputs/$version
   bash "$d/gentoo.sh" status
   bash "$d/gentoo.sh" build
   ```

   Substitute your configured label. These commands no longer need `/tmp`,
   `/steps`, the source checkout, or the original installation medium.

## T440p command sequence (current batch)

1. **Host:** create your TOML as above. Fill `hardware_disks` with observed
   whole-disk by-id paths, serials and capacity bounds for root, bootstrap boot
   medium, and extra disk. Optional root/extra model and WWN constraints can
   also be supplied. Root, boot and extra identities must be distinct. The
   existing workspace must live on the configured extra disk.

2. **Host:** prepare and transfer a hardware bundle:

   ```sh
   python3 system/gentoo/prepare.py --config my-gentoo.toml \
     --mode hardware --output /tmp/gentoo-hardware-inputs
   ```

3. **T440p bootstrap environment:** with `/proc`, `/sys`, device links and the
   workspace available, run:

   ```sh
   bash /tmp/gentoo-hardware-inputs/gentoo.sh build
   ```

   The driver checks workspace parent disk by-id, serial, capacity, and any
   configured model/WWN before mounting it. Root and boot disks are not opened
   for writes by this batch; their operational identity checks will accompany
   the later storage commands. The build order and final sysroot checks are
   the same as in the VM. No hardware firmware operation is needed.

4. **T440p after interruption/reboot:** use step 4 of the VM sequence with your
   workspace label. No firmware, GRUB, signing, or token operation is involved
   in this batch. The later hardware signed-boot path will be separate from
   the unsigned VM direct-kernel path.

The later series will add an explicit modern-kernel shutdown/cold-boot boundary,
then C++ runtimes, ABI execution checks, Portage and encrypted installation. Installed-kernel boot,
workspace migration, desktop completion and hardware acceptance will each gain
commands when their implementation is reviewable. No automatic reboot is used.

## Configuration schema

`config.example.toml` is the complete schema and defaults. Unknown sections or
keys fail validation, including removed `build.profile`, `[vm]`, `[vm_disks]`,
VM CPU/RAM fields, and `workspace.scratch_uuid`. Partial TOML files inherit all
omitted defaults. Types are exact (a Boolean is not an integer). Numeric values
must be positive and finite; Portage load average accepts a float. Control
characters are forbidden in all strings.

| Section | Meaning and validation |
| --- | --- |
| `build` | `source_jobs`: make parallelism for source builds before Portage. |
| `portage` | `jobs`: emerge concurrent packages; `load_average`: emerge load limit; `make_jobs`: MAKEOPTS `-j`. Independent settings, applied when Portage is introduced. |
| `system` | Separate login account, hostname/FQDN, locale, keymap, timezone, weak-password policy and VG name. Names are constrained; path-like values cannot traverse parents. |
| `layout` | Root, swap, snapshot reserve and minimum home sizes, in GiB. Home will receive remaining allocatable capacity. Reserve remains unallocated for generations. |
| `workspace` | Filesystem label, optional existing UUID, network interface. Label discovery must produce exactly one device. Scratch UUIDs will be discovered/generated by host preparation. |
| `images` | Workspace 128, scratch 128, root 500 and extra 128 GiB defaults. Planned root capacity is max(root image size, sum(layout) + 2 GiB metadata allowance). This batch only calculates it. |
| `hardware_disks` | Whole-disk by-id paths, serials, optional model/WWN, inclusive capacity ranges in bytes. Identities required only for hardware; all ranges are checked for consistency. |
| `packages` | Optional base/desktop lists, USE/license/keyword/mask policies and firmware selection. Relative paths resolve beside the operator's TOML, are checked to exist, and are copied to `policy/KEY` in the bundle. Empty values select defaults introduced by later batches. |

The preparer quotes every value with `shlex.quote`, exports only fixed variable
names, and renders package paths as bundle-relative names. Original host paths
and TOML are not needed at runtime. Generated `config.sh` uses the predictable
`GENTOO_SECTION_KEY` naming convention plus `GENTOO_MODE`.
`config.inputs.sh` omits only build/Portage concurrency settings, so operational
tuning can change without changing source/configuration fingerprints. Policies
must be included by content in the stages that consume them.

## Durable state and resumption

`prepare.py` copies its explicit `BUNDLE_FILES` list, optional policy files, two
configuration renderings, and `SHA256SUMS` into a new output directory. It never
overwrites an existing output. Each future batch must extend that list for the
runtime artifacts it introduces. SHA256 checks verify content integrity; the
bundle must come from your reviewed checkout.

The driver verifies the manifest before use, then copies only listed inputs to
`inputs/SHA256_OF_MANIFEST/` on the workspace. It verifies the copy, flushes it,
and publishes the directory by rename. Older snapshots are preserved.
`current-inputs` is replaced atomically only after verification. Incomplete
`.pending.*` directories can remain after interruption; they are never resumed
as valid snapshots and no completed snapshot is overwritten.

Each run creates `logs/run-UTC_TIMESTAMP.UNIQUE`, captures both output streams,
and waits for the logger before exiting. Early failures before the workspace
is checked are reported to the caller's terminal. State lives in `state/`.
The input milestone has a receipt per input snapshot, so adding later scripts
or changing concurrency creates a new snapshot without invalidating compiler
receipts. `status` lists receipt paths; `build` verifies outputs while resuming.
A checksum of the entire snapshot is never a compiler-stage fingerprint.

The shared stage helper takes explicit recipe/source/config/prerequisite inputs.
Paths used to hash recipes must be stable relative paths from the snapshot,
not its content-addressed absolute directory. A compiler stage includes its
recipe, relevant helper files, selected source locks and prerequisite receipts.
Include semantic configuration and consumed
policy content only when that stage actually consumes them. These initial
compiler recipes use no account/storage/package choices; their only TOML input
is operational source concurrency, which is excluded from fingerprints.
Documentation and later stages stay out of that list. A changed fingerprint or failed output
check stops for inspection rather than silently mixing incompatible builds.

Before starting, each stage flushes and atomically records a `.inputs`
fingerprint. Interrupted work can resume only with those same inputs; a recipe
edit cannot silently reuse a half-configured Makefile. A stage runs sequential
commands under `errexit`, verifies its actual outputs, then flushes and renames
a `.done` receipt atomically. Failed stages have no completion receipt.
Out-of-tree builds resume with make; partial installations are repeated into
the same workspace. A later stage must supply both a repeatable action and a
meaningful output verifier. There is no legacy experimental receipt migration.

The driver holds `flock` on a stable inode for its lifetime and its build
children. Kernel process cleanup releases it after the last holder exits;
no stale PID directory or reboot is needed. The lock file itself is never
unlinked. A still-running child retains the lock after its parent dies, so
inspect/stop that process before resuming. Concurrent drivers fail immediately.

Source helpers accept pinned SHA256 or SHA512, verify cached archives on every
use, download into a unique partial file, verify before publication, and refuse
a corrupted cache instead of replacing it silently. Single-source locks keep
future downloads out of earlier-stage fingerprints. Extraction publishes a
complete tree by rename and records its exact lock entry in `.source-pin`.
Interrupted `.unpack.*` directories are ignored; a tree with a missing/mismatched
pin is rejected. Argp generates its configure script within its extracted tree.
See [SOURCES.md](SOURCES.md) for versions, provenance, and source-check evidence.

## Checks and acceptance boundary

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_gentoo*.py' -v
for script in system/gentoo/gentoo.sh system/gentoo/lib/*.sh system/gentoo/stages/*.sh; do
  bash -n "$script" || exit
done
```

Tests use disposable ordinary directories: configuration rejection and shell
injection, relative policy copying, reproducible manifests, semantic concurrency
handling, durable snapshot survival/corruption, receipt output verification and
changed-input refusal, failed-stage resumption, lock contention/SIGKILL release,
source-cache integrity, and missing/ambiguous workspace rejection. They do not
exercise a real guest mount or a complete bootstrap. Full VM replay remains an
acceptance requirement after final integration; hardware acceptance is separate.

## Batch 2 build stages and acceptance

`build` now continues after the durable-input milestone. Prepare a **new** host
bundle as in the command sequence and transfer it to the guest; an older saved
batch-1 snapshot intentionally still runs its original batch-1 code. Start the
new bundle once to select it as `current-inputs`. Previously saved inputs and
logs remain on disk. A batch-1 workspace with no compiler outputs can be reused;
experimental workspaces and their receipts are not imported.

| Stage | Commands/transition | Verified outputs |
| --- | --- | --- |
| 10 | Check original source-built host compiler, libraries and build tools | Host C library and C++ compile/run probes; stable host input identity |
| 11 | Configure/make/install binutils for `x86_64-elf` | Real x86_64 assembly and relocatable link |
| 12 | Configure C-only bare GCC without target headers or shared runtimes; `all-gcc all-target-libgcc` | Exact compiler version/triple, ELF64 object and libgcc archive |
| 13 | Source-build argp, then static libelf, then feature-limited rsync | Host compile/link/run checks for argp/libelf and a real rsync copy |
| 14 | Linux `headers_install` with explicit x86_64 architecture, separate output tree and source-built rsync | Pinned Linux version and x86 32/64 UAPI syscall headers |
| 15 | Build Linux-target binutils with explicit `=/...` sysroot search paths; install both triplet aliases | i386 emulation, all six rooted library directories, usable tool aliases |
| 16 | Build/install multilib C front end with `all-gcc`; defer libgcc until startup objects exist | GCC 16.2.0, correct Linux triple, reported m32/m64 multilibs and real ELF32/ELF64 objects |
| 17 | Separate i686 then x86_64 glibc configure trees; `install-headers`, upstream generic stubs template, `csu/subdir_lib` and temporary empty libc link targets | Correct-class real startup objects and audit copies of temporary source-built libc DSOs |
| 18 | Header-aware shared/multilib C compiler, then both libgcc ABIs | Every archive member and shared runtime has the expected ELF class/machine; no-libc division link probes |
| 19 | Finish real i686 glibc, then x86_64 glibc, using their existing build trees | ABI-specific stubs, libc linker scripts, ELF libc/loader files and startup objects; canonical i386 loader symlink |
| 20 | Refresh C libgcc against completed glibc, then compile/link stdio programs for both ABIs | Compiler sysroot, both runtimes, libc dependencies and `/lib/ld-linux.so.2` or `/lib64/ld-linux-x86-64.so.2` interpreter |

The implementation preserves the reference's cycle-breaking sequence. Glibc
2.44 has no `install-bootstrap-headers` target, so the temporary `gnu/stubs.h`
is the exact upstream generic template; no `stubs-32.h` or `stubs-64.h` is
fabricated. Real glibc installations replace the temporary libc DSOs and
headers. Earlier-stage verifiers accept those intended replacements while
still checking their original audit outputs and actual startup-object ABIs.

The libgcc wrapper chooses `/usr/lib32` or `/usr/lib64` from each invocation's
`-m32`/`-m64`, supplies its startup-object `-B` path to GCC's own xgcc, and is
bound into generated recursive libgcc Makefiles. This prevents either ABI from
accidentally using the other's startup files. External probes use the same
ABI-specific `-B` paths. C++ runtimes and further intermediate-compiler search
fixes belong to batch 4 after the modern-kernel boot.

Glibc's per-file `CFLAGS-libc_sigaction.c` disables compiler-generated
asynchronous unwind tables while retaining the handwritten signal-restorer
CFI. Host elfutils builds only its internal support and static libelf archive;
the fts/obstack configure cache answers apply only to those selected targets.
The build never installs a host shared libelf. The scripts do not run package
test suites; compile/link checks here are explicit acceptance probes.

Everything is below `/build/gentoo-bootstrap`: `toolchain/` contains the bare
compiler; `linux-target-binutils/` and `linux-target-tools/bin/` hold Linux tools
and aliases; `linux-toolchain/` holds the C compiler/runtimes; `host-tools/`
holds argp, static libelf and rsync; `sysroot-x86_64/` holds the GNU headers,
loaders and libraries. `build/` retains resumable object trees and `probes/`.
`distfiles/` is the verified source cache. Source trees stay in `sources/`.
No installed root or target disk is modified.

`build.source_jobs` controls source make parallelism. None of the three Portage
settings is consumed before Portage exists. Changing concurrency creates a new
input snapshot but preserves compiler fingerprints; locale/account/layout
changes also do not alter these compiler recipes because they are not consumed
here. The next batches will fingerprint those choices where they matter.

Both VM and hardware runs may still be using the original 32-bit kernel at
this point. The acceptance probes compile/link ELF64 and ELF32 but execute
neither target ABI. Successful linking is not a runtime-execution claim;
both real execution checks follow the x86_64 kernel handoff in batch 4. No
kernel is built and no shutdown/reboot is requested in batch 2.

Batch 2 verification includes disposable real ELF objects and mixed-ABI
archive rejection, rooted-linker-path rejection, SHA512/cache checks, interrupted
extraction and changed-recipe refusal, bundle completeness and dependency order.
A native smoke build exercises pinned rsync and Linux UAPI header export. It
is not a fresh musl-host compiler/glibc rebuild or a guest boot. Complete VM
replay remains required after original-bootstrap integration.
