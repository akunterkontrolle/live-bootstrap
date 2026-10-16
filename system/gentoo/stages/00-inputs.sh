#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# First milestone: all inputs must survive loss of /steps and install media.
gentoo_inputs_action() {
    gentoo_verify_bundle "$SCRIPT_DIR"
    [ -s "$SCRIPT_DIR/config.sh" ] && [ -s "$SCRIPT_DIR/config.inputs.sh" ]
}
gentoo_inputs_verify() { gentoo_verify_bundle "$SCRIPT_DIR"; }

bundle_digest=$(sha256sum < "$SCRIPT_DIR/SHA256SUMS" | cut -d' ' -f1)
# Each snapshot has its own input receipt; later batches can add scripts without
# altering the receipts for expensive compiler stages.
gentoo_run_stage "00-inputs-$bundle_digest" "$bundle_digest" gentoo_inputs_action gentoo_inputs_verify
