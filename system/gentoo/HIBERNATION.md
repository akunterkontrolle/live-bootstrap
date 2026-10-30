# Generation-aware hibernation

This is an opt-in installation after desktop and recovery boot acceptance.
It supports ordinary hibernation on the encrypted root/home/swap layout,
using the pinned elogind 255.24 hook API. It does not enter a sleep state.
Hybrid sleep and suspend-then-hibernate remain disabled. Ordinary suspend to
RAM does not create hibernation markers.

## Install the reviewed batch

Commit and transfer the complete reviewed source directory, including the
three files under `dracut-ms1504-resume-guard`. Keep immutable installed source
releases and their existing desktop receipts unchanged. The hibernation
installer creates a separate content-addressed tool release, preserving the
previous files as backups. It updates both generation entrypoints so their
hibernation checks cannot silently remain at the previous version.

Before starting another transaction, inspect `sudo ms1504-generation status`.
The T440p currently retains base and desktop-1, filling its configured two
slots. Review retention and free capacity explicitly before `begin`. Do not
automatically prune either checkpoint. Its 100 GiB reserve could accommodate
three 32 GiB snapshots after reviewing the reported free extents and existing
snapshot health and increasing the configured retention limit to three.

Run from the normal root origin, with no hibernation image or marker:

```sh
sudo ms1504-generation begin
sudo python3 /path/to/reviewed/system/gentoo/hibernate.py install
sudo ms1504-generation run -- dracut --no-hostonly --omit resume \
  --add 'crypt lvm ms1504-resume-guard' --kver "$(uname -r)" \
  "/boot/initramfs-$(uname -r)-hibernate.img" "$(uname -r)"
sudo lsinitrd -m "/boot/initramfs-$(uname -r)-hibernate.img"
```

Verify `ms1504-resume-guard`, crypt and LVM are present and the stock `resume`
module is absent. Inspect that the image includes `lib/ms1504-resume-lib.sh`,
the custom pre-mount hook, `/etc/ms1504-hibernate-ready`, and required wireless
firmware. Preserve existing generation artifact directories; publish a new
generation name, for example:

```sh
sudo ms1504-generation seal --generation desktop-hibernate-1 \
  --kernel "/boot/vmlinuz-$(uname -r)" \
  --initramfs "/boot/initramfs-$(uname -r)-hibernate.img"
sudo reboot
```

The installer does not reload elogind or change the running resume device.
The reboot loads the guarded initramfs and hook-veto configuration together.
Retained rollback entries explicitly use `noresume`, including entries whose
older initramfs contained stock dracut resume. They remain recovery choices;
they must not restore an image created by another generation.

## Check, then test physically

After booting the newly sealed normal generation:

```sh
sudo ms1504-generation verify-running
sudo ms1504-hibernate check
```

The initramfs configures the verified encrypted swap major/minor before root
mounts, including on ordinary boots with no image. Elogind can then expose
hibernation to Xfce. If necessary, log out and in to refresh the menu.

Save work. From the local desktop, choose Hibernate, or run
`loginctl hibernate`. Power the laptop on, unlock encrypted root, and select
the same normal generation. Confirm the original session resumes, then check
Wi-Fi, audio, `/extra`, input, CPU scaling and a subsequent clean reboot.
This physical test is still required; successful fixture tests are not
hardware acceptance. Do not use a different kernel or recovery generation to
restore the image.

## Failure handling and safeguards

The pre hook checks the live root, boot generation, boot artifacts, active
swap, persistent home, runtime resume device, configuration and update state.
It records generation, kernel release, filesystem UUIDs and boot ID on
encrypted `/home`, then briefly freezes/unfreezes only `/home` to checkpoint
ext4 metadata. This lets the initramfs read the marker using `ro,noload`,
without replaying the hibernated filesystem's journal. Root is never frozen.
The configured `AllowSuspendInterrupts=yes` makes a failed hook cancel sleep.

The only initramfs resume path checks the image signature, marker, UUIDs,
kernel release and generation before writing `/sys/power/resume`. A failed
restore stops before mounting root. It never erases swap or automatically
discards an untracked image. Recovery entries cannot resume shared swap.

After successful restoration, or a kernel sleep attempt returning with
ordinary swap, the post hook checks the original boot identity and archives
the marker. If another pre hook vetoes sleep, elogind may not call post; a
stale marker is deliberately retained. After a cold boot with ordinary swap:

```sh
sudo ms1504-hibernate clear-stale
```

This refuses same-boot manual cleanup and any remaining image. Inspect errors
instead of deleting markers or recreating swap. Generation mutations and
monitored package changes refuse pending markers or nonordinary swap; the
shared generation lock serializes marker creation with those operations.
The installed source-only emerge wrapper holds the same shared lock while
packages are built and refuses markers or an image signature before starting;
hibernation cannot race a package operation. Bootstrap callers remain unchanged.
Signing and firmware trust remain separate acceptance gates.
