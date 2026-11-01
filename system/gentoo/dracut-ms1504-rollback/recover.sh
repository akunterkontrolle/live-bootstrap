#!/bin/sh
. /lib/dracut-lib.sh
if getargbool 0 rd.ms1504.recovery; then
    echo 'Internal RAM recovery: root will NOT be mounted for normal boot.' >/dev/console
    if setsid --ctty --wait /usr/bin/ms1504-python /lib/ms1504-offline/generation_recovery.py --menu </dev/console >/dev/console 2>&1; then
        sync
        reboot -f
    fi
    echo 'Recovery needs inspection. Normal boot is blocked; an advanced RAM shell follows.' >/dev/console
    # Never fall through to the resume guard or switch_root, even if a shell
    # exits or a recovery command fails. Keep the machine in RAM for inspection.
    while :; do
        setsid --ctty --wait /bin/bash -i </dev/console >/dev/console 2>&1
        echo 'Recovery shell exited; normal boot remains blocked. Reboot when ready.' >/dev/console
    done
fi
