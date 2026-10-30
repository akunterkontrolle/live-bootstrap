#!/bin/sh
. /lib/dracut-lib.sh
. /lib/ms1504-resume-lib.sh
ms1504_resume || die 'Generation-aware resume validation failed; refusing to mount root.'
