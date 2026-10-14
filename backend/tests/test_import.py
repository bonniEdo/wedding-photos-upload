import hashlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1]/'face-service'/'import_photos.py'

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root/'data'
        self.originals = self.data/'originals'
        self.originals.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def run_import(self, *arguments, drive='false'):
        env = {**os.environ, 'DATA_DIR': str(self.data), 'DRIVE_ENABLED': drive}
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, arguments)],
                              env=env, capture_output=True, text=True, check=True)

    def jobs(self):
        return [json.loads(p.read_text(encoding='utf8')) for p in (self.data/'jobs').glob('*.json')]

    def test_default_folder_reuses_originals_and_rerun_is_idempotent(self):
        photo = self.originals/'合照.JPG'
        photo.write_bytes(b'local-photo')
        self.run_import()
        job, = self.jobs()
        self.assertEqual(job['file'], photo.name)
        self.assertEqual(job['driveStatus'], 'disabled')
        self.assertEqual(list(self.originals.iterdir()), [photo])
        self.assertEqual(photo.read_bytes(), b'local-photo')
        before = (self.data/'jobs'/f'{job["id"]}.json').read_bytes()
        self.assertIn('已加入 0 張', self.run_import(self.originals).stdout)
        self.assertEqual((self.data/'jobs'/f'{job["id"]}.json').read_bytes(), before)

    def test_existing_upload_manifest_is_not_registered_again(self):
        photo = self.originals/'guest-photo.jpg'
        photo.write_bytes(b'uploaded-photo')
        jobs = self.data/'jobs'
        jobs.mkdir()
        job = {'id': 'a'*64, 'file': photo.name, 'driveStatus': 'disabled'}
        manifest = jobs/f'{job["id"]}.json'
        manifest.write_text(json.dumps(job), encoding='utf8')
        before = manifest.read_bytes()
        self.assertIn('已加入 0 張', self.run_import().stdout)
        self.assertEqual(self.jobs(), [job])
        self.assertEqual(manifest.read_bytes(), before)

    def test_subfolders_reuse_paths_and_duplicate_content_is_skipped(self):
        nested = self.originals/'攝影師'
        nested.mkdir()
        (nested/'one.png').write_bytes(b'same-photo')
        (nested/'two.png').write_bytes(b'same-photo')
        self.run_import()
        job, = self.jobs()
        self.assertEqual(job['file'], '攝影師/one.png')
        self.assertEqual(sorted(p.name for p in nested.iterdir()), ['one.png', 'two.png'])
        self.assertEqual(len(list(self.originals.rglob('*.png'))), 2)

    def test_external_photos_are_copied_and_queued_for_enabled_drive(self):
        folder = self.root/'photos'
        folder.mkdir()
        photo = folder/'external.jpg'
        photo.write_bytes(b'external-photo')
        self.run_import(folder, drive='true')
        job, = self.jobs()
        self.assertEqual(job['file'], hashlib.sha256(b'external-photo').hexdigest()+'.jpg')
        self.assertEqual(job['driveStatus'], 'pending')
        self.assertEqual((self.originals/job['file']).read_bytes(), photo.read_bytes())
        self.assertEqual(photo.read_bytes(), b'external-photo')

    def test_skip_drive_preserves_local_disabled_status(self):
        (self.originals/'local.jpg').write_bytes(b'local-only')
        self.run_import('--skip-drive')
        self.assertEqual(self.jobs()[0]['driveStatus'], 'disabled')

if __name__ == '__main__':
    unittest.main()
