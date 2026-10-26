"""Discover the maintained, opt-in disposable LVM probe in the ordinary suite."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

spec = spec_from_file_location('generation_lvm_probe', Path(__file__).resolve().parents[1] / 'system/gentoo/probes/generation-lvm.py')
probe = module_from_spec(spec)
spec.loader.exec_module(probe)
RealSnapshotLifecycle = probe.RealSnapshotLifecycle
