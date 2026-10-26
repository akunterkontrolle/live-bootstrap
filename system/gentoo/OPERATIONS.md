# Operations reference

Use [README.md](README.md) for the ordered commands. This file contains resource
requirements, checkpoint preservation, verification and recovery details.

## Requirements and configuration

Run host commands from the repository root. Use Python 3.11+, Bash, QEMU
(`qemu-system-x86_64`, `qemu-img`), util-linux (`blkid`, `losetup`, `mount`,
`umount`), e2fsprogs, psmisc `fuser`, xz and GnuPG `gpgv`. Install `socat` for
the printed serial-console command. VM lifecycle operations use sudo so they
can inspect open images and mount the stopped workspace read-only. Build
prerequisites inside the bootstrap environment are checked by the driver.

Copy `config.example.toml` to a local operator file and edit it before building.
`--cores`, `--qemu-ram` and `--target-size` select original-bootstrap resources;
TOML independently controls later source/Portage concurrency, account, locale,
layout and package policy. Passwords, passphrases and private keys never belong
in TOML. Package-policy paths resolve relative to the TOML and are copied into
the immutable bundle. Keep the selected TOML with the run record.

The storage sections have separate roles; none overrides another:

| TOML section | Meaning | Applies to |
| --- | --- | --- |
| `[layout]` | LVM allocation inside the installed root disk: fixed root/swap, unallocated reserve, and minimum home size. Home receives the remaining space. | VM and hardware |
| `[workspace]` | Existing build filesystem label/UUID and bootstrap network interface. It specifies no sizes or partition layout. | VM and hardware |
| `[images]` | Capacities of the separate virtual workspace, scratch, root and extra disks. | VM only |
| `[hardware_disks]` | Physical disk identities and acceptable capacity ranges; these are checks, not requested sizes. | Hardware only |

`layout.root_gib = 200` means a 200 GiB root logical volume, while
`images.root_gib = 500` means a 500 GiB virtual disk containing root, swap,
home, reserve and metadata. When creating a new VM root image, its capacity is
`max(images.root_gib, root_gib + swap_gib + reserve_gib + home_min_gib + 2)`
using the four `[layout]` values in the sum. Existing images are not resized.
Hardware instead checks that the actual root disk can hold that same minimum;
it fails if too small. Workspace capacity is independent of this root layout.

The VM defaults to 128 GiB workspace, 128 GiB scratch, 500 GiB root and 128 GiB
extra images, in addition to the original bootstrap disk. Root capacity grows
to fit the configured layout when first created. Workspace/scratch are ext4;
root/extra begin as empty containers. Images are sparse, but require actual
host free space as builds grow. Before Gentoo system builds, checks require
50 GiB free inside the workspace and 24 GiB inside scratch, plus recorded host
headroom of 8 GiB for workspace and 70 GiB for scratch. Budget more for retained
sources, backup copies and later builds. The README VM example allocates 48 GiB RAM
and 18 cores; reduce concurrency and resource arguments to suit the host.
Hardware uses the existing workspace for scratch and requires at least 70 GiB
free there. A full source build takes substantial time; retain logs and caches.

## Preserve and boot-test a checkpoint

Before validation, shut the successful VM down completely. Save an independent
copy **outside Git** of every image and its complete backing chain, the original
bootstrap media, TOML, launcher JSON records, immutable inputs, receipts, and
kernel/initramfs artifacts. Inspect each qcow2 image with
`qemu-img info --backing-chain IMAGE`. Copy every dependency or create standalone
copies with `qemu-img convert` while powered off. A linked snapshot alone is not
an independent backup. Record checksums and original paths.

Boot-test an isolated working copy of that backup, with launcher paths adjusted
to its copied images; ensure no disk path still points to the original. Keep
the saved backup unchanged after the test. The launcher records absolute paths,
so moving a directory alone does not relocate a runnable installation. Inspect
the saved record and full QEMU command before starting the copy. Retain the
previous working launcher and CPU model for this test; consolidated CPU checks
cannot certify an older base that predates CPU receipts.

Use an entirely separate target/run directory and fresh images for acceptance.
Changing flags in the repaired base cannot retroactively optimize its packages.
Backup creation and its boot test remain required operational work; repository
unit tests do not perform them.

## Base verification and replay acceptance

A fresh replay must use the ordinary repository procedure with no pasted
repairs, guest-only source fixes or edited receipts. Record run ID, repository
commit, TOML, CPU exposure and logs. Exercise interrupted build resumption after
the modern handoff and interrupted storage/install resumption. Preserve
completed volume UUIDs and verify that resumption does not reformat them.
Unexpected failures require source fixes and review, followed by repetition of
affected gates from appropriate clean inputs.

After installed boot, check login and sudo, the configured locale/timezone,
wired connectivity and DNS, encrypted root/home/swap activation, and both ABIs:

```sh
sudo -v
locale
date
ip -br address
ip route
getent hosts gentoo.org
findmnt /
findmnt /home
sudo swapon --show
sudo cryptsetup status gentoo-crypt
sudo lvs
sudo python3 /usr/local/libexec/gentoo-continuation/cpu-policy.py \
  --receipt /etc/portage/native-cpu-policy.json \
  --detector /usr/bin/cpuid2cpuflags --compiler /usr/bin/gcc
```

As the login user, compile, inspect and run both runtime ABIs:

```sh
(
  set -eu
  abi_dir=$(mktemp -d)
  trap 'rm -rf -- "$abi_dir"' EXIT
  printf '#include <stdio.h>\nint main(void) { puts("C runtime ok"); return 0; }\n' > "$abi_dir/check.c"
  printf '#include <string>\nint main() { std::string s("runtime"); return s.size() != 7; }\n' > "$abi_dir/check.cc"
  for abi in 32 64; do
    gcc -m"$abi" -O2 -march=native "$abi_dir/check.c" -o "$abi_dir/c-$abi"
    g++ -m"$abi" -O2 -march=native "$abi_dir/check.cc" -o "$abi_dir/cxx-$abi"
    readelf -h "$abi_dir/c-$abi" "$abi_dir/cxx-$abi"
    "$abi_dir/c-$abi"
    "$abi_dir/cxx-$abi"
  done
)
```

The build's runtime receipts test both ABIs; installed execution is a separate
acceptance check. Reboot with `sudo reboot`, unlock again, and repeat the checks.
Keep the results with the run record.

Also check the installed account/session services:

```sh
printf 'XDG_RUNTIME_DIR=%s\n' "${XDG_RUNTIME_DIR-}"
ls -ld "/run/user/$(id -u)"
getent group uucp
sudo rc-status --all
```

The runtime directory must belong to the login user, have mode 0700, and match
`XDG_RUNTIME_DIR`. The OpenRC user service must start after a fresh login/reboot;
there must be no `root:uucp` ownership error. Fresh installation configures the
PAM provider automatically. For diagnosis of an older installed system, see
[CONTINUATION-PLAN.md](CONTINUATION-PLAN.md); repository edits do not update its
installed helpers or immutable input bundle.

## Recovery

For bootstrap recovery, boot the saved modern kernel against the original
bootstrap root and run `/usr/local/sbin/gentoo-resume`. This restores the recorded
workspace and network. Inspect `/build/gentoo-bootstrap/logs`, `state`,
`current-inputs`, `current-handoff` and `install-base-state` before retrying.
The durable driver offers `status`, `plan`, `storage-resume` and `install-root`
as shown above. Retain `inputs`, handoff, source caches and repair-state backups;
they are runtime evidence, even though temporary repository repair tools have
been retired. A failed mount or identity check is a reason to inspect the
actual device, not to manufacture a link or completion marker. If shutdown
reports busy mounts, leave the root shell alive, inspect the holders, and retry
the maintained shutdown helper after releasing them.

On the host, `vm.py status --run-dir ...` displays the run state. Omitting
`--apply` from lifecycle commands prints their plan. Existing images are
validated and reused without resizing or reformatting. Never launch two guests
against the same disks. Preserve a failed run before trying new inputs.

For root storage initialization interrupted after partitioning, inspect the
plan and use `storage-resume`. A cancelled new-passphrase prompt can leave a
verified blank partition; resumption asks for `CREATE LUKS …` before creating
LUKS. Mismatched new passphrases retry before format intent. An interrupted
format with no verifiable completion requires inspection, never receipt edits.

For `/extra`, inspect its journal and use `sudo ms1504-extra resume` only when
initialization and partition receipts exist. If they do not, stop and inspect;
there is no generic safe command to repeat a partially completed partitioning
operation. After initialization completes, use `restore` and `verify`. Keep
both migration staging and original backup; do not use `discard-migration`
during acceptance.

Desktop logs: `/extra/portage/logs/complete-desktop.log`; stage receipts:
`/var/lib/ms1504-gentoo/desktop`. After an interrupted build, inspect the failure,
fix and review maintained source, then rerun `sudo complete-desktop` with the
same reviewed inputs. Do not remove receipts, change the repository snapshot,
or replace immutable inputs to force progress.

## CPU and source policy

Gentoo C/C++ packages use `-O2 -pipe -march=native -mtune=native` with
`-fstack-protector-strong -D_FORTIFY_SOURCE=2`. A checksum-pinned cpuid2cpuflags
prerequisite is source-built before the first ebuild; detection occurs inside
the VM or on the T440p. Detected `CPU_FLAGS_X86` apply globally through
`package.use/00-cpu-flags`, following
[Gentoo's CPU flags guidance](https://wiki.gentoo.org/wiki/CPU_FLAGS_%2A).
Gentoo's own package replaces the temporary detector in the native base.
Semantic receipts bind the detected features and resolved compiler targets;
changed CPU capability sets or compiler targeting stop resumption. The VM is
tied to its recorded exposed CPU capabilities. Do not substitute host detection,
change its CPU model, or move a native-built base to an incompatible machine.
Earlier bootstrap toolchains keep recipe-specific flags. The kernel uses a
portable generic x86-64 target; generated performance/hardening settings and
compiler compatibility are checked before its build.

Host, chroot and installed continuation emerge callers share source-only
validation against the pinned Portage parser/effective settings, rejecting
binary-enabling aliases/options, binary repositories and populated package
caches. Keep strict Manifest verification and `-assume-digests` (the minus
**disables** that feature). Locks and snapshot hashes verify pinned bytes; they
do not independently authenticate upstream authors or a compromised lock edit.
The kernel additionally has a pinned-key detached-signature check. See
[BASE-REVIEW.md](BASE-REVIEW.md) for the reconciled findings and evidence limits.

## Manual desktop alternative

The installed base can be preserved and developed manually as an explicit
alternative. First make and boot-test another powered-off backup. Keep the
native CPU flags, detected package-use settings and source-only guard in place;
inspect dependency plans and fetch/build through `/usr/local/sbin/emerge`.
Select the later desktop profile deliberately, review desktop USE/keywords and
Rust's source bootstrap before installing selected packages. Record those
choices and validate graphical login, networking, audio and reboot separately.
Manual changes create a new lineage; do not claim automated desktop acceptance
or resume old installation receipts over that modified root.

The README continuation exercises `ms1504-extra` and `complete-desktop` only
after their stated review gates. `ms1504-signed-generation` remains a separately
reviewed entry point. Signing and
rollback transactions, firmware handling, workspace migration and encrypted
`/extra`, and Rust/desktop dependency convergence need their own reviews and
acceptance gates. [SIGNED-GENERATIONS.md](SIGNED-GENERATIONS.md) describes the
signing design; its presence does not certify physical firmware or rescue.

## Maintainer checks

Run the same maintained checks as CI from the repository root with Python
3.11.4+ (including the standard tar extraction data filter):

```sh
python3 tests/run_gentoo_checks.py --portage-archive /path/to/portage-3.0.82.2.tar.bz2
```

Use `--fetch-portage` instead to download the locked archive into a temporary
check directory. The runner verifies its checksum, syntax-checks maintained
shell/Python files and runs the Gentoo tests with the actual pinned Portage API.
QEMU and ext4 tools enable disposable image/paused-CPU launcher checks. Some
optional tests need source-built static Python/OpenSSL/zlib artifacts and Linux
user/mount/PID namespaces; package availability checks accept an extracted pinned
repository through `GENTOO_TEST_REPOSITORY`. Missing optional inputs report skips. CI checks do not perform the full
VM replay or hardware preflight. Cleanup paths and coverage disposition are
recorded in [CLEANUP.md](CLEANUP.md); images and runtime data are excluded.

## T440p CPU frequency after the kernel handoff

Observed hardware diagnostics showed all eight logical CPUs online, SMT enabled,
the expected source/Portage concurrency in the matching boot bundle, ample
available memory, and the passive `intel_cpufreq` driver using the `userspace`
governor. The generated kernel defaulted to that governor, but `init=/bin/bash`
starts no service to request frequencies. All sampled clocks were about 800 MHz;
the sample was taken after stopping the build, so loaded throughput still needs
verification.

On the already built kernel, bootstrap root can select the available performance
governor without a rebuild or changes to build receipts:

```sh
for policy in /sys/devices/system/cpu/cpufreq/policy*; do
    printf '%s\n' performance > "$policy/scaling_governor" || break
done
grep -H . /sys/devices/system/cpu/cpufreq/policy*/scaling_governor
```

Resume the build normally. The governor requests the maximum allowed frequency;
actual clocks still depend on power and thermal limits. This runtime change lasts
for the current boot. The maintained kernel fragment now explicitly requests
CPUFreq and a performance default, and verifies those settings in generated
configuration. Do not modify the running build's recorded fragment or kernel
receipts to apply it retroactively. Desktop power policy remains separate work.

See [Linux CPUFreq documentation](https://docs.kernel.org/admin-guide/pm/cpufreq.html)
for governor behavior. Missing swap is a separate issue; with the observed ample
memory it is not evidence of current memory pressure. Do not hide a live build's
temporary files by mounting a new filesystem on `/tmp`.
