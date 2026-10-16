# T440p signed boot and root generations

This procedure is for the encrypted **hardware** installation only. The VM
continues to boot its installed kernel and initramfs directly through QEMU.
It does not need Libreboot, GRUB signing, or this manager. The manager checks
`/etc/ms1504-root-identity` and refuses a VM profile.

## Signing key and first signed boot

Create a dedicated OpenPGP signing key on an offline, trusted workstation. The
GRUB public key must be binary OpenPGP data. A secret key can be exported by
`export-boot-signing-key.sh FINGERPRINT OUTPUT_DIRECTORY`; GPG prompts for the
export encryption passphrase. Keep the resulting `secret-key.gpg` on encrypted
removable media. The secret is decrypted into a temporary mode-0700 `/run`
GPG home only while signing. Alternatively, use a GPG home with an OpenPGP
hardware token. Passphrases are entered through GPG pinentry and are never
configuration values or command arguments.

Copy only `boot.key` to `/etc/ms1504-boot/boot.key` on the installed T440p,
then copy the example to `/etc/ms1504-signed-generation.toml`. Set the actual
VG/LV names, root-owned state directory, boot directory, and complete primary
fingerprint. Choose a classic LVM snapshot size and retain count that fit the
unallocated reserve in `/etc/ms1504-root-identity`; the default is 32 GiB per
snapshot and two retained generations. Protect the config as root-owned and
not group/other-writable. Verify that the public key fingerprint matches the
configured fingerprint before initializing.

The installed system provides `/usr/local/sbin/ms1504-signed-generation`.
Supply exactly one signing source for mutating commands:

```sh
sudo ms1504-signed-generation --secret-key-file /media/keys/secret-key.gpg initialize
# Or use a token-backed GPG home:
sudo ms1504-signed-generation --gnupg-home /root/.gnupg initialize
sudo ms1504-signed-generation status
```

`initialize` signs the currently installed kernel and initramfs, validates the
GRUB script, and atomically publishes the signed menu wrapper. This operation
assumes the installed T440p firmware already trusts the matching key and is
configured to enforce detached signatures. It does not edit firmware or
install a boot policy.

## Firmware hardening boundary

The repository supplies `harden-libreboot-payload.sh`, which takes a
hash-pinned extracted GRUB payload and inserts the key trust, signature
enforcement, superuser, and PBKDF2 verifier commands. It emits a payload hash
receipt but never changes a ROM. `firmware-build-receipt.sh` binds a separately
built ROM to two identical, verified backups and a board-data preservation
report. It checks image size, hashes, board and revision identity. The exact
working T440p Libreboot revision, source archive, matching build tooling, and
actual board-specific data are machine-specific inputs; this checkout does
not invent them or claim a hardened ROM has been built. Identify and audit
those inputs on the actual machine before using these helpers. Keep at least
two offline copies of verified original firmware backups.

The optional `firmware-flash.sh` is separate from installation and update
commands. First review exact DMI product/baseboard/BIOS identity and the
operator-approved flashrom version, then run `preflight`, followed by `backup`
to a separately mounted device. It requires two identical full-chip reads and
checks write access through a read-only probe. `flash` additionally requires a
matching build receipt, unchanged current firmware, an exact interactive
confirmation, flashrom verification, and a full readback comparison. Any
ambiguous chip, read/write access failure, identity change, or mismatch stops
the procedure and calls for external recovery. Do not add force flags or
retry an interrupted write. Internal laptop flashing can interact with the
embedded controller and may leave the machine unusable; [flashrom's laptop
guidance](https://flashrom.org/Laptops) recommends particular caution.

## Update, rollback, and recovery

Before Portage or other root changes, create a root snapshot and make it the
default recovery entry:

```sh
sudo ms1504-signed-generation --secret-key-file /media/keys/secret-key.gpg begin
sudo ms1504-signed-generation status
```

The root is briefly frozen only during snapshot creation, and thaw is attempted
even if `lvcreate` fails. The package update can then proceed. Check `status`
while work runs: it reports each COW allocation and warns at the configured
limit (80% by default). Stop the update and investigate if any snapshot is full,
invalid, or at the threshold. The tool never removes a snapshot automatically.

After installing the new kernel and rebuilding the matching initramfs, publish
them under a new generation name:

```sh
sudo ms1504-signed-generation --secret-key-file /media/keys/secret-key.gpg \
  seal --generation gentoo-2026-10 --kernel /boot/vmlinuz-6.x.y \
  --initramfs /boot/initramfs-6.x.y.img
```

The manager signs and verifies both artifacts, checks GRUB syntax and retained
snapshot health, then publishes a signed menu whose entries retain root, home,
swap, and generation activation arguments. A seal interrupted after signing can
resume only with the same generation name and inputs. The old snapshot stays
available after a successful update. Select an older generation for the next
boot with `rollback --generation gentoo-2026-09`; this changes the signed
default and does not rewrite the live root.

Retention defaults to two prior generations and is bounded by the configured
LVM reserve. When full, explicitly prune an old generation by exact name. The
manager asks for an exact TTY confirmation, removes the menu entry before the
LV, and writes an interruption receipt. Inspect `status` and LVM before
resuming if it reports a pruning receipt.

For offline recovery, boot trusted recovery media, unlock the encrypted VG,
mount either the root origin or selected rollback snapshot at a temporary
directory, and invoke the explicit merge operation. Do not run from the
installed root or with involved LVs mounted:

```sh
sudo ms1504-signed-generation --config /path/to/target-generation.toml \
  merge-rollback --generation gentoo-2026-09 --recovery-root /mnt/target-root \
  --recovery-receipts /mnt/recovery-media/generation-receipts
```

The command verifies root identity, checks hibernation state, refuses active or
mounted LVs, requires exact TTY confirmation, records a durable merge intent
on a separately mounted persistent filesystem outside the target VG, and waits
for the classic snapshot merge. It mounts the restored origin to verify the
generation marker and repair the root's transaction state before deactivating
it. After a merge interruption, leave the target offline and inspect LVM
metadata and the external `merge-pending-*` receipt; do not retry blindly.
Boot the restored root and verify its generation and encrypted storage before
returning to normal use.

The initramfs hibernation guard compares the saved `/home` generation marker
with the generation named on the selected signed menu entry before enabling
resume from swap. A mismatch or untracked hibernation image stops in dracut
emergency handling rather than resuming into a different root generation.

Signed generations authenticate recovery choices and detect modified artifacts.
They do not prevent replay of an older, still-valid signed generation; this
workflow has no TPM-backed anti-replay state.
