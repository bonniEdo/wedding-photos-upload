"""Pure, conservative mapping of new detections onto immutable old observations.

Backfill never replaces old embeddings, grouping decisions, coordinates or assets.
The caller owns the backup, files, transaction, revision and explicit approval of
``new_faces``.  All enhanced data for old observations is namespaced metadata.
"""
import copy
import hashlib
import json
import math
import re

from face_state import face_id, revision


SCHEMA_VERSION = 1
MIN_MAPPING_IOU = .50
MIN_MAPPING_MARGIN = .20
_METADATA_FIELDS = ('quality', 'quality_metrics', 'crop', 'thumbnail', 'pipeline_version',
                    'detector_version', 'recognizer_version', 'source')
_ANALYSIS_FIELDS = ('pipeline', 'pipeline_version', 'image_size', 'preview_size',
                    'partial', 'partial_reason', 'tiles_run')


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                          allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError('回填資料必須是有效的 JSON') from error


def _fingerprint(value):
    return hashlib.sha256(_canonical(value).encode('utf8')).hexdigest()


def _number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def _size(value):
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(_number(v) and v > 0 for v in value)):
        return [float(v) for v in value]
    return None


def _box(value):
    if (isinstance(value, (list, tuple)) and len(value) >= 4
            and all(_number(v) for v in value[:4]) and min(value[2:4]) > 0):
        return [float(v) for v in value[:4]]
    return None


def _same_size(first, second):
    return all(abs(a-b) <= 1e-6 for a, b in zip(first, second))


def _iou(first, second):
    width = max(0., min(first[0]+first[2], second[0]+second[2]) - max(first[0], second[0]))
    height = max(0., min(first[1]+first[3], second[1]+second[3]) - max(first[1], second[1]))
    intersection = width * height
    return intersection / max(first[2]*first[3] + second[2]*second[3] - intersection, 1e-12)


def _plausible(first, second):
    # A slightly shifted, tiny or partial detection may still be the old face.
    # Suppressing a questionable new proposal is safer than duplicating it.
    if _iou(first, second) > 0:
        return True
    return (abs(first[0]+first[2]/2-second[0]-second[2]/2) <= .55*(first[2]+second[2])
            and abs(first[1]+first[3]/2-second[1]-second[3]/2) <= .55*(first[3]+second[3]))


def photo_signature(state, photo_id):
    """Fingerprint all old evidence, ignoring only this module's additive fields.

    Synthesized IDs and persisted IDs have identical signatures. Any correction,
    changed embedding, old geometry, deletion or reordering invalidates a pending
    candidate, while metadata-only retries remain idempotent.
    """
    photo = state.get('photos', {}).get(photo_id)
    if not isinstance(photo, dict) or not isinstance(photo.get('faces'), list):
        raise ValueError('回填照片不存在或人臉資料不完整')
    faces = []
    for index, face in enumerate(photo['faces']):
        if not isinstance(face, dict):
            raise ValueError('回填照片的人臉資料不完整')
        normalized = copy.deepcopy(face)
        normalized['face_id'] = face_id(photo_id, index, face)
        normalized.pop('backfill_metadata', None)
        faces.append(normalized)
    return _fingerprint({'photo_id': photo_id, 'faces': faces})


def _old_geometry(face, image_size, fallback_preview):
    declared_size = _size(face.get('image_size'))
    if declared_size and image_size and not _same_size(declared_size, image_size):
        return None, 'image_size_changed'
    if face.get('coordinate_space', 'preview-exif') not in ('original-exif', 'preview-exif'):
        return None, 'unknown_coordinate_space'
    if face.get('coordinate_space') == 'original-exif':
        value = _box(face.get('bbox'))
        return value, None if value else 'invalid_existing_bbox'
    # The engine's original_bbox already incorporates EXIF and preview scaling.
    original = _box(face.get('original_bbox'))
    if original is not None and declared_size is not None and image_size is not None:
        return original, None
    box = _box(face.get('bbox'))
    preview = _size(face.get('preview_size')) or fallback_preview
    if box is None:
        return None, 'invalid_existing_bbox'
    if image_size is None or preview is None:
        return None, 'existing_geometry_unavailable'
    sx, sy = image_size[0]/preview[0], image_size[1]/preview[1]
    return [box[0]*sx, box[1]*sy, box[2]*sx, box[3]*sy], None


def _new_geometry(face, image_size, fallback_preview):
    declared_size = _size(face.get('image_size'))
    if declared_size and image_size and not _same_size(declared_size, image_size):
        return None
    original = _box(face.get('original_bbox'))
    if original is not None:
        return original
    box = _box(face.get('bbox'))
    space = face.get('coordinate_space')
    if space == 'original-exif':
        return box
    preview = _size(face.get('preview_size')) or fallback_preview
    if box is not None and space == 'preview-exif' and preview and image_size:
        sx, sy = image_size[0]/preview[0], image_size[1]/preview[1]
        return [box[0]*sx, box[1]*sy, box[2]*sx, box[3]*sy]
    return None


def _metadata(face, box, image_size, preview):
    result = {key: copy.deepcopy(face[key]) for key in _METADATA_FIELDS if key in face}
    result.update(analysis_face_id=face.get('face_id'), original_bbox=box,
                  coordinate_space='original-exif', image_size=image_size,
                  preview_size=_size(face.get('preview_size')) or preview)
    return result


def candidate_fingerprint(candidate):
    """Content-derived candidate ID; revision and runtime timing are not evidence."""
    return _fingerprint({key: value for key, value in candidate.items()
                         if key not in ('id', 'base_revision')})


def build_candidate(state, photo_id, analysis, source_sha256, pipeline_key, preview_size=None):
    if not isinstance(source_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', source_sha256):
        raise ValueError('原圖 SHA-256 無效')
    if not isinstance(pipeline_key, str) or not pipeline_key or len(pipeline_key) > 1000:
        raise ValueError('回填模型版本無效')
    signature = photo_signature(state, photo_id)
    raw_faces = analysis.get('faces') if isinstance(analysis, dict) else analysis.faces
    raw_metadata = analysis.get('metadata', {}) if isinstance(analysis, dict) else analysis.metadata
    if not isinstance(raw_faces, list) or not isinstance(raw_metadata, dict):
        raise ValueError('回填分析資料不完整')
    # A JSON round trip copies all nested values and rejects non-finite numbers.
    detections = json.loads(_canonical(raw_faces))
    if any(not isinstance(face, dict) for face in detections):
        raise ValueError('回填分析的人臉資料不完整')
    analysis_metadata = {key: copy.deepcopy(raw_metadata[key]) for key in _ANALYSIS_FIELDS
                         if key in raw_metadata}
    image_size = _size(raw_metadata.get('image_size'))
    if image_size is None:
        image_size = next((_size(face.get('image_size')) for face in detections
                           if _size(face.get('image_size')) is not None), None)
    photo = state['photos'][photo_id]
    fallback_preview = (_size(preview_size) or _size(photo.get('analysis', {}).get('preview_size')))
    detection_preview = _size(raw_metadata.get('preview_size')) or fallback_preview
    analysis_metadata['image_size'] = image_size
    analysis_metadata['preview_size'] = detection_preview
    analysis_metadata['partial'] = bool(raw_metadata.get('partial', False))
    analysis_metadata.setdefault('partial_reason', None)
    original = photo['faces']
    old_ids = [face_id(photo_id, index, face) for index, face in enumerate(original)]
    if len(set(old_ids)) != len(old_ids):
        raise ValueError('回填照片的人臉識別碼重複')
    old_geometries = [_old_geometry(face, image_size, fallback_preview) for face in original]
    new_boxes = [_new_geometry(face, image_size, detection_preview) for face in detections]
    scores = [[_iou(box, other) if box and other else 0. for other in new_boxes]
              for box, _ in old_geometries]
    mapped = []
    mapped_old = set(); mapped_new = set()
    for index, row in enumerate(scores):
        if not row:
            continue
        ordered = sorted(range(len(row)), key=lambda j: (-row[j], j))
        target = ordered[0]; score = row[target]
        runner_up = row[ordered[1]] if len(ordered) > 1 else 0.
        competing = max((other[target] for i, other in enumerate(scores) if i != index), default=0.)
        if (score < MIN_MAPPING_IOU or runner_up >= MIN_MAPPING_IOU or competing >= MIN_MAPPING_IOU
                or score-runner_up < MIN_MAPPING_MARGIN or score-competing < MIN_MAPPING_MARGIN):
            continue
        mapped.append({'face_id': old_ids[index], 'original_index': index,
                       'analysis_index': target, 'match_iou': round(score, 8),
                       'metadata': _metadata(detections[target], new_boxes[target], image_size,
                                             detection_preview)})
        mapped_old.add(index); mapped_new.add(target)
    ambiguous = []
    for index, (box, reason) in enumerate(old_geometries):
        if index in mapped_old:
            continue
        nearby = [j for j, other in enumerate(new_boxes) if box and other and _plausible(box, other)]
        ambiguous.append({'reason': reason or ('ambiguous_mapping' if nearby else 'unmatched_existing'),
                          'face_ids': [old_ids[index]], 'analysis_indices': nearby})
    new_faces = []
    unknown_old = any(box is None for box, _ in old_geometries)
    for index, (face, box) in enumerate(zip(detections, new_boxes)):
        if index in mapped_new:
            continue
        nearby = [i for i, (old, _) in enumerate(old_geometries) if box and old and _plausible(box, old)]
        overlapping = [j for j, other in enumerate(new_boxes)
                       if j != index and box and other and _iou(box, other) >= .3]
        reason = ('invalid_detection_geometry' if box is None else
                  'existing_geometry_unavailable' if unknown_old else
                  'overlap_existing' if nearby else 'overlapping_detections' if overlapping else None)
        if reason:
            ambiguous.append({'reason': reason, 'face_ids': [old_ids[i] for i in nearby],
                              'analysis_indices': [index]})
            continue
        vector = face.get('embedding')
        if (not isinstance(vector, list) or not vector or not all(_number(v) for v in vector)
                or math.hypot(*vector) < 1e-12):
            ambiguous.append({'reason': 'invalid_detection_embedding', 'face_ids': [],
                              'analysis_indices': [index]})
            continue
        candidate_face_id = _fingerprint(['backfill-face-v1', photo_id, source_sha256,
                                          pipeline_key, index, face])[:32]
        new_face = copy.deepcopy(face)
        new_face.update(face_id=candidate_face_id, bbox=copy.deepcopy(box), original_bbox=copy.deepcopy(box),
                        coordinate_space='original-exif', image_size=image_size,
                        preview_size=_size(face.get('preview_size')) or detection_preview)
        new_face.pop('person_id', None)
        new_faces.append({'candidate_face_id': candidate_face_id, 'analysis_index': index,
                          'face': new_face,
                          'provenance': {'source_sha256': source_sha256, 'pipeline_key': pipeline_key,
                                         'analysis_face_id': face.get('face_id')}})
    candidate = {'schema_version': SCHEMA_VERSION, 'photo_id': photo_id,
                 'source_sha256': source_sha256, 'pipeline_key': pipeline_key,
                 'base_revision': revision(state), 'original_count': len(original),
                 'original_signature': signature, 'mapped': mapped, 'ambiguous': ambiguous,
                 'new_faces': new_faces, 'analysis': analysis_metadata}
    candidate['id'] = candidate_fingerprint(candidate)
    return candidate


def validate_candidate(state, candidate):
    if not isinstance(candidate, dict) or candidate.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('回填候選版本無效')
    if candidate.get('id') != candidate_fingerprint(candidate):
        raise ValueError('回填候選內容已變更')
    photo_id = candidate.get('photo_id')
    if candidate.get('original_signature') != photo_signature(state, photo_id):
        raise ValueError('照片的人臉或人工校正已更新，請重新分析回填')
    original = state['photos'][photo_id]['faces']
    if candidate.get('original_count') != len(original):
        raise ValueError('照片人臉數量已更新，請重新分析回填')
    used = set()
    for mapped in candidate.get('mapped', []):
        index = mapped.get('original_index')
        if (not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(original)
                or index in used or mapped.get('face_id') != face_id(photo_id, index, original[index])):
            raise ValueError('回填候選人臉對應無效')
        used.add(index)
    return True


def apply_metadata(state, candidate):
    """Add mapped metadata only; return changed-face count, never advance revision."""
    validate_candidate(state, candidate)
    original = state['photos'][candidate['photo_id']]['faces']
    prepared = []
    for mapped in candidate['mapped']:
        face = original[mapped['original_index']]
        previous = face.get('backfill_metadata', {})
        if not isinstance(previous, dict):
            raise ValueError('既有人臉回填資料格式異常')
        metadata = {**copy.deepcopy(mapped['metadata']), 'candidate_id': candidate['id'],
                    'source_sha256': candidate['source_sha256'], 'pipeline_key': candidate['pipeline_key'],
                    'match_iou': mapped['match_iou']}
        if candidate['id'] in previous and previous[candidate['id']] != metadata:
            raise ValueError('既有人臉回填資料與候選不一致')
        prepared.append((face, mapped['face_id'], metadata))
    changed = 0
    for face, key, metadata in prepared:
        updated = not face.get('face_id') or candidate['id'] not in face.get('backfill_metadata', {})
        if not face.get('face_id'):
            face['face_id'] = key
        face.setdefault('backfill_metadata', {}).setdefault(candidate['id'], metadata)
        changed += int(updated)
    return changed
