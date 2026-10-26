# T440p bootstrap disk, workspace and boot handoffs

**Confirmed integration defect:** the original kernel-bootstrap path generates
`DISK=sda` by default, then partitions and formats that whole disk before the
Gentoo continuation runs. It does not use Gentoo's physical disk identities.
Do not start another hardware run from these instructions until that early
selection/confirmation path has been corrected and reviewed. The current
operator has verified that this run used the intended extra SSD.

## What the original bootstrap actually does

1. `rootfs.py:create_configuration_file` normally writes `DISK=sda`. With a
   prebuilt repository or external sources it writes `DISK=sdb1` instead.
2. `steps/jump/move_disk.sh` treats a whole device as a request to create an
   MBR partition beginning at sector 2097152, then runs `mkfs.ext4 -F -F` on
   partition 1. This overwrites an already prepared workspace there.
3. It copies the bootstrap system onto that filesystem and switches root to it.
   OpenELA and the remaining original bootstrap execute from this new root.
4. `steps/improve/make_bootable.sh` installs GRUB on that disk and writes
   `/boot/grub/grub.cfg`. This alone does not prove the actual firmware will
   load this GRUB installation or configuration.
5. The final Gentoo after-hook expects a labeled workspace. Without reconciling
   that expectation with the newly created filesystem it fails discovery.

Earlier guidance to prepare a separate `GENTOO_BUILD` partition before this
path, and to assume the bootstrap root remains on USB, was incorrect.

## Disk roles in the current verified run

| Role | Disk / filesystem |
| --- | --- |
| Protected initial boot medium (`boot_by_id`) | USB used to start the original bootstrap; keep it intact for recovery. |
| Bootstrap root and build workspace | Newly created ext4 partition on extra (850 PRO); mount the same filesystem at `/build` for Gentoo. |
| Installed Gentoo root | Separate root SSD (870 EVO), initialized only at the later explicit storage boundary. |

`boot_by_id` remains the protected USB identity in this run's existing input
bundle. It does not select the continuation kernel, actual current `/`, or
Gentoo's `/boot`. Do not change it to extra: current schema/storage checks
require root, boot and extra to be distinct whole disks.

## Reuse the running bootstrap filesystem

Before changing even its label, inspect the running system:

```sh
cat /steps/bootstrap.cfg
cat /proc/mounts
blkid -c /dev/null
```

Resolve the root partition's physical parent and verify it against configured
extra serial, capacity, model and WWN. If it is on the intended root SSD instead,
stop: the later root initializer must never erase its own running bootstrap.
Do not infer the physical identity from `sda`.

Model matching accepts the exact configured model or its first 16 characters
with trailing padding removed, matching the SCSI INQUIRY product field. Thus
`Samsung SSD 850` can match `Samsung SSD 850 PRO 1TB`. Arbitrary shorter prefixes
are rejected. Serial, WWN, capacity, unique inventory matching and conflicting
by-id-link checks remain enforced. Installed receipts record the actual observed
model; subsequent receipt checks remain exact.

For this run, with extra verified and no Gentoo stages completed, the ext4
filesystem can serve as `GENTOO_BUILD` without reformatting or moving its files:

1. Check `/steps/gentoo-inputs/config.sh` for its workspace label and UUID.
2. Apply that label to the verified partition with `e2label`. Preserve its UUID.
   Ensure no other attached filesystem has the same expected label.
3. An empty configured UUID selects by label. A configured UUID must match the
   new filesystem's actual UUID. A stale UUID requires a reviewed new input
   bundle before first Gentoo entry; never rewrite completed snapshots/receipts.
4. Let the ordinary after-hook start Gentoo, or retry its original entry if it
   already stopped at discovery. Gentoo mounts this same filesystem at `/build`.
5. Preserve the original bootstrap root as a checkpoint. Later `/extra`
   initialization erases it, so migrate only from installed Gentoo on the
   separate root SSD with the bootstrap filesystem offline/read-only.

Reduced migration keeps unknown paths. Reusing the bootstrap root also retains
its unrelated root-tree files unless explicitly omitted by policy, so inspect
`retention-plan` and capacity rather than assuming the earlier VM's 613 MiB
retained size applies to hardware. The current selection also rejects special
files; offline device nodes in the bootstrap root would stop migration. Review
this combined root/workspace source shape before hardware `/extra` erasure.

## Permanent integration work still required

Before another fresh hardware run, bind the early bootstrap target to observed
physical identity and explicit erasure confirmation. Decide how its bootstrap
root becomes the workspace, record its new UUID, and review both boot handoffs.
Gentoo's later storage checks cannot protect a disk already formatted by the
earlier jump script. Keep backup/rescue and fresh VM acceptance requirements.

## Modern-kernel handoff

At the milestone, use the printed shutdown helper. On a trusted host with the
workspace mounted read-only, verify the saved kernel source/signature:

```sh
python3 system/gentoo/kernel_source.py --build-root MOUNT/gentoo-bootstrap
```

Replace `MOUNT` with that workspace mount. Through the existing firmware Linux
boot commands, load `gentoo-bootstrap/artifacts/kernel-6.12.111/bzImage` from the
workspace or a verified copy on the existing boot medium. Use the **bootstrap
root** UUID and your recorded console arguments:

```text
root=PARTUUID=ACTUAL_BOOTSTRAP_PARTITION_UUID rootfstype=ext4 init=/bin/bash rw rootwait
```

Use the partition UUID of the actual bootstrap root on extra, not the USB or
future Gentoo root. GRUB can locate its kernel filesystem with `search --fs-uuid`,
but Linux without an initramfs cannot use that filesystem UUID as `root=UUID=`;
its [early block lookup](https://github.com/torvalds/linux/blob/v6.12/block/early-lookup.c)
supports `PARTUUID=`. Verify a nonzero, unique MBR disk identifier before using
an MBR PARTUUID. Do not change the partition table merely to obtain one.
After cold boot, run `/usr/local/sbin/gentoo-resume` as root. It
restores workspace/network and checks the kernel, CPU and immutable source.

## Installed-system boot

After `install-root` completes, read
`gentoo-bootstrap/install-base-state/installed-boot.receipt` on the workspace.
Use its installed kernel/initramfs artifacts and exact LUKS/LVM command line
through the already working unsigned firmware route. Record the actual firmware
commands and verify any copied artifacts. The bootstrap-root command line above
is not the installed-root command line. Unlock root, log in, complete the base
checks and reboot cleanly.

The VM's direct QEMU kernel boot does not test these firmware commands. Firmware
flashing, signed generations and trust enforcement are separate reviewed work.
