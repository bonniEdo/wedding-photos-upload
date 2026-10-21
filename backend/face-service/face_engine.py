"""Local YuNet/SFace pipelines. No service, environment or index side effects."""
from dataclasses import dataclass, field
from pathlib import Path
import io
import math
import os
import re
import time
import uuid

import cv2
import numpy as np
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()
DETECTOR_MODEL = 'face_detection_yunet_2023mar.onnx'
RECOGNIZER_MODEL = 'face_recognition_sface_2021dec.onnx'


@dataclass(frozen=True)
class EngineConfig:
    max_pixels: int = 60_000_000
    tile_size: int = 1024
    overlap: float = .25
    max_tiles: int = 32
    tile_seconds: float = 10.
    min_face_pixels: int = 32
    min_sharpness: float = 20.

    def __post_init__(self):
        if (self.max_pixels < 1 or self.tile_size < 128 or not 0 <= self.overlap < .75
                or self.max_tiles < 1 or not math.isfinite(self.tile_seconds)
                or self.tile_seconds <= 0 or self.min_face_pixels < 1
                or not math.isfinite(self.min_sharpness) or self.min_sharpness < 0):
            raise ValueError('Invalid face engine limits')


@dataclass
class Analysis:
    faces: list
    metadata: dict
    preview: np.ndarray = field(repr=False)
    crops: list = field(default_factory=list, repr=False)


def load_image(raw, max_pixels=60_000_000):
    with Image.open(io.BytesIO(raw)) as source:
        if source.width * source.height > max_pixels:
            raise ValueError('Image exceeds decoded pixel limit')
        return ImageOps.exif_transpose(source).convert('RGB')


def iou(a, b):
    x = max(a[0], b[0]); y = max(a[1], b[1])
    w = max(0., min(a[0] + a[2], b[0] + b[2]) - x)
    h = max(0., min(a[1] + a[3], b[1] + b[3]) - y)
    intersection = w * h
    return intersection / max(a[2] * a[3] + b[2] * b[3] - intersection, 1e-12)


def map_detection(row, sx=1., sy=1., dx=0., dy=0.):
    """Transform bbox AND five landmarks, preserving the final detector score."""
    result = np.asarray(row, dtype=np.float32).copy()
    result[0] = result[0] * sx + dx; result[1] = result[1] * sy + dy
    result[2] *= sx; result[3] *= sy
    result[4:14:2] = result[4:14:2] * sx + dx
    result[5:14:2] = result[5:14:2] * sy + dy
    return result


def deduplicate(detections):
    """Conservative NMS: overlapping boxes must also agree on landmarks."""
    ordered = sorted(detections, key=lambda d: (-float(d[0][-1]), *map(float, d[0][:4])))
    kept = []
    for row, source in ordered:
        if not np.isfinite(row).all() or row[2] <= 0 or row[3] <= 0:
            continue
        duplicate = False
        for previous, _ in kept:
            scale = max(1., min(row[2], row[3], previous[2], previous[3]))
            landmarks = np.linalg.norm(row[4:14].reshape(5, 2) - previous[4:14].reshape(5, 2), axis=1)
            if iou(row, previous) >= .4 and float(np.mean(landmarks)) / scale <= .2:
                duplicate = True; break
        if not duplicate:
            kept.append((row, source))
    return sorted(kept, key=lambda d: (*map(float, d[0][:4]), -float(d[0][-1])))


def tile_boxes(width, height, config):
    def starts(length):
        end = max(0, length - config.tile_size)
        step = max(1, round(config.tile_size * (1 - config.overlap)))
        return list(dict.fromkeys([*range(0, end, step), end]))
    # A generator avoids allocating an unbounded list for extreme panoramas.
    for y in starts(height):
        for x in starts(width):
            yield x, y, min(width, x + config.tile_size), min(height, y + config.tile_size)


def square_crop(img, bbox, padding=.25):
    h, w = img.shape[:2]
    x, y, fw, fh = bbox[:4]
    side = min(max(w, h), max(1, int(math.ceil(max(fw, fh) * (1 + 2 * padding)))))
    cx = x + fw / 2; cy = y + fh / 2
    left = int(round(cx - side / 2)); top = int(round(cy - side / 2))
    right = left + side; bottom = top + side
    clipped = img[max(0, top):min(h, bottom), max(0, left):min(w, right)]
    if not clipped.size:
        raise ValueError('Face crop outside image')
    if side > 512:
        # Resize BEFORE square padding: a narrow panorama must not allocate a giant square.
        scale = 512 / side
        pad_top = min(511, round(max(0, -top) * scale))
        pad_left = min(511, round(max(0, -left) * scale))
        width = min(512-pad_left, max(1, round(clipped.shape[1] * scale)))
        height = min(512-pad_top, max(1, round(clipped.shape[0] * scale)))
        clipped = cv2.resize(clipped, (width, height), interpolation=cv2.INTER_AREA)
        return cv2.copyMakeBorder(clipped, pad_top, 512-pad_top-height,
                                  pad_left, 512-pad_left-width, cv2.BORDER_REPLICATE)
    return cv2.copyMakeBorder(clipped, max(0, -top), max(0, bottom - h),
                             max(0, -left), max(0, right - w), cv2.BORDER_REPLICATE)


def atomic_image(path, pixels):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + '.' + uuid.uuid4().hex + '.tmp.jpg')
    try:
        if not cv2.imwrite(str(temporary), pixels):
            raise OSError(f'Cannot save image: {path.name}')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def persist_analysis(analysis, data_dir, photo_id):
    """All required assets land before the caller commits its index transaction."""
    if not photo_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', photo_id):
        raise ValueError('Invalid photo id')
    root = Path(data_dir)
    for face, (thumb, crop) in zip(analysis.faces, analysis.crops):
        atomic_image(root / 'thumbs' / face['thumbnail'], thumb)
        atomic_image(root / 'face_crops' / face['crop'], crop)
    if len(analysis.faces) != len(analysis.crops):
        raise ValueError('Missing face assets')
    atomic_image(root / 'previews' / f'{photo_id}.jpg', analysis.preview)


class FaceEngine:
    """Caller serializes access to the OpenCV model objects."""
    def __init__(self, detector, recognizer, config=None):
        self.detector = detector; self.recognizer = recognizer
        self.config = config or EngineConfig()

    @classmethod
    def from_models(cls, models, config=None):
        models = Path(models)
        return cls(cv2.FaceDetectorYN.create(str(models / DETECTOR_MODEL), '', (320, 320), .9, .3, 5000),
                   cv2.FaceRecognizerSF.create(str(models / RECOGNIZER_MODEL), ''), config)

    def _detect(self, pixels):
        h, w = pixels.shape[:2]
        self.detector.setInputSize((w, h))
        _, rows = self.detector.detect(pixels)
        return [] if rows is None else list(rows)

    def analyze(self, raw, photo_id=None, pipeline='legacy'):
        if pipeline not in ('legacy', 'enhanced'):
            raise ValueError('Unknown face pipeline')
        started = time.perf_counter()
        image = load_image(raw, self.config.max_pixels)
        original_width, original_height = image.size
        preview_image = image.copy(); preview_image.thumbnail((1600, 1600))
        preview = cv2.cvtColor(np.asarray(preview_image), cv2.COLOR_RGB2BGR)
        ph, pw = preview.shape[:2]
        sx, sy = original_width / pw, original_height / ph
        whole = self._detect(preview)
        partial = False; partial_reason = None; tiles_run = 0
        if pipeline == 'legacy':
            pixels = preview
            detections = [(row, 'whole') for row in whole]
        else:
            pixels = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
            detections = [(map_detection(row, sx, sy), 'whole') for row in whole]
            # Large photos get tiles even if the whole-image pass found some faces.
            if max(original_width, original_height) > self.config.tile_size:
                tiles_started = time.perf_counter()
                for box in tile_boxes(original_width, original_height, self.config):
                    if tiles_run >= self.config.max_tiles:
                        partial = True; partial_reason = 'tile_limit'; break
                    if time.perf_counter() - tiles_started >= self.config.tile_seconds:
                        partial = True; partial_reason = 'tile_time_limit'; break
                    x, y, right, bottom = box
                    tile = np.ascontiguousarray(pixels[y:bottom, x:right])
                    detections.extend((map_detection(row, dx=x, dy=y), f'tile:{x}:{y}')
                                      for row in self._detect(tile))
                    tiles_run += 1
                # A model call cannot be interrupted; report overruns even on the last tile.
                if time.perf_counter() - tiles_started > self.config.tile_seconds:
                    partial = True; partial_reason = partial_reason or 'tile_time_limit'
            detections = deduplicate(detections)
        version = 'yunet-sface-legacy-v1' if pipeline == 'legacy' else 'yunet-sface-tiled-v1'
        faces = []; crops = []
        for i, (row, source) in enumerate(detections):
            if not np.isfinite(row).all() or row[2] <= 0 or row[3] <= 0:
                continue
            aligned = self.recognizer.alignCrop(pixels, row)
            feature = self.recognizer.feature(aligned).flatten().astype(np.float32)
            norm = float(np.linalg.norm(feature))
            if not np.isfinite(feature).all() or not math.isfinite(norm) or norm < 1e-12:
                raise ValueError('Invalid face embedding')
            feature /= norm
            bbox = [float(v) for v in row[:4]]
            original_bbox = bbox if pipeline == 'enhanced' else [bbox[0]*sx, bbox[1]*sy, bbox[2]*sx, bbox[3]*sy]
            sharpness = float(cv2.Laplacian(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
            landmark_points = row[4:14].reshape(5, 2)
            complete = bool(np.all(landmark_points >= [0, 0]) and
                            np.all(landmark_points < [pixels.shape[1], pixels.shape[0]]))
            eligible = (min(original_bbox[2:]) >= self.config.min_face_pixels
                        and sharpness >= self.config.min_sharpness and complete)
            face_id = uuid.uuid5(uuid.NAMESPACE_URL, f'wedding-face:{photo_id}:{version}:{i}').hex
            # crop names are versioned: a failed/retried new pipeline cannot overwrite old thumbnails.
            thumb = f'{photo_id}_{i}.jpg' if pipeline == 'legacy' else f'{photo_id}_{face_id}.jpg'
            crop = square_crop(pixels, bbox)
            if pipeline == 'legacy':
                x, y, fw, fh = bbox; pad = .25 * max(fw, fh)
                h, w = pixels.shape[:2]
                old_crop = pixels[max(0, int(y-pad)):min(h, int(y+fh+pad)),
                                  max(0, int(x-pad)):min(w, int(x+fw+pad))]
                thumbnail = cv2.resize(old_crop, (160, 160)) if old_crop.size else cv2.resize(crop, (160, 160))
            else:
                thumbnail = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_AREA)
            if crop.shape[0] > 512:
                crop = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_AREA)
            quality_metrics = {'version': 1, 'detector_score': float(row[-1]),
                               'original_face_size': original_bbox[2:], 'sharpness': sharpness,
                               'landmarks_complete': complete, 'sample_eligible': eligible}
            faces.append({'face_id': face_id, 'bbox': bbox, 'original_bbox': original_bbox,
                          'coordinate_space': 'original-exif' if pipeline == 'enhanced' else 'preview-exif',
                          'image_size': [original_width, original_height], 'preview_size': [pw, ph],
                          'embedding': feature.tolist(), 'thumbnail': thumb if photo_id else None,
                          'crop': f'{face_id}.jpg' if photo_id else None,
                          'quality': bbox[2] * bbox[3] * float(row[-1]),
                          'quality_metrics': quality_metrics, 'pipeline_version': version,
                          'detector_version': DETECTOR_MODEL, 'recognizer_version': RECOGNIZER_MODEL,
                          'source': source})
            if photo_id:
                crops.append((thumbnail, crop))
        return Analysis(faces, {'pipeline': pipeline, 'pipeline_version': version,
                                'image_size': [original_width, original_height], 'preview_size': [pw, ph],
                                'partial': partial, 'partial_reason': partial_reason, 'tiles_run': tiles_run,
                                'elapsed_seconds': time.perf_counter() - started}, preview, crops)
