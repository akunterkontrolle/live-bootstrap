"""Bootstrap only from an effective source seed, then visit each Rust minor."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

spec = spec_from_file_location('desktop_rust', Path(__file__).resolve().parents[1] / 'system/gentoo/desktop-rust-bootstrap.py')
helper = module_from_spec(spec); spec.loader.exec_module(helper)


class RustBootstrapTests(unittest.TestCase):
    def test_newer_recipe_with_masked_bootstrap_is_not_a_seed(self):
        visible = ['dev-lang/rust-1.74.1-r101', 'dev-lang/rust-1.90.0-r1', 'dev-lang/rust-1.97.1']
        iuse = {visible[0]: 'mrustc-bootstrap system-llvm', visible[1]: 'mrustc-bootstrap system-llvm', visible[2]: 'system-llvm'}
        flags = {visible[0]: 'mrustc-bootstrap system-llvm', visible[1]: 'system-llvm', visible[2]: 'system-llvm'}
        self.assertEqual(helper.enabled_seeds(visible, iuse.get, flags.get), [visible[0]])

    def test_chain_starts_at_usable_seed_and_visits_each_minor(self):
        visible = [f'dev-lang/rust-1.{minor}.0' for minor in range(74, 98)]
        best = lambda candidates: max(candidates, key=lambda cpv: helper.release_minor(cpv))
        chain = helper.select_chain(visible, [visible[0]], best)
        self.assertEqual(chain, visible)
        self.assertEqual(len(chain), 24)
        with self.assertRaisesRegex(ValueError, 'missing visible intermediate'):
            helper.select_chain([cpv for cpv in visible if cpv != 'dev-lang/rust-1.76.0'], [visible[0]], best)

    def test_no_effective_seed_fails_before_any_build(self):
        with self.assertRaisesRegex(ValueError, 'no visible source Rust'):
            helper.select_chain(['dev-lang/rust-1.90.0'], [], lambda values: values[0])
