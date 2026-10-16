import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import rootfs
from lib.generator import Generator, install_gentoo_after_hook


class GentooIntegrationTests(unittest.TestCase):
    def args(self, **changes):
        values = dict(extra_builds=[], gentoo_config=None, qemu=False,
                      bare_metal=False, tmpfs=False, no_create_config=False)
        values.update(changes)
        return SimpleNamespace(**values)

    def test_opt_in_validation_leaves_original_modes_unselected(self):
        self.assertFalse(rootfs.validate_extra_builds(self.args(), None))
        self.assertTrue(rootfs.validate_extra_builds(
            self.args(extra_builds=['gentoo'], qemu=True), None))
        self.assertTrue(rootfs.validate_extra_builds(
            self.args(extra_builds=['gentoo'], bare_metal=True), None))

    def test_generated_bootstrap_config_is_unchanged_without_opt_in(self):
        values = dict(arch='x86', force_timestamps=False, chroot=False, bwrap=False,
                      update_checksums=False, cores=18, swap=0, internal_ci=None,
                      interactive=False, qemu=True, bare_metal=False, kernel=None,
                      repo=None, external_sources=False, build_kernels=False,
                      configurator=False, mirrors=['https://mirror.invalid'],
                      extra_builds=[])
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'steps').mkdir()
            previous = os.getcwd()
            try:
                os.chdir(temp)
                rootfs.create_configuration_file(SimpleNamespace(**values))
                config = (Path(temp) / 'steps' / 'bootstrap.cfg').read_text()
                self.assertNotIn('GENTOO_', config)
                values['extra_builds'] = ['gentoo']
                rootfs.create_configuration_file(SimpleNamespace(**values))
                config = (Path(temp) / 'steps' / 'bootstrap.cfg').read_text()
                self.assertIn('GENTOO_BOOTSTRAP=True', config)
                self.assertIn('GENTOO_MODE=vm', config)
            finally:
                os.chdir(previous)

    def test_gentoo_config_and_unsupported_modes_fail_early(self):
        class Parser:
            @staticmethod
            def error(message):
                raise ValueError(message)

        with self.assertRaisesRegex(ValueError, 'requires --extra-builds'):
            rootfs.validate_extra_builds(self.args(gentoo_config='machine.toml'), Parser())
        with self.assertRaisesRegex(ValueError, 'requires --qemu or --bare-metal'):
            rootfs.validate_extra_builds(self.args(extra_builds=['gentoo']), Parser())
        with self.assertRaisesRegex(ValueError, 'tmpfs'):
            rootfs.validate_extra_builds(
                self.args(extra_builds=['gentoo'], qemu=True, tmpfs=True), Parser())

    def test_generator_hook_bundles_inputs_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'bundle'
            source.mkdir()
            (source / 'gentoo.sh').write_text('#!/bin/bash\n')
            (source / 'config.sh').write_text('export GENTOO_MODE=vm\n')
            steps = root / 'steps'
            steps.mkdir()
            install_gentoo_after_hook(str(steps), str(source))
            hook = steps / 'after' / '90-gentoo-continuation.sh'
            self.assertTrue(os.access(hook, os.X_OK))
            self.assertEqual((steps / 'gentoo-inputs' / 'config.sh').read_text(),
                             'export GENTOO_MODE=vm\n')
            self.assertIn('bash /steps/gentoo-inputs/gentoo.sh build', hook.read_text())
            with self.assertRaises(FileExistsError):
                install_gentoo_after_hook(str(steps), str(source))

    def test_generator_without_gentoo_adds_no_after_hook(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'steps').mkdir()
            (root / 'target').mkdir()
            generator = Generator.__new__(Generator)
            generator.git_dir = str(root)
            generator.target_dir = str(root / 'target')
            generator.gentoo_inputs = None
            generator.get_packages = lambda: None
            generator.steps()
            self.assertFalse((root / 'target' / 'steps' / 'after').exists())

    def test_vm_hook_uses_saved_managed_lifecycle_and_explicit_resource_args(self):
        source = (Path(rootfs.__file__).read_text())
        self.assertIn('vm.py', source)
        self.assertIn('"prepare"', source)
        self.assertIn('"start"', source)
        self.assertIn('"--cores"', source)
        # Existing rootfs values are passed unchanged to the generated QEMU argv.
        self.assertIn("'-m', str(args.qemu_ram) + 'M'", source)
        self.assertIn("'-smp', str(args.cores)", source)

    def test_vm_prepare_receives_original_qemu_resources_unchanged(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(rootfs, 'run_as_root') as run_root:
            target = SimpleNamespace(path=temp)
            qemu = ['-enable-kvm', '-m', '49152M', '-smp', '18',
                    '-drive', 'file=/tmp/bootstrap.img,format=raw']
            rootfs.start_managed_gentoo_vm(
                SimpleNamespace(qemu_cmd='qemu-system-x86_64'), target,
                qemu, '/tmp/my-gentoo.toml')
            command = Path(temp, 'gentoo-initial-qemu.json')
            import json
            self.assertEqual(json.loads(command.read_text()), ['qemu-system-x86_64', *qemu])
            self.assertEqual(run_root.call_count, 2)


if __name__ == '__main__':
    unittest.main()
