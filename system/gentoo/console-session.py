#!/usr/bin/env python3
"""Give a bootstrap command a controlling serial terminal when init has none."""
# SPDX-License-Identifier: GPL-3.0-or-later
import fcntl
import os
import sys
import termios


def main(argv):
    if not argv:
        raise SystemExit('usage: console-session.py COMMAND [ARG ...]')
    child = os.fork()
    if child == 0:
        try:
            if os.isatty(0):
                # Fork first: a process-group leader cannot call setsid().
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)
                os.tcsetpgrp(0, os.getpgrp())
            os.environ['GENTOO_CONSOLE_SESSION'] = '1'
            os.execvp(argv[0], argv)
        except Exception as error:
            print('gentoo: cannot start console session: ' + str(error), file=sys.stderr, flush=True)
            os._exit(1)
    while True:
        try:
            _, status = os.waitpid(child, 0)
            break
        except InterruptedError:
            continue
    result = os.waitstatus_to_exitcode(status)
    if result < 0:
        result = 128 - result
    if os.getpid() == 1:
        print('Continuation stopped; returning PID 1 to an interactive shell.', flush=True)
        os.execv('/bin/bash', ['/bin/bash', '-i'])
    return result


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
