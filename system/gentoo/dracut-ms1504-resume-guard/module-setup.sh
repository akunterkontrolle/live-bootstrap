#!/bin/bash

check() {
    return 0
}

depends() {
    echo 'crypt lvm'
    return 0
}

install() {
    if [[ " $dracutmodules " == *" resume "* ]]; then
        dfatal 'ms1504 resume guard requires omission of the stock resume module'
        return 1
    fi
    inst_multiple blkid awk readlink cat uname mount umount mkdir stat
    inst_simple "$moddir/resume-lib.sh" /lib/ms1504-resume-lib.sh
    if [[ -f ${dracutsysrootdir:-}/etc/ms1504-hibernate-ready ]]; then
        inst_simple /etc/ms1504-hibernate-ready
    fi
    inst_hook pre-mount 05 "$moddir/check-generation.sh"
}
