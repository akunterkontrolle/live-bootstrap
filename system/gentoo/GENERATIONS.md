# Hardware checkpoints before desktop

Status: the corrected disposable LVM probe passed on the actual T440p on
2026-10-26 (kernel 6.12.111, LVM 2.03.39) in 1.666 seconds. Post-test inspection
confirmed no remaining test VG or loop device. The real-root generation
initialization and recovery-entry boot test have not run. Firmware is unchanged
by every command here.

`ms1504-generation` defaults to `/etc/ms1504-generation.toml`. Its explicit
`boot.policy = "unsigned"` creates recovery checkpoints without a signing key.
The legacy `ms1504-signed-generation` command continues to require signed
configuration. Signing and firmware enforcement follow desktop acceptance;
see [SIGNED-GENERATIONS.md](SIGNED-GENERATIONS.md). Do not create its signed
configuration as a workaround for the desktop gate.

## 1. Prepare and preserve the installed base

Run these commands on the installed T440p, after deploying and verifying the
current source release using [INSTALLED-UPDATES.md](INSTALLED-UPDATES.md):

```sh
sudo ms1504-continuation-update --verify
sudo ms1504-extra verify
sudo cat /etc/ms1504-root-identity
sudo vgs --units g -o vg_name,vg_free
sudo lvs -a -o vg_name,lv_name,origin,lv_size,data_percent
findmnt -rn -o SOURCE,TARGET,FSTYPE
```

Keep independent backups of important data, configuration, boot files, both
LUKS headers and the extra-disk key. Confirm rescue media can unlock and inspect
the installation. A root snapshot shares the physical disk: it is not a backup,
and it does not roll back `/home` or `/extra`.

Copy the example beside the installed manager, then edit the actual VG name
from `/etc/ms1504-root-identity` (usually `ms1504` on this T440p):

```sh
source_dir=$(dirname "$(readlink -f /usr/local/sbin/ms1504-generation)")
sudo install -o root -g root -m 0600 "$source_dir/generation.example.toml" /etc/ms1504-generation.toml
sudoedit /etc/ms1504-generation.toml
```

Do not replace an existing configuration or initialized policy. Start with
32 GiB per snapshot, two retained generations, threshold 80%, and verify the
actual free reserve covers them. Review free root and `/extra` build space too.
The example intentionally has no signing-key fields.

Before the real-root commands below, run the maintained opt-in LVM probe on
the T440p (or a Linux test host with LVM2, util-linux and e2fsprogs):

```sh
sudo env MS1504_TEST_LVM=1 python3 "$source_dir/probes/generation-lvm.py" -v
```

It allocates a new 4 GiB sparse file and loop device, restricts every LVM
operation to that device, creates its own VG with a 2 GiB origin and 1 GiB
snapshot, and tests preservation and offline merge. It accepts no installation disk argument and removes only
its fixture. Allow physical space for fixture writes. A missing capability or
tool fails the explicitly requested probe; ordinary CI skips it. The real
boot-menu/recovery-entry test follows on the T440p below. No full VM replay is
required.

The accepted hardware run used the maintained source copied into a private
`/extra` test directory over fingerprint-verified SSH. Source SHA256:

- `signed-generation.py`: `a04a35e513f6aff4da223170a9cb02d58aac0c86dd91dbb53be0188e9b8736e4`
- `probes/generation-lvm.py`: `23d6906a1070efea4e1414bd98dad88a4ab47db0af0a740e1a025fbf43ca03d6`

The test found and corrected locale-dependent numeric reports, an oversized
COW request relative to the original fixture, and an unexpected LVM prompt.
Deactivate the origin alone: LVM also deactivates its classic snapshots. Naming
a snapshot explicitly prompts to change its origin. Automated helper commands
now receive closed stdin, and probe failures include captured stderr.

## 2. Initialize, then protect the base

```sh
sudo ms1504-generation initialize
sudo ms1504-generation verify-running
sudo ms1504-generation begin
sudo ms1504-generation check-pending
sudo ms1504-generation status
```

Initialization copies the running kernel/initramfs into a named generation,
saves the previous menu under `/var/lib/ms1504-generations`, and publishes an
explicitly unsigned menu inside encrypted `/boot`. Hash receipts detect local
changes; they are not signatures. The manager takes ownership of the menu;
`ms1504-grub-config --apply` then refuses to overwrite it.

`begin` creates a classic LVM snapshot and makes its recovery entry the default.
LVM/device-mapper synchronizes the filesystem for snapshot creation; the manager
does not hold an outer freeze on `/`, which could block LVM's metadata writes.
See the [upstream fsfreeze manual](https://kernel.googlesource.com/pub/scm/utils/util-linux/util-linux/+/refs/tags/v2.41.1/sys-utils/fsfreeze.8.adoc).

Reboot and select the recovery entry. Run `sudo ms1504-generation verify-running`
to check its generation and actual root device. Then reboot, select the first
normal-origin entry, and run `verify-running` and `check-pending` again. This
check is required before desktop acceptance; do not start package changes from
a recovery snapshot. The menu loads retained artifacts from the root origin,
then mounts the selected origin/snapshot as Linux root. It is not independent
of the origin disk or a substitute for rescue media.

If initialization or `begin` is interrupted, inspect the menu and receipts,
then repeat the same command with the same configuration. A receipted existing
snapshot is checked and reused, not recreated. After `begin`, retain the default
recovery entry until `seal` successfully publishes the new generation.

## 3. Desktop transaction and resumption

After the recovery-entry boot test and return to the origin:

```sh
sudo ms1504-generation check-pending
sudo complete-desktop
```

The driver verifies a real pending transaction and runs under
`ms1504-generation run -- COMMAND`, which checks snapshot health every five
seconds. At the COW threshold or a validation failure it interrupts the build
process group and retains recovery state. There is a sampling/termination delay;
reserve sufficient headroom and inspect `status` during long builds. The
monitor never prunes snapshots or extends them automatically.

After an ordinary interrupted package build, inspect the failure and rerun
`sudo complete-desktop` with unchanged reviewed inputs. At the safety threshold,
stop and review capacity or recovery; do not lower the threshold, delete a
snapshot, or rerun `begin` to pretend it is healthy. Keep the same source release
and desktop inputs throughout this batch.

After a successful desktop build, publish the new generation, using the actual
matching kernel/initramfs pair (the example retains the running kernel):

```sh
sudo ms1504-generation seal --generation desktop-1 \
  --kernel "/boot/vmlinuz-$(uname -r)" \
  --initramfs "/boot/initramfs-$(uname -r).img"
sudo ms1504-generation status
sudo reboot
```

If a new kernel was built, use its release in both paths. A seal interrupted
before completion must resume with the same name and artifact contents. Once
seal starts, package changes are blocked until it finishes or recovery is
performed. After reboot, run `verify-running`, verify `/extra`, login/runtime
services, networking, both ABIs, and the hardware desktop checks in
[CONTINUATION-PLAN.md](CONTINUATION-PLAN.md). Keep the base snapshot.

## 4. Recovery and retention

For a completed transaction, `rollback --generation NAME` selects the retained
root snapshot as next default. While a transaction is pending, its recovery
entry is already the default. Selecting it does not restore the origin in place.
Use trusted rescue media for a permanent offline `merge-rollback`.

Mount the origin root at `/mnt/target-root` to read its configuration/state,
keep target `/home` and swap inactive, and use a separately mounted persistent
recovery device outside the target VG for receipts:

```sh
sudo ms1504-generation --config /mnt/target-root/etc/ms1504-generation.toml \
  merge-rollback --generation BASE_NAME --recovery-root /mnt/target-root \
  --recovery-receipts /mnt/recovery-media
```

This command can recover the newest pending or completed base transaction.
Older generations remain selectable for boot, but merging an older generation
is refused before any mutation; its multi-generation state transition needs
a later review. It
checks identity, swap/hibernation state and mounts, requires exact confirmation,
saves transaction details outside root, deactivates the involved LVs, merges,
and repairs the restored menu and state before unmounting. Signed recovery also
requires the appropriate signing credentials. After an interrupted merge, keep
the system offline and inspect the external receipts and LVM; do not retry
blindly or edit receipts. Verify the restored root through rescue and reboot.

Pruning is explicit, by exact generation name and interactive confirmation;
there is no automatic retention deletion. Do not discard migration staging,
older source releases, or data backups during this acceptance batch.
