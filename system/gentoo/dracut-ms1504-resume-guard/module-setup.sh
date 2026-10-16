#!/bin/bash

check() {
    return 0
}

depends() {
    echo resume
    return 0
}

install() {
    inst_multiple blkid awk
    inst_hook pre-mount 05 "$moddir/check-generation.sh"
}
