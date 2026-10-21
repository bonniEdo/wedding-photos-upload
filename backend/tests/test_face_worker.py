"""Exercise the real worker/asset/index boundary using isolated data and fake models."""
import asyncio
import copy
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'face-service'))
from core import empty_state, add_photo, merge_people
from face_engine import Analysis


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.environment = patch.dict(os.environ, {'DATA_DIR': str(cls.root), 'FACE_PIPELINE': 'legacy',
                                                  'GROUP_MODE': 'legacy', 'ADMIN_PASSWORD': 'test-admin-password'})
        cls.environment.start()
        spec = importlib.util.spec_from_file_location('isolated_face_worker',
                Path(__file__).resolve().parents[1]/'face-service'/'app.py')
        cls.service = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.service
        spec.loader.exec_module(cls.service)

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop(); cls.directory.cleanup()

    def setUp(self):
        self.app = self.service
        for folder in ('jobs', 'originals', 'previews', 'thumbs', 'face_crops'):
            path = self.root/folder; path.mkdir(exist_ok=True)
            for item in path.iterdir():
                item.unlink()
        self.app.STATE_PATH.unlink(missing_ok=True)
        self.app.state = empty_state(); self.app.stop.clear()
        self.app.FACE_PIPELINE = 'legacy'; self.app.GROUP_MODE = 'legacy'
        self.photo_id = 'a'*64
        (self.root/'jobs'/f'{self.photo_id}.json').write_text(json.dumps({'file': 'input.jpg', 'name': 'input'}))
        (self.root/'originals'/'input.jpg').write_bytes(b'fake-image')
        pixels = np.zeros((160, 160, 3), dtype=np.uint8)
        face = {'face_id': 'face-a', 'thumbnail': 'a.jpg', 'crop': 'face-a.jpg',
                'embedding': [1., 0.], 'bbox': [0, 0, 50, 50], 'quality': 100,
                'quality_metrics': {'sample_eligible': True, 'detector_score': .99,
                                    'original_face_size': [50, 50], 'sharpness': 100}}
        self.result = Analysis([face], {'partial': False, 'pipeline': 'legacy'}, pixels, [(pixels, pixels)])
        parent = self
        class FakeEngine:
            def analyze(self, raw, photo_id, pipeline):
                parent.app.stop.set()
                return copy.deepcopy(parent.result)
        self.app.engine = FakeEngine()

    def test_worker_commits_index_only_after_all_assets_and_preserves_old_merge(self):
        for pid, vector in [('old-a', [1., 0.]), ('old-b', [0., 1.])]:
            face = {**self.result.faces[0], 'embedding': vector}
            add_photo(self.app.state, pid, pid, [face])
        ids = list(self.app.state['people'])
        merge_people(self.app.state, ids, ids[0])
        old = copy.deepcopy(self.app.state)
        self.app.worker()
        persisted = json.loads(self.app.STATE_PATH.read_text())
        self.assertIn(self.photo_id, persisted['photos'])
        self.assertEqual(persisted['merge_history'], old['merge_history'])
        self.assertEqual(persisted['aliases'], old['aliases'])
        self.assertEqual(persisted['photos']['old-a'], old['photos']['old-a'])
        self.assertEqual(persisted['people'][ids[0]]['thumbnail'], old['people'][ids[0]]['thumbnail'])
        for path in ('previews/'+self.photo_id+'.jpg', 'thumbs/a.jpg', 'face_crops/face-a.jpg'):
            self.assertTrue((self.root/path).is_file())

    def test_asset_failure_is_retryable_without_completed_photo_or_people(self):
        from face_engine import atomic_image
        calls = 0
        def fail_second(path, pixels):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('disk-full')
            return atomic_image(path, pixels)
        with patch('face_engine.atomic_image', side_effect=fail_second), self.assertLogs('uvicorn.error', logging.ERROR):
            self.app.worker()
        self.assertEqual(self.app.state['photos'], {})
        self.assertEqual(self.app.state['people'], {})
        self.assertEqual(self.app.state['errors'][self.photo_id]['attempts'], 1)
        self.assertTrue((self.root/'originals'/'input.jpg').exists())
        self.app.state['errors'][self.photo_id]['retry_at'] = 0
        self.app.stop.clear(); self.app.worker()
        self.assertIn(self.photo_id, self.app.state['photos'])
        self.assertNotIn(self.photo_id, self.app.state['errors'])

    def test_shadow_detection_failure_keeps_legacy_result_and_index(self):
        self.app.FACE_PIPELINE = 'shadow'
        parent = self
        class FailingShadow:
            def analyze(self, raw, photo_id, pipeline):
                parent.app.stop.set()
                if pipeline == 'enhanced':
                    raise RuntimeError('shadow-model-failure')
                return copy.deepcopy(parent.result)
        self.app.engine = FailingShadow()
        with self.assertLogs('uvicorn.error', logging.ERROR):
            self.app.worker()
        photo = self.app.state['photos'][self.photo_id]
        self.assertEqual(photo['analysis']['shadow'], {'error': 'RuntimeError'})
        self.assertEqual(len(photo['faces']), 1)

    def test_find_never_persists_query_or_runs_shadow(self):
        self.app.FACE_PIPELINE = 'shadow'
        self.app.state = empty_state()
        add_photo(self.app.state, 'old', 'old', self.result.faces)
        before = copy.deepcopy(self.app.state)
        class Upload:
            async def read(self, size):
                return b'fake'
            async def close(self):
                pass
        result = asyncio.run(self.app.find(Upload(), .3))
        self.assertEqual(result['matches'][0]['id'], 'old')
        self.assertEqual(self.app.state, before)
        self.assertFalse(list((self.root/'face_crops').iterdir()))
        self.assertFalse(self.app.STATE_PATH.exists())


if __name__ == '__main__':
    unittest.main()
