import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'face-service'))
from face_engine import (EngineConfig, FaceEngine, atomic_image, deduplicate, load_image,
                         map_detection, persist_analysis, square_crop)


def row(x=10, y=10, width=40, height=40, score=.99):
    return np.array([x, y, width, height, x+10, y+10, x+30, y+10,
                     x+20, y+20, x+10, y+30, x+30, y+30, score], dtype=np.float32)


def encoded(width=200, height=200, orientation=None):
    image = Image.new('RGB', (width, height), 'white')
    data = io.BytesIO(); exif = Image.Exif()
    if orientation:
        exif[274] = orientation
    image.save(data, format='JPEG', exif=exif)
    return data.getvalue()


class Detector:
    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def setInputSize(self, size):
        self.size = size

    def detect(self, image):
        values = next(self.outputs, [])
        return None, np.array(values) if len(values) else None


class Recognizer:
    def __init__(self):
        self.seen = []

    def alignCrop(self, pixels, detection):
        self.seen.append((pixels.shape, detection.copy()))
        return np.random.default_rng(42).integers(0, 255, (112, 112, 3), dtype=np.uint8)

    def feature(self, aligned):
        return np.array([[3., 4.]], dtype=np.float32)


class EngineTests(unittest.TestCase):
    def test_exif_orientation_and_pixel_limit_before_decode(self):
        raw = encoded(300, 180, 6)
        self.assertEqual(load_image(raw).size, (180, 300))
        with self.assertRaisesRegex(ValueError, 'pixel limit'):
            load_image(raw, 100)

    def test_bbox_landmarks_transform_score_unchanged(self):
        original = row()
        mapped = map_detection(original, 2, 3, 100, 200)
        np.testing.assert_allclose(mapped[:4], [120, 230, 80, 120])
        np.testing.assert_allclose(mapped[4:6], [140, 260])
        self.assertEqual(mapped[-1], original[-1])
        self.assertEqual(original[0], 10)

    def test_dedup_removes_tile_repeat_but_keeps_adjacent_person(self):
        first = row(); duplicate = row(11, 11, score=.98)
        adjacent = row(35, 10)
        result = deduplicate([(duplicate, 'tile'), (adjacent, 'tile2'), (first, 'whole')])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][1], 'whole')

    def test_overlapping_people_with_different_landmarks_are_not_suppressed(self):
        first = row(); other = row(10, 10)
        other[4:14:2] += 15
        self.assertEqual(len(deduplicate([(first, 'a'), (other, 'b')])), 2)

    def test_enhanced_aligns_original_landmarks_and_reports_partial_tiles(self):
        recognizer = Recognizer()
        engine = FaceEngine(Detector([[row(100, 100)], []]), recognizer,
                            EngineConfig(max_tiles=1))
        result = engine.analyze(encoded(3200, 1600), 'p', 'enhanced')
        face = result.faces[0]
        np.testing.assert_allclose(face['bbox'], [200, 200, 80, 80])
        self.assertEqual(recognizer.seen[0][0][:2], (1600, 3200))
        self.assertTrue(result.metadata['partial'])
        self.assertEqual(result.metadata['partial_reason'], 'tile_limit')
        self.assertEqual(result.metadata['tiles_run'], 1)
        self.assertEqual(face['coordinate_space'], 'original-exif')
        self.assertTrue(face['quality_metrics']['sample_eligible'])
        self.assertEqual(result.preview.shape[:2], (800, 1600))

    def test_tiles_run_even_when_whole_pass_already_found_a_face(self):
        engine = FaceEngine(Detector([[row()], [row(100, 100)], [], []]), Recognizer())
        result = engine.analyze(encoded(2000, 500), 'p', 'enhanced')
        self.assertGreaterEqual(result.metadata['tiles_run'], 2)
        self.assertGreaterEqual(len(result.faces), 2)
        self.assertFalse(result.metadata['partial'])

    def test_time_budget_is_explicit_partial_result(self):
        engine = FaceEngine(Detector([[]]), Recognizer(), EngineConfig(tile_seconds=1))
        with patch('face_engine.time.perf_counter', side_effect=[0., 0., 2., 2., 2.]):
            result = engine.analyze(encoded(2000, 500), 'p', 'enhanced')
        self.assertTrue(result.metadata['partial'])
        self.assertEqual(result.metadata['tiles_run'], 0)

    def test_legacy_keeps_preview_coordinates_and_exact_feature_normalization(self):
        recognizer = Recognizer()
        engine = FaceEngine(Detector([[row(100, 100)]]), recognizer)
        result = engine.analyze(encoded(3200, 1600), 'p')
        np.testing.assert_allclose(result.faces[0]['embedding'], [.6, .8])
        np.testing.assert_allclose(result.faces[0]['bbox'], [100, 100, 40, 40])
        self.assertEqual(recognizer.seen[0][0][:2], (800, 1600))
        self.assertEqual(result.faces[0]['thumbnail'], 'p_0.jpg')

    def test_square_crop_preserves_shape_at_edges_and_does_not_upscale_private_crop(self):
        pixels = np.zeros((100, 200, 3), dtype=np.uint8)
        crop = square_crop(pixels, [0, 0, 20, 40])
        self.assertEqual(crop.shape[:2], (60, 60))
        result = FaceEngine(Detector([[row()]]), Recognizer()).analyze(encoded(), 'p', 'enhanced')
        self.assertEqual(result.crops[0][0].shape[:2], (160, 160))
        self.assertLess(result.crops[0][1].shape[0], 512)

    def test_assets_have_stable_ids_and_query_creates_no_files(self):
        raw = encoded()
        engine = FaceEngine(Detector([[row()], [row()], [row()]]), Recognizer())
        first = engine.analyze(raw, 'p', 'enhanced')
        second = engine.analyze(raw, 'p', 'enhanced')
        self.assertEqual(first.faces[0]['face_id'], second.faces[0]['face_id'])
        with tempfile.TemporaryDirectory() as directory:
            persist_analysis(first, directory, 'p')
            self.assertEqual(len(list(Path(directory).rglob('*.jpg'))), 3)
            self.assertFalse(list(Path(directory).rglob('*.tmp*')))
            query = engine.analyze(raw)
            self.assertIsNone(query.faces[0]['thumbnail'])
            self.assertIsNone(query.faces[0]['crop'])
            self.assertEqual(query.crops, [])
            self.assertEqual(len(list(Path(directory).rglob('*.jpg'))), 3)

    def test_image_write_failure_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('face_engine.cv2.imwrite', return_value=False):
                with self.assertRaises(OSError):
                    atomic_image(Path(directory)/'a.jpg', np.zeros((10, 10, 3), dtype=np.uint8))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_panorama_crop_padding_has_bounded_memory(self):
        pixels = np.zeros((20, 10000, 3), dtype=np.uint8)
        crop = square_crop(pixels, [0, 0, 10000, 20])
        self.assertEqual(crop.shape[:2], (512, 512))

    def test_invalid_engine_limits_and_empty_images(self):
        for kwargs in ({'max_tiles': 0}, {'overlap': 1}, {'tile_seconds': float('nan')}):
            with self.assertRaises(ValueError):
                EngineConfig(**kwargs)
        result = FaceEngine(Detector([[]]), Recognizer()).analyze(encoded(), 'p')
        self.assertEqual(result.faces, [])
        self.assertFalse(result.metadata['partial'])


if __name__ == '__main__':
    unittest.main()
