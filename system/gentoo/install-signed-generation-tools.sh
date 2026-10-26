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
destination=/usr/local/share/ms1504-bootstrap/gentoo
install -d -m 0755 "$destination" "$destination/probes" /usr/local/sbin
for file in signed-generation.py generation.py generation.example.toml GENERATIONS.md signed-generation.example.toml SIGNED-GENERATIONS.md; do
    temporary="$destination/.$file.new"
    install -m 0644 "$source_dir/$file" "$temporary"
    if [[ $file == signed-generation.py || $file == generation.py ]]; then chmod 0755 "$temporary"; fi
    mv -f -- "$temporary" "$destination/$file"
done
install -m 0644 "$source_dir/probes/generation-lvm.py" "$destination/probes/generation-lvm.py"
ln -sfn "$destination/generation.py" /usr/local/sbin/ms1504-generation
ln -sfn "$destination/signed-generation.py" /usr/local/sbin/ms1504-signed-generation
python3 -m py_compile "$destination/signed-generation.py"
echo 'Installed unsigned/signed generation entrypoints, config examples, and runbooks.'
