# Gentoo continuation — batches 1–11

## Prerequisites and ownership

Host commands run from the repository root with Python 3.11+. VM operations
need QEMU (`qemu-system-x86_64`, `qemu-img`), util-linux (`blkid`, `losetup`,
`mount`, `umount`), e2fsprogs (`mkfs.ext4`, `dumpe2fs`), psmisc `fuser`, xz and
GnuPG `gpgv`. Use root for lifecycle operations so open-image checks can inspect
other users' processes and continuation can mount the workspace read-only.
`socat` is useful for the serial console. The original command selects KVM or
TCG and any CPU model, display and networking options; the helper records them.
Sparse images need real host free space as their contents grow.

Guest commands run as root in the completed live-bootstrap Linux environment.
They need Bash, GNU coreutils/findutils, awk, sed and util-linux, including
`flock`, `mountpoint`, `blkid` and `mount`. Hardware identification also needs
`lsblk`, `blockdev`, `/sys` and persistent `/dev/disk/by-id` links. Source builds
use the original source-built GCC/G++, GMP/MPFR/MPC/zlib static libraries,
OpenSSL and zlib development headers/libraries for Python's `ssl` and `zlib`
modules, make, tar/decompressors, wget, Bison, Flex, Perl, Python and autotools. No guest
TOML parser is used. The preflight compiles/runs host prerequisite probes and
binds the host compiler, component, header and archive checksums to this run.

The host owns TOML parsing, execution-mode selection, input copying, VM resources
and image paths. The guest owns source builds, receipts and the durable handoff.
`--mode vm|hardware` remains explicit in `prepare.py` until batch 14 derives it
from `rootfs.py --qemu|--bare-metal`. Existing `--cores`, `--qemu-ram` and
`--target-size` continue to select VM resources and original-bootstrap capacity.
TOML supplies independent source/Portage concurrency, account, locale, layout
and package choices. It cannot override those original-bootstrap switches.

`vm.py prepare --apply` creates ONLY four regular image files owned by the run:
128 GiB workspace, 128 GiB scratch, 500 GiB installation root and 128 GiB extra
by default. Root capacity grows to fit the configured layout when creating a
new image. The workspace and scratch get ext4 filesystems; root and extra are
empty qcow2 containers awaiting explicit storage initialization in batch 10.
There is no separate synthetic boot disk: the recorded original bootstrap disk
is the boot medium. Existing images are validated and reused without resizing,
truncating or reformatting. No host physical disk is accepted.

Builds write under `/build/gentoo-bootstrap` only after proving the workspace
mount. Hardware requires an existing writable ext4 workspace on the configured
extra disk; the build never creates that filesystem. This batch has no physical
partitioning, LUKS initialization, root installation, signing or firmware write.
Passwords, passphrases, private keys and tokens do not belong in TOML/policies.

Original-bootstrap integration is still deferred to batch 14: no new
`rootfs.py` arguments or `/after` hook are enabled yet. Transporting the input
bundle into an already booted build environment remains manual during review.

## VM command sequence

1. **Host:** copy/edit `config.example.toml` and prepare a new input bundle:

   ```sh
   python3 system/gentoo/prepare.py --config my-gentoo.toml \
     --mode vm --output /tmp/gentoo-inputs-v7
   ```

2. **Host, previous VM stopped:** put the original rootfs.py QEMU argument array
   in `initial-qemu.json`, once. Use actual absolute image/kernel paths and the
   CPU/RAM selected by your original invocation. See the example below. Prepare
   a private run directory (first without `--apply` to inspect the plan):

   ```sh
   sudo python3 system/gentoo/vm.py prepare --run-dir OUT/gentoo-run \
     --config my-gentoo.toml --qemu-command initial-qemu.json --apply
   sudo python3 system/gentoo/vm.py start --run-dir OUT/gentoo-run --apply
   ```

3. **Guest:** after the original bootstrap completes, transfer the complete
   `/tmp/gentoo-inputs-v7` directory into the guest, preserving all files. Run:

   ```sh
   bash /tmp/gentoo-inputs-v7/gentoo.sh build
   ```

   The driver builds/resumes batches 1–7 and prints **SHUTDOWN MILESTONE** after
   verifying the kernel, matching modules, inputs and saved post-reboot scripts.

4. **Guest console, explicit shutdown:** substitute the exact handoff path
   printed by the driver. Do not exit the PID-1 Bash shell:

   ```sh
   cd /
   bash /build/gentoo-bootstrap/handoff/INPUT_DIGEST/shutdown.sh
   ```

   Wait for the host's foreground QEMU command to return. No build command or
   error trap automatically shuts down, restarts or kills the VM.

5. **Host:** extract/verify the saved kernel and cold-boot it using the recorded
   resources and actual image paths, without repeating configuration settings:

   ```sh
   sudo python3 system/gentoo/vm.py continue --run-dir OUT/gentoo-run --apply
   ```

6. **Guest:** mount the workspace and use its recorded handoff:

   ```sh
   mkdir -p /proc /sys /build
   mountpoint -q /proc || mount -t proc proc /proc
   mountpoint -q /sys || mount -t sysfs sysfs /sys
   mount -t ext4 -L GENTOO_BUILD /build  # omit if already mounted
   version=$(cat /build/gentoo-bootstrap/current-handoff)
   bash /build/gentoo-bootstrap/handoff/$version/resume.sh
   ```

   Substitute your configured label. The resume script mounts devpts/tmpfs as
   needed and runs the exact saved input snapshot. The driver rechecks outputs
   and verifies Linux 6.12.111, x86_64, a boot ID different from the kernel's build boot, and the GNU build ID
   from `/sys/kernel/notes` matching the saved vmlinux build ID. It then completes
   the multilib C/C++ toolchain and executes both ABIs (batch 4), then builds
   the pinned repository, host Python and Portage configuration (batch 5),
   constructs the native compiler and runtimes (batches 6–7), bootstraps Gentoo's
   multilib `@system` (batch 8), and installs the encrypted-base runtime policy
   (batch 9). Package emerge begins in batch 8. After an interruption, repeat
   steps 5–6; completed stages keep their receipts.

## T440p command sequence

1. **Host:** fill `hardware_disks` with observed whole-disk by-id paths, serials
   and capacity bounds; optionally constrain root/extra model and WWN. Root,
   boot medium and extra disk must differ. Prepare/transfer a hardware bundle:

   ```sh
   python3 system/gentoo/prepare.py --config my-gentoo.toml \
     --mode hardware --output /tmp/gentoo-hardware-inputs-v7
   ```

2. **T440p bootstrap environment:** with `/proc`, `/sys`, device links and the
   existing workspace available, run:

   ```sh
   bash /tmp/gentoo-hardware-inputs-v7/gentoo.sh build
   ```

   The driver checks the workspace disk's by-id, serial, capacity and optional
   model/WWN before mounting it. It stops at the same shutdown milestone.

3. **T440p console:** run the printed handoff's `shutdown.sh` from `/`, as in VM
   step 4. On a trusted host, mount the now-offline workspace and verify its
   source signature before booting the new kernel:

   ```sh
   python3 system/gentoo/kernel_source.py --build-root MOUNT/gentoo-bootstrap
   ```

4. **T440p existing boot environment:** use the currently working bootloader's
   existing manual Linux-boot procedure to load the saved
   `gentoo-bootstrap/artifacts/kernel-6.12.111/bzImage`. If it cannot read the
   workspace, copy the verified image to the existing boot medium. Pass
   `root=UUID=ACTUAL_BOOTSTRAP_ROOT_UUID rootfstype=ext4 init=/bin/bash rw rootwait`
   and the usual console arguments. This boots the existing bootstrap root,
   not the future encrypted installation. An already enforced signing policy
   must use its existing authorized signing procedure. This batch supplies no
   policy bypass, new firmware, signed-generation manager or hardware boot edit.

5. **T440p new kernel:** follow VM step 6 with the workspace's configured label.
   Wired e1000e, essential storage, console/input and USB drivers are built in;
   Wi-Fi firmware is introduced with the runtime policy in batch 9. Validate
   actual T440p devices separately; VM support does not establish hardware
   acceptance. Batch 11 installs the signed generation manager, but it does not
   modify existing firmware. Follow [SIGNED-GENERATIONS.md](SIGNED-GENERATIONS.md)
   for the hardware-specific key setup and acceptance boundary.

## Recorded VM invocation and image ownership

Example `initial-qemu.json` for an original run WITHOUT `--kernel` (replace the
paths and resource values with the actual original arguments):

```json
["qemu-system-x86_64", "-enable-kvm", "-m", "49152M", "-smp", "18",
 "-drive", "file=/ABS/BOOTSTRAP/init.img,format=raw",
 "-drive", "file=/ABS/BOOTSTRAP/external.img,format=raw",
 "-machine", "kernel-irqchip=split", "-nic", "user,ipv6=off,model=e1000",
 "-no-reboot", "-nographic"]
```

Omit the second drive if the original run had no external image. With
`--kernel`, retain the actual `-kernel`, `-initrd` if present, and `-append`
arguments; the original root selector is retained for continuation. Otherwise
it defaults to the reference's `/dev/sda1`. Relative paths resolve once against
`--qemu-cwd` (default: current directory). The normal rootfs.py argument forms
are supported; ambiguous drive/index syntax, firmware and monitor/serial
customizations fail explicitly. Initial disks are raw or standalone qcow2 IDE
images; external backing chains and host block devices are rejected.

`gentoo-vm.json` records the original argument array/cwd, CPU/RAM, normalized
launch options, actual absolute paths, capacities, generated serials and real
filesystem UUIDs. `start` retains the original boot inputs and adds the four
managed disks using the reference-compatible USB EHCI storage transport.
`continue` replaces the original kernel/initramfs/append with the saved modern
kernel and an explicit `init=/bin/bash` bootstrap-root command line. The VM
has no Libreboot, custom ROM, signing or hardened-GRUB dependency.

Original disks are never created or resized by this helper. `--target-size`
still belongs to the original bootstrap. For an existing manual workspace,
place the image at `RUN/images/workspace.img` while offline; prepare validates
its capacity/type/label, checks any configured workspace UUID, and records its
actual UUID. The same validation/adoption applies to an existing scratch
image without asking for a scratch UUID. Subsequent preparations reuse the
recorded identities. Existing run metadata/image sizing cannot be changed
silently; retain that run and use a new run directory for a different layout.

Image preparation records identities before creating missing images. New images
are initialized under unique `.pending-*` names, verified and fsynced, then
published exclusively without replacing a target. An interruption can leave an
unpublished pending file; inspect/remove it separately. Resume validates existing
final images and creates only missing ones. The helper refuses loop-attached or
open images, duplicate inodes/roles, symlinks, format/backing/size mismatches,
filesystem identity mismatches and unclean workspace/scratch filesystems. It
does not run repair operations. A dirty filesystem requires offline inspection
and deliberate repair before reuse.

All lifecycle commands default to a plan except `status`. `--apply` authorizes
creation/launch; a lifecycle `flock` serializes operations and remains held while
QEMU runs. `status --run-dir OUT/gentoo-run` prints the record and lock state.
A surviving QEMU is still caught by open-image checks if its parent dies. Stale
socket files require inspecting their owner before manual removal. The helper
never stops an unrelated VM or force-quits a running guest.

The helper prints a `socat` serial command, records stdout/stderr from the guest
in `console.log` through QEMU's chardev, and saves the exact last executed argv
in `last-launch.json`. A serial client may detach without ending QEMU/logging.
The original 32-bit kernel can use less memory/address space than the recorded
VM RAM; cold booting x86_64 makes the larger resource allocation usable without
changing it in TOML or resupplying it to the continuation command.

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
for script in system/gentoo/gentoo.sh system/gentoo/lib/*.sh system/gentoo/stages/*.sh \
    system/gentoo/post-reboot.sh system/gentoo/shutdown.sh; do
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

## Batch 3 kernel, modules and durable handoff

Stages 30–32 extend the bundle list without changing batch-2 compiler recipes,
locks or shared toolchain/state helpers. Adding this batch does not invalidate
completed compiler receipts. Transfer a new v3 bundle and enter through that
copy once; an old saved input snapshot intentionally still runs its old code.

Stage 30 uses the pinned Linux source in a separate `build/linux-continuation`
output directory. It merges `x86_64_defconfig` with the shipped fragment, runs
noninteractive `olddefconfig`, and requires every requested y/m/disabled/string
setting to survive resolution. It builds `bzImage` with the Linux/GNU C cross
compiler and original host GCC plus static libelf. The artifact records its
configuration, System.map, exact release, GNU build ID, build boot ID and a kernel-only
checksum manifest. Boot-protocol magic and the x86_64 flag are checked.

Stage 31 builds and installs modules from that same configuration and verifies
all `.ko` files as ELF64/x86_64 with matching vermagic. Essential root/storage,
USB, console, IA32 execution and T440p drivers are built in; coretemp supplies
an explicit loadable module. Source/build symlinks are removed from installed
modules. The final relative-path manifest covers the kernel, configuration and
module files. Dependency indexes are deferred until source-built depmod is
available during root installation; no external host depmod is imported.

Stage 32 stores `handoff/INPUT_DIGEST/{handoff.json,resume.sh,shutdown.sh}`.
The JSON binds the input digest, filesystem UUID, build boot ID, release,
artifact manifest and kernel/module receipt hashes. `current-handoff` is
published atomically only after those files verify. Every post-reboot command,
configuration and build input is on the workspace before shutdown, independent
of `/steps`, temporary transfer directories and the install medium. An
interruption between handoff publication and its receipt compares/reuses the
same content rather than overwriting a different transaction.

On continuation, the host requires a clean, offline ext4 workspace, mounts it
with `ro,noload,nosuid,nodev,noexec`, verifies the input and kernel/module
manifests, stage receipts and handoff identities, verifies the upstream source
signature with the retained public key, and stages the kernel under
`kernels/ARTIFACT_MANIFEST_SHA256/INPUT_DIGEST/`. It always unmounts on an extraction failure.
The staged image is checked again against the workspace before use. The saved
configuration/manifest, handoff and source-signature receipt accompany it.

`shutdown.sh` is explicit and bootstrap-specific. It relocates itself to `/run`
so Bash's open script cannot keep `/build` busy, blocks new drivers with a
sentinel, and refuses a held driver lock. It syncs, closes the workspace lock,
unmounts non-root ext filesystems, remounts root read-only, checks no writable
disk filesystem remains, then requests SysRq poweroff. Any failed unmount or
remount stops before poweroff. If poweroff itself fails, inspect the console;
after ensuring shutdown is no longer running, remove its `/run` sentinel and
remount root read-write if resuming. There is no stale PID lock requiring a
reboot.

Batch 3 tests include real disposable ext4/qcow2 creation, unchanged image reuse,
size refusal, publication failure, open-image/loop/inode checks, record/resource
ownership, process locks, manifest/path safety, artifact/input/receipt tampering,
read-only mount cleanup and synthetic x86 boot headers. A paused-CPU TCG
smoke starts actual QEMU, queries its block devices over QMP, checks the exact
image attachments, and quits before firmware/guest execution. It runs outside
the Unix-socket restriction when required; that fixture is skipped if a
sandbox denies the connection. The pinned real kernel
source/signature and retained release key were checked with GPG. A new full
kernel/module build, actual guest shutdown/cold boot and T440p devices are not
accepted by those fixture tests. Complete fresh VM replay remains required
after final integration; hardware acceptance remains separate.


## Batch 4 multilib C/C++ completion

For a workspace already completed with batch 3, prepare and transfer the new
v4 bundle using the same mode/configuration, then invoke that new bundle's
`gentoo.sh build` once in the verified modern-kernel guest. The old saved resume
script intentionally runs its old snapshot. The new entry reuses the existing
compiler and kernel receipts, selects a new durable handoff, and continues
without another reboot if the running kernel passes the existing cold-boot
and build-ID checks. Subsequent resumptions use the newly selected handoff in
step 6. If still on the original kernel, this bundle stops at the shutdown
milestone; it cannot enter stage 40 until that handoff succeeds. VM and T440p
use the same compiler stages; neither path signs or modifies firmware here.

The existing pinned GCC 16.2.0 source is verified again; no new archive/version
or binary toolchain is introduced. The C++ build uses a separate
`build/gcc-linux-cxx` object tree and the existing GNU sysroot. Its host compilers
remain the original source-built `/usr/bin/gcc` and `/usr/bin/g++`. The modern
kernel must support IA32 execution (the batch-3 fragment enables it). Source
parallelism still comes only from `build.source_jobs`; Portage does not exist
at this milestone.

The visible order is:

1. `40-gcc-cxx`: configure C/C++ with POSIX threads and m32/m64, generate and
   bind GCC's target nm wrapper, build the front ends, and compile actual
   template-bearing ELF32/ELF64 objects with `xg++`/`cc1plus`.
2. `41-cxx-libgcc`: generate ABI-aware target compiler wrappers, bind recursive
   libgcc Makefiles, remove only the two known generated ELF64 startup symlinks,
   check each ABI's real glibc startup objects, and build/install libgcc plus
   the C/C++ drivers. Shared runtime and private libgcc search directories are
   passed explicitly to intermediate compilers; `--sysroot` alone is insufficient.
3. `42-libstdcxx`: configure the target C++ runtime after shared libgcc exists,
   correct only the generated libtool CXX tags with their ABI's complete startup
   paths and wrapper, build/install libstdc++, and inspect both ABIs' shared and
   static libstdc++ and libsupc++ archives. Upstream source trees stay unchanged.
4. `43-runtime-acceptance`: compile/link/execute C and C++ for both ABIs through
   both installed and intermediate drivers (eight executable probes). C exercises
   libc and integer division; C++ exercises threads, allocation, strings, vectors,
   iostreams and exception unwinding. Each executable's ELF class, interpreter
   and needed libraries are checked. Execution must exit zero with exact output.

Execution invokes the source-built loader under `sysroot-x86_64/lib32` or
`lib64`, with a clean environment, disabled loader cache and explicit library
paths. Every resolved disk DSO must belong to this run's sysroot or toolchain
and match the executable ABI. No GNU loader/library is copied into the bootstrap
root's `/lib`. Probe programs and objects remain in `build/probes/cxx`; the
append-only invocation log records loader resolutions and actual execution
results. The `43-runtime-acceptance.done` receipt is published only after all
checks pass and reruns all eight probes on every resume.

Each of the four stages binds its recipe/helpers and prerequisite receipt;
stage 40 also binds the GCC source lock and completed sysroot links. Interrupted
builds retain their object trees and input bindings. Changed unfinished recipes
are refused; do not remove receipts to force reuse. Changing source-job counts,
documentation or later-stage scripts does not invalidate these builds. Earlier
compiler/kernel recipes and locks remain unchanged. The batch-3 handoff recipe
now exposes its verified boot gate to the driver; its content-addressed receipt
naturally belongs to the newly selected bundle.

Focused tests cover generated nm/libgcc/libtool rules, idempotent ABI token
replacement, unexpected startup-object refusal, last-ABI-option wrapper behavior,
the pre-build boot gate, and loader isolation and execution failures. Disposable
native C/C++ programs exercise the real loader and C++ runtime, including threads
and unwinding. These fixtures use the host runtime and do not establish a full
GCC 16.2.0/glibc 2.44 guest build or accept the kernel handoff. A fresh complete
VM build/reboot through this milestone remains required in the final replay.


## Batch 5 Gentoo source snapshot and Portage host

After batch 4 passes all eight runtime probes, transfer a fresh v5 bundle with
the same mode and configuration and invoke `gentoo.sh build` in the verified
modern-kernel guest. Earlier compiler receipts remain reusable; the new input
snapshot takes a new handoff identity and proceeds without another restart.
An invocation before the modern-kernel check stops at the saved shutdown
milestone and cannot fetch or build batch-5 sources.

Stage 50 fetches or verifies the SHA-512-pinned Gentoo repository archive at
commit `5548522e2c2fa10a4c009348e9ba84c44783f47d`. It verifies the repository
identity, the pinned Portage ebuild's Python compatibility and the selected
amd64 desktop profile. It stores a sorted checksum record for every `Manifest`
under `state/gentoo-manifests-COMMIT.sha256`; a second receipt covers the full
extracted tree, including file modes and symlink targets. Repository input
lives under `sources/`; downloaded archives live in `distfiles/`. Stage 52 checks
the full tree before and after the Portage self-check. The emerge helper checks
all Manifests before and after each later package operation. A changed, missing
or added Manifest stops the driver and leaves a `.changed` diagnostic beside
the baseline.

Stage 51 first fingerprints the bootstrap OpenSSL/zlib development headers and
libraries needed by CPython's native modules. It then builds bzip2 1.0.8 and
CPython 3.12.15 from pinned sources into `host-tools/`, using the original
bootstrap's native GCC. It runs `make` and
`make install`, not Python's test suite. The acceptance check verifies the
version and imports `bz2`, `ssl`, `zlib`, `hashlib`, `json` and `urllib`; bzip2
compression must round-trip. Python's configure-time compiler wrapper corrects
only the host's musl multiarch query. Build parallelism uses
`build.source_jobs`, independently of all Portage concurrency values. The
Python and bzip2 sources and their build tree remain available for resumption.

Stage 52 verifies Portage 3.0.82.2 from its own pinned archive and checks its
ebuild against the repository snapshot. The reviewed desktop profile and
`repos.conf` point at that snapshot. `make.conf` sets `FEATURES` to disable
package tests and binary packages, disable Manifest generation, and require
strict digest checking. It leaves Portage's `--jobs`, `--load-average`, and
`MAKEOPTS=-j` connected to the three independent TOML settings. Distfiles and
temporary build data stay on the workspace. Generated configuration is
refreshed on each invocation, so concurrency can change without invalidating
completed source builds.

This first host configuration creates a `portage` group and non-login account
with GID/UID 250 in the temporary bootstrap guest if those IDs are free. An
existing conflicting account or ID causes a stop. No account is created in the
future installed root. The `emerge` helper checks repository Manifests before
and after every future Portage operation, forces source-only package selection,
and rejects binary-package or concurrency overrides. It also provides an
executable probe against Portage's strict `digestcheck`: a valid SHA-512 source passes, corrupted
bytes, missing source files and size-only entries fail, and the Manifest stays
unchanged. This probe exercises source integrity handling; it does not run an
ebuild test phase.

The stage receipts bind each source lock, builder, audit code and required
prior-stage receipt. Completed compiler fingerprints do not include these new
locks or helper files. If a source build is interrupted, resume with the same
bundle and workspace. Changed unfinished recipes are refused. Batch 5 verifies
the Portage command and source audit but does not run emerge or install packages;
the native root and native compiler are introduced in batch 6. Batch 7 adds the
native runtime libraries, Python, Perl and pinned Portage without starting an
ebuild operation.

Focused checks validate the source locks against the available pinned archives,
Manifest snapshot mutation detection, the generated concurrency policy, bundle
completeness, dependency order, Python/shell syntax and prior Gentoo regression
fixtures. They do not build Python inside the bootstrap guest or establish a
fresh end-to-end Portage guest run. The full VM replay remains part of final
acceptance.

## Batch 6 native root and compiler

After the modern-kernel handoff and Portage setup, stage 60 seeds
`native-root-x86_64/` from the verified multilib sysroot. The copy is published
by rename and tied to the sysroot receipt; an interrupted or unreceipted root
is left for inspection instead of being overlaid again. Stage 61 cross-builds
Bash 5.3 and coreutils 9.11 into that root from their individually pinned
archives. Their ELF64 outputs are run through the root's glibc loader before
the stage receipt is written. Stage 62 then builds native binutils and GCC
16.2.0 in the proven build order. Its probes compile and execute C and C++
programs as ELF32 and ELF64. Stage 63 completes the native temporary-tool
floor: m4, ncurses, diffutils, file, findutils, gawk, grep, gzip, make, patch,
sed, tar, and xz. GNU make's lzip archive is unpacked with a separately
source-built host lzip. Each target tool is run through the native root's
glibc loader before its receipt is written. The sources, build trees, and
receipts live on the workspace. Resume with the same bundle and workspace
after an interruption. The compiler build fixes Gentoo's multilib layout at
`/usr/lib32` and `/usr/lib64`; GCC bootstrap, OpenMP and non-C/C++ runtimes
remain disabled at this temporary-tool stage.

No ebuild or emerge operation is started by these stages. Source-built
`scanelf` is added before the first package operation that requires it.
The native builds have not been executed in a fresh guest; these source stages
are validated here by static order, bundle, pin and shell checks only.

## Batch 7 native runtimes and Portage

After the native compiler and utility floor, stage 70 builds zlib, bzip2,
libffi, Perl 5.42.2, OpenSSL 3.5.9 and CPython 3.12.15 inside the native root.
The source archives are SHA512 locked; libffi's pin matches its entry in the
pinned Gentoo Manifest. Per-source receipts include the recipe and installed
output digest, so a completed runtime can be verified after interruption.
Python's acceptance probe imports `_ctypes`, `bz2`, `lzma`, `ssl` and `zlib`,
which checks the actual libffi/OpenSSL/compression runtime rather than only the
interpreter version. Perl checks `Config`, `ExtUtils::MakeMaker`, `Digest::SHA`
and `IPC::Cmd`.

Stage 71 copies and hashes the pinned Gentoo repository and Portage source into
the native root, installs the no-multilib seed profile and source-only Portage
policy, then checks `emerge --version`, locale handling and Portage's bzip2
lookup. It applies the three independent Portage concurrency settings. Until
the following stage starts, distfiles and temporary build data stay on the
workspace. No
package operation runs in this batch; scanelf is added before a later ebuild
phase requires it. VM and T440p builds use the same x86_64 runtime stages.
Resume with the same input bundle and workspace after interruption. A fresh
guest replay is still required to validate the native runtime build end to end.

## Batch 8 Gentoo bootstrap and multilib `@system`

After stage 71, the continuation runs eight ordered system phases in the same
durable workspace: prepare the split-usr multilib profile and strict source
policy; build a TLS-capable Wget; seed audited dependency-cycle providers;
register the source-built glibc and Perl runtimes temporarily; build
`scanelf` and expose verified 32-bit startup objects; run Gentoo's pinned
bootstrap; merge and converge multilib `@system`; then verify both ABIs,
OpenRC, runtime imports and the remaining `@system` graph. The three Portage
settings remain independent: `portage.jobs`, `portage.load_average` and
`portage.make_jobs`. Wget and the temporary MPFR-enabled Gawk use
`build.source_jobs`.

The temporary `package.provided` entries and bootstrap USE/cycle overrides
are recorded and retired only after Gentoo installs their real providers.
Portage remains source-only, uses the pinned repository's Manifests without
changing them, disables ebuild test phases, and checks the repository before
and after each emerge operation. Source-built `scanelf` is installed before
the bootstrap compiler operation that needs it. Interrupted phases retain
their logs, inputs and source/build trees on the workspace; rerun the same
`bash gentoo.sh build` command in the same guest. Fingerprints exclude
concurrency settings, so changing Portage concurrency does not invalidate a
completed source recipe.

The VM continuation mounts only the scratch UUID passed by the VM launcher,
after verifying it is a distinct existing ext4 filesystem. It requires the
launcher-recorded host free-space values plus at least 50 GiB free in the
workspace and 24 GiB on scratch. Hardware has no synthetic scratch device;
it uses `/build/gentoo-scratch` on the already identified workspace and
requires 70 GiB free. Neither path formats a device. The native root binds
scratch at `/gentoo-scratch` for Portage temporary builds and distfiles.

This batch's checks cover ordered dependencies, unique source-lock entries,
the complete generated input bundle, SHA256 manifest verification, Bash
syntax and the existing Gentoo regression fixtures. They do not execute the
Gentoo bootstrap or a full `@system` merge in a fresh VM; those remain part of
the final replay acceptance.

## Batch 9 encrypted-base runtime

Stage 90 runs only after the first source-built multilib `@system` receipt. It
installs the reviewed encrypted-base package set into the native root: OpenRC
recovery and administration tools, cryptsetup with Argon2, LVM, networking,
firmware, dracut, and the selected filesystem and device utilities. The default
package, USE, license, keyword and firmware savedconfig files are explicit
bundle inputs; the existing TOML package paths can replace each policy. The
license transition narrows the bootstrap's temporary `*` allowance to
`-* @FREE`, with a single redistributable firmware exception.

Before merging, the stage validates the selected atoms with Portage, checks the
saved modern kernel config for built-in dm-crypt, device mapper and ext4, plans
the closure, and records a source audit. It fetches the source archives before
merging, uses the established no-test/source-only Portage settings, checks the
pinned repository and Manifests around each emerge, and requires `@world` to
be converged afterward. Acceptance checks cover installed package atoms,
cryptsetup Argon2 and LVM USE, OpenRC/networking/recovery commands and the
dracut policy. Portage concurrency remains independently controlled by the
three existing settings; it is left out of the recipe fingerprint.

The package files and stage logs, plans, source audit and receipts remain on
the workspace, so interruption resumes with the same `bash gentoo.sh build`
command and bundle. The stage changes only the native build root; storage
initialization and installation into an encrypted target remain for batch 10.
VM execution uses the launcher-recorded scratch filesystem; hardware uses the
existing workspace scratch directory and its larger free-space requirement.
Static checks validate the package policy, stage order, bundle manifest and
shell syntax. A fresh guest merge has not yet been run and remains part of the
final VM replay.

## Batch 10: encrypted installed base

These commands run only after batch 9 has built the native Gentoo root and
runtime. The installer resolves the root disk again from its recorded VM
serial/capacity or the configured hardware by-id path, serial and capacity
bounds. It identifies boot/workspace and extra disks separately and protects
them. Hardware additionally checks configured root/extra WWN and model values.
The target root must be unmounted and cannot back `/build`; workspace, boot and
extra media remain in use.

In the guest, inspect the proposal first:

```sh
bash /build/gentoo-bootstrap/inputs/INPUT_DIGEST/gentoo.sh plan
```

The exact durable input path is printed by `gentoo.sh build` and is recorded at
`/build/gentoo-bootstrap/current-inputs`. To initialize a new target, run the
following interactively, verify the displayed disk identity, and type the
exact `ERASE <serial> <major:minor>` line. It creates GPT and LUKS2, prompts
twice for a passphrase, then creates LVM root/swap/home and filesystems:

```sh
bash /build/gentoo-bootstrap/inputs/INPUT_DIGEST/gentoo.sh storage-init
```

After interruption, use `plan` to inspect receipts and detected disk state.
Resume only after durable start, target, partition and LUKS receipts agree:

```sh
bash /build/gentoo-bootstrap/inputs/INPUT_DIGEST/gentoo.sh storage-resume
```

Ambiguous or partially written destructive state stops for inspection;
resumption never repartitions or formats a completed volume. Once storage is
complete, install or resume the encrypted root:

```sh
bash /build/gentoo-bootstrap/inputs/INPUT_DIGEST/gentoo.sh install-root
```

This prompts for the LUKS passphrase and new root/user passwords (never stored
in configuration or logs), copies the source-built base, installs the kernel,
modules and exact verified continuation bundle, configures accounts, password
policy, locale, keymap, timezone, OpenRC services, sudo and LVM-backed fstab,
then builds and checks a crypt/LVM initramfs. Root installation can be repeated
without repartitioning or reformatting. Receipts and boot artifacts stay on the
workspace; installed kernel/initramfs copies are bound to a durable hash
receipt. The storage receipt binds the disk identity and configured LV layout;
changing either after initialization stops resumption for inspection. The base
runtime policy now includes `app-admin/passwdqc`; its changed policy fingerprint
rebuilds stage 90 on the next `gentoo.sh build` run before installation.

For VM acceptance, shut down the guest cleanly and on the host run:

```sh
sudo python3 system/gentoo/vm.py boot-installed \
  --run-dir OUT/gentoo-run --apply
```

This boots the installed kernel/initramfs directly with recorded QEMU resources
and the encrypted root's LUKS/LVM command line. The initramfs prompts for the
LUKS passphrase. It does not depend on GRUB, Libreboot or signing. Hardware
remains on its existing bootstrap boot path until the reviewed signed boot is
initialized. No real disk initialization or guest installation was run while
preparing this batch.

## Batch 11: signed T440p generations and firmware boundary

The installed hardware root now includes `/etc/ms1504-root-identity`, a
generation marker, a dracut hibernation-generation guard, and the reviewed
`ms1504-signed-generation` command. The manager deliberately refuses the VM
profile. On hardware, prepare a dedicated OpenPGP key offline, copy the binary
public key to `/etc/ms1504-boot/boot.key`, and configure the example at
`/etc/ms1504-signed-generation.toml`. Its fingerprint must match the public
key, and the LVM snapshot reserve must cover the configured retained count.
See [SIGNED-GENERATIONS.md](SIGNED-GENERATIONS.md) for exact setup, update,
rollback, pruning, and offline-merge commands.

The initial signed boot requires firmware GRUB that already trusts the key and
enforces signatures. The payload-hardening helper is generic and hash-pinned,
but no T440p ROM is produced here: the exact working Libreboot revision,
matching build tools, and board-specific data must be identified and audited
on the actual machine. The optional internal flash helper is not part of
installation or updates. Its read-only preflight, dual verified backups, exact
confirmation, and post-write readback do not replace external recovery.
Flashrom documents embedded-controller risks for laptops in its
[laptop guidance](https://flashrom.org/Laptops).

For updates, `begin` makes and verifies the rollback snapshot before package
work. `status` reports snapshot COW usage; no cleanup happens automatically.
`seal` publishes a new signed default only after both kernel artifacts and the
menu pass verification. Explicit rollback selects a retained boot entry;
explicit prune removes one generation after a typed confirmation. Offline
snapshot merge requires recovery media and leaves durable receipts for
inspection after interruption. Two previous generations are retained by
default, subject to the configured reserve. Signed generations do not prevent
replay of an older valid signed image because this flow has no TPM-backed
anti-replay state.

Batch 11's checks are host-only unit fixtures, shell/Python syntax checks, and
repository regressions. They do not test real T440p firmware, a flash chip,
encrypted boot, snapshot merge, or a token. Hardware acceptance still needs
backup/preflight, signed boot, tamper rejection, rollback, offline recovery,
and device/power-management checks. The complete VM replay remains an
end-of-series requirement; this batch preserves its direct kernel/initramfs
boot path.
