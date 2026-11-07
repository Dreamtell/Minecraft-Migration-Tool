"""Automatic duplicate selection, preview/execution consistency and rollback."""
import itertools
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Minecraft迁移工具'))
os.environ['MCTOOL_LANG'] = 'zh'
from core import migrator
from core.migration_plan import build_migration_plan, _choose_mod
from utils import history_store, i18n


class DuplicateModTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mctool-duplicates-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src, self.tgt = self.root / 'source', self.root / 'target'
        for root in (self.src, self.tgt):
            (root / 'mods').mkdir(parents=True)
        migrator._MODID_CACHE.clear()

    def jar(self, name, version, modid='example', payload=b'', root=None):
        path = (root or self.src) / 'mods' / name
        with zipfile.ZipFile(path, 'w') as jar:
            jar.writestr('fabric.mod.json', json.dumps({'id': modid, 'version': version}))
            jar.writestr('payload.bin', payload)
        return path

    def plan(self, names, **kw):
        return build_migration_plan(self.src, self.tgt, '', names, [], **kw)

    def test_newest_of_three_independent_of_list_order(self):
        names = ['old.jar', 'middle.jar', 'new.jar']
        for name, version in zip(names, ['5.11.7', '5.11.8', '5.11.9']):
            self.jar(name, version)
        for order in itertools.permutations(names):
            with self.subTest(order=order):
                plan = self.plan(order)
                self.assertEqual([x['src'].name for x in plan['files']], ['new.jar'])
                self.assertEqual(plan['mod_resolutions'][0]['reason'], 'newest')
                self.assertEqual(len(plan['mod_resolutions'][0]['skipped']), 2)

    def test_identical_renamed_file_keeps_first_without_changing_source(self):
        a = self.jar('apple.jar', '2.5.1')
        b = a.with_name('[苹果皮] apple.jar')
        shutil.copy2(a, b)
        original = {p.name: p.read_bytes() for p in (a, b)}
        plan = self.plan([b.name, a.name])
        self.assertEqual(plan['files'][0]['src'], b)
        self.assertEqual(plan['mod_resolutions'][0]['reason'], 'identical')
        self.assertEqual(plan['total_files'], 1)
        self.assertEqual(plan['total_size'], b.stat().st_size)
        self.assertEqual(original, {p.name: p.read_bytes() for p in (a, b)})

    def test_equal_version_different_content_preserves_first(self):
        a = self.jar('preferred.jar', '1.0', payload=b'user chosen')
        self.jar('other.jar', '1.0', payload=b'other')
        plan = self.plan(['preferred.jar', 'other.jar'])
        self.assertEqual(plan['files'][0]['src'], a)
        self.assertEqual(plan['mod_resolutions'][0]['reason'], 'equal_version')

    def test_identical_bytes_with_different_inferred_versions_keep_first(self):
        first = self.jar('example-1.0.jar', '${version}')
        renamed = first.with_name('example-9.0.jar')
        shutil.copy2(first, renamed)
        self.assertEqual(_choose_mod([first, renamed]), (first, 'identical'))

    def test_same_size_jars_with_different_tail_are_not_identical(self):
        payload = b'x' * (2 * 1024 * 1024)
        first = self.jar('first.jar', '1.0', payload=payload + b'a')
        second = self.jar('second.jar', '1.0', payload=payload + b'b')
        self.assertEqual(first.stat().st_size, second.stat().st_size)
        self.assertEqual(_choose_mod([first, second]), (first, 'equal_version'))

    def test_unknown_version_preserves_first_and_explains(self):
        a = self.jar('preferred.jar', 'custom-build')
        self.jar('numeric.jar', '99.0')
        plan = self.plan(['preferred.jar', 'numeric.jar'])
        self.assertEqual(plan['files'][0]['src'], a)
        self.assertEqual(plan['mod_resolutions'][0]['reason'], 'unknown')

    def test_release_wins_over_prerelease(self):
        self.jar('beta.jar', '2.0-beta2')
        release = self.jar('release.jar', '2.0')
        self.assertEqual(self.plan(['beta.jar', 'release.jar'])['files'][0]['src'], release)

    def test_unknown_ids_are_not_grouped_together(self):
        for name in ('unknown-a.jar', 'unknown-b.jar'):
            with zipfile.ZipFile(self.src / 'mods' / name, 'w'):
                pass
        plan = self.plan(['unknown-a.jar', 'unknown-b.jar'])
        self.assertEqual(len(plan['files']), 2)
        self.assertEqual(plan['mod_resolutions'], [])

    def test_same_path_alias_not_counted_as_another_mod(self):
        path = self.jar('one.jar', '1.0')
        plan = self.plan(['one.jar', str(path)])
        self.assertEqual(plan['total_files'], 1)
        self.assertEqual(plan['mod_resolutions'], [])

    def test_external_jar_versions_and_marker_use_same_selection(self):
        self.jar('old.jar', '1.0')
        external = self.jar('new.jar', '2.0')
        outside = self.root / 'external.jar'
        external.rename(outside)
        plan = self.plan(['old.jar', str(outside)], marker='★')
        self.assertEqual(plan['files'][0]['src'], outside)
        self.assertEqual(plan['files'][0]['dst'].name, '★ external.jar')

    def test_dry_and_real_share_plan_stats_logs_and_restore(self):
        old = self.jar('old.jar', '1.0', root=self.tgt)
        original = old.read_bytes()
        self.jar('middle.jar', '2.0')
        new = self.jar('new.jar', '3.0')
        names = ['middle.jar', 'new.jar']
        plan = self.plan(names)
        records = []
        with mock.patch.object(history_store, 'save_record', side_effect=lambda r: records.append(r)):
            for dry in (True, False):
                if not dry:
                    migrator.do_backup(self.tgt, record_id='duplicates')
                self.assertTrue(migrator.run_migration(self.src, self.tgt, '', names, [], dry,
                                                       True, plan=plan))
                rec = records[-1]
                self.assertEqual(rec['stats']['mods'], {'success': 1, 'skipped': 1, 'failed': 0})
                self.assertEqual(rec['mod_resolutions'], plan['mod_resolutions'])
                self.assertIn('middle.jar', rec['log'])
                self.assertIn('new.jar', rec['log'])
                if dry:
                    self.assertEqual(old.read_bytes(), original)
                    self.assertFalse((self.tgt / 'mods/new.jar').exists())
        self.assertEqual(list((self.tgt / 'mods').glob('*.jar')), [self.tgt / 'mods/new.jar'])
        self.assertEqual((self.tgt / 'mods/new.jar').read_bytes(), new.read_bytes())
        self.assertTrue(migrator.do_restore(self.tgt, backup_dir=migrator.get_record_backup_path(self.tgt, 'duplicates')))
        self.assertEqual(old.read_bytes(), original)
        self.assertFalse((self.tgt / 'mods/new.jar').exists())
        self.assertTrue((self.src / 'mods/middle.jar').exists())

    def test_no_overwrite_is_still_honored_after_selection(self):
        self.jar('old.jar', '1.0')
        self.jar('new.jar', '2.0')
        installed = self.jar('new.jar', '3.0', root=self.tgt)
        before = installed.read_bytes()
        plan = self.plan(['old.jar', 'new.jar'], overwrite=False)
        self.assertEqual(plan['total_files'], 0)
        self.assertTrue(migrator.run_migration(self.src, self.tgt, '', ['old.jar', 'new.jar'], [],
                                              False, False, plan=plan, add_history=False))
        self.assertEqual(installed.read_bytes(), before)
        self.assertFalse((self.tgt / 'mods/old.jar').exists())

    def test_failed_selected_copy_does_not_install_discarded_version(self):
        self.jar('old.jar', '1.0')
        self.jar('new.jar', '2.0')
        with mock.patch.object(migrator, 'safe_copy', return_value=(False, 'disk full')):
            self.assertFalse(migrator.run_migration(self.src, self.tgt, '', ['old.jar', 'new.jar'], [],
                                                   False, True, add_history=False))
        self.assertFalse(list((self.tgt / 'mods').glob('*.jar')))

    def test_hashing_checks_cancellation(self):
        a = self.jar('a.jar', '1.0')
        b = self.jar('b.jar', '2.0')
        event = threading.Event()
        event.set()
        with self.assertRaises(migrator.MigrationCancelled):
            _choose_mod([a, b], event.is_set)

    def test_mods_folder_in_extras_does_not_reintroduce_marked_duplicates(self):
        self.jar('old.jar', '1.0')
        new = self.jar('new.jar', '2.0')
        plan = self.plan(['old.jar', 'new.jar'], extras=['mods'], marker='★')
        self.assertEqual([(x['src'], x['dst'].name) for x in plan['files']], [(new, '★ new.jar')])
        self.assertEqual(plan['total_files'], 1)
        self.assertTrue(migrator.run_migration(self.src, self.tgt, '', [], [], False,
                                              True, plan=plan, add_history=False))
        self.assertEqual([p.name for p in (self.tgt / 'mods').glob('*.jar')], ['★ new.jar'])

    def test_extras_only_resolves_versions_and_replaces_existing_mod(self):
        self.jar('old.jar', '1.0')
        new = self.jar('new.jar', '2.0')
        self.jar('installed.jar', '0.5', root=self.tgt)
        plan = self.plan([], extras=['mods/old.jar', 'mods/new.jar'])
        self.assertEqual([x['src'] for x in plan['files']], [new])
        records = []
        with mock.patch.object(history_store, 'save_record', side_effect=records.append):
            self.assertTrue(migrator.run_migration(self.src, self.tgt, '', [], [], False,
                                                  True, plan=plan))
        self.assertEqual(records[-1]['stats']['extra'], {'success': 1, 'skipped': 1, 'failed': 0})
        self.assertEqual(records[-1]['stats']['mods']['skipped'], 0)
        self.assertEqual([p.name for p in (self.tgt / 'mods').glob('*.jar')], ['new.jar'])

    def test_newer_mod_from_extras_participates_in_same_selection(self):
        self.jar('old.jar', '1.0')
        new = self.jar('new.jar', '2.0')
        plan = self.plan(['old.jar'], extras=['mods'])
        self.assertEqual([x['src'] for x in plan['files']], [new])
        self.assertEqual(plan['mod_resolutions'][0]['skipped_categories'], ['mods'])

    def test_jar_outside_mods_is_not_filtered_by_mod_id(self):
        original = self.jar('mod.jar', '1.0')
        (self.src / 'archives').mkdir()
        shutil.copy2(original, self.src / 'archives/copy.jar')
        plan = self.plan(['mod.jar'], extras=['archives'])
        self.assertEqual(plan['total_files'], 2)
        self.assertEqual(plan['mod_resolutions'], [])

    def test_resolution_message_translates_to_english(self):
        self.jar('a.jar', '1.0')
        self.jar('b.jar', '2.0')
        with mock.patch.object(i18n, '_激活', True):
            message = self.plan(['a.jar', 'b.jar'])['mod_resolutions'][0]['message']
        self.assertIn('keeping b.jar', message)
        self.assertIn('skipping a.jar', message)


if __name__ == '__main__':
    unittest.main(verbosity=2)
