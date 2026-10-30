#!/usr/bin/env python3
"""Manage explicit unsigned/signed boot generations and classic-LVM rollback snapshots.

This deliberately does not run emerge or build a kernel. begin() protects the
current root before package work; seal() publishes the reviewed new artifacts.
"""
from __future__ import annotations

import argparse
import atexit
import fcntl
import hashlib
import json
import signal
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime, timezone

CONFIG = Path(os.environ.get("MS1504_GENERATION_CONFIG", "/etc/ms1504-signed-generation.toml"))
LOCK = Path("/run/ms1504-generations/lock")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SIGNER: tuple[list[str], dict[str, str]] | None = None


def need(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(args: list[str], *, input: bytes | None = None) -> bytes:
    try:
        return subprocess.run(args, input=input, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env={**os.environ, "LC_ALL": "C"},
                              **({"stdin": subprocess.DEVNULL} if input is None else {})).stdout
    except FileNotFoundError as exc:
        raise RuntimeError(f"required command is unavailable: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"command failed ({args[0]}): {detail}") from exc


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    need(not path.is_symlink(), f"refusing a symlink destination: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def protected_path(path: Path) -> None:
    need(path.is_absolute() and ".." not in path.parts, f"unsafe absolute path: {path}")
    for part in [*reversed(path.parents), path]:
        need(not part.is_symlink(), f"symlink in protected path: {part}")
        if part.exists():
            stat = part.stat()
            need(stat.st_uid == 0 and not stat.st_mode & 0o022, f"unprotected path: {part}")


def read_config() -> dict:
    try:
        config = tomllib.loads(CONFIG.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"cannot read {CONFIG}: {exc}") from exc
    schema = {"system": {"volume_group", "root_lv", "home_lv", "swap_lv"},
              "boot": {"directory", "public_key", "signing_fingerprint"},
              "snapshots": {"size_gib", "maximum_data_percent", "retain_generations"},
              "state": {"directory"}}
    need(set(config) == set(schema), "configuration has unknown or missing sections")
    policy = config.get("boot", {}).get("policy", "signed")
    need(policy in ("signed", "unsigned"), "[boot].policy must be signed or unsigned")
    if "policy" in config.get("boot", {}):
        schema["boot"] = {"directory", "policy"} | ({"public_key", "signing_fingerprint"} if policy == "signed" else set())
    for section, keys in schema.items():
        need(isinstance(config[section], dict) and set(config[section]) == keys,
             f"[{section}] has unknown or missing keys")
        if section != "snapshots":
            need(all(type(value) is str for value in config[section].values()),
                 f"all values in [{section}] must be strings")
    for key in ("volume_group", "root_lv", "home_lv", "swap_lv"):
        need(re.fullmatch(r"[A-Za-z0-9+_.-]+", config["system"].get(key, "")) is not None,
             f"invalid [system].{key}")
    config["policy"] = policy
    if policy == "signed":
        fingerprint = config["boot"]["signing_fingerprint"].replace(" ", "")
        need(re.fullmatch(r"[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64}", fingerprint) is not None,
             "[boot].signing_fingerprint must be a full OpenPGP fingerprint")
        config["boot"]["signing_fingerprint"] = fingerprint.upper()
    config["vg"] = config["system"]["volume_group"]
    config["root"] = f"/dev/{config['vg']}/{config['system']['root_lv']}"
    config["state_dir"] = Path(config["state"]["directory"])
    config["boot_dir"] = Path(config["boot"]["directory"])
    config["public_key"] = Path(config["boot"].get("public_key", "/etc/ms1504-boot/boot.key"))
    for key, minimum, maximum in (("size_gib", 1, 96), ("maximum_data_percent", 1, 99)):
        value = config["snapshots"].get(key)
        need(type(value) is int and minimum <= value <= maximum,
             f"[snapshots].{key} must be an integer from {minimum} to {maximum}")
    retention = config['snapshots']['retain_generations']
    need(retention == 'auto' or (type(retention) is int and retention > 0),
         '[snapshots].retain_generations must be "auto" or a positive integer')
    for path in (config["boot_dir"], config["state_dir"], config["public_key"]):
        need(path.is_absolute(), "boot, state, and public-key paths must be absolute")
    need(config["state_dir"] not in (Path("/"), Path("/boot")) and
         config["boot_dir"] not in (Path("/"), config["state_dir"]),
         "state and boot directories cannot overlap a filesystem root")
    need(config["boot_dir"] == Path("/boot"), "boot directory must be /boot on encrypted root")
    if CONFIG.exists():
        need(not CONFIG.is_symlink(), "generation config must not be a symlink")
        mode = CONFIG.stat().st_mode & 0o777
        need(CONFIG.stat().st_uid == 0 and not (mode & 0o022),
             "generation config must be root-owned and not group/other-writable")
    return config


def read_root_identity(cfg: dict) -> dict[str, str]:
    path = Path("/etc/ms1504-root-identity")
    need(path.is_file() and not path.is_symlink(), "installed root identity receipt is missing")
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        need(key not in result, f"duplicate installed root identity field: {key}")
        result[key] = value
    return validate_root_identity(result, cfg)


def validate_root_identity(result: dict[str, str], cfg: dict) -> dict[str, str]:
    need(result.get("profile") == "hardware", "generation transactions are hardware-only")
    need(result.get("volume_group") == cfg["vg"] and
         result.get("root_lv") == cfg["system"]["root_lv"] and
         result.get("home_lv") == cfg["system"]["home_lv"] and
         result.get("swap_lv") == cfg["system"]["swap_lv"],
         "configured LVM layout differs from the installed root identity")
    reserve = result.get("reserve_gib", "")
    retention = cfg['snapshots']['retain_generations']
    minimum_slots = 1 if retention == 'auto' else retention
    need(reserve.isdecimal() and int(reserve) >=
         cfg["snapshots"]["size_gib"] * minimum_slots,
         "configured LVM reserve cannot hold the requested rollback generations")
    return result


def signer_args(args: argparse.Namespace, fingerprint: str) -> tuple[list[str], dict[str, str]]:
    global _SIGNER
    if _SIGNER is not None:
        return _SIGNER
    need(bool(args.secret_key_file) != bool(args.gnupg_home),
         "choose exactly one of --secret-key-file or --gnupg-home")
    env = os.environ.copy()
    env["GPG_TTY"] = os.environ.get("GPG_TTY") or (os.ttyname(0) if sys.stdin.isatty() else "")
    base = ["gpg", "--batch", "--yes"]
    if args.gnupg_home:
        home = Path(args.gnupg_home)
        need(home.is_dir(), f"GnuPG home does not exist: {home}")
        base += ["--homedir", str(home)]
    else:
        secret = Path(args.secret_key_file)
        need(secret.is_file(), f"encrypted secret key file is missing: {secret}")
        gnupg = Path(tempfile.mkdtemp(prefix="ms1504-gpg-", dir="/run"))
        os.chmod(gnupg, 0o700)
        def cleanup_gnupg() -> None:
            subprocess.run(["gpgconf", "--homedir", str(gnupg), "--kill", "gpg-agent"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            shutil.rmtree(gnupg, ignore_errors=True)
        atexit.register(cleanup_gnupg)
        base += ["--homedir", str(gnupg)]
        decrypt = subprocess.Popen(["gpg", "--decrypt", str(secret)], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=env)
        importer = subprocess.run(base + ["--import"], stdin=decrypt.stdout,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        assert decrypt.stdout is not None
        decrypt.stdout.close()
        decrypt.wait()
        need(decrypt.returncode == 0 and importer.returncode == 0,
             "could not decrypt/import offline signing key; check pinentry and file passphrase")
        env["MS1504_TEMP_GNUPG_HOME"] = str(gnupg)
    listing = subprocess.run(base + ["--with-colons", "--list-secret-keys", fingerprint],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    need(listing.returncode == 0 and b"sec:" in listing.stdout,
         f"signing secret key {fingerprint} is unavailable")
    _SIGNER = (base + ["--local-user", fingerprint, "--detach-sign", "--output"], env)
    return _SIGNER


def signed(cfg: dict) -> bool:
    return cfg.get("policy", "signed") == "signed"


def sign(path: Path, args: argparse.Namespace, cfg: dict) -> None:
    if not signed(cfg):
        atomic_write(Path(str(path) + ".sha256"),
                     f"sha256={hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
        return
    base, env = signer_args(args, cfg["boot"]["signing_fingerprint"])
    signature = Path(str(path) + ".sig")
    proc = subprocess.run(base + [str(signature), str(path)], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env)
    if proc.returncode:
        raise RuntimeError("GPG signing failed; check pinentry and key access")
    with signature.open("rb") as stream:
        os.fsync(stream.fileno())


def verify_signature(path: Path, args: argparse.Namespace, cfg: dict) -> None:
    if not signed(cfg):
        expected = parse_fields(Path(str(path) + ".sha256")).get("sha256")
        need(expected == hashlib.sha256(path.read_bytes()).hexdigest(), f"unsigned artifact checksum differs: {path}")
        return
    base, env = signer_args(args, cfg["boot"]["signing_fingerprint"])
    verify = [item for item in base if item not in ("--detach-sign", "--output", "--local-user",
                                                     cfg["boot"]["signing_fingerprint"])]
    proc = subprocess.run(verify + ["--verify", str(path) + ".sig", str(path)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    need(proc.returncode == 0, f"existing signature does not verify: {path}")


def save_signed_artifacts(dest: Path, pairs: list[tuple[Path, str]], args: argparse.Namespace,
                          cfg: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        need(dest.is_dir() and not dest.is_symlink(),
             f"generation path is not a regular directory: {dest}")
        for source, name in pairs:
            target = dest / name
            need(target.is_file() and not target.is_symlink(),
                 f"incomplete generation directory needs inspection: {dest}")
            need(hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest(),
                 f"existing artifact differs from the selected input: {target}")
            if Path(str(target) + (".sig" if signed(cfg) else ".sha256")).is_file():
                verify_signature(target, args, cfg)
            else:
                sign(target, args, cfg)
            verify_signature(target, args, cfg)
        return
    temporary = Path(tempfile.mkdtemp(prefix=f".{dest.name}.", dir=dest.parent))
    try:
        for source, name in pairs:
            need(source.is_file() and not source.is_symlink(), f"boot input is missing or indirect: {source}")
            target = temporary / name
            shutil.copyfile(source, target)
            os.chmod(target, 0o644)
            with target.open("rb") as stream:
                os.fsync(stream.fileno())
            sign(target, args, cfg)
            verify_signature(target, args, cfg)
            os.chmod(Path(str(target) + (".sig" if signed(cfg) else ".sha256")), 0o644)
        os.chmod(temporary, 0o755)
        fsync_dir(temporary)
        os.rename(temporary, dest)
        fsync_dir(dest.parent)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def verify_public_key(cfg: dict) -> None:
    if not signed(cfg):
        return
    need(cfg["public_key"].is_file(), f"GRUB public key is missing: {cfg['public_key']}")
    need(not cfg["public_key"].read_bytes().startswith(b"-----BEGIN PGP"),
         "GRUB public key must be binary OpenPGP format, not ASCII armor")
    result = run(["gpg", "--batch", "--show-keys", "--with-colons", str(cfg["public_key"])])
    primary = None
    for line in result.splitlines():
        fields = line.split(b":")
        if fields[0] == b"pub":
            primary = None
        elif fields[0] == b"fpr" and primary is None:
            primary = fields[9].decode().upper()
            break
    need(cfg["boot"]["signing_fingerprint"] == primary,
         "configured fingerprint does not match the primary key in boot.key")


def record_generation(cfg: dict, generation: str, menu_id: str, snapshot: str = "none") -> None:
    artifacts = [cfg["boot_dir"] / "generations" / generation / "vmlinuz",
                 cfg["boot_dir"] / "generations" / generation / "initramfs.img",
                 cfg["boot_dir"] / "grub" / "menus" / menu_id / "grub.cfg"]
    rows = [f"generation={generation}", f"menu={menu_id}", f"rollback_snapshot={snapshot}",
            f"policy={cfg.get('policy', 'signed')}",
            f"fingerprint={cfg['boot'].get('signing_fingerprint', 'none')}",
            f"created={datetime.now(timezone.utc).isoformat()}"]
    rows += [f"sha256:{path.name}={hashlib.sha256(path.read_bytes()).hexdigest()}" for path in artifacts]
    receipt = cfg["state_dir"] / f"generation-{generation}.receipt"
    atomic_write(receipt, ("\n".join(rows) + "\n").encode(), 0o600)


def root_is_live(cfg: dict) -> None:
    identity = read_root_identity(cfg)
    need(identity.get("profile") == "hardware", "production generation changes require profile=hardware")
    source = run(["findmnt", "-rn", "-M", "/", "-o", "SOURCE"]).decode().strip()
    need(os.path.realpath(source) == os.path.realpath(cfg["root"]),
         f"running root is {source}, expected {cfg['root']}")
    need(cfg["boot_dir"].stat().st_dev == Path("/").stat().st_dev and
         cfg["state_dir"].stat().st_dev == Path("/").stat().st_dev,
         "boot and generation state must reside on the encrypted root filesystem")


def make_menu(cfg: dict, current: str, snapshots: list[dict[str, str]] | str | None,
              default: int = 0,
              base_cmdline: list[str] | None = None) -> bytes:
    need(NAME.fullmatch(current) is not None, "invalid current generation in menu")
    entries = []
    choices = [(f"Gentoo {current}", cfg["system"]["root_lv"], current)]
    if isinstance(snapshots, str):
        snapshots = [{"old": current, "snapshot": snapshots}]
    for item in snapshots or []:
        ident, lv = item["old"], item["snapshot"]
        need(NAME.fullmatch(ident) is not None and
             re.fullmatch(r"[A-Za-z0-9+_.-]+", lv) is not None,
             "invalid rollback generation in menu")
        choices.append((f"Rollback {ident}", lv, ident))
    need(0 <= default < len(choices), "default menu index is outside the available entries")
    for name, lv, ident in choices:
        root = f"/dev/{cfg['vg']}/{lv}"
        escape = lambda value: value.replace("-", "--")
        lvm_root = f"(lvm/{escape(cfg['vg'])}-{escape(cfg['system']['root_lv'])})"
        kernel = f"/boot/generations/{ident}/vmlinuz"
        initrd = f"/boot/generations/{ident}/initramfs.img"
        cmdline = list(base_cmdline) if base_cmdline is not None else Path("/etc/kernel/cmdline").read_text().split()
        need(all(re.fullmatch(r"[A-Za-z0-9=._:/,+-]+", token) for token in cmdline),
             "kernel command line contains a token unsafe for a GRUB menu")
        cmdline = [x for x in cmdline if x != "noresume" and not x.startswith(("root=", "rd.lvm.lv=", "ms1504.generation="))]
        # Old retained initramfs images can contain unguarded stock dracut
        # resume rules. Disable those paths even before their own guard runs.
        if lv != cfg['system']['root_lv']:
            cmdline.append('noresume')
        cmdline += [f"root={root}", f"rd.lvm.lv={cfg['vg']}/{lv}",
                    f"rd.lvm.lv={cfg['vg']}/{cfg['system']['home_lv']}",
                    f"rd.lvm.lv={cfg['vg']}/{cfg['system']['swap_lv']}",
                    f"ms1504.generation={ident}"]
        if not signed(cfg):
            entries.append(f"""menuentry '{name} (unsigned)' {{
    insmod lvm
    insmod ext2
    set root={lvm_root}
    linux {kernel} {' '.join(cmdline)}
    initrd {initrd}
}}""")
            continue
        entries.append(f"""menuentry '{name}' --unrestricted {{
    set root={lvm_root}
    if verify_detached {kernel} {kernel}.sig; then
        if verify_detached {initrd} {initrd}.sig; then
            linux {kernel} {' '.join(cmdline)}
            initrd {initrd}
        fi
    fi
}}""")
    return (f"set default={default}\nset timeout=10\n" + "\n".join(entries) + "\n").encode()


def wrapper_config(cfg: dict | None = None) -> bytes:
    if cfg is not None and not signed(cfg):
        return b"# Managed unsigned generations; no signature enforcement.\nconfigfile /boot/grub/active/grub.cfg\n"
    return b"""if verify_detached /boot/grub/active/grub.cfg /boot/grub/active/grub.cfg.sig; then
    configfile /boot/grub/active/grub.cfg
fi
"""


def publish(cfg: dict, menu: bytes, args: argparse.Namespace, *, initialize: bool = False) -> str:
    verify_public_key(cfg)
    root = cfg["boot_dir"] / "grub"
    need(not root.is_symlink(), "GRUB state directory must not be a symlink")
    wrapper_path = root / "grub.cfg"
    suffix = ".sig" if signed(cfg) else ".sha256"
    if not initialize:
        need(wrapper_path.is_file() and Path(str(wrapper_path) + suffix).is_file(),
             "fixed GRUB entry is not the managed generation wrapper; run initialize")
        verify_signature(wrapper_path, args, cfg)
    generations = root / "menus"
    generations.mkdir(parents=True, exist_ok=True)
    ident = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns()}-{secrets.token_hex(3)}"
    target = generations / ident
    target.mkdir(mode=0o755)
    os.chmod(target, 0o755)
    menu_path = target / "grub.cfg"
    atomic_write(menu_path, menu)
    checker = shutil.which("grub-script-check")
    need(checker is not None, "grub-script-check is required before publishing a generation menu")
    run([checker, str(menu_path)])
    sign(menu_path, args, cfg)
    verify_signature(menu_path, args, cfg)
    os.chmod(Path(str(menu_path) + suffix), 0o644)
    fsync_dir(target)
    # A directory symlink switch publishes the menu and its signature together.
    link_tmp = root / ".active.new"
    if link_tmp.exists() or link_tmp.is_symlink():
        need(link_tmp.is_symlink(), "unexpected file occupies temporary active-menu path")
        link_tmp.unlink()
    os.symlink(str(Path("menus") / ident), link_tmp)
    active = root / "active"
    need(not active.exists() or active.is_symlink(), "active menu is not a managed symlink")
    os.replace(link_tmp, root / "active")
    fsync_dir(root)
    if initialize:
        staged = root / ".grub.cfg.initializing"
        atomic_write(staged, wrapper_config(cfg))
        sign(staged, args, cfg)
        verify_signature(staged, args, cfg)
        os.replace(str(staged) + suffix, str(wrapper_path) + suffix)
        os.replace(staged, wrapper_path)
        os.chmod(wrapper_path, 0o644)
        os.chmod(Path(str(wrapper_path) + suffix), 0o644)
        fsync_dir(root)
    return ident


def policy_binding(cfg: dict) -> bytes:
    return (json.dumps({"policy": cfg.get("policy", "signed"), "system": cfg["system"],
                        "boot": cfg["boot"], "state": str(cfg["state_dir"])}, sort_keys=True) + "\n").encode()


def check_policy(cfg: dict, *, initialize: bool = False, target: Path = Path("/")) -> None:
    owner = cfg["boot_dir"] / "grub" / "generation-policy.json"
    if owner.exists():
        need(not owner.is_symlink() and owner.read_bytes() == policy_binding(cfg),
             "boot policy/layout differs from initialized generation ownership; explicit migration required")
    else:
        need(initialize or signed(cfg), "unsigned generations are not initialized")
    if not signed(cfg):
        need(not (cfg["boot_dir"] / "grub/grub.cfg.sig").exists() and
             not (target / "etc/ms1504-signed-generation.toml").exists(),
             "refusing unsigned operation while signed configuration/artifacts exist")


def command_initialize(cfg: dict, args: argparse.Namespace) -> None:
    root_is_live(cfg)
    read_root_identity(cfg)
    verify_public_key(cfg)
    need(not (cfg["state_dir"] / "pending").exists(),
         "incomplete generation transaction exists; inspect before initialization")
    fixed_menu = cfg["boot_dir"] / "grub" / "grub.cfg"
    check_policy(cfg, initialize=True)
    need(not (cfg["state_dir"] / "initialized").exists(),
         "generation boot is already initialized; refusing to replace its active menu")
    # Preserve the previous usable menu before claiming ownership.
    if fixed_menu.is_file():
        backup = cfg["state_dir"] / "pre-generation-grub.cfg"
        if not backup.exists():
            atomic_write(backup, fixed_menu.read_bytes(), 0o600)
    fixed_signature = Path(str(fixed_menu) + ".sig")
    if fixed_menu.is_file() and fixed_signature.is_file() and not (cfg["boot_dir"] / "grub/generation-policy.json").exists():
        try:
            verify_signature(fixed_menu, args, cfg)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("signed boot is already initialized; refusing to replace its active menu")
    marker = Path("/etc/ms1504-generation")
    need(marker.is_file(), "installed generation marker is missing")
    generation = marker.read_text().splitlines()[0].strip()
    need(NAME.fullmatch(generation), "installed generation marker is invalid")
    cmdline = Path("/etc/kernel/cmdline").read_text().split()
    need(any(x == f"ms1504.generation={generation}" for x in cmdline),
         "kernel command line and installed generation marker differ")
    src_kernel = Path(f"/boot/vmlinuz-{os.uname().release}")
    src_initrd = Path(f"/boot/initramfs-{os.uname().release}.img")
    need(src_kernel.is_file() and src_initrd.is_file(), "current kernel/initramfs pair is missing")
    need(not actual_root_snapshots(cfg), "root snapshots already exist; inspect them before initialization")
    dest = cfg["boot_dir"] / "generations" / generation
    save_signed_artifacts(dest, [(src_kernel, "vmlinuz"), (src_initrd, "initramfs.img")], args, cfg)
    atomic_write(cfg["boot_dir"] / "grub/generation-policy.json", policy_binding(cfg))
    menu_id = publish(cfg, make_menu(cfg, generation, None), args, initialize=True)
    record_generation(cfg, generation, menu_id)
    atomic_write(cfg["state_dir"] / "initialized", (generation + "\n").encode(), 0o600)
    print(f"Initialized {cfg.get('policy', 'signed')} generation {generation}; menu receipt {menu_id}")


def parse_fields(path: Path) -> dict[str, str]:
    need(path.is_file() and not path.is_symlink(), f"transaction receipt is missing or unsafe: {path}")
    fields: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        need(key not in fields, f"duplicate transaction field {key} in {path}")
        fields[key] = value
    return fields


def retained_transactions(cfg: dict) -> list[dict[str, str]]:
    rows = []
    for path in cfg["state_dir"].glob("completed-*/transaction"):
        need(not path.parent.is_symlink(), f"transaction directory must not be a symlink: {path.parent}")
        fields = parse_fields(path)
        need(NAME.fullmatch(fields.get("old", "")) is not None and
             fields.get("snapshot") == f"{cfg['system']['root_lv']}_rollback_{fields.get('old', '')}" and
             fields.get("size_gib", "").isdecimal(), f"malformed retained transaction receipt: {path}")
        fields["directory"] = str(path.parent)
        if fields.get("snapshot"):
            rows.append(fields)
    rows.sort(key=lambda row: row.get("started", ""), reverse=True)
    need(len({row["snapshot"] for row in rows}) == len(rows), "duplicate retained snapshot names exist")
    return rows


def snapshot_health(cfg: dict, snapshot: str) -> tuple[float, int]:
    output = run(["lvs", "--noheadings", "--units", "b", "--nosuffix", "-o",
                  "lv_attr,origin,data_percent,lv_size", f"/dev/{cfg['vg']}/{snapshot}"]).decode().split()
    need(len(output) == 4, f"cannot inspect snapshot LV {snapshot}")
    attr, origin, percent_text, size_text = output
    need(origin == cfg["system"]["root_lv"], f"snapshot origin differs: {snapshot}")
    need(attr.startswith("s"), f"LV is not a classic snapshot: {snapshot}")
    need(percent_text not in ("", "-"), f"snapshot COW usage is unavailable: {snapshot}")
    need(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", percent_text) is not None,
         f"snapshot COW usage is not a C-locale number: {percent_text!r}")
    percent = float(percent_text)
    need(0 <= percent <= 100, f"snapshot COW usage is invalid: {snapshot}")
    need(percent < 100 and "I" not in attr, f"snapshot is full or invalid: {snapshot}")
    need(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", size_text) is not None,
         f"snapshot COW size is invalid: {snapshot}")
    return percent, int(float(size_text))


def actual_root_snapshots(cfg: dict) -> set[str]:
    listed = run(["lvs", "--noheadings", "-o", "lv_name,origin", cfg["vg"]]).decode().splitlines()
    return {fields[0] for line in listed if len(fields := line.split()) == 2 and
            fields[1] == cfg["system"]["root_lv"]}


def snapshot_rows(cfg: dict, *, enforce_threshold: bool = True) -> list[dict[str, str]]:
    rows = retained_transactions(cfg)
    expected = {row["snapshot"] for row in rows}
    need(actual_root_snapshots(cfg) == expected,
         "unreceipted or missing root snapshot detected; inspect LVM and receipts")
    for row in rows:
        percent, size = snapshot_health(cfg, row["snapshot"])
        if enforce_threshold:
            need(percent < cfg["snapshots"]["maximum_data_percent"],
                 f"snapshot {row['snapshot']} COW usage exceeds the configured safety threshold")
        row["data_percent"] = str(percent)
        row["size_bytes"] = str(size)
    return rows


def snapshot_capacity(cfg: dict, rows: list[dict[str, str]], identity: dict | None = None) -> dict:
    """Use actual COW allocation, the reserved budget and currently free extents."""
    identity = identity if identity is not None else read_root_identity(cfg)
    reserve = int(identity['reserve_gib']) * 1024**3
    allocated = sum(snapshot_health(cfg, row['snapshot'])[1] for row in rows)
    values = run(['vgs', '--noheadings', '--units', 'b', '--nosuffix', '-o',
                  'vg_free,vg_extent_size', cfg['vg']]).decode().split()
    need(len(values) == 2 and all(value.isdecimal() for value in values),
         'cannot inspect volume-group free space and extent size')
    free, extent = map(int, values)
    need(extent > 0, 'invalid volume-group extent size')
    requested = cfg['snapshots']['size_gib'] * 1024**3
    rounded = ((requested + extent - 1) // extent) * extent
    available = min(free, max(0, reserve - allocated))
    limit = len(rows) + available // rounded
    retention = cfg['snapshots']['retain_generations']
    if retention != 'auto':
        limit = min(limit, retention)
    return dict(retention=retention, retained=len(rows), allowed_slots=limit,
                additional_slots=max(0, limit - len(rows)), reserve_bytes=reserve,
                allocated_bytes=allocated, free_bytes=free, next_snapshot_bytes=rounded)


def create_snapshot(cfg: dict, snapshot: str, size_gib: int) -> None:
    # device-mapper synchronizes/freezes the filesystem during snapshot creation.
    # An outer root fsfreeze can deadlock LVM archive/log writes on that root.
    run(["lvcreate", "--snapshot", "--size", f"{size_gib}G", "--name", snapshot, cfg["root"]])
    snapshot_health(cfg, snapshot)


def publish_retained_menu(cfg: dict, current: str, rows: list[dict[str, str]], args: argparse.Namespace,
                          *, default: int = 0) -> str:
    snapshots = [{"old": row["old"], "snapshot": row["snapshot"]} for row in rows]
    return publish(cfg, make_menu(cfg, current, snapshots, default=default), args)


def command_begin(cfg: dict, args: argparse.Namespace) -> None:
    root_is_live(cfg)
    check_policy(cfg)
    identity = read_root_identity(cfg)
    verify_public_key(cfg)
    need(not Path("/home/.ms1504-hibernate/hibernation-pending").exists(),
         "pending hibernation state must be resolved before starting a root update")
    active = cfg["boot_dir"] / "grub" / "active" / "grub.cfg"
    need(active.is_file(), "no active generation menu; run initialize first")
    verify_signature(active, args, cfg)
    marker = Path("/etc/ms1504-generation").read_text().splitlines()[0].strip()
    need(NAME.fullmatch(marker), "installed generation marker is invalid")
    verify_artifact_receipt(cfg, marker)
    pending = cfg["state_dir"] / "pending"
    size = cfg["snapshots"]["size_gib"]
    if pending.exists():
        need(pending.is_dir() and not pending.is_symlink(), "pending transaction directory is unsafe")
        fields = parse_fields(pending / "transaction")
        need(fields.get("new", "") == "", "a generation seal is interrupted; resume it with seal")
        need(fields.get("old") == marker and fields.get("snapshot") == f"{cfg['system']['root_lv']}_rollback_{marker}",
             "pending transaction belongs to another root generation")
        need(fields.get("size_gib", "").isdecimal() and int(fields["size_gib"]) == size,
             "pending snapshot size differs from the configured transaction")
    else:
        rows = snapshot_rows(cfg)
        capacity = snapshot_capacity(cfg, rows, identity)
        need(capacity['additional_slots'] > 0,
             'no snapshot slot available within the retention limit, reserved budget and free extents; '
             'inspect status and explicitly prune or free space before another update')
        snapshot = f"{cfg['system']['root_lv']}_rollback_{marker}"
        need(not Path(f"/dev/{cfg['vg']}/{snapshot}").exists(), "unreceipted rollback LV exists; inspect before proceeding")
        pending.mkdir(parents=True, mode=0o700)
        atomic_write(pending / "transaction",
                     f"old={marker}\nsnapshot={snapshot}\nsize_gib={size}\nstarted={datetime.now(timezone.utc).isoformat()}\n".encode(), 0o600)
        fields = parse_fields(pending / "transaction")
    need(NAME.fullmatch(fields.get("old", "")) is not None and
         re.fullmatch(r"[A-Za-z0-9+_.-]+", fields.get("snapshot", "")) is not None and
         fields.get("size_gib", "").isdecimal(), "pending transaction fields are malformed")
    snapshot = fields["snapshot"]
    if not Path(f"/dev/{cfg['vg']}/{snapshot}").exists():
        # A receipt without an LV is a safe resumable point. The freeze is only
        # held across lvcreate and always released before receipt publication.
        create_snapshot(cfg, snapshot, int(fields["size_gib"]))
    else:
        snapshot_health(cfg, snapshot)
    percent, allocation = snapshot_health(cfg, snapshot)
    need(percent < cfg["snapshots"]["maximum_data_percent"] and allocation >= size * 1024**3,
         "pending snapshot allocation/health is unsafe")
    uuid = run(["lvs", "--noheadings", "-o", "lv_uuid", f"/dev/{cfg['vg']}/{snapshot}"]).decode().strip()
    need(bool(uuid) and not any(x.isspace() for x in uuid), "snapshot UUID is unavailable")
    need(not fields.get("snapshot_uuid") or fields["snapshot_uuid"] == uuid, "pending snapshot UUID changed")
    fields["snapshot_uuid"] = uuid
    atomic_write(pending / "transaction", ("\n".join(f"{k}={v}" for k, v in fields.items()) + "\n").encode(), 0o600)
    rows = retained_transactions(cfg)
    need(actual_root_snapshots(cfg) == {row["snapshot"] for row in rows} | {snapshot},
         "unreceipted or missing root snapshot detected; inspect LVM and receipts")
    for row in rows:
        percent, _ = snapshot_health(cfg, row["snapshot"])
        need(percent < cfg["snapshots"]["maximum_data_percent"], "retained snapshot reached COW threshold")
    rows.insert(0, {**fields, "directory": str(pending)})
    menu_id = publish_retained_menu(cfg, marker, rows, args, default=1)
    atomic_write(pending / "menu", (menu_id + "\n").encode(), 0o600)
    print(f"Update transaction ready. Default is rollback snapshot {snapshot}; menu {menu_id}.")


def command_seal(cfg: dict, args: argparse.Namespace) -> None:
    root_is_live(cfg)
    check_policy(cfg)
    read_root_identity(cfg)
    verify_public_key(cfg)
    need(not Path("/home/.ms1504-hibernate/hibernation-pending").exists(),
         "pending hibernation state must be resolved before publishing a new generation")
    pending_dir = cfg["state_dir"] / "pending"
    need(pending_dir.is_dir() and not pending_dir.is_symlink(), "pending transaction directory is unsafe")
    pending = pending_dir / "transaction"
    need(pending.is_file(), "no pending transaction; run begin before package updates")
    fields = parse_fields(pending)
    generation = args.generation
    need(NAME.fullmatch(generation), "invalid generation name")
    need(NAME.fullmatch(fields.get("old", "")) is not None and
         fields.get("snapshot") == f"{cfg['system']['root_lv']}_rollback_{fields.get('old', '')}" and
         fields.get("size_gib", "").isdecimal(), "pending transaction fields are malformed")
    need(generation != fields["old"], "new generation must differ from current generation")
    destination = cfg["state_dir"] / f"completed-{fields['old']}"
    need(not destination.exists(), "completed transaction destination already exists; inspect receipts")
    if fields.get("new"):
        need(fields["new"] == generation, "interrupted seal must resume with the same generation name")
    for key, value in (("kernel_sha256", hashlib.sha256(Path(args.kernel).read_bytes()).hexdigest()),
                       ("initramfs_sha256", hashlib.sha256(Path(args.initramfs).read_bytes()).hexdigest())):
        need(not fields.get(key) or fields[key] == value, "interrupted seal inputs changed")
        fields[key] = value
    fields["new"] = generation
    atomic_write(pending, ("\n".join(f"{key}={value}" for key, value in fields.items() if key != "directory") + "\n").encode(), 0o600)
    live_generation = Path("/etc/ms1504-generation").read_text().strip()
    need(live_generation in (fields["old"], generation),
         "running generation differs from both the pending base and requested output")
    need(Path(f"/dev/{cfg['vg']}/{fields['snapshot']}").exists(), "rollback snapshot LV is missing")
    need(fields.get("snapshot_uuid") == run(["lvs", "--noheadings", "-o", "lv_uuid",
         f"/dev/{cfg['vg']}/{fields['snapshot']}"]).decode().strip(), "snapshot UUID changed")
    percent, _ = snapshot_health(cfg, fields["snapshot"])
    need(percent < cfg["snapshots"]["maximum_data_percent"],
         "rollback snapshot COW use reached the configured safety threshold")
    kernel, initrd = Path(args.kernel), Path(args.initramfs)
    need(kernel.is_file() and initrd.is_file(), "new kernel and initramfs must both exist")
    dest = cfg["boot_dir"] / "generations" / generation
    save_signed_artifacts(dest, [(kernel, "vmlinuz"), (initrd, "initramfs.img")], args, cfg)
    # While the current menu defaults to the old snapshot, prepare the new
    # marker and command line. A failed menu build leaves rollback as default.
    current = Path("/etc/ms1504-generation")
    cmdline_file = Path("/etc/kernel/cmdline")
    old_cmdline = cmdline_file.read_bytes()
    cmdline = [x for x in old_cmdline.decode().split()
               if not x.startswith(("root=", "rd.lvm.lv=", "ms1504.generation="))]
    cmdline += [f"root={cfg['root']}", f"rd.lvm.lv={cfg['vg']}/{cfg['system']['root_lv']}",
                f"rd.lvm.lv={cfg['vg']}/{cfg['system']['home_lv']}",
                f"rd.lvm.lv={cfg['vg']}/{cfg['system']['swap_lv']}",
                f"ms1504.generation={generation}"]
    # Every boot artifact is verified and signed before this point. If power
    # fails after either marker write, the old signed menu still defaults to
    # the rollback snapshot and this same seal can resume from its receipt.
    atomic_write(current, (generation + "\n").encode())
    atomic_write(cmdline_file, (" ".join(cmdline) + "\n").encode())
    rows = retained_transactions(cfg)
    need(actual_root_snapshots(cfg) == {row["snapshot"] for row in rows} | {fields["snapshot"]},
         "unreceipted or missing root snapshot detected; inspect LVM and receipts")
    for row in rows:
        percent, _ = snapshot_health(cfg, row["snapshot"])
        need(percent < cfg["snapshots"]["maximum_data_percent"],
             f"retained snapshot {row['snapshot']} COW use reached the configured safety threshold")
    rows.insert(0, {**fields, "directory": str(pending_dir)})
    menu_id = publish_retained_menu(cfg, generation, rows, args)
    record_generation(cfg, generation, menu_id, fields["snapshot"])
    atomic_write(cfg["state_dir"] / "last-committed",
                 f"generation={generation}\nprevious={fields['old']}\nmenu={menu_id}\n".encode(), 0o600)
    pending_dir.rename(destination)
    fsync_dir(cfg["state_dir"])
    print(f"Published {cfg.get('policy', 'signed')} generation {generation}; prior root retained as {fields['snapshot']}.")


def current_root_source() -> str:
    return os.path.realpath(run(["findmnt", "-rn", "-M", "/", "-o", "SOURCE"]).decode().strip())


def command_rollback(cfg: dict, args: argparse.Namespace) -> None:
    root_is_live(cfg)
    check_policy(cfg)
    verify_public_key(cfg)
    need(not (cfg["state_dir"] / "pending").exists(),
         "an update transaction is still pending; complete or recover it before rollback selection")
    committed = parse_fields(cfg["state_dir"] / "last-committed")
    current = committed.get("generation", "")
    marker = Path("/etc/ms1504-generation").read_text().strip()
    rows = snapshot_rows(cfg, enforce_threshold=False)
    need(rows, "no retained rollback snapshot is available")
    requested = getattr(args, "generation", None)
    row = next((item for item in rows if item["old"] == requested), None) if requested else rows[0]
    need(row is not None, f"no retained rollback generation matches: {requested}")
    need(marker == current and current_root_source() == os.path.realpath(cfg["root"]),
         "change the rollback default from the current root origin")
    default = 1 + rows.index(row)
    menu_id = publish_retained_menu(cfg, current, rows, args, default=default)
    print(f"Rollback generation {row['old']} is now the default ({row['snapshot']}); menu {menu_id}.")


def check_pending(cfg: dict) -> None:
    root_is_live(cfg)
    check_policy(cfg)
    need(not (cfg["state_dir"] / "pruning").exists(), "unfinished pruning requires recovery")
    fields = parse_fields(cfg["state_dir"] / "pending/transaction")
    marker = Path("/etc/ms1504-generation").read_text().strip()
    need(NAME.fullmatch(marker) is not None and fields.get("old") == marker and not fields.get("new"),
         "pending transaction does not protect the current generation or seal has started")
    snapshot = f"{cfg['system']['root_lv']}_rollback_{marker}"
    need(fields.get("snapshot") == snapshot and fields.get("size_gib") == str(cfg["snapshots"]["size_gib"]),
         "pending snapshot identity/allocation differs")
    need(fields.get("snapshot_uuid") == run(["lvs", "--noheadings", "-o", "lv_uuid",
         f"/dev/{cfg['vg']}/{snapshot}"]).decode().strip(), "pending snapshot UUID differs")
    rows = retained_transactions(cfg)
    need(actual_root_snapshots(cfg) == {row["snapshot"] for row in rows} | {snapshot},
         "unreceipted or missing root snapshot detected")
    for name in [snapshot] + [row["snapshot"] for row in rows]:
        percent, size = snapshot_health(cfg, name)
        need(percent < cfg["snapshots"]["maximum_data_percent"], f"snapshot {name} reached COW safety threshold")
        need(size >= cfg["snapshots"]["size_gib"] * 1024**3, f"snapshot {name} allocation is too small")
    menu_id = (cfg["state_dir"] / "pending/menu").read_text().strip()
    need(NAME.fullmatch(menu_id) is not None, "pending menu identity is malformed")
    active = cfg["boot_dir"] / "grub/active"
    need(active.is_symlink() and os.readlink(active) == str(Path("menus") / menu_id),
         "pending recovery menu is not active")
    menu = active / "grub.cfg"
    need(menu.read_bytes() == make_menu(cfg, marker, [{"old": marker, "snapshot": snapshot}] +
         [{"old": row["old"], "snapshot": row["snapshot"]} for row in rows], default=1),
         "pending recovery menu differs from the protected transaction")
    if not signed(cfg):
        verify_signature(menu, argparse.Namespace(), cfg)
        verify_signature(cfg["boot_dir"] / "grub/grub.cfg", argparse.Namespace(), cfg)
    verify_artifact_receipt(cfg, marker)


def verify_artifact_receipt(cfg: dict, generation: str) -> None:
    receipt = parse_fields(cfg["state_dir"] / f"generation-{generation}.receipt")
    need(receipt.get("generation") == generation, "generation receipt identity differs")
    for name in ("vmlinuz", "initramfs.img"):
        path = cfg["boot_dir"] / "generations" / generation / name
        need(path.is_file() and not path.is_symlink() and
             receipt.get("sha256:" + name) == hashlib.sha256(path.read_bytes()).hexdigest(),
             f"protected boot artifact differs: {path}")


def command_verify_running(cfg: dict) -> None:
    read_root_identity(cfg)
    check_policy(cfg)
    marker = Path("/etc/ms1504-generation").read_text().strip()
    need(NAME.fullmatch(marker) is not None, "invalid running generation marker")
    cmdline = Path("/proc/cmdline").read_text().split()
    need([item for item in cmdline if item.startswith("ms1504.generation=")] == [f"ms1504.generation={marker}"],
         "running kernel generation differs from its root")
    actual = current_root_source()
    snapshot = f"/dev/{cfg['vg']}/{cfg['system']['root_lv']}_rollback_{marker}"
    need(actual in {os.path.realpath(cfg["root"]), os.path.realpath(snapshot)}, "running root is outside the generation layout")
    selected_roots = [item[5:] for item in cmdline if item.startswith("root=")]
    need(len(selected_roots) == 1, "missing or duplicate root boot argument")
    selected = selected_roots[0]
    need(os.path.realpath(selected) == actual, "running root differs from selected root")
    verify_artifact_receipt(cfg, marker)
    print(f"Running generation {marker} verified on {actual}; boot_policy={cfg.get('policy', 'signed')}")


def command_run(cfg: dict, args: argparse.Namespace) -> None:
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    need(bool(command), "run requires a command after --")
    check_pending(cfg)
    child = subprocess.Popen(command, start_new_session=True)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        while True:
            try:
                result = child.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                check_pending(cfg)
        need(result == 0, f"protected command exited with status {result}")
        check_pending(cfg)
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGINT)
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def hibernation_guard(cfg: dict) -> None:
    marker = Path("/home/.ms1504-hibernate/hibernation-pending")
    need(not marker.exists() and not marker.is_symlink(),
         "resolve pending hibernation state before changing generations")
    swap = f"/dev/{cfg['vg']}/{cfg['system']['swap_lv']}"
    need(run(["blkid", "-p", "-s", "TYPE", "-o", "value", swap]).decode().strip() == "swap",
         "encrypted swap contains a hibernation image or unknown signature")


def command_status(cfg: dict) -> None:
    root = Path("/etc/ms1504-generation")
    print(f"boot_policy={cfg.get('policy', 'signed')} (firmware enforcement is a separate hardware acceptance)")
    print(f"root={cfg['root']}")
    print(f"generation={root.read_text().strip() if root.is_file() else 'uninitialized'}")
    pending = cfg["state_dir"] / "pending"
    print(f"transaction={'pending' if pending.exists() else 'none'}")
    for path in sorted(cfg["state_dir"].glob("merge-pending-*")):
        print(f"recovery_receipt={path.name} requires inspection")
    if (cfg["state_dir"] / "pruning").exists():
        print("recovery_receipt=pruning requires inspection")
    rows = retained_transactions(cfg)
    if pending.exists():
        check_pending(cfg)
        rows.insert(0, parse_fields(pending / "transaction"))
    problems = ["unfinished pruning requires recovery"] if (cfg["state_dir"] / "pruning").exists() else []
    try:
        actual = actual_root_snapshots(cfg)
        expected = {row["snapshot"] for row in rows}
        if actual != expected:
            problems.append("snapshot inventory differs")
            print(f"snapshot_inventory=REQUIRES_INSPECTION expected={sorted(expected)} actual={sorted(actual)}")
    except (RuntimeError, OSError) as exc:
        problems.append(str(exc))
        print(f"snapshot_inventory=REQUIRES_INSPECTION {exc}")
    for row in rows:
        snapshot = row["snapshot"]
        try:
            percent, size = snapshot_health(cfg, snapshot)
            threshold = cfg["snapshots"]["maximum_data_percent"]
            if percent >= threshold:
                problems.append(f"snapshot {snapshot} reached safety threshold")
            status = f"{'WARNING' if percent >= threshold else 'healthy'} data_percent={percent:g} threshold={threshold} cow_bytes={size}"
        except (RuntimeError, OSError) as exc:
            problems.append(str(exc))
            status = f"REQUIRES_INSPECTION {exc}"
        print(f"rollback={row.get('old', '?')} snapshot={snapshot} {status}")
    if not problems:
        capacity = snapshot_capacity(cfg, rows)
        print('snapshot_capacity: ' + ' '.join(f'{key}={value}' for key, value in capacity.items()))
    need(not problems, "; ".join(problems))


def confirm_tty(prompt: str, expected: str) -> None:
    need(sys.stdin.isatty(), "this operation requires an interactive terminal")
    print(f"{prompt}\nType exactly: {expected}", file=sys.stderr)
    with open("/dev/tty", "r", encoding="utf-8") as tty:
        need(tty.readline().rstrip("\n") == expected, "confirmation did not match; no destructive operation requested")


def command_prune(cfg: dict, args: argparse.Namespace) -> None:
    root_is_live(cfg)
    check_policy(cfg)
    verify_public_key(cfg)
    need(not (cfg["state_dir"] / "pending").exists(),
         "finish or recover the pending update before pruning")
    current = Path("/etc/ms1504-generation").read_text().strip()
    need(NAME.fullmatch(current) is not None, "current generation marker is invalid")
    rows = retained_transactions(cfg)
    actual = actual_root_snapshots(cfg)
    expected = {item["snapshot"] for item in rows}
    pruning = cfg["state_dir"] / "pruning"
    interrupted = parse_fields(pruning) if pruning.exists() else None
    if interrupted:
        need(interrupted.get("generation") == args.generation, "resume the recorded pruning operation first")
        need(interrupted.get("snapshot") == f"{cfg['system']['root_lv']}_rollback_{args.generation}", "pruning snapshot identity differs")
        need(actual in (expected, expected - {interrupted["snapshot"]}), "unexpected snapshot inventory during prune recovery")
    else:
        need(actual == expected, "unreceipted or missing root snapshot detected; inspect LVM and receipts")
    row = next((item for item in rows if item.get("old") == args.generation), None)
    if row is None and interrupted:
        need(interrupted["snapshot"] not in actual, "prune receipt lost while its LV still exists")
        row = {"old": args.generation, "snapshot": interrupted["snapshot"],
               "directory": str(cfg["state_dir"] / f"completed-{args.generation}")}
    need(row is not None, f"no retained generation matches {args.generation}")
    need(row["old"] != current, "cannot prune the currently installed generation")
    snapshot = row["snapshot"]
    snapshot_path = Path(f"/dev/{cfg['vg']}/{snapshot}")
    if snapshot_path.exists():
        origin = run(["lvs", "--noheadings", "-o", "origin", str(snapshot_path)]).decode().strip()
        need(origin == cfg["system"]["root_lv"], "selected snapshot has an unexpected origin")
        mounts = run(["findmnt", "-rn", "-o", "SOURCE"]).decode().splitlines()
        need(os.path.realpath(str(snapshot_path)) not in
             {os.path.realpath(source) for source in mounts}, "selected snapshot is mounted")
    confirmation = f"PRUNE {row['old']} {snapshot}"
    confirm_tty("This permanently removes this rollback snapshot and its retained boot artifacts.", confirmation)
    pruning = cfg["state_dir"] / "pruning"
    atomic_write(pruning, f"generation={row['old']}\nsnapshot={snapshot}\nstarted={datetime.now(timezone.utc).isoformat()}\n".encode(), 0o600)
    remaining = [item for item in rows if item is not row]
    for item in remaining:
        snapshot_health(cfg, item["snapshot"])
    menu_id = publish_retained_menu(cfg, current, remaining, args)
    if snapshot_path.exists():
        run(["lvremove", "--yes", str(snapshot_path)])
    completed = Path(row["directory"])
    need(completed.parent == cfg["state_dir"] and completed.name == f"completed-{row['old']}",
         "rollback receipt directory is outside the managed state tree")
    if completed.exists():
        shutil.rmtree(completed)
    artifact_dir = cfg["boot_dir"] / "generations" / row["old"]
    if artifact_dir.exists():
        need(artifact_dir.parent == cfg["boot_dir"] / "generations" and not artifact_dir.is_symlink(),
             "rollback artifact directory is unsafe")
        shutil.rmtree(artifact_dir)
    receipt = cfg["state_dir"] / f"generation-{row['old']}.receipt"
    if receipt.exists():
        need(receipt.is_file() and not receipt.is_symlink(), "generation receipt is unsafe")
        receipt.unlink()
    pruning.unlink(missing_ok=True)
    fsync_dir(cfg["state_dir"])
    print(f"Pruned rollback generation {row['old']}; active generation menu is {menu_id}.")


def command_merge_rollback(cfg: dict, args: argparse.Namespace) -> None:
    need(args.recovery_root, "merge-rollback requires --recovery-root pointing to the mounted target root")
    target = Path(args.recovery_root)
    need(target.is_dir() and (target / "etc/ms1504-root-identity").is_file(),
         "recovery root does not contain the installed target identity")
    identity_fields = parse_fields(target / "etc/ms1504-root-identity")
    validate_root_identity(identity_fields, cfg)
    state = target / cfg["state_dir"].relative_to("/")
    current_marker = (target / "etc/ms1504-generation").read_text().strip()
    need(NAME.fullmatch(current_marker) is not None, "target generation marker is malformed")
    check_policy({**cfg, "boot_dir": target / "boot"}, target=target)
    completed = state / f"completed-{args.generation}"
    if not completed.exists():
        completed = state / "pending"
    fields = parse_fields(completed / "transaction")
    need(fields.get("snapshot") == f"{cfg['system']['root_lv']}_rollback_{args.generation}",
         "rollback snapshot name differs from the requested generation")
    need(fields.get("old") == args.generation and NAME.fullmatch(fields.get("old", "")) is not None,
         "selected rollback receipt does not match the requested generation")
    recorded = retained_transactions({**cfg, "state_dir": state})
    pending_dir = state / "pending"
    newest = parse_fields(pending_dir / "transaction")["old"] if pending_dir.exists() else (recorded[0]["old"] if recorded else None)
    need(newest == args.generation, "offline merge currently supports the newest recovery generation only")
    expected_snapshots = {row["snapshot"] for row in recorded}
    if pending_dir.exists():
        expected_snapshots.add(parse_fields(pending_dir / "transaction")["snapshot"])
    need(actual_root_snapshots(cfg) == expected_snapshots, "unreceipted or missing snapshot before offline merge")
    snapshot = f"/dev/{cfg['vg']}/{fields['snapshot']}"
    origin = cfg["root"]
    need(Path(snapshot).exists(), "rollback snapshot LV is missing")
    snapshot_health(cfg, fields["snapshot"])
    if fields.get("snapshot_uuid"):
        need(fields["snapshot_uuid"] == run(["lvs", "--noheadings", "-o", "lv_uuid", snapshot]).decode().strip(),
             "rollback snapshot UUID changed")
    origin_name = run(["lvs", "--noheadings", "-o", "origin", snapshot]).decode().strip()
    need(origin_name == cfg["system"]["root_lv"], f"snapshot origin mismatch: {origin_name}")
    need(current_root_source() not in {os.path.realpath(origin), os.path.realpath(snapshot)},
         "boot a recovery environment; the origin root cannot be merged while running")
    mounted_root = os.path.realpath(run(["findmnt", "-rn", "-M", str(target), "-o", "SOURCE"]).decode().strip())
    need(mounted_root in {os.path.realpath(origin), os.path.realpath(snapshot)},
         "recovery-root must be the configured origin or selected rollback snapshot mount")
    need(not (target / "home/.ms1504-hibernate/hibernation-pending").exists(),
         "resolve pending hibernation state before merging a root generation")
    run(["umount", "-R", str(target)])
    mounts = run(["findmnt", "-rn", "-o", "SOURCE,TARGET"]).decode().splitlines()
    snapshot_devices = [f"/dev/{cfg['vg']}/{name}" for name in sorted(expected_snapshots)]
    devices = (origin, *snapshot_devices, f"/dev/{cfg['vg']}/{cfg['system']['home_lv']}",
               f"/dev/{cfg['vg']}/{cfg['system']['swap_lv']}")
    for device in devices:
        mounted = [line.split(None, 1)[1] for line in mounts if len(line.split(None, 1)) == 2
                   and os.path.realpath(line.split(None, 1)[0]) == os.path.realpath(device)]
        need(not mounted, f"refusing merge while {device} is mounted at {', '.join(mounted)}")
    swaps = {os.path.realpath(row.split()[0]) for row in Path("/proc/swaps").read_text().splitlines()[1:]}
    need(not swaps.intersection(os.path.realpath(device) for device in devices), "target swap is active; swapoff it in recovery first")
    swap_type = run(["blkid", "-p", "-s", "TYPE", "-o", "value", devices[-1]]).decode().strip()
    need(swap_type == "swap", "resolve hibernation/unknown swap state before offline merging")
    receipt_dir = Path(args.recovery_receipts)
    need(receipt_dir.is_absolute() and receipt_dir.is_dir() and os.path.ismount(receipt_dir),
         "recovery receipts must be stored on a separately mounted persistent filesystem")
    receipt_source = os.path.realpath(run(["findmnt", "-rn", "-M", str(receipt_dir), "-o", "SOURCE"]).decode().strip())
    receipt_type = run(["findmnt", "-rn", "-M", str(receipt_dir), "-o", "FSTYPE"]).decode().strip()
    need(receipt_type not in ("tmpfs", "ramfs") and receipt_source not in
         {os.path.realpath(origin), os.path.realpath(snapshot),
          os.path.realpath(f"/dev/{cfg['vg']}/{cfg['system']['home_lv']}"),
          os.path.realpath(f"/dev/{cfg['vg']}/{cfg['system']['swap_lv']}")},
         "recovery receipts must survive a root merge on storage outside the target LVM volumes")
    target_lvs = {os.path.realpath(value) for value in run(["lvs", "--noheadings", "-o", "lv_path", cfg["vg"]]).decode().split()}
    receipt_ancestors = {os.path.realpath(value) for value in run(["lsblk", "-s", "-r", "-n", "-o", "PATH", receipt_source]).decode().split()}
    need(not target_lvs.intersection(receipt_ancestors), "recovery receipts are backed by the target VG")
    merge_receipt = receipt_dir / f"merge-pending-{fields['old']}"
    completed_receipt = receipt_dir / f"merge-complete-{fields['old']}"
    need(not merge_receipt.exists() and not completed_receipt.exists(),
         "an earlier merge attempt has a durable pending receipt; inspect LVM metadata before retrying")
    confirmation = f"MERGE {fields['snapshot']} INTO {cfg['system']['root_lv']}"
    confirm_tty("This permanently restores the selected root snapshot into the root LV.", confirmation)
    atomic_write(merge_receipt,
                 f"generation={fields['old']}\nsnapshot={fields['snapshot']}\nstarted={datetime.now(timezone.utc).isoformat()}\n".encode(), 0o600)
    # Persist all transaction inputs outside the root before changing LVM.
    atomic_write(receipt_dir / f"merge-transaction-{fields['old']}",
                 ("\n".join(f"{k}={v}" for k, v in fields.items()) + "\n").encode(), 0o600)
    # Deactivating the origin also deactivates its dependent classic snapshots.
    # Naming a snapshot explicitly instead prompts to change its origin.
    run(["lvchange", "--activate", "n", origin])
    run(["lvconvert", "--merge", snapshot])
    if Path(snapshot).exists():
        run(["lvchange", "--activate", "y", origin])
        deadline = time.monotonic() + 24 * 60 * 60
        while Path(snapshot).exists() and time.monotonic() < deadline:
            time.sleep(2)
        if Path(snapshot).exists():
            raise RuntimeError("offline merge did not complete; leave target offline and inspect LVM")
    run(["lvchange", "--activate", "y", origin])
    need(not os.path.ismount(target), "recovery mountpoint unexpectedly remained mounted")
    run(["mount", "-t", "ext4", "-o", "rw", origin, str(target)])
    try:
        restored_marker = target / "etc/ms1504-generation"
        need(restored_marker.read_text().strip() == fields["old"],
             "merged snapshot generation marker differs from its receipt")
        restored_pending = state / "pending"
        if restored_pending.exists():
            pending_fields = parse_fields(restored_pending / "transaction")
            need(pending_fields.get("old") == fields["old"] and
                 pending_fields.get("snapshot") == fields["snapshot"],
                 "restored pending transaction does not match the merged snapshot; inspect before cleanup")
            shutil.rmtree(restored_pending)
        restored_cfg = {**cfg, "boot_dir": target / "boot", "state_dir": state}
        if signed(cfg):
            restored_cfg["public_key"] = target / cfg["public_key"].relative_to("/")
        # The consumed snapshot must not remain in either restored receipts or menu.
        stale = state / f"completed-{fields['old']}"
        if stale.exists():
            shutil.rmtree(stale)
        rows = retained_transactions(restored_cfg)
        actual = actual_root_snapshots(cfg)
        # Older receipts can predate an explicitly pruned LV. Keep them as history,
        # but do not restore them as live recovery entries after merging.
        for row in rows:
            if row["snapshot"] not in actual:
                archive = state / "recovered-receipts"
                archive.mkdir(exist_ok=True, mode=0o700)
                Path(row["directory"]).rename(archive / Path(row["directory"]).name)
        rows = retained_transactions(restored_cfg)
        need(actual_root_snapshots(cfg) == {row["snapshot"] for row in rows},
             "restored root has unreceipted snapshots; keep recovery mounted for inspection")
        cmdline = (target / "etc/kernel/cmdline").read_text().split()
        menu = make_menu(cfg, fields["old"], [{"old": row["old"], "snapshot": row["snapshot"]} for row in rows], base_cmdline=cmdline)
        # Signed offline recovery requires credentials to publish the repaired menu.
        menu_id = publish(restored_cfg, menu, args, initialize=True)
        atomic_write(state / "last-committed",
                     f"generation={fields['old']}\nprevious=none\nmenu={menu_id}\n".encode(), 0o600)
        atomic_write(state / f"merged-{fields['old']}",
                     f"generation={fields['old']}\nsnapshot={fields['snapshot']}\ncompleted={datetime.now(timezone.utc).isoformat()}\n".encode(), 0o600)
        atomic_write(completed_receipt,
                     f"generation={fields['old']}\nsnapshot={fields['snapshot']}\ncompleted={datetime.now(timezone.utc).isoformat()}\n".encode(), 0o600)
        fsync_dir(state)
        fsync_dir(receipt_dir)
        merge_receipt.unlink(missing_ok=True)
        fsync_dir(receipt_dir)
    finally:
        run(["umount", str(target)])
    run(["lvchange", "--activate", "n", origin])
    print("Rollback snapshot merged. Boot recovery media, activate and mount root, then verify its generation marker.")


def main(*, default_config: Path | None = None, require_signed: bool = True) -> int:
    global CONFIG
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config or CONFIG)
    keys = parser.add_mutually_exclusive_group(required=False)
    keys.add_argument("--secret-key-file", help="encrypted GPG secret-key export; imported into temporary /run GPG home")
    keys.add_argument("--gnupg-home", help="GPG home, including a hardware-token key stub")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    sub.add_parser("check-pending")
    sub.add_parser("verify-running")
    monitored = sub.add_parser("run")
    monitored.add_argument("command", nargs=argparse.REMAINDER)
    sub.add_parser("initialize")
    sub.add_parser("begin")
    seal = sub.add_parser("seal")
    seal.add_argument("--generation", required=True)
    seal.add_argument("--kernel", required=True)
    seal.add_argument("--initramfs", required=True)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--generation", help="retained generation to make the default")
    prune = sub.add_parser("prune")
    prune.add_argument("--generation", required=True, help="exact retained generation to remove")
    merge = sub.add_parser("merge-rollback")
    merge.add_argument("--generation", required=True, help="exact generation snapshot to restore")
    merge.add_argument("--recovery-root", required=True)
    merge.add_argument("--recovery-receipts", required=True,
                       help="mounted persistent filesystem outside the target LVM volumes")
    args = parser.parse_args()
    CONFIG = args.config
    lock_fd = None
    try:
        need(os.geteuid() == 0, "run this tool as root")
        protected_path(args.config)
        cfg = read_config()
        if args.action != "merge-rollback":
            protected_path(cfg["state_dir"])
            protected_path(cfg["boot_dir"])
        need(not require_signed or signed(cfg), "legacy signed-generation entry point requires signed policy")
        if args.action != "merge-rollback":
            check_policy(cfg, initialize=args.action == "initialize")
        if not signed(cfg):
            need(not args.secret_key_file and not args.gnupg_home, "unsigned policy does not accept signing credentials")
        LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        protected_path(LOCK)
        lock_fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        shared = args.action in ("status", "check-pending", "verify-running", "run")
        fcntl.flock(lock_fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        if args.action != "merge-rollback":
            check_policy(cfg, initialize=args.action == "initialize")
        if args.action != "merge-rollback":
            state = cfg["state_dir"]
            need(not state.is_symlink(), "generation state must not be a symlink")
            state.mkdir(parents=True, exist_ok=True, mode=0o700)
            need(state.stat().st_uid == 0 and not state.stat().st_mode & 0o022,
                 "generation state must be root-owned and protected")
        if args.action not in ("status", "verify-running", "check-pending", "merge-rollback"):
            hibernation_guard(cfg)
        if args.action == "status":
            command_status(cfg)
        elif args.action == "verify-running":
            command_verify_running(cfg)
        elif args.action == "check-pending":
            check_pending(cfg)
            print("Healthy pending root-generation transaction verified.")
        elif args.action == "run":
            command_run(cfg, args)
        else:
            if signed(cfg):
                need(bool(args.secret_key_file) != bool(args.gnupg_home),
                     "mutating commands require exactly one signing source: --secret-key-file or --gnupg-home")
            if args.action == "initialize": command_initialize(cfg, args)
            elif args.action == "begin": command_begin(cfg, args)
            elif args.action == "seal": command_seal(cfg, args)
            elif args.action == "rollback": command_rollback(cfg, args)
            elif args.action == "prune": command_prune(cfg, args)
            else: command_merge_rollback(cfg, args)
        return 0
    except KeyboardInterrupt:
        print("generation: interrupted; pending recovery checkpoint retained", file=sys.stderr)
        return 130
    except (RuntimeError, OSError, KeyError, ValueError) as exc:
        print(f"generation: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
