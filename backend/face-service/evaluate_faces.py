"""Offline face evaluation. Never imports app.py or writes a live service index.

init creates an UNLABELLED manifest; run emits predictions and metrics (if annotated).
All boxes in the manifest and predictions use EXIF-corrected ORIGINAL image pixels.
"""
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import sys
import time

import cv2
import numpy as np
from dotenv import dotenv_values

from core import add_photo, empty_state, similarity
from face_engine import DETECTOR_MODEL, RECOGNIZER_MODEL, EngineConfig, FaceEngine, iou
from matching import MatchConfig

BASE = Path(__file__).resolve().parent.parent
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def inside(path, directory):
    return path == directory or directory in path.parents


def safe_destination(path, image_root=None):
    """Protect default AND configured DATA_DIR, including symlinked destinations."""
    destination = Path(path).resolve()
    settings = {**dotenv_values(BASE / '.env'), **os.environ}
    roots = [(BASE / 'data').resolve(), (BASE / 'models').resolve()]
    if settings.get('DATA_DIR'):
        # Node and Python resolve relative DATA_DIR against their launch directory;
        # protect both common launch locations instead of guessing one.
        raw = Path(settings['DATA_DIR'])
        roots += [raw.resolve(), (BASE / raw).resolve()]
    if image_root is not None:
        roots.append(Path(image_root).resolve())
    if any(inside(destination, root) for root in roots):
        raise ValueError('Evaluation output must be outside service data, models and source photos')
    return destination


def write_new_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def init_manifest(photos, destination):
    root = Path(photos).resolve()
    if not root.is_dir():
        raise ValueError('Photo directory not found')
    target = safe_destination(destination, root)
    entries = []
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.suffix.lower() in EXTENSIONS:
            entries.append({'id': f'photo-{len(entries)+1:04}', 'file': str(path.relative_to(root)),
                            'sha256': digest(path), 'split': 'calibration', 'scene_id': 'unassigned',
                            'annotated': False, 'faces': []})
    if not entries:
        raise ValueError('No supported photos found')
    document = {'version': 1, 'root': str(root), 'coordinate_space': 'original-exif',
                'notes': 'Set scene_id/split; annotate boxes and anonymous identities manually. '
                         'Set annotated=true only after checking ALL evaluable faces. Never copy predictions as truth.',
                'photos': entries, 'queries': []}
    write_new_json(target, document)
    return len(entries)


def validate_manifest(document, manifest_path):
    if document.get('version') != 1 or document.get('coordinate_space') != 'original-exif':
        raise ValueError('Manifest needs version=1 and coordinate_space=original-exif')
    root = Path(document['root'])
    if not root.is_absolute():
        root = Path(manifest_path).resolve().parent / root
    root = root.resolve()
    seen = set(); hashes = {}; scenes = {}
    for entry in document['photos'] + document.get('queries', []):
        entry_id = entry.get('id')
        if not isinstance(entry_id, str) or not entry_id or entry_id in seen:
            raise ValueError('Photo and query IDs must be unique non-empty strings')
        seen.add(entry_id)
        if entry.get('split') not in ('calibration', 'test'):
            raise ValueError('Each entry must have split=calibration or test')
        scene = entry.get('scene_id')
        if not isinstance(scene, str) or not scene:
            raise ValueError('Each entry needs a scene_id to guard against burst leakage')
        if scene in scenes and scenes[scene] != entry['split']:
            raise ValueError('A scene/burst cannot appear in both splits')
        scenes[scene] = entry['split']
        path = (root / entry['file']).resolve()
        if not inside(path, root) or not path.is_file():
            raise ValueError('Every source file must exist inside manifest root')
        actual = digest(path)
        if entry.get('sha256') != actual:
            raise ValueError(f'Content changed or missing sha256: {entry_id}')
        if actual in hashes:
            raise ValueError('Duplicate photo/query content; deduplicate before evaluation')
        hashes[actual] = entry['split']
        for face in entry.get('faces', []):
            box = face.get('bbox', [])
            if (len(box) != 4 or not all(isinstance(v, (int, float)) and np.isfinite(v) for v in box)
                    or min(box[:2]) < 0 or min(box[2:]) <= 0):
                raise ValueError('Face bbox must be finite original-pixel [x,y,width,height]')
            identity = face.get('identity')
            if identity is not None and (not isinstance(identity, str) or not identity):
                raise ValueError('Identity must be an anonymous string or null')
    photo_ids = {p['id'] for p in document['photos']}
    for query in document.get('queries', []):
        if query.get('annotated') and ('relevant_photo_ids' not in query or
                                      not set(query['relevant_photo_ids']) <= photo_ids):
            raise ValueError('Annotated queries need valid relevant_photo_ids')
        if query.get('annotated'):
            photo_split = {p['id']: p['split'] for p in document['photos']}
            if any(photo_split[pid] != query['split'] for pid in query['relevant_photo_ids']):
                raise ValueError('Query relevance must reference photos from the same split')
    return root


def detection_matches(predicted, labelled, threshold=.5):
    """Score-ordered, one-to-one IoU evaluation; ignore regions don't count as negatives."""
    remaining = {i for i, face in enumerate(labelled) if not face.get('ignore', False)}
    ignored = [face['bbox'] for face in labelled if face.get('ignore', False)]
    matches = []; unmatched = []
    order = sorted(range(len(predicted)), key=lambda i: -predicted[i]['quality_metrics']['detector_score'])
    for index in order:
        box = predicted[index]['original_bbox']
        candidates = sorted(((iou(box, labelled[j]['bbox']), j) for j in remaining), reverse=True)
        if candidates and candidates[0][0] >= threshold:
            label_index = candidates[0][1]; remaining.remove(label_index)
            matches.append((index, label_index))
        elif not any(iou(box, region) >= threshold for region in ignored):
            unmatched.append(index)
    return matches, unmatched, sorted(remaining)


def ratio(a, b):
    return a / b if b else None


def detection_metrics(counts):
    tp, fp, fn = counts
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': ratio(tp, tp+fp), 'recall': ratio(tp, tp+fn)}


def grouping_metrics(observations):
    tp = fp = fn = 0
    for first, second in itertools.combinations(observations, 2):
        same_truth = first['identity'] == second['identity']
        same_prediction = first['person_id'] == second['person_id']
        tp += same_truth and same_prediction
        fp += not same_truth and same_prediction
        fn += same_truth and not same_prediction
    identities = {}; groups = {}
    for row in observations:
        identities.setdefault(row['identity'], set()).add(row['person_id'])
        groups.setdefault(row['person_id'], set()).add(row['identity'])
    return {'labelled_observations': len(observations), 'same_person_pairs': tp+fn,
            'true_same_pairs': tp, 'false_merge_pairs': fp, 'false_split_pairs': fn,
            'pair_precision': ratio(tp, tp+fp), 'pair_recall': ratio(tp, tp+fn),
            'split_identities': sum(len(v) > 1 for v in identities.values()),
            'mixed_groups': sum(len(v) > 1 for v in groups.values()),
            'note': 'Conditional on detected, IoU-matched faces; also inspect detection recall. '
                    'Pairs are correlated; no independent-pair confidence interval is claimed.'}


def summary(values):
    return {'count': len(values), 'p50': float(np.percentile(values, 50)) if values else None,
            'p95': float(np.percentile(values, 95)) if values else None}


def evaluate(document, root, engine, pipeline, mode, split, match_config=None, legacy_threshold=.50,
             legacy_margin=.06, search_threshold=.30, small_pixels=64):
    state = empty_state(); counts = [0, 0, 0]; small = [0, 0, 0]
    timings = []; grouping_times = []; predicted_rows = []; labelled_observations = []; annotated = 0
    partial = 0; undecidable = 0; dropped_identity = 0
    for entry in document['photos']:
        if entry['split'] != split:
            continue
        analysis = engine.analyze((root / entry['file']).read_bytes(), entry['id'], pipeline)
        timings.append(analysis.metadata['elapsed_seconds']); partial += analysis.metadata['partial']
        group_started = time.perf_counter()
        add_photo(state, entry['id'], entry['id'], analysis.faces, legacy_threshold, legacy_margin,
                  mode=mode, match_config=match_config, analysis=analysis.metadata)
        grouping_times.append(time.perf_counter()-group_started)
        observations = state['photos'][entry['id']]['faces']
        labels = entry.get('faces', [])
        if entry.get('annotated'):
            annotated += 1
            matched, unmatched, missing = detection_matches(analysis.faces, labels)
            counts = [counts[0]+len(matched), counts[1]+len(unmatched), counts[2]+len(missing)]
            small[0] += sum(min(labels[j]['bbox'][2:]) <= small_pixels for _, j in matched)
            small[1] += sum(min(analysis.faces[i]['original_bbox'][2:]) <= small_pixels for i in unmatched)
            small[2] += sum(min(labels[j]['bbox'][2:]) <= small_pixels for j in missing)
            for i, j in matched:
                identity = labels[j].get('identity')
                if identity is not None:
                    labelled_observations.append({'identity': identity, 'person_id': observations[i]['person_id']})
                else:
                    undecidable += 1
            dropped_identity += sum(labels[j].get('identity') is not None for j in missing)
        predicted_rows.append({'id': entry['id'], 'analysis': analysis.metadata, 'faces': observations})
    searches = []
    selected_photos = set(state['photos'])
    for query in document.get('queries', []):
        if query['split'] != split:
            continue
        started = time.perf_counter()
        faces = engine.analyze((root / query['file']).read_bytes(), None, pipeline).faces
        result = {'id': query['id'], 'status': 'ok' if len(faces) == 1 else 'no_face' if not faces else 'multiple_faces'}
        matches = []
        if len(faces) == 1:
            for pid, photo in state['photos'].items():
                score = max((similarity(faces[0]['embedding'], f['embedding']) for f in photo['faces']), default=-1)
                if score >= search_threshold:
                    matches.append((score, pid))
            matches.sort(key=lambda pair: (-pair[0], pair[1]))
        result['elapsed_seconds'] = time.perf_counter() - started
        result['matches'] = [{'id': pid, 'score': score} for score, pid in matches]
        if query.get('annotated'):
            relevant = set(query['relevant_photo_ids']) & selected_photos
            hits = len(relevant & {pid for _, pid in matches[:20]})
            result.update(relevant_count=len(relevant), precision_at_20=hits/20,
                          recall_at_20=ratio(hits, len(relevant)),
                          threshold_recall=ratio(len(relevant & {pid for _, pid in matches}), len(relevant)))
        searches.append(result)
    metrics = {'status': 'labelled' if annotated else 'unlabelled', 'annotated_photos': annotated,
               'photos': len(predicted_rows), 'people': len(state['people']), 'partial_photos': partial,
               'detection': detection_metrics(counts) if annotated else None,
               'small_face_detection': detection_metrics(small) if annotated else None,
               'small_face_definition': f'min(original bbox width,height)<={small_pixels}px',
               'matched_unidentified_faces': undecidable, 'missed_identified_faces': dropped_identity,
               'grouping': grouping_metrics(labelled_observations) if labelled_observations else None,
               'analysis_seconds': summary(timings),
               'grouping_seconds': summary(grouping_times),
               'search_seconds': summary([q['elapsed_seconds'] for q in searches]),
               'search_metric_definition': 'precision@20=hits/20; failed/no-face/multi-face queries retain zero hits',
               'searches': searches}
    return {'metrics': metrics, 'predictions': predicted_rows}


def run(args):
    manifest_path = Path(args.manifest).resolve()
    document = json.loads(manifest_path.read_text(encoding='utf8'))
    root = validate_manifest(document, manifest_path)
    if args.order == 'reverse':
        document['photos'] = list(reversed(document['photos']))
    output = safe_destination(args.output, root)
    if inside(manifest_path, output):
        raise ValueError('Output directory must not contain the input manifest')
    if not any(p['split'] == args.split for p in document['photos']):
        raise ValueError('Selected split has no photos')
    config = EngineConfig(max_tiles=args.max_tiles, tile_seconds=args.tile_seconds)
    match_config = MatchConfig(threshold=args.group_threshold, margin=args.group_margin,
                               single_threshold=args.single_threshold, sample_threshold=args.sample_threshold,
                               sample_margin=args.sample_margin)
    engine = FaceEngine.from_models(args.models, config)
    output.mkdir(parents=True, exist_ok=False)
    report = {'manifest_sha256': digest(manifest_path), 'manifest_order': [p['id'] for p in document['photos'] if p['split']==args.split],
              'split': args.split, 'order': args.order, 'python': platform.python_version(), 'platform': platform.platform(),
              'opencv': cv2.__version__, 'opencv_threads': cv2.getNumThreads(), 'numpy': np.__version__,
              'models': {name: digest(Path(args.models)/name) for name in (DETECTOR_MODEL, RECOGNIZER_MODEL)},
              'code_sha256': {name: digest(Path(__file__).parent/name)
                              for name in ('evaluate_faces.py', 'face_engine.py', 'core.py', 'matching.py')},
              'engine_config': config.__dict__, 'match_config': match_config.__dict__,
              'legacy_threshold': args.legacy_threshold, 'legacy_margin': args.legacy_margin,
              'search_threshold': args.search_threshold,
              'warning': 'Predictions and timings are not accuracy evidence without human labels. '
                         'No thresholds are selected from this split by this tool. Timings exclude asset writes; '
                         'run repeated/interleaved trials for speed conclusions.', 'runs': {}}
    for pipeline, mode in itertools.product(args.pipeline, args.group_mode):
        result = evaluate(document, root, engine, pipeline, mode, args.split, match_config,
                          args.legacy_threshold, args.legacy_margin, args.search_threshold, args.small_pixels)
        key = f'{pipeline}-{mode}'
        write_new_json(output/f'{key}-predictions.json', result['predictions'])
        report['runs'][key] = result['metrics']
    try:
        import resource
        report['process_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    except ImportError:
        report['process_peak_rss_bytes'] = None
    report['peak_rss_note'] = 'Process-wide high-water mark across all runs, not a per-pipeline isolated measurement.'
    write_new_json(output/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Create UNLABELLED annotation scaffold; does not run models')
    init.add_argument('--photos', required=True); init.add_argument('--manifest', required=True)
    evaluate_parser = sub.add_parser('run', help='Run offline predictions and optional labelled metrics')
    evaluate_parser.add_argument('--manifest', required=True); evaluate_parser.add_argument('--output', required=True)
    evaluate_parser.add_argument('--models', default=str(BASE/'models'))
    evaluate_parser.add_argument('--split', required=True, choices=['calibration', 'test'])
    evaluate_parser.add_argument('--order', choices=['manifest', 'reverse'], default='manifest')
    evaluate_parser.add_argument('--pipeline', nargs='+', choices=['legacy', 'enhanced'], default=['legacy', 'enhanced'])
    evaluate_parser.add_argument('--group-mode', nargs='+', choices=['legacy', 'shadow', 'enhanced'], default=['legacy', 'enhanced'])
    evaluate_parser.add_argument('--max-tiles', type=int, default=32)
    evaluate_parser.add_argument('--tile-seconds', type=float, default=10.)
    evaluate_parser.add_argument('--small-pixels', type=int, default=64)
    for name, default in [('group-threshold', .60), ('group-margin', .08), ('single-threshold', .65),
                          ('sample-threshold', .70), ('sample-margin', .10), ('legacy-threshold', .50),
                          ('legacy-margin', .06), ('search-threshold', .30)]:
        evaluate_parser.add_argument('--'+name, type=float, default=default)
    args = parser.parse_args()
    try:
        if args.command == 'init':
            print(f'Created unlabelled manifest: {init_manifest(args.photos, args.manifest)} photos')
        else:
            if (args.small_pixels <= 0 or not all(np.isfinite(v) and 0 <= v <= 1
                    for v in (args.legacy_threshold, args.legacy_margin, args.search_threshold))):
                raise ValueError('Invalid evaluation thresholds')
            report = run(args)
            print(json.dumps({key: {'photos': value['photos'], 'status': value['status'],
                                   'partial': value['partial_photos'], 'seconds': value['analysis_seconds']}
                              for key, value in report['runs'].items()}, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f'Evaluation failed: {error}\n')


if __name__ == '__main__':
    main()
