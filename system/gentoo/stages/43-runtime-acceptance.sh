#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Recompile, link and execute on every resume. A receipt alone is insufficient.
cxx_runtime_action() { cxx_runtime_verify; }
fingerprint=$(cxx_fingerprint stages/43-runtime-acceptance.sh lib/runtime.sh \
    probes/runtime.c probes/runtime.cc "$WORK_ROOT/state/42-libstdcxx.done")
gentoo_run_stage 43-runtime-acceptance "$fingerprint" cxx_runtime_action cxx_runtime_verify
printf 'Multilib C/C++ accepted: installed and intermediate drivers compile, link and execute ELF64/ELF32.\n'
printf 'Batch 4 complete. Stop here for review; no Portage or installed-root changes yet.\n'
