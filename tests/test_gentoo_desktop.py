from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


class DesktopBatchTests(unittest.TestCase):
    def test_bundle_and_installer_include_desktop_runtime_inputs(self):
        prepare = (GENTOO / 'prepare.py').read_text()
        installer = (GENTOO / 'install-base.sh').read_text()
        for name in ('complete-desktop.sh', 'desktop-packages.list', 'desktop-package.use',
                     'desktop-package.accept_keywords', 'desktop-package.mask',
                     'desktop-rust-bootstrap.py', 'desktop-lightdm.conf'):
            self.assertIn("'" + name + "'", prepare)
        self.assertIn('complete-desktop.sh', installer)
        self.assertIn('gentoo-source-receipt', installer)
        self.assertIn('ms1504-signed-generation', installer)

    def test_policy_selects_source_desktop_and_disables_cd_burning(self):
        packages = (GENTOO / 'desktop-packages.list').read_text()
        use = (GENTOO / 'desktop-package.use').read_text()
        for atom in ('dev-lang/rust', 'www-client/firefox', 'xfce-base/xfce4-meta',
                     'x11-misc/lightdm', 'media-video/pipewire',
                     'media-video/wireplumber', 'media-sound/pavucontrol',
                     'net-misc/networkmanager'):
            if atom == 'net-misc/networkmanager':
                self.assertIn(atom, (GENTOO / 'policy/defaults/base-packages.list').read_text())
            else:
                self.assertIn(atom, packages)
        self.assertIn('xfce-base/xfce4-meta -cdr', use)
        self.assertIn("export USE='-test -cdr'", (GENTOO / 'complete-desktop.sh').read_text())
        self.assertNotIn('firefox-bin', packages)
        self.assertNotIn('rust-bin', packages)

    def test_rust_chain_is_derived_from_visible_source_ebuilds(self):
        helper = (GENTOO / 'desktop-rust-bootstrap.py').read_text()
        driver = (GENTOO / 'complete-desktop.sh').read_text()
        self.assertIn('mrustc-bootstrap', helper)
        self.assertIn('select_chain', helper)
        self.assertIn('desktop-rust-bootstrap.py', driver)
        self.assertIn('emerge --oneshot', driver)

    def test_desktop_driver_checks_encrypted_extra_and_records_resume_inputs(self):
        driver = (GENTOO / 'complete-desktop.sh').read_text()
        for text in ('mountpoint -q /extra', 'cryptsetup status', 'flock -n 9',
                     'desktop-plan.done', 'desktop-sources-fetched.done',
                     'desktop-build.done', 'GENTOO_PORTAGE_MAKE_JOBS',
                     'GENTOO_PORTAGE_JOBS', 'GENTOO_PORTAGE_LOAD_AVERAGE'):
            self.assertIn(text, driver)
        self.assertIn('ms1504-signed-generation', (GENTOO / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()
