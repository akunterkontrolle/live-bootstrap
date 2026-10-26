#!/usr/bin/env python3
"""Neutral entry point for explicit unsigned or signed generation transactions."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

if __name__ == '__main__':
    spec = spec_from_file_location('ms1504_generation', Path(__file__).resolve().with_name('signed-generation.py'))
    manager = module_from_spec(spec)
    spec.loader.exec_module(manager)
    raise SystemExit(manager.main(default_config=Path('/etc/ms1504-generation.toml'), require_signed=False))
