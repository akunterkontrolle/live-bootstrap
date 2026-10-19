# Base checkpoint review

The scope is a source-built encrypted base, login, both runtime ABIs and clean
reboot. The previously repaired VM reached an installed base boot. It is a
fallback, not acceptance of these consolidated inputs. A fresh VM replay with
unchanged recorded CPU exposure is required before recommending destructive
T440p installation. The first hardware boot may use the existing unsigned
firmware route. This change neither flashes firmware nor enables signing.

## Resolution of the two installer analyses

References below identify the overview's §4.3 findings and the deep analysis's
§1/§2 findings, including proposals that this consolidation deliberately does
not adopt.

| Finding | Resolution and evidence |
| --- | --- |
| Overview 2; deep §1.2 stable keywords | Keep `ACCEPT_KEYWORDS="amd64"`. It states the base's stable policy. |
| Overview 3; deep §1.1/§2.4 digest flags | Correct the analysis: `-assume-digests` disables the feature, just like `-getbinpkg` and `-buildpkg`. Keep `-assume-digests -digest strict` and the existing real Portage Manifest rejection probe. Removing the negative token would weaken the explicit policy. |
| Deep §1.1/§2.4 source-only enforcement | Host, native, nested bootstrap and desktop emerge callers share `source-only-emerge.py`. It uses the pinned Portage parser for aliases and combined options, rejects binary-enabling defaults and configuration-root overrides, reads effective FEATURES/binhost/binrepos settings, and checks effective/default package caches before and after each command, including failures. It does not infer package type from filename extensions. |
| Overview 4; deep source authentication | The locks pin HTTPS archive bytes using reviewed checksums. Gentoo Manifests verify source content against the pinned repository; checksums and local snapshot manifests do not independently authenticate an upstream author or a compromised lock update. The kernel retains its separate pinned-key signature verification. The new cpuid prerequisite has an archive checksum, not an upstream-signature claim. No new signing subsystem is introduced. |
| Overview 5; deep §1.2/§2.5 userspace CPU targeting | Supersede the portable-installed-default proposal: both VM and T440p Gentoo packages use `-O2 -pipe -march=native -mtune=native`, with the existing stack-protector/FORTIFY flags, through installed base and desktop. Earlier bootstrap/toolchain recipes keep their required flags. A new build is required; configuration edits cannot optimize completed packages. |
| Deep §1.2 CPU feature policy | Build pinned cpuid2cpuflags 18 from source before the first ebuild. Detect in the actual guest/target; generate global `package.use/00-cpu-flags` and explicit bootstrap USE additions. Record CPUID flags, kernel-visible capabilities, resolved compiler targets and target options as semantic inputs. Check the record before resuming; refuse changed capability sets, including additions that could change later native outputs. Install Gentoo's own pinned package once the system is operational, with an exact-version `~amd64` keyword exception for 18 (the pinned repository has not stabilized it). Retain the small static workspace prerequisite for future bootstrap resumption. |
| Overview 6; deep §1.2/§2.3 kernel and early tools | Request performance optimization, a generic x86-64 CPU, strong stack protection, FORTIFY, hardened usercopy, strict kernel/module permissions and address randomization. Check every generated setting and compile a protection probe before Kbuild. The actual pinned Linux 6.12.111 Kconfig accepted the fragment with the validation host compiler. VM/T440p builds must repeat that check with their actual source-built compiler. Native userspace flags are not passed to Kbuild. |
| Overview 7; deep §1.3/§2.1 handoff and discovery | Publish `/usr/local/sbin/gentoo-resume` and versioned helpers on the bootstrap root before shutdown. It verifies those helpers, mounts virtual filesystems, selects the recorded workspace UUID, verifies the content-addressed source and checks the durable resume script before invoking it. A source or workspace mismatch stops continuation. |
| Deep §1.3/§2.1 udev assumptions | Resolve whole disks from kernel sysfs inventory and verify serial, capacity and configured WWN/model. Root/extra hardware configurations require all four identity constraints; the protected USB boot medium requires serial/capacity. Use canonical kernel model/WWN attributes and the read-only unit-serial VPD page when a text serial is absent; model constraints must use that observed kernel representation, which may be shorter than a udev marketing name. A unique match is required even if a by-id link exists; a conflicting, dangling or nonlink entry refuses. Never manufacture a by-id link or treat `udevadm trigger` as evidence of discovery. The later extra-disk helper shares this identity resolution, but its migration transactions remain outside base acceptance. |
| Deep §1.3 workspace creation | Retain the existing pre-existing ext4 workspace requirement. The proposed automatic workspace formatter is excluded. Discovery/resumption creates no workspace filesystem. |
| Deep §1.3/§2.2 installed command line | Retain the LUKS/LVM boot command line: the previous VM passed an actual encrypted-root boot. That resolves the analysis's unknown for that run; a fresh replay and first T440p boot remain separate acceptance gates. |
| Deep §2.6 profiles | Use the base split-usr multilib profile from host configuration through the first Gentoo system. Keep a separate explicit desktop profile transition. Remove the unused seed no-multilib transition. |
| Deep §2.7 repository identity | Derive the commit from `sources/gentoo-repository.lock` in the build, installer and desktop caller. No separately hard-coded installer/desktop commit remains. |
| Overview 1; deep §2.9 CI and tests | Batch 1 transferred replay failures into ordinary behavioral regressions. Batch 2 adds pinned-Portage option/settings tests, CPU checks, hardware-discovery tests, standalone root-handoff tests and kernel checks. CI wiring and temporary test/payload removal belong to batch 3. Passing fixtures is not a complete guest replay. |
| Overview 8; deep §2.12 input/opt-in integration | Retain literal shell rendering, schema validation, explicit VM/hardware ownership and opt-in original-bootstrap integration. Hardware schema validation now also requires root/extra model and WWN. |
| Deep §2.10 abandoned-run cleanup | Preserve images, backing chains, caches and guest recovery data. Batch 3 removes only explicitly listed repository repair/inspection files after their useful behavior has tests. No blanket `git clean` or image deletion is authorized by consolidation. |
| Overview §4.4; deep §2.8 Rust/desktop | Desktop dependency convergence, Rust's source bootstrap and installed desktop acceptance remain later work with their own review and execution gates. The shared source-only and native CPU policy already applies to that caller. |
| Deep §2.11 signed generations/firmware | Signing/generation transactions, rollback policy, firmware handling and physical rescue/flash procedures require separate reviews and acceptance gates. Their previous static analysis does not certify them. No destructive T440p installation is recommended until fresh VM acceptance and a read-only hardware preflight. |

## Native CPU inputs and validation limits

The prerequisite is upstream cpuid2cpuflags v18, checksum-pinned in
`sources/cpuid2cpuflags.lock`, built from its three x86 C translation units with
the existing source-built GNU cross compiler and static runtime. This does not
require a Portage package or native autotools. The normal procedure never
copies host-detected CPU flags into a guest.

The workspace records `state/native-cpu-policy.json` and the detector's binary
checksum before package builds. The native/installed root retains
`/etc/portage/native-cpu-policy.json` and the global package-use file. Stage 80
checks that Gentoo's own detector and compiler agree with the recorded policy.
After installed boot, the same check can be run explicitly:

```sh
sudo python3 /usr/local/libexec/gentoo-continuation/cpu-policy.py \
  --receipt /etc/portage/native-cpu-policy.json \
  --detector /usr/bin/cpuid2cpuflags --compiler /usr/bin/gcc
```

The VM launcher already checks its recorded original argument array and reuses
its CPU model. Do not edit that record, change the exposed CPU features, or move
a native-built installation to another CPU during validation. Compiler target
changes also stop continuation for review.

The prerequisite's static source build, the pinned Portage API tests and the
actual Linux Kconfig resolution were run on the validation host. They establish
those specific behaviors, not ELF32/musl bootstrap compatibility, a fresh full
VM installation, or T440p device support. The fresh VM and hardware gates in
the consolidation plan remain required.
