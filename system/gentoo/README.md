# Gentoo continuation — batch 1

## Prerequisites and ownership

Host commands run from the repository root, using Python 3.11 or newer (stdlib
`tomllib`). Guest commands run as root in the completed live-bootstrap Linux
build environment. The guest needs Bash, GNU coreutils/findutils, awk, sed,
util-linux (`blkid`, `mount`, `flock`, `blockdev`, and `lsblk` for hardware
identification), a mounted `/proc`, and, for hardware, `/sys` and persistent
`/dev/disk/by-id` links. Future downloads use wget and pinned SHA256 digests.
The guest does not need Python or a TOML parser for this batch.

The workspace must already be writable ext4 with the configured label and must
be visible to the guest. This batch does not create disks or filesystems. It
only mounts the selected existing workspace at `/build` and writes under
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
   inputs and logs, verifies the input milestone, and exits successfully.
   Batch 1 stops here; it builds no compiler and requires no reboot.

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
   the later storage commands. The command stops at the input milestone.

4. **T440p after interruption/reboot:** use step 4 of the VM sequence with your
   workspace label. No firmware, GRUB, signing, or token operation is involved
   in this batch. The later hardware signed-boot path will be separate from
   the unsigned VM direct-kernel path.

The later series will add an explicit modern-kernel shutdown/cold-boot boundary,
then multilib, Portage and encrypted installation. Installed-kernel boot,
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
not its content-addressed absolute directory. A compiler stage should include
its recipe, relevant helper files, selected source locks/patches, semantic
configuration, consumed policy content and prerequisite receipts. Documentation
and later stages stay out of that list. A changed fingerprint or failed output
check stops for inspection rather than silently mixing incompatible builds.

A stage runs sequential commands under `errexit`, verifies its actual outputs,
then flushes and renames a receipt atomically. Failed stages have no receipt.
A later stage must supply both a repeatable action and a meaningful output
verifier; this batch provides only the input milestone. There is no legacy
experimental receipt migration.

The driver holds `flock` on a stable inode for its lifetime and its build
children. Kernel process cleanup releases it after the last holder exits;
no stale PID directory or reboot is needed. The lock file itself is never
unlinked. A still-running child retains the lock after its parent dies, so
inspect/stop that process before resuming. Concurrent drivers fail immediately.

Source helpers require a pinned SHA256, verify cached files on every use,
download into a unique partial file, verify before publication, and refuse a
corrupted cache instead of replacing it silently. Batch 2 supplies the first
source locks and builds. No sources are downloaded in batch 1.

## Checks and acceptance boundary

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_gentoo.py' -v
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
