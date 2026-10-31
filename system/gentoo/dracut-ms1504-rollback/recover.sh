#!/bin/sh
. /lib/dracut-lib.sh
if getargbool 0 rd.ms1504.recovery; then
    echo 'Internal RAM recovery: root will NOT be mounted for normal boot.' >/dev/console
    echo 'Run: /usr/bin/ms1504-python /lib/ms1504-offline/generation_recovery.py GENERATION' >/dev/console
    echo 'Example generation: desktop-hibernate-1. After success run: reboot -f' >/dev/console
    echo 'Signed recovery additionally needs --secret-key-file PATH on unlocked signing storage.' >/dev/console
    # Never fall through to the resume guard or switch_root, even if a shell
    # exits or a recovery command fails. Keep the machine in RAM for inspection.
    while :; do
        setsid --ctty --wait /bin/bash -i </dev/console >/dev/console 2>&1
        echo 'Recovery shell exited; normal boot remains blocked. Reboot when ready.' >/dev/console
    done
fi
