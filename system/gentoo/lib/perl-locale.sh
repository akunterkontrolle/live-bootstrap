#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# The glibc continuation needs NAME=value metadata for composite LC_ALL.
perl_locale_install_policy() {
    local directory="$NATIVE_ROOT/etc/portage" target
    chroot_env /usr/bin/python3.12 -c '
import ctypes, locale
libc = ctypes.CDLL(None)
libc.setlocale.argtypes = (ctypes.c_int, ctypes.c_char_p)
libc.setlocale.restype = ctypes.c_char_p
assert libc.setlocale(locale.LC_ALL, b"C") == b"C"
assert libc.setlocale(locale.LC_TIME, b"C.UTF-8") == b"C.UTF-8"
value = libc.setlocale(locale.LC_ALL, None)
assert value, "libc LC_ALL query failed"
parts = dict(part.split(b"=", 1) for part in value.split(b";"))
assert parts[b"LC_TIME"] == b"C.UTF-8" and parts[b"LC_CTYPE"] == b"C", value
print("Native libc composite locale uses verified name/value pairs")
'
    install -d -m 0755 "$directory/env" "$directory/package.env"
    target="$directory/env/90-gentoo-perl-locale.conf"
    cat > "$target.tmp" <<'EOF'
# glibc LC_ALL syntax, verified by the continuation's libc and Perl probes.
# The pinned Perl ebuild supports EXTRA_ECONF Configure arguments.
EXTRA_ECONF="${EXTRA_ECONF} -Dd_perl_lc_all_uses_name_value_pairs=define -Ud_perl_lc_all_separator -Ud_perl_lc_all_category_positions_init"
EOF
    if [ -e "$target" ] || [ -L "$target" ]; then
        [ -f "$target" ] && [ ! -L "$target" ] && cmp -s "$target.tmp" "$target" || {
            rm -f "$target.tmp"
            gentoo_die 'existing Perl locale environment differs; inspect before replacing'
        }
    fi
    chmod 0644 "$target.tmp"; mv "$target.tmp" "$target"
    target="$directory/package.env/90-gentoo-perl-locale"
    printf 'dev-lang/perl 90-gentoo-perl-locale.conf\n' > "$target.tmp"
    if [ -e "$target" ] || [ -L "$target" ]; then
        [ -f "$target" ] && [ ! -L "$target" ] && cmp -s "$target.tmp" "$target" || {
            rm -f "$target.tmp"
            gentoo_die 'existing Perl locale package selection differs; inspect before replacing'
        }
    fi
    chmod 0644 "$target.tmp"; mv "$target.tmp" "$target"
}

perl_locale_probe() {
    local program
    program=$(cat <<'PERL'
use strict;
use warnings;
use Config;
use POSIX qw(setlocale LC_ALL LC_CTYPE LC_TIME);
die "Perl lacks glibc name/value locale metadata\n"
    unless $Config{d_perl_lc_all_uses_name_value_pairs} eq 'define';
for my $base ('C', 'C.UTF-8') {
    setlocale(LC_ALL, $base) or die "Cannot set $base\n";
    my ($category, $name, $value, $other, $expected) = $base eq 'C'
        ? (LC_TIME, 'LC_TIME', 'C.UTF-8', 'LC_CTYPE', 'C')
        : (LC_CTYPE, 'LC_CTYPE', 'C', 'LC_TIME', 'C.UTF-8');
    setlocale($category, $value) or die "Cannot change $name\n";
    my $all = setlocale(LC_ALL);
    my %parts = map { split /=/, $_, 2 } split /;/, $all;
    die "Incorrect mixed locale: $all\n"
        unless $parts{$name} eq $value && $parts{$other} eq $expected;
    setlocale(LC_ALL, $all) or die "Composite locale round-trip failed\n";
    die "Locale round-trip changed $name\n" unless setlocale($category) eq $value;
}
setlocale(LC_ALL, 'C') or die "Cannot restore C locale\n";
print "Perl mixed-category locale round-trips passed\n";
PERL
)
    chroot_env /usr/bin/python3.12 -c '
import os, subprocess, sys
os.setgroups([])
os.setgid(250)
os.setuid(250)
for value in ("C", "C.UTF-8"):
    subprocess.run(["/usr/bin/perl", "-e", sys.argv[1]],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": value, "LANG": value},
        timeout=20, check=True)
' "$program"
}

ensure_perl_locale() {
    perl_locale_install_policy
    if perl_locale_probe; then return; fi
    PHASE=repair-gentoo-perl-locale-metadata
    run_emerge --oneshot =dev-lang/perl-5.44.0
    perl_locale_probe || gentoo_die 'rebuilt Gentoo Perl still fails mixed-locale acceptance'
}
