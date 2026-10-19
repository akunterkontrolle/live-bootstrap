# Source-built Gentoo base

This continuation takes live-bootstrap to a multilib Gentoo base with encrypted
root, home and swap, a login account, sudo, locale and wired networking.
**The repaired VM has booted successfully; the consolidated procedure still
requires a fresh VM replay. Destructive T440p installation is not yet cleared.**
The base boot and clean reboot are the checkpoint. Signing, recovery generations,
firmware changes, workspace migration, encrypted `/extra` and desktop completion
have separate review and acceptance gates. The first T440p boot may use its
existing unsigned firmware boot route.

## Before starting

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

The VM defaults to 128 GiB workspace, 128 GiB scratch, 500 GiB root and 128 GiB
extra images, in addition to the original bootstrap disk. Root capacity grows
to fit the configured layout when first created. Workspace/scratch are ext4;
root/extra begin as empty containers. Images are sparse, but require actual
host free space as builds grow. Before Gentoo system builds, checks require
50 GiB free inside the workspace and 24 GiB inside scratch, plus recorded host
headroom of 8 GiB for workspace and 70 GiB for scratch. Budget more for retained
sources, backup copies and later builds. The example below allocates 48 GiB RAM
and 18 cores; reduce concurrency and resource arguments to suit the host.
Hardware uses the existing workspace for scratch and requires at least 70 GiB
free there. A full source build takes substantial time; retain logs and caches.

## Preserve the working fallback

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

## VM: original bootstrap through installed base

1. **Host:** choose a new target directory. The following is a complete fresh-run
   example, without prebuilt package or early-stage seeds:

   ```sh
   cp system/gentoo/config.example.toml my-gentoo.toml
   # Edit my-gentoo.toml, especially concurrency and layout.
   ./rootfs.py --qemu --target target-gentoo-fresh \
     --cores 18 --qemu-ram 49152 --target-size 32G \
     --extra-builds gentoo --gentoo-config my-gentoo.toml
   ```

   The host prepares images under `target-gentoo-fresh/gentoo-run`, records the
   original QEMU arguments, prints the serial connection command, and captures
   `console.log` in that run directory. The original bootstrap's final hook
   starts the Gentoo build automatically. Keep the recorded CPU model unchanged
   throughout the run, including resume and installed boots.

2. **Guest root console:** at `SHUTDOWN MILESTONE`, use the **exact printed**
   `cd /; bash .../shutdown.sh` command. The message does not itself power off.
   The helper stops the verified DHCP daemon, unmounts children before parents,
   and refuses poweroff if mounts remain busy. Do not exit the PID-1 Bash shell
   or kill QEMU as the normal shutdown procedure. Wait for QEMU to exit.

3. **Host, VM stopped:** cold-boot the saved modern kernel:

   ```sh
   sudo python3 system/gentoo/vm.py continue \
     --run-dir target-gentoo-fresh/gentoo-run --apply
   ```

   The launcher verifies the handoff and kernel source signature. **Guest root:**

   ```sh
   /usr/local/sbin/gentoo-resume
   ```

   This bootstrap-root entry works before `/build` is mounted. It verifies its
   helpers, mounts virtual filesystems and the recorded workspace UUID, checks
   the immutable source bundle, restores wired DHCP/DNS, and invokes the durable
   build. It verifies the running kernel and CPU inputs before continuation.
   The build proceeds through native CPU detection, Gentoo toolchains,
   multilib `@system`, and base runtime packages. After an interrupted build,
   rerun this entry on the modern bootstrap kernel.

4. **Guest root, base runtime complete:** select the recorded immutable bundle
   and inspect the installation proposal:

   ```sh
   inputs=$(cat /build/gentoo-bootstrap/current-inputs)
   driver=/build/gentoo-bootstrap/inputs/$inputs/gentoo.sh
   bash "$driver" status
   bash "$driver" plan
   ```

   Verify that the target is the run's root image and that boot, workspace,
   scratch and extra remain distinct. The plan does not initialize storage.

5. **Guest root, destructive boundary:** initialize the new root image:

   ```sh
   bash "$driver" storage-init
   ```

   Review the displayed identity and type the exact `ERASE <serial> <major:minor>`
   confirmation. This creates GPT, LUKS2, LVM and root/home/swap filesystems;
   passphrases are prompted interactively. After interruption, inspect `plan`
   and use **`storage-resume`**, never another initialization:

   ```sh
   bash "$driver" plan
   bash "$driver" storage-resume
   ```

   Completed volumes are preserved. Ambiguous format-start state stops for
   inspection. Do not edit receipts to force progress.

6. **Guest root:** install or resume copying/configuring the base:

   ```sh
   bash "$driver" install-root
   ```

   This prompts for the unlock passphrase and account passwords, configures
   the installed system, and publishes kernel/initramfs artifacts and receipts.
   It does not reformat completed storage. From `/`, run the handoff shutdown
   command printed in step 2, then wait for QEMU to exit.

7. **Host:** boot the installed kernel/initramfs:

   ```sh
   sudo python3 system/gentoo/vm.py boot-installed \
     --run-dir target-gentoo-fresh/gentoo-run --apply
   ```

   **Installed guest:** unlock encrypted root and log in as the configured user.
   Complete the checks below, reboot cleanly with `sudo reboot`, and repeat the
   boot/login checks. QEMU directly loads the installed kernel/initramfs using
   the tested LUKS/LVM command line; this does not exercise hardware firmware.

## T440p: preflight and gated base workflow

1. **Read-only rescue/live Linux on the actual T440p:** retain a usable rescue
   medium and verify that it can boot, see the disks, and unlock existing data.
   Record the currently working firmware boot commands before making changes.
   Collect identities and resources without formatting or changing partitions:

   ```sh
   lsblk -d -b -o NAME,SIZE,SERIAL,WWN,MODEL
   ls -l /dev/disk/by-id
   lscpu
   ip -br link
   ip route
   df -h
   ```

   Read the chosen whole disks' `/sys/class/block/NAME/device/model`, `serial`
   and `wwid` attributes where present, and reconcile them with the configured
   identity. Kernel model strings may be shorter than udev marketing names.
   Configure root and extra **serial, capacity bounds, WWN and canonical model**,
   plus protected boot-medium serial/capacity. All three disks must differ.
   Discovery requires one unique sysfs identity match; missing by-id links are
   supported, conflicting links refuse. `udevadm trigger` is not proof of identity.
   Use actual-target CPU detection, check wired DHCP/DNS, storage, USB, keyboard,
   display and rescue access. Observe existing workspace UUID, free space and
   backing disk. The workspace must already be writable ext4; this procedure
   does not create it. Do not initialize `/extra` during the base stage.

2. **Acceptance gate:** complete the fresh VM acceptance list below and review
   the read-only T440p preflight before recommending destructive hardware
   installation. The remaining steps describe that later authorized run.
   **Host:** prepare a new hardware bootstrap image with observed identities:

   ```sh
   cp system/gentoo/config.example.toml my-t440p.toml
   # Fill hardware_disks and the existing workspace UUID; review all settings.
   python3 system/gentoo/prepare.py --config my-t440p.toml \
     --mode hardware --output /tmp/gentoo-t440p-inputs
   ./rootfs.py --bare-metal --target target-t440p-fresh \
     --extra-builds gentoo --gentoo-config my-t440p.toml
   ```

   Follow the original bootstrap's image/media instructions and existing rescue
   route. Writing boot media is itself destructive to that medium and requires
   a separately verified device choice. No generic `dd` target is implied here.
   Boot on the T440p using its existing firmware route. The embedded after-hook
   starts the Gentoo build; hardware CPU detection runs on this machine.

3. **T440p bootstrap root:** at the modern-kernel milestone use the printed
   shutdown command. With the workspace offline, a trusted host can verify the
   kernel source/signature:

   ```sh
   python3 system/gentoo/kernel_source.py --build-root MOUNT/gentoo-bootstrap
   ```

   Replace `MOUNT` with the read-only workspace mount. Use the existing firmware
   Linux-boot commands to load
   `gentoo-bootstrap/artifacts/kernel-6.12.111/bzImage` from that workspace, or
   its verified copy on the existing boot medium. Keep the bootstrap root UUID:

   ```text
   root=UUID=ACTUAL_BOOTSTRAP_ROOT_UUID rootfstype=ext4 init=/bin/bash rw rootwait
   ```

   Add the existing console arguments. Record the actual UUID; this command
   line boots the bootstrap root, not the future encrypted root. After cold
   boot, run `/usr/local/sbin/gentoo-resume` as root.

4. **T440p bootstrap root:** after the base runtime completes, follow VM steps
   4–6 with this run's durable bundle. **`storage-init` erases the configured
   physical root disk.** Recheck serial, WWN, model and capacity at the prompt.
   Resume interrupted storage with `storage-resume`; install with `install-root`.
   Keep the workspace and protected boot medium intact.

5. **T440p existing firmware route:** use the installed boot artifacts and the
   LUKS/LVM command line recorded in the workspace's
   `gentoo-bootstrap/install-base-state/installed-boot.receipt`. Arrange access
   to those verified artifacts through the already working unsigned boot route;
   do not guess a GRUB device number or copy the bootstrap-root command line.
   Unlock, log in, verify the base, and reboot cleanly. This is a separate
   hardware acceptance result. Firmware flashing and signing enforcement are
   excluded from this workflow.

## Base acceptance and recovery

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

## CPU policy and source integrity

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

## Later work and the manual desktop alternative

The installed base can be preserved and developed manually as an explicit
alternative. First make and boot-test another powered-off backup. Keep the
native CPU flags, detected package-use settings and source-only guard in place;
inspect dependency plans and fetch/build through `/usr/local/sbin/emerge`.
Select the later desktop profile deliberately, review desktop USE/keywords and
Rust's source bootstrap before installing selected packages. Record those
choices and validate graphical login, networking, audio and reboot separately.
Manual changes create a new lineage; do not claim automated desktop acceptance
or resume old installation receipts over that modified root.

The existing `complete-desktop`, `ms1504-extra` and `ms1504-signed-generation` tools are
future entry points, not commands to run at the base checkpoint. Signing and
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
