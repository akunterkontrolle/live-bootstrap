# Update installed continuation code and boot from encrypted root

This deploys extra/desktop/GRUB helpers, not Gentoo packages or a new kernel.
It preserves installed configuration, custom package policy, CPU/source identity,
completed build inputs and runtime journals. The pinned Gentoo repository must
remain unchanged. A started desktop build requires a separate receipt-aware
review; the updater refuses to replace its driver or policy.

## 1. Export on the host

From the repository root, choose a new output filename:

```sh
python3 system/gentoo/installed-update.py \
  --export /tmp/gentoo-installed-update.tar.gz
```

Copy that archive, its `.sha256` file, and `system/gentoo/installed-update.py`
to a transport USB. The archive contains maintained continuation sources and
runbooks, including current working-tree changes, with per-file hashes. Local
hardware TOML, private keys, .git, images and caches are excluded. The printed
source-manifest hash identifies the actual exported files; the Git commit alone
does not describe uncommitted changes. These are integrity hashes, not a new
upstream signature scheme.

## 2. Activate inside installed Gentoo

Boot the currently working installed root, using the old workspace entry if
necessary. Mount the transport USB at `/mnt/key`. Stop any extra/desktop operation.
First verify the archive from its USB directory:

```sh
cd /mnt/key
sha256sum -c gentoo-installed-update.tar.gz.sha256
sudo python3 /mnt/key/installed-update.py \
  --archive /mnt/key/gentoo-installed-update.tar.gz
sudo python3 /mnt/key/installed-update.py \
  --archive /mnt/key/gentoo-installed-update.tar.gz --apply
sudo ms1504-continuation-update --verify
```

Verify prints the active source commit, source-manifest SHA256 and release path.
Compare the manifest hash with the host export. It checks deployed contents and
permissions and all managed entrypoints. It establishes code identity, not an
absence of bugs or hardware acceptance.

Releases live under `/usr/local/share/ms1504-bootstrap/releases/HASH`. The old
installed directory and releases stay intact. The entrypoints `ms1504-extra`,
`complete-desktop`, `ms1504-grub-config` and `ms1504-continuation-update` select
the release. Installed `config.sh` and referenced custom policy files retain
their bytes and permissions. The updater journals interrupted activation;
retry the same archive to finish it. Do not change archive inputs while an
activation is pending. It never edits `/usr/portage`, `/etc/portage`, signing
configuration, immutable installation snapshots or completed receipts.

## 3. Generate and test the encrypted-root boot menu

```sh
sudo ms1504-grub-config
sudo ms1504-grub-config --apply
```

This writes `/boot/grub/grub.cfg` on encrypted root using its filesystem UUID,
`/etc/kernel/cmdline`, and matching `/boot/vmlinuz-6.12.111` and
`/boot/initramfs-6.12.111.img`. It preserves the dracut crypt/LVM/root/resume
arguments and adds `ms1504.boot=encrypted-root`; it never adds `init=/bin/bash`.
It validates GRUB syntax and saves an old-menu backup/boot record under
`/var/lib/ms1504-gentoo/boot`. It does not run grub-install or modify firmware. It refuses to overwrite the
menu once signed-generation configuration exists; use the reviewed signing
manager at that later stage.

Reboot using **Libreboot's embedded GRUB directly**, unlock LUKS, expose the
installed root LV, and load its `/boot/grub/grub.cfg` through the existing
firmware route. Do not chainload the GRUB installed on the extra SSD: that MBR
will be erased later. The menu assumes the encrypted root is already exposed;
it does not change the firmware's unlock/search policy.

After booting that entry:

```sh
sudo ms1504-grub-config --check-running
```

This requires the boot-route marker, correct running kernel/root UUID, recorded
arguments and unchanged local boot files. It guards against accidentally erasing
the old boot route while still using its entry. It cannot independently attest
which first-stage GRUB firmware launched. A boot test with the extra SSD absent
is the strongest practical check of independence; reconnect it before migration.
Keep rescue access and the old boot disk until the new route has booted.

A fresh `install-root` generates this encrypted-root menu directly and uses the
target's source-built GRUB checker. It does not need an intermediate installed-
system menu on the workspace. The earlier compilation/kernel handoff remains
separate. Existing installed kernels are not rebuilt by this code update;
reapply the runtime performance governor setting if that kernel defaults to
`userspace` (see OPERATIONS.md).

## 4. Migrate the combined bootstrap root/workspace

For this T440p's old bootstrap root on extra, explicitly select the scoped policy:

```sh
sudo ms1504-extra plan
sudo ms1504-extra migrate --bootstrap-root
```

The command resolves the recorded source and mounts it read-only without journal
replay. `installed-bootstrap-root-v1` retains only `gentoo-bootstrap`, applying
the existing exclusions for disposable build/source/toolchain/native-root trees.
Unclassified files inside the retained subtree remain included; everything
outside it, including the old OS, device nodes and operator files, is excluded.
Preserve an independent backup of any such data and the original bootstrap
checkpoint before erasing extra. This is installed continuation retention, not
full bootstrap recovery. The VM's `installed-continuation-v1` policy is unchanged.

Wait for copy/hash/metadata verification. Old migration staging/journals are
preserved when selecting the new scope; switching is refused after extra
initialization starts. Then, after the encrypted-root boot check succeeds:

```sh
sudo ms1504-extra initialize --source /mnt/gentoo-workspace
sudo ms1504-extra restore
sudo ms1504-extra verify
sudo reboot
```

`initialize` erases the extra SSD after its exact confirmation. Its boot gate
must pass before erasure. After reboot, check `/extra`, services and verification
again; retain staging and backup data throughout acceptance. Hardware desktop
still has its separate signed-generation transaction requirement.

## Fresh root-disk erasure policy

Fresh `storage-init` warns about existing signatures, then its existing exact
`ERASE <serial> <major:minor>` confirmation authorizes clearing old partition
and LUKS/filesystem signatures on that root target. GPT and first-partition
signature clearing have identity-bound receipts and signature backups. Mounted
descendants, active swap/holders, inspection errors and identity mismatches
refuse erasure. Plan/resume may inspect their existing mapper; destructive
clearing never accepts active holders.

Resume does not erase installer-owned LUKS headers or completed volumes. Once
`luks.started` exists, missing completion needs inspection. Legacy journals
without the new erase approval keep their prior blank-only behavior. Do not
apply the fresh-erasure policy by editing old receipts or installation bundles.


Extra initialization also records an identity/migration/key-bound first-clear
approval after its erase confirmation and new GPT checkpoint. It clears old
ext4 or other signatures on the new partition before creating LUKS, with signature
backups and durable clear intent/completion. An installer-owned LUKS/filesystem
intent prevents any repeat erasure. Older in-progress journals without that
approval keep their blank-only resume behavior and require explicit cleanup of
verified old residue rather than fabricated approval receipts.
