import unittest
from pathlib import Path
from thumbwork.app_name_to_package import resolve_package_ids
from thumbwork.smoke_runner import default_scenario_paths, load_scenario

class PackageTests(unittest.TestCase):
    def test_bundled_resources(self):
        self.assertTrue(resolve_package_ids('Weibo'))
        for path in default_scenario_paths().values():
            self.assertEqual(load_scenario(path).width, 1080)
        import thumbwork
        self.assertTrue((Path(thumbwork.__file__).parent / 'resources/system_prompt.md').is_file())
