# Source-built Gentoo: operator guide

Build live-bootstrap into a multilib Gentoo base with encrypted root, home and
swap, a login account, sudo and wired networking. **The base checkpoint is an
installed boot followed by a clean reboot.** `/extra` and desktop are later steps.

| Status | Evidence / remaining work |
| --- | --- |
| Current development VM | Installed base boots; encrypted `/extra` migration, restore, verification and reboot passed; OpenRC user service starts after PAM configuration and reboot. |
| Fresh replay | Still required from original bootstrap with no pasted repairs or edited receipts. Other base checks must be recorded. |
| Desktop | Dependency-policy fixes are in source; build and graphical-login acceptance pending. |
| T440p | Current bootstrap runs on verified extra SSD. Early bootstrap disk selection/formatting needs correction before another fresh hardware run; hardware acceptance pending. |
| Later stages | Signing, rollback, firmware and general upgrades retain separate reviews. Audio acceptance is deferred to real hardware. |

Run **host** commands from the repository root. **Bootstrap root** means the
root shell in the original/bootstrap system; **installed user** means the login
account in the installed Gentoo system. Replace example paths and resources for
your run. Keep passwords and keys out of configuration files.

## 1. Prepare and preserve

1. Install host tools: Python 3.11+, Bash, QEMU, util-linux, e2fsprogs, psmisc,
   xz and `gpgv`; add `socat` for the printed serial-console command.
2. Save and boot-test an independent powered-off copy of any working VM before
   new validation. Include all backing chains, launcher records, configuration
   and boot artifacts. Use a separate directory and fresh images for a replay.
3. Budget real disk space for growing sparse images and backups. VM defaults:
   128 GiB workspace, 128 GiB scratch, 500 GiB root, 128 GiB extra, plus the
   original bootstrap disk. Hardware workspace needs at least 70 GiB free.
   TOML storage roles: `[layout]` allocates root/swap/home/reserve **inside the
   root disk**; `[workspace]` identifies the separate build filesystem;
   `[images]` sizes whole virtual disks and applies only to the VM. For example,
   `layout.root_gib = 200` is a root LV inside `images.root_gib = 500`.
4. Keep the VM's recorded CPU model unchanged. Gentoo packages use native CPU
   optimization detected on the actual guest/target; a VM build is not a
   transferable T440p installation.

See [OPERATIONS.md](OPERATIONS.md) for resource checks, configuration semantics,
backup verification and source-authentication limits.

## 2. Start a fresh VM

1. **Host:** copy the configuration, edit concurrency/account/locale/layout,
   and start the original bootstrap. Adjust the example's 18 cores and 48 GiB RAM.

   ```sh
   cp system/gentoo/config.example.toml my-gentoo.toml
   ./rootfs.py --qemu --target target-gentoo-fresh \
     --cores 18 --qemu-ram 49152 --target-size 32G \
     --extra-builds gentoo --gentoo-config my-gentoo.toml
   ```

   The launcher creates workspace/scratch filesystems and empty root/extra
   images under `target-gentoo-fresh/gentoo-run`. It records the CPU/QEMU command,
   prints the serial connection command and saves `console.log`. Gentoo starts
   automatically after the original bootstrap.

2. **Bootstrap root:** at `SHUTDOWN MILESTONE`, run the exact printed
   `cd /; bash .../shutdown.sh` command. Wait for QEMU to exit. Keep the PID-1
   shell alive until shutdown; the milestone message alone does not power off.

3. **Host, VM stopped:** boot the saved modern kernel.

   ```sh
   sudo python3 system/gentoo/vm.py continue \
     --run-dir target-gentoo-fresh/gentoo-run --apply
   ```

4. **Bootstrap root:** start or resume the Gentoo build.

   ```sh
   /usr/local/sbin/gentoo-resume
   ```

   This restores mounts, recorded workspace and wired networking, verifies
   inputs/CPU/kernel, and builds the native multilib base. After interruption,
   boot the same modern bootstrap kernel and rerun this command. When the base
   runtime completes, continue with section 4.

## 3. Prepare and start the T440p

1. **Rescue/live Linux on the T440p, read-only preflight:** verify backups and
   a bootable rescue route; record existing firmware boot commands. Identify
   physical disks, wired networking and available storage.

   ```sh
   lsblk -p -b -o NAME,TYPE,SIZE,FSTYPE,LABEL,UUID,MODEL,SERIAL,WWN
   blkid -c /dev/null
   lscpu
   ip -br address
   ip route
   df -h
   ```

   Configure observed serial, capacity bounds, canonical sysfs model and WWN
   for root/extra, plus serial/capacity for the protected USB boot medium.
   Root, extra and boot must be different disks. Missing by-id links are supported;
   conflicting links or ambiguous identities stop discovery.

2. **Resolve the early bootstrap/workspace integration before a new run.**
   The original kernel-bootstrap path defaults to `DISK=sda`, partitions/formats
   it and moves the bootstrap root there **before Gentoo's identity checks**.
   A pre-created workspace on that disk is overwritten. Do not start another
   fresh hardware run until early disk selection/confirmation is corrected.

   In the current run, the operator verified this filesystem is on the intended
   extra SSD. Reuse it as `GENTOO_BUILD` by reconciling its label/configured UUID,
   without formatting again. It serves both as the bootstrap `/` and, when
   mounted at `/build`, the Gentoo workspace. Follow
   [HARDWARE-WORKSPACE.md](HARDWARE-WORKSPACE.md) for the actual disk roles,
   recovery requirements and outstanding integration work.

3. **Future fresh-run gate:** correct/review the early formatter above, complete
   the fresh VM replay and review the T440p preflight before proceeding.
   **Host:** after that integration review, configure the observed identities
   and the chosen workspace discovery policy.

   ```sh
   cp system/gentoo/config.example.toml my-t440p.toml
   python3 system/gentoo/prepare.py --config my-t440p.toml \
     --mode hardware --output /tmp/gentoo-t440p-inputs
   ./rootfs.py --bare-metal --target target-t440p-fresh \
     --extra-builds gentoo --gentoo-config my-t440p.toml
   ```

   Prepare the bootstrap USB using the original bootstrap media procedure and
   a verified device choice. Boot using the existing firmware route. Gentoo
   starts automatically and detects CPU features on the T440p.

4. **Bootstrap root:** at the modern-kernel milestone, run the printed shutdown
   helper. Follow the [hardware kernel handoff](HARDWARE-WORKSPACE.md#modern-kernel-handoff)
   to boot the saved modern kernel against the **bootstrap root**, then run:

   ```sh
   /usr/local/sbin/gentoo-resume
   ```

   After the native base build completes, follow section 4. Firmware flashing
   and signing enforcement are outside this base procedure.

## 4. Install the base — VM or T440p

1. **Bootstrap root:** select this run's immutable driver and inspect its plan.

   ```sh
   inputs=$(cat /build/gentoo-bootstrap/current-inputs)
   driver=/build/gentoo-bootstrap/inputs/$inputs/gentoo.sh
   bash "$driver" status
   bash "$driver" plan
   ```

   Verify root target identity and protected boot/workspace disks. On hardware,
   recheck serial, WWN, model and capacity against your preflight record.

2. **Destructive boundary:** initialize the configured root disk.

   ```sh
   bash "$driver" storage-init
   ```

   Type the displayed `ERASE <serial> <major:minor>` confirmation, then answer
   passphrase prompts. Existing tables/LUKS/filesystem signatures on that root
   target are warned about and cleared after confirmation. This creates
   GPT/LUKS2/LVM and root/home/swap; mounted or active targets are refused.
   **After an interruption**, inspect the plan and resume instead:

   ```sh
   bash "$driver" plan
   bash "$driver" storage-resume
   ```

   Completed volumes are preserved. Stop on unverifiable format intent; never
   edit receipts or restart initialization to force progress.

3. **Bootstrap root:** install or resume the base, answering password prompts.

   ```sh
   bash "$driver" install-root
   ```

   This also generates `/boot/grub/grub.cfg` inside encrypted root using the
   installed kernel, matching initramfs and recorded dracut arguments. Run the
   printed handoff shutdown helper from `/` and wait for poweroff.

4. **VM host:** boot the installed kernel/initramfs.

   ```sh
   sudo python3 system/gentoo/vm.py boot-installed \
     --run-dir target-gentoo-fresh/gentoo-run --apply
   ```

   **T440p:** use Libreboot's existing unlock/LVM route to read the installed
   encrypted `/boot/grub/grub.cfg`. No intermediate installed-system entry on
   extra is needed for a fresh run. See
   [hardware boot handoff](HARDWARE-WORKSPACE.md#installed-system-boot) and
   [existing-system update instructions](INSTALLED-UPDATES.md).

5. **Installed user:** unlock root, log in and run the
   [base acceptance checks](OPERATIONS.md#base-verification-and-replay-acceptance):
   sudo, locale/timezone, DHCP/DNS, root/home/swap, CPU policy, both C/C++ ABIs,
   `uucp` group and OpenRC user runtime. Run `sudo reboot`, unlock and repeat.
   Save results and a boot-tested independent checkpoint before continuation.

## 5. Continue into encrypted `/extra`

1. **Installed user:** inspect identities and migration capacity.

   ```sh
   sudo ms1504-extra plan
   ```

2. **VM only:** mount the original, offline workspace read-only by recorded UUID,
   inspect the retained selection, then copy it into encrypted `/home`.

   ```sh
   workspace_uuid=$(awk -F= '$1=="workspace_uuid" {print $2}' /etc/ms1504-extra-identity)
   test -n "$workspace_uuid"
   sudo mkdir -p /mnt/gentoo-workspace
   sudo mount -t ext4 -o ro,noload "UUID=$workspace_uuid" /mnt/gentoo-workspace
   sudo ms1504-extra retention-plan --source /mnt/gentoo-workspace
   sudo ms1504-extra migrate --source /mnt/gentoo-workspace
   ```

   **T440p with combined bootstrap root/workspace:** first deploy the current
   helpers and boot-test the encrypted-root menu as described in
   [INSTALLED-UPDATES.md](INSTALLED-UPDATES.md). Then one command resolves and
   mounts the recorded workspace read-only, then copies and verifies it:

   ```sh
   sudo ms1504-extra migrate --bootstrap-root
   ```

   The hardware scope retains only the needed `gentoo-bootstrap` subtree; back
   up any other old-root data separately. VM retention is unchanged. Hardware
   initialization refuses until the encrypted-root boot check passes.
   The source must be offline and not mounted elsewhere. A failed copy can be
   retried with the same command. Wait for successful manifest verification.
   [Retention policy](WORKSPACE-RETENTION.md) omits disposable bootstrap work
   trees, keeps continuation artifacts and unknown files, and preserves the
   original source. It does not preserve full bootstrap resumption on `/extra`.

3. **Destructive boundary, after copy verification and the applicable review:**
   VM initialization erases its extra image; hardware initialization erases
   the extra SSD **including the original workspace**.

   ```sh
   sudo ms1504-extra initialize --source /mnt/gentoo-workspace
   sudo ms1504-extra restore
   sudo ms1504-extra verify
   sudo reboot
   ```

4. **After reboot:** verify automatic unlock/mount and retained data.

   ```sh
   findmnt /extra
   sudo ms1504-extra verify
   sudo rc-status --all
   ```

   Keep migration staging, key backups and the independent base checkpoint.
   For interrupted initialization, use the [recovery procedure](OPERATIONS.md#recovery).

## 6. Build and test the desktop

1. **Installed VM, after `/extra` verification and Rust/desktop review:**

   ```sh
   sudo complete-desktop
   ```

   The same command resumes after an inspected interruption. It changes the
   profile/packages and builds the source Rust chain before its final desktop
   plan; it is not a dry run. Log: `/extra/portage/logs/complete-desktop.log`.
   The maintained desktop policy includes libdbusmenu `gtk3` and libvpx `postproc`.

2. Reboot, test LightDM/Xfce login, Firefox/VLC, sudo, user services, networking,
   `/extra`, logout and another clean reboot. **Audio is deferred to the T440p.**

3. **T440p desktop is separately gated:** the current driver requires reviewed
   signing configuration and a pending `ms1504-signed-generation` transaction. Follow
   [CONTINUATION-PLAN.md](CONTINUATION-PLAN.md) before starting it on hardware.
   VM desktop results do not validate graphics, audio, Wi-Fi/Bluetooth, input,
   battery/thermals, suspend or firmware trust on the physical machine.

## Reference and later work

- [Installed code updates and encrypted-root boot](INSTALLED-UPDATES.md): verified
  USB deployment, preserved local settings and hardware migration commands.

- [Operations](OPERATIONS.md): backups, resources, acceptance, recovery, CPU/source
  policy, manual desktop alternative and CI/maintainer commands.
- [Continuation plan](CONTINUATION-PLAN.md): detailed test gates and VM/hardware
  acceptance matrix.
- [Upgrade design](UPGRADE-DESIGN.md): proposed maintenance and package-pin policy;
  a general upgrade workflow has not been implemented or validated.
- [Signed generations](SIGNED-GENERATIONS.md): later signing/rollback design;
  firmware flashing and trust enforcement require separate review.
- [Base review](BASE-REVIEW.md) and [cleanup record](CLEANUP.md): audit decisions
  and repository cleanup; images, caches and recovery data are retained.
