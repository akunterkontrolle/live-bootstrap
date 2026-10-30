#!/bin/sh
# elogind passes pre/post and the actual operation. Keep ordinary S3 unchanged.
case "$2" in
    suspend) exit 0 ;;
    hibernate) ;;
    *) echo 'ms1504: unreviewed sleep operation refused' >&2; exit 1 ;;
esac
case "$1" in pre|post) ;; *) exit 1 ;; esac
exec /usr/local/sbin/ms1504-hibernate "$1"
