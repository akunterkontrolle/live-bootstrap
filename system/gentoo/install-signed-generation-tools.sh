#!/bin/bash
# Install reviewed generation-management files onto an already-built Gentoo.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: sudo $0 /path/to/linux24-gentoo/system/gentoo" >&2
    exit 2
fi
[[ $EUID -eq 0 ]] || { echo 'Run this installer as root.' >&2; exit 1; }
install -d -m 0700 /run/ms1504-generations
exec 9>/run/ms1504-generations/lock
flock -n 9 || { echo "A generation operation is running; stop it before installing tools." >&2; exit 1; }
source_dir=$(readlink -f -- "$1")
[[ -s $source_dir/probes/generation-lvm.py && -s $source_dir/generation.py && -s $source_dir/generation.example.toml && -s $source_dir/GENERATIONS.md &&
   -s $source_dir/signed-generation.py && -s $source_dir/signed-generation.example.toml &&
   -s $source_dir/SIGNED-GENERATIONS.md ]] || {
    echo "Generation source files are incomplete: $source_dir" >&2
    exit 1
}
files=(signed-generation.py generation.py generation_transition.py generation_recovery.py
    GENERATION-TRANSITIONS.md generation.example.toml GENERATIONS.md signed-generation.example.toml SIGNED-GENERATIONS.md
    hibernate.py hibernate-hook.sh hibernate-sleep.conf source-only-emerge.py probes/generation-lvm.py
    dracut-ms1504-resume-guard/module-setup.sh dracut-ms1504-resume-guard/check-generation.sh
    dracut-ms1504-resume-guard/resume-lib.sh dracut-ms1504-rollback/module-setup.sh dracut-ms1504-rollback/recover.sh)
for file in "${files[@]}"; do
    [[ -s $source_dir/$file && ! -L $source_dir/$file ]] || { echo "Missing regular source: $file" >&2; exit 1; }
done
python3 - "$source_dir" <<'PY'
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
spec = spec_from_file_location('install_generations', Path(sys.argv[1]) / 'signed-generation.py')
m = module_from_spec(spec)
spec.loader.exec_module(m)
m.CONFIG = Path('/etc/ms1504-generation.toml')
if m.CONFIG.exists():
    m.protected_path(m.CONFIG)
    cfg = m.read_config()
    m.root_is_live(cfg)
    m.hibernation_guard(cfg)
    if (cfg['state_dir'] / 'pending').exists():
        m.check_pending(cfg)
    else:
        m.need((cfg['state_dir'] / 'signing-migration').exists(),
               'begin a protected generation transaction before updating installed tools')
PY
release=$(python3 - "$source_dir" "${files[@]}" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
checksum = hashlib.sha256()
for name in sorted(sys.argv[2:]):
    content = (root / name).read_bytes()
    if name.endswith('.py'):
        compile(content, name, 'exec')
    checksum.update(name.encode() + b'\0' + content + b'\0')
print(checksum.hexdigest())
PY
)
destination=/usr/local/share/ms1504-generation-tools/$release
install -d -m 0755 "$destination" /usr/local/sbin
for file in "${files[@]}"; do
    install -d -m 0755 "$destination/$(dirname "$file")"
    if [[ -e $destination/$file ]]; then
        cmp -s "$source_dir/$file" "$destination/$file" || { echo "Changed installed release: $file" >&2; exit 1; }
    else
        install -m 0644 "$source_dir/$file" "$destination/$file"
    fi
done
chmod 0755 "$destination/generation.py" "$destination/signed-generation.py" "$destination/hibernate.py"
# Update the global package guard and hibernation entry point as a cohort:
# their migration interlocks must not remain on an older pinned implementation.
guard=/usr/local/libexec/gentoo-continuation/source-only-emerge.py
if [[ -e $guard ]]; then
    [[ -f $guard && ! -L $guard ]] || { echo 'Custom package guard; inspect before updating.' >&2; exit 1; }
    old_digest=$(sha256sum "$guard"); old_digest=${old_digest%% *}
    install -d -m 0700 /var/lib/ms1504-generation-tools
    install -m 0600 "$guard" "/var/lib/ms1504-generation-tools/source-only-emerge-$old_digest.py"
    install -m 0755 "$destination/source-only-emerge.py" "$guard.new"
    mv -f "$guard.new" "$guard"
fi
for pair in ms1504-generation:generation.py ms1504-signed-generation:signed-generation.py ms1504-hibernate:hibernate.py; do
    entry=${pair%%:*}; file=${pair#*:}
    [[ ! -e /usr/local/sbin/$entry || -L /usr/local/sbin/$entry ]] || { echo "Custom entry point: $entry" >&2; exit 1; }
    ln -sfn "$destination/$file" "/usr/local/sbin/.$entry.new"
    mv -Tf "/usr/local/sbin/.$entry.new" "/usr/local/sbin/$entry"
done
echo "Installed generation, recovery, migration and hibernation tools: $release"
