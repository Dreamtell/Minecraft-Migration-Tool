"""Regression tests for dependency-first Forge metadata and cached wrong IDs."""
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Minecraft迁移工具'))
os.environ['MCTOOL_LANG'] = 'zh'
from core import scanner, migrator
from core.migration_plan import build_migration_plan

DEPENDENCY_FIRST = '''# modId = "commented_example"
[[dependencies.better_tab]]
modId = "craft_config"
mandatory = true
versionRange = "[1,)"
[[mods]]
modId = 'better_tab'
version = '2.1.4'
displayName = "Better Tab"
authors = "tricube"
description = """Line one.
Line two."""
'''


class ForgeMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='forge-metadata-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src, self.tgt = self.root / 'source', self.root / 'target'
        for root in (self.src, self.tgt):
            (root / 'mods').mkdir(parents=True)
        migrator._MODID_CACHE.clear()

    def jar(self, name, content=DEPENDENCY_FIRST, root=None, entry='META-INF/mods.toml'):
        path = (root or self.src) / 'mods' / name
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr(entry, content)
        return path

    def test_dependency_and_commented_ids_are_not_mod_identity(self):
        jar = self.jar('better_tab.jar')
        self.assertEqual(scanner.get_mod_metadata(jar), ('better_tab', '2.1.4', 'Forge'))
        self.assertEqual(migrator._modid_of(jar), 'better_tab')

    def test_detail_fields_use_same_declared_mod_table(self):
        info = scanner.get_full_mod_metadata(self.jar('better_tab.jar'))
        self.assertEqual(info['modid'], 'better_tab')
        self.assertEqual(info['version'], '2.1.4')
        self.assertEqual(info['authors'], 'tricube')
        self.assertEqual(info['description'], 'Line one.\nLine two.')
        self.assertEqual(info['dependencies'], 'craft_config')

    def test_dependency_only_or_malformed_toml_never_identifies_dependency(self):
        for text in ('[[dependencies.example]]\nmodId="craft_config"\n',
                     '[[dependencies.example]]\nmodId="craft_config"\n[[mods]\n'):
            jar = self.jar('unreadable.jar', text)
            self.assertIsNone(migrator._modid_of(jar))

    def test_neoforge_uses_own_mod_table(self):
        jar = self.jar('neo.jar', entry='META-INF/neoforge.mods.toml')
        self.assertEqual(scanner.get_mod_metadata(jar), ('better_tab', '2.1.4', 'NeoForge'))

    def test_real_dependency_does_not_block_plan_or_remove_library(self):
        self.jar('better_tab.jar')
        library = '[[mods]]\nmodId="craft_config"\nversion="1.1.2"\n'
        self.jar('craft_config.jar', library)
        installed = self.jar('installed_config.jar', library, root=self.tgt)
        before = installed.read_bytes()
        plan = build_migration_plan(self.src, self.tgt, '', ['better_tab.jar', 'craft_config.jar'], [])
        self.assertEqual(plan['total_files'], 2)
        self.assertTrue(migrator.run_migration(self.src, self.tgt, '', ['better_tab.jar'], [],
                                              False, True, add_history=False))
        self.assertEqual(installed.read_bytes(), before)
        self.assertTrue((self.tgt / 'mods/better_tab.jar').exists())

    def test_genuine_duplicate_resolved_and_reports_both_files(self):
        self.jar('one.jar')
        self.jar('two.jar')
        plan = build_migration_plan(self.src, self.tgt, '', ['one.jar', 'two.jar'], [])
        self.assertEqual(len(plan['files']), 1)
        for expected in ('one.jar', 'two.jar', 'better_tab'):
            self.assertIn(expected, plan['mod_resolutions'][0]['message'])

    def test_legacy_wrong_id_cache_reparsed_once_then_warm(self):
        jar = self.jar('better_tab.jar')
        stat = jar.stat()
        cache_path = self.src / 'mods_meta_cache.json'
        cache_path.write_text(json.dumps({jar.name: {
            'fingerprint': f'{stat.st_mtime_ns}_{stat.st_size}',
            'modid': 'craft_config', 'version': '2.1.4', 'mod_type': 'Forge',
        }}), encoding='utf-8')
        with mock.patch.object(scanner, 'get_mod_metadata', wraps=scanner.get_mod_metadata) as parse:
            result = scanner.scan_mod_differences(self.src, self.tgt)
            parse.assert_called_once_with(jar)
        self.assertEqual(result[0][5], 'better_tab')
        with mock.patch.object(scanner, 'get_mod_metadata', side_effect=AssertionError('cache missed')):
            self.assertEqual(scanner.scan_mod_differences(self.src, self.tgt), result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
