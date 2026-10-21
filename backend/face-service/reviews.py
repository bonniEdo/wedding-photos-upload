"""Human review and reversible face corrections; caller commits a transactional copy."""
import copy
import hashlib
import itertools
import json
import time
import uuid

import numpy as np

from face_state import (active_constraints, advance, face_id, face_map, latest_operation,
                        observations, require_latest, revision)
from matching import cosine, eligible, representatives, sample_record, select_representatives


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def face_view(key, photo_id, face):
    return {'face_id': key, 'photo_id': photo_id, 'person_id': face['person_id'],
            'crop_url': f'/api/admin/faces/{key}/crop', 'photo_url': f'/api/photos/{photo_id}/image',
            'bbox': face['bbox'], 'coordinate_space': face.get('coordinate_space', 'preview-exif'),
            'image_size': face.get('image_size'), 'preview_size': face.get('preview_size'),
            'quality': 'clear' if eligible(face) else 'unknown' if 'quality_metrics' not in face else 'limited',
            'confirmed': face.get('manual_confirmed', False)}


def build_reviews(state, threshold=.50):
    lookup = face_map(state)
    by_person = {}
    pending = []
    ignored = state.get('ignored_reviews', {})
    for key, (photo_id, _, face) in lookup.items():
        pid = face['person_id']
        by_person.setdefault(pid, []).append((key, photo_id, face))
        reason = face.get('matching', {}).get('reason')
        if not face.get('manual_confirmed') and (state['people'].get(pid, {}).get('provisional') or
                reason in ('low_quality', 'ambiguous', 'same_photo_conflict')):
            review_id = fingerprint(['pending', key, pid])
            if review_id not in ignored:
                pending.append({'id': review_id, 'kind': 'pending', 'face_ids': [key],
                                'person_ids': [pid], 'faces': [face_view(key, photo_id, face)],
                                'reason': reason or 'provisional'})
    excluded = {frozenset(pair) for _, _, pair in active_constraints(state, lookup)}
    banks = {}
    signatures = {}
    for pid, rows in by_person.items():
        # Prefer measured/representative observations; don't propose merges from known bad crops.
        candidates = [r for r in rows if eligible(r[2]) or 'quality_metrics' not in r[2]]
        anchors = {r['face_id'] for r in representatives(state['people'][pid], pid)}
        candidates.sort(key=lambda r: (r[0] not in anchors, -float(r[2].get('quality', 0)), r[0]))
        chosen = []
        for row in candidates:
            if not chosen or max(cosine(row[2]['embedding'], r[2]['embedding']) for r in chosen) < .995:
                chosen.append(row)
            if len(chosen) == 3:
                break
        banks[pid] = chosen
        signatures[pid] = fingerprint([(r[0], r[2]['embedding']) for r in rows])
    duplicates = []
    for first, second in itertools.combinations(sorted(banks), 2):
        if frozenset((first, second)) in excluded:
            continue
        pairs = [(cosine(a[2]['embedding'], b[2]['embedding']), a, b)
                 for a in banks[first] for b in banks[second]]
        if not pairs:
            continue
        score, a, b = max(pairs, key=lambda p: (p[0], p[1][0], p[2][0]))
        if score < threshold:
            continue
        review_id = fingerprint(['duplicate', first, second, signatures[first], signatures[second]])
        if review_id in ignored:
            continue
        duplicates.append({'id': review_id, 'kind': 'duplicate', 'person_ids': [first, second],
                           'face_ids': [a[0], b[0]], 'score': round(score, 4),
                           'same_photo': bool(set(state['people'][first]['photo_ids']) &
                                              set(state['people'][second]['photo_ids'])),
                           'faces': [face_view(a[0], a[1], a[2]), face_view(b[0], b[1], b[2])]})
    duplicates.sort(key=lambda r: (-r['score'], r['id']))
    constraints = []
    for key, entry in state.get('face_constraints', {}).items():
        constraints.append({'id': key, 'face_ids': entry['face_ids'],
                            'faces': [face_view(fid, lookup[fid][0], lookup[fid][2])
                                      for fid in entry['face_ids'] if fid in lookup]})
    return {'revision': revision(state), 'reviews': duplicates + pending, 'constraints': constraints}


def rebuild_corrected_person(state, pid, fallback, removed=()):
    """Remove known contaminated anchors, retain untraceable legacy anchors, keep empty IDs."""
    rows = [(key, photo_id, face) for key, photo_id, _, face in observations(state) if face['person_id'] == pid]
    person = copy.deepcopy(fallback)
    person['photo_ids'] = list(dict.fromkeys(photo_id for _, photo_id, _ in rows))
    person['empty'] = not bool(rows)
    if not rows:
        person.update(samples=[], representatives=[], provisional=True)
        state['people'][pid] = person
        return
    trusted = [r for r in rows if eligible(r[2]) or 'quality_metrics' not in r[2]]
    current_vectors = [r[2]['embedding'] for r in rows]
    good_vectors = [r[2]['embedding'] for r in trusted]
    removed_vectors = [r['embedding'] for r in removed]
    def equals(vector, pool):
        return any(np.array_equal(vector, other) for other in pool)
    def retained(vector):
        if equals(vector, good_vectors):
            return True
        return not equals(vector, removed_vectors) and not equals(vector, current_vectors)
    samples = [list(v) for v in fallback.get('samples', []) if retained(v)]
    records = [r for r in representatives(fallback, pid) if retained(r['embedding'])]
    for key, _, face in trusted:
        vector = face['embedding']
        if not equals(vector, samples):
            samples.append(list(vector))
        compatible = next((r['source_group'] for r in records if np.array_equal(r['embedding'], vector)), pid)
        record = sample_record({**face, 'face_id': key}, face.get('manual_source_group', compatible))
        records.append(record)
    person['samples'] = samples
    person['representatives'] = select_representatives(records)
    person['provisional'] = not bool(samples)
    removed_thumbs = {r.get('thumbnail') for r in removed}
    current_thumbs = {r[2].get('thumbnail') for r in rows}
    head_removed = person.get('thumbnail') in removed_thumbs and person.get('thumbnail') not in current_thumbs
    if head_removed:
        person['thumbnail_locked'] = False
    if head_removed or not person.get('thumbnail_locked'):
        key, _, best = max(trusted or rows, key=lambda r: (sample_record({**r[2], 'face_id': r[0]}, pid)['rank'],
                                                          float(r[2].get('quality', 0)), r[0]))
        person.update(thumbnail=best.get('thumbnail'), quality=best.get('quality', 0),
                      thumbnail_rank=sample_record({**best, 'face_id': key}, pid)['rank'])
    state['people'][pid] = person


def record_operation(state, kind, **payload):
    entry = {'id': uuid.uuid4().hex, 'kind': kind, 'created_at': time.time(), **payload}
    state.setdefault('admin_history', []).append(entry)
    advance(state)
    return {'operation_id': entry['id'], 'revision': revision(state)}


def reassign_faces(state, face_ids, target_id=None):
    lookup = face_map(state)
    if not face_ids or len(face_ids) > 100 or len(set(face_ids)) != len(face_ids):
        raise ValueError('請選擇 1 到 100 張不重複的人臉')
    if any(fid not in lookup for fid in face_ids):
        raise ValueError('找不到選定人臉，請重新整理')
    if target_id is not None and target_id not in state['people']:
        raise ValueError('目標人物已更新，請重新整理')
    sources = {lookup[fid][2]['person_id'] for fid in face_ids}
    if target_id is not None and sources == {target_id}:
        raise ValueError('人臉已在目標人物中；可改用確認操作')
    for _, entry, _ in active_constraints(state, lookup):
        pair = entry['face_ids']
        joins_opposite = target_id and any(fid in face_ids and lookup[other][2]['person_id'] == target_id
                                          for fid, other in (pair, list(reversed(pair))))
        if all(fid in face_ids for fid in pair) or joins_opposite:
            raise ValueError('目標人物與選定人臉有不同人紀錄，請先撤銷排除')
    target_id = target_id or uuid.uuid4().hex
    affected = sources | {target_id}
    before = {pid: copy.deepcopy(state['people'][pid]) for pid in affected if pid in state['people']}
    faces_before = {fid: copy.deepcopy(lookup[fid][2]) for fid in face_ids}
    if target_id not in state['people']:
        first = lookup[face_ids[0]][2]
        state['people'][target_id] = {'samples': [], 'representatives': [], 'photo_ids': [],
                                     'thumbnail': first.get('thumbnail'), 'quality': first.get('quality', 0)}
    for fid in face_ids:
        face = lookup[fid][2]
        face.update(person_id=target_id, manual_confirmed=True, manual_source_group=f'confirmed:{target_id}')
    for pid in affected:
        rebuild_corrected_person(state, pid, before.get(pid, state['people'][pid]), faces_before.values())
    state['people'][target_id]['provisional'] = False
    result = record_operation(state, 'reassign', people_before=before, faces_before=faces_before,
                              target_id=target_id, affected_ids=sorted(affected))
    return {**result, 'person_id': target_id, 'moved_count': len(face_ids)}


def resolve_review(state, review_id, action, target_id=None, catalog=None):
    catalog = catalog or build_reviews(state)
    review = next((r for r in catalog['reviews'] if r['id'] == review_id), None)
    if review is None:
        raise ValueError('複核建議已更新，請重新整理')
    if action == 'merge' and review['kind'] == 'duplicate':
        from core import merge_people
        return merge_people(state, review['person_ids'], target_id)
    if action == 'skip':
        previous = state.setdefault('ignored_reviews', {}).get(review_id)
        state['ignored_reviews'][review_id] = True
        return record_operation(state, 'skip', key=review_id, previous=previous)
    if action == 'different' and review['kind'] == 'duplicate':
        key = uuid.uuid4().hex
        state.setdefault('face_constraints', {})[key] = {'face_ids': review['face_ids'], 'created_at': time.time()}
        return {**record_operation(state, 'different', key=key), 'constraint_id': key}
    if action == 'approve' and review['kind'] == 'pending':
        lookup = face_map(state); fid = review['face_ids'][0]; face = lookup[fid][2]; pid = face['person_id']
        before = copy.deepcopy(state['people'][pid]); face_before = copy.deepcopy(face)
        face['manual_confirmed'] = True
        rebuild_corrected_person(state, pid, before)
        state['people'][pid]['provisional'] = False
        return record_operation(state, 'approve', people_before={pid: before}, faces_before={fid: face_before},
                                affected_ids=[pid])
    raise ValueError('此複核操作不適用')


def revoke_constraint(state, constraint_id):
    previous = state.get('face_constraints', {}).get(constraint_id)
    if previous is None:
        raise ValueError('排除紀錄已更新，請重新整理')
    del state['face_constraints'][constraint_id]
    return record_operation(state, 'revoke', key=constraint_id, previous=previous)


def undo_correction(state, operation_id):
    entry = latest_operation(state)
    if not entry or entry['kind'] == 'merge':
        raise ValueError('請從最近一次人工校正開始復原')
    require_latest(state, operation_id, entry['kind'])
    kind = entry['kind']
    if kind in ('reassign', 'approve'):
        lookup = face_map(state)
        if any(fid not in lookup for fid in entry['faces_before']):
            raise ValueError('人臉資料不完整，無法復原')
        removed = [copy.deepcopy(lookup[fid][2]) for fid in entry['faces_before']]
        for fid, original in entry['faces_before'].items():
            current = lookup[fid][2]
            backfilled = copy.deepcopy(current.get('backfill_metadata', {}))
            current.clear(); current.update(copy.deepcopy(original))
            current.setdefault('face_id', fid)
            if backfilled: current.setdefault('backfill_metadata', {}).update(backfilled)
        for pid in entry['affected_ids']:
            fallback = entry['people_before'].get(pid, state['people'][pid])
            if pid in entry['people_before']:
                from core import _rebuild_person
                _rebuild_person(state, pid, fallback)
                state['people'][pid]['empty'] = not bool(state['people'][pid]['photo_ids'])
            else:
                rebuild_corrected_person(state, pid, fallback, removed)
    elif kind == 'backfill_add':
        from backfill_jobs import undo_added_faces
        undo_added_faces(state, entry)
    elif kind == 'different':
        state.get('face_constraints', {}).pop(entry['key'], None)
    elif kind == 'skip':
        if entry['previous'] is None:
            state.get('ignored_reviews', {}).pop(entry['key'], None)
        else:
            state.setdefault('ignored_reviews', {})[entry['key']] = entry['previous']
    elif kind == 'revoke':
        state.setdefault('face_constraints', {})[entry['key']] = entry['previous']
    else:
        raise ValueError('未知的人工操作類型')
    state['admin_history'].pop()
    advance(state, embeddings=kind == 'backfill_add')
    return {'restored_operation': operation_id, 'revision': revision(state)}


def operation_history(state):
    latest = latest_operation(state)
    entries = [{'id': e['id'], 'kind': 'merge', 'created_at': e['created_at'],
                'merged_count': len(e['people'])} for e in state.get('merge_history', [])]
    entries += [{k: e[k] for k in ('id', 'kind', 'created_at')} for e in state.get('admin_history', [])
                if e['kind'] != 'merge']
    entries.sort(key=lambda e: e['created_at'])
    for entry in entries:
        entry['can_undo'] = bool(latest and latest['id'] == entry['id'])
    return entries
