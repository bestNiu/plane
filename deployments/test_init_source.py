"""Run with python3 -m unittest discover -s deployments -p 'test_*.py'."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("init_source", Path(__file__).with_name("init-source.py"))
init_source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(init_source)


class InitSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.original_root = init_source.ROOT
        self.addCleanup(setattr, init_source, "ROOT", self.original_root)
        init_source.ROOT = Path(self.temp.name)
        (init_source.ROOT / "apps/api").mkdir(parents=True)
        shutil.copytree(self.original_root / "deployments/env", init_source.ROOT / "deployments/env")
        self.args = argparse.Namespace(
            db_host="db.example.com", db_name="plane", db_user="plane",
            base_url="http://localhost:8090", sslmode="require",
        )

    def test_generates_restricted_files_and_consistent_secrets(self):
        init_source.generate(self.args, "password$'@")
        paths = [init_source.ROOT / "apps/api/.env"] + list((init_source.ROOT / ".deploy").glob("*.env"))
        self.assertEqual(len(paths), 4)
        for path in paths:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("${", path.read_text())
        api = paths[0].read_text()
        self.assertIn("password%24%27%40@pg-egress:15432", api)
        self.assertIn("sslmode=require", api)
        api_secret = next(row for row in api.splitlines() if row.startswith("LIVE_SERVER_SECRET_KEY="))
        self.assertIn(api_secret, (init_source.ROOT / ".deploy/live.env").read_text())

    def test_refuses_overwriting_configuration(self):
        init_source.generate(self.args, "password")
        path = init_source.ROOT / "apps/api/.env"
        original = path.read_text()
        with self.assertRaises(FileExistsError):
            init_source.generate(self.args, "different")
        self.assertEqual(path.read_text(), original)

    def test_rejects_newlines_before_writing(self):
        with self.assertRaises(ValueError):
            init_source.generate(self.args, "password\nINJECTED=value")
        self.assertFalse((init_source.ROOT / "apps/api/.env").exists())


if __name__ == "__main__":
    unittest.main()
