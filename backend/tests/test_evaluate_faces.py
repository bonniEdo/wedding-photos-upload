import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'face-service'))
from evaluate_faces import (detection_matches, evaluate, grouping_metrics, init_manifest,
                            safe_destination, validate_manifest)
from face_engine import Analysis


def prediction(box, vector=None):
    return {'face_id': 'f', 'bbox': box, 'original_bbox': box, 'thumbnail': 'x.jpg',
            'embedding': vector or [1, 0], 'quality': 100,
            'quality_metrics': {'detector_score': .99, 'sample_eligible': True,
                                'original_face_size': [50, 50], 'sharpness': 100}}


class Engine:
    def __init__(self, faces):
        self.faces = iter(faces)

    def analyze(self, raw, photo_id, pipeline):
        return Analysis(next(self.faces), {'elapsed_seconds': .1, 'partial': False},
                        np.zeros((10, 10, 3), dtype=np.uint8))


class EvaluationTests(unittest.TestCase):
    def test_init_is_unlabelled_content_addressed_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'photos'; root.mkdir()
            Image.new('RGB', (20, 20)).save(root/'a.jpg')
            target = Path(directory)/'manifest.json'
            self.assertEqual(init_manifest(root, target), 1)
            document = json.loads(target.read_text())
            self.assertFalse(document['photos'][0]['annotated'])
            self.assertEqual(validate_manifest(document, target), root.resolve())
            with self.assertRaises(FileExistsError):
                init_manifest(root, target)
            (root/'a.jpg').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Content changed'):
                validate_manifest(document, target)

    def test_protected_data_and_source_destinations_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch('evaluate_faces.dotenv_values', return_value={'DATA_DIR': str(root)}):
                with patch.dict('os.environ', {'DATA_DIR': str(root)}):
                    with self.assertRaises(ValueError):
                        safe_destination(root/'reports'/'a.json')
            with self.assertRaises(ValueError):
                safe_destination(root/'a.json', root)
            alias = root/'alias'; target = root/'data'; target.mkdir(); alias.symlink_to(target)
            with patch.dict('os.environ', {'DATA_DIR': str(target)}):
                with self.assertRaises(ValueError):
                    safe_destination(alias/'report.json')

    def test_scene_split_leakage_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'photos'; root.mkdir()
            for i in range(2):
                Image.new('RGB', (20+i, 20), 'white').save(root/f'{i}.jpg')
            manifest = Path(directory)/'manifest.json'; init_manifest(root, manifest)
            document = json.loads(manifest.read_text())
            document['photos'][1]['split'] = 'test'
            with self.assertRaisesRegex(ValueError, 'scene/burst'):
                validate_manifest(document, manifest)

    def test_detection_is_one_to_one_with_ignore_regions(self):
        predicted = [prediction([0, 0, 20, 20]), prediction([0, 0, 20, 20]), prediction([50, 50, 20, 20])]
        labelled = [{'bbox': [0, 0, 20, 20]}, {'bbox': [50, 50, 20, 20], 'ignore': True},
                    {'bbox': [100, 100, 20, 20]}]
        matched, extra, missing = detection_matches(predicted, labelled)
        self.assertEqual(matched, [(0, 0)])
        self.assertEqual(extra, [1]); self.assertEqual(missing, [2])

    def test_grouping_false_merge_and_split_are_reported_separately(self):
        metrics = grouping_metrics([{'identity': 'A', 'person_id': 'x'},
                                    {'identity': 'A', 'person_id': 'y'},
                                    {'identity': 'B', 'person_id': 'x'}])
        self.assertEqual(metrics['false_merge_pairs'], 1)
        self.assertEqual(metrics['false_split_pairs'], 1)
        self.assertEqual(metrics['split_identities'], 1)
        self.assertEqual(metrics['mixed_groups'], 1)

    def test_unlabelled_run_does_not_claim_accuracy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'a.jpg').write_bytes(b'fake')
            document = {'photos': [{'id': 'a', 'file': 'a.jpg', 'split': 'calibration', 'annotated': False}]}
            result = evaluate(document, root, Engine([[prediction([0, 0, 20, 20])]]),
                              'legacy', 'legacy', 'calibration')
            self.assertEqual(result['metrics']['status'], 'unlabelled')
            self.assertIsNone(result['metrics']['detection'])
            self.assertIsNone(result['metrics']['grouping'])

    def test_failed_query_remains_in_metrics_with_zero_recall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('a', 'q'):
                (root/f'{name}.jpg').write_bytes(b'fake')
            document = {'photos': [{'id': 'a', 'file': 'a.jpg', 'split': 'test', 'annotated': True,
                                    'faces': [{'bbox': [0, 0, 20, 20], 'identity': 'A'}]}],
                        'queries': [{'id': 'q', 'file': 'q.jpg', 'split': 'test', 'annotated': True,
                                     'relevant_photo_ids': ['a']}]}
            result = evaluate(document, root, Engine([[prediction([0, 0, 20, 20])], []]),
                              'legacy', 'legacy', 'test')
            query = result['metrics']['searches'][0]
            self.assertEqual(query['status'], 'no_face')
            self.assertEqual(query['recall_at_20'], 0)
            self.assertEqual(query['precision_at_20'], 0)
            self.assertEqual(result['metrics']['detection']['recall'], 1)


if __name__ == '__main__':
    unittest.main()
