"""JSON persistence and conservative incremental face grouping; no database."""
import json, os, uuid
from pathlib import Path
import numpy as np
from matching import (MatchConfig, assign_photo, eligible, policy_fingerprint, representatives,
                      sample_record, select_representatives)
from face_state import advance, assert_merge_allowed, require_latest

def atomic_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf8') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def empty_state():
    return {'version': 1, 'photos': {}, 'people': {}, 'errors': {}}

def similarity(a, b):
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(np.dot(a, b) / max(float(np.linalg.norm(a) * np.linalg.norm(b)), 1e-12))

def _observation(face, photo_id, index, person_id):
    import copy
    result = copy.deepcopy(face)
    result.setdefault('face_id', uuid.uuid5(uuid.NAMESPACE_URL, f'wedding-face:{photo_id}:legacy:{index}').hex)
    result.update(person_id=person_id, thumbnail=face.get('thumbnail'), quality=face.get('quality', 0))
    return result


def add_photo(state, photo_id, name, faces, threshold=.50, margin=.06, *,
              mode='legacy', match_config=None, analysis=None):
    if mode not in ('legacy', 'shadow', 'enhanced'):
        raise ValueError('Unknown grouping mode')
    if photo_id in state['photos']:
        return False
    config = match_config or MatchConfig()
    if mode == 'enhanced':
        result = _add_photo_enhanced(state, photo_id, name, faces, config, analysis)
        advance(state, embeddings=True)
        return result
    shadow = None; shadow_error = None
    if mode == 'shadow':
        try:
            shadow = assign_photo(state['people'], faces, config)
        except Exception as error:
            # Shadow diagnostics must never turn a valid legacy ingestion into a failed job.
            shadow_error = type(error).__name__
    previous_ids = set(state['people'])
    _add_photo_legacy(state, photo_id, name, faces, threshold, margin)
    photo = state['photos'][photo_id]
    if analysis is not None:
        photo['analysis'] = analysis
    if shadow is not None:
        for decision, observation in zip(shadow, photo['faces']):
            actual = observation['person_id']
            decision['legacy_person_id'] = actual if actual in previous_ids else None
            decision['disagrees'] = decision['person_id'] != decision['legacy_person_id']
        photo['matching_shadow'] = {'policy': policy_fingerprint(config), 'decisions': shadow}
    elif shadow_error is not None:
        photo['matching_shadow'] = {'policy': policy_fingerprint(config), 'error': shadow_error}
    advance(state, embeddings=True)
    return True


def _add_photo_legacy(state, photo_id, name, faces, threshold, margin):
    used = set()
    observations = []
    for index, face in enumerate(faces):
        candidates = []
        for pid, person in state['people'].items():
            if pid in used or not person.get('samples') or person.get('empty') or person.get('provisional'): continue
            # Fixed exemplars prevent continually drifting clusters.
            score = max(similarity(face['embedding'], e) for e in person['samples'])
            candidates.append((score, pid))
        candidates.sort(reverse=True)
        confident = (candidates and candidates[0][0] >= threshold and
                     (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= margin))
        if confident:
            pid = candidates[0][1]
            person = state['people'][pid]
            if len(person['samples']) < person.get('sample_limit', 5): person['samples'].append(face['embedding'])
        else:
            pid = uuid.uuid4().hex
            person = {'samples': [face['embedding']], 'photo_ids': [],
                      'thumbnail': face['thumbnail'], 'quality': face['quality']}
            state['people'][pid] = person
        if not person.get('thumbnail_locked') and face['quality'] > person['quality']:
            person['thumbnail'] = face['thumbnail']; person['quality'] = face['quality']
        person['photo_ids'].append(photo_id)
        used.add(pid)
        observations.append(_observation(face, photo_id, index, pid))
    state['photos'][photo_id] = {'name': name, 'faces': observations}
    state['errors'].pop(photo_id, None)
    return True


def _add_photo_enhanced(state, photo_id, name, faces, config, analysis):
    decisions = assign_photo(state['people'], faces, config)
    observations = []
    for index, (face, decision) in enumerate(zip(faces, decisions)):
        pid = decision['person_id']
        if pid is None:
            pid = uuid.uuid4().hex
            state['people'][pid] = {'samples': [face['embedding']] if eligible(face) else [], 'photo_ids': [],
                                    'thumbnail': face['thumbnail'], 'quality': face['quality'],
                                    'thumbnail_rank': sample_record(_observation(face, photo_id, index, pid), pid)['rank'],
                                    'representatives': [], 'provisional': not eligible(face)}
            decision['source_group'] = pid
            decision['sample_eligible'] = eligible(face)
        person = state['people'][pid]
        observation = _observation(face, photo_id, index, pid)
        observation['matching'] = {**decision, 'policy': policy_fingerprint(config)}
        if decision['sample_eligible']:
            pool = representatives(person, pid)
            pool.append(sample_record(observation, decision['source_group']))
            person['representatives'] = select_representatives(pool, config.representative_limit)
            if len(person['samples']) < person.get('sample_limit', 5):
                if not any(np.array_equal(face['embedding'], v) for v in person['samples']):
                    person['samples'].append(face['embedding'])
        rank = sample_record(observation, decision['source_group'])['rank']
        if (eligible(face) and not person.get('thumbnail_locked') and
                rank > person.get('thumbnail_rank', -1.)):
            person['thumbnail'] = face['thumbnail']; person['quality'] = face['quality']
            person['thumbnail_rank'] = rank
        person['photo_ids'].append(photo_id)
        observations.append(observation)
    state['photos'][photo_id] = {'name': name, 'faces': observations,
                                'grouping_strategy': 'whole-photo-v1',
                                'matching_policy': policy_fingerprint(config)}
    if analysis is not None:
        state['photos'][photo_id]['analysis'] = analysis
    state['errors'].pop(photo_id, None)
    return True


def resolve_person(state, person_id):
    seen = set()
    while person_id in state.get('aliases', {}):
        if person_id in seen: raise ValueError('人物連結異常')
        seen.add(person_id)
        person_id = state['aliases'][person_id]
    return person_id


def merge_people(state, person_ids, target_id=None):
    """Called on a transactional copy, under the service state lock."""
    import copy, time
    if not isinstance(person_ids, list) or not 2 <= len(person_ids) <= 100:
        raise ValueError('請選擇 2 到 100 個大頭貼')
    if any(not isinstance(pid, str) for pid in person_ids) or len(set(person_ids)) != len(person_ids):
        raise ValueError('人物選擇不能重複')
    if any(pid not in state['people'] for pid in person_ids):
        raise ValueError('人物資料已更新，請重新整理後再選擇')
    target_id = target_id or person_ids[0]
    if target_id not in person_ids: raise ValueError('保留的頭貼必須在選擇的人物中')
    assert_merge_allowed(state, person_ids)
    snapshots = {pid: copy.deepcopy(state['people'][pid]) for pid in person_ids}
    changes = []
    selected = set(person_ids)
    for photo_id, photo in state['photos'].items():
        for i, face in enumerate(photo['faces']):
            if face['person_id'] in selected and face['person_id'] != target_id:
                changes.append([photo_id, i, face['person_id']])
                face['person_id'] = target_id
    target = state['people'][target_id]
    target['photo_ids'] = list(dict.fromkeys(pid for person in snapshots.values() for pid in person['photo_ids']))
    target['empty'] = not bool(target['photo_ids'])
    # Merging retains every prior exemplar; total exemplars do not grow simply by merging.
    target['samples'] = [e for person in snapshots.values() for e in person['samples']]
    # Preserve every manually selected source mode, including legacy exemplars.
    target['representatives'] = [r for pid, person in snapshots.items() for r in representatives(person, pid)]
    target['provisional'] = not bool(target['representatives'])
    target['sample_limit'] = len(target['samples']) + 5
    target['manually_merged'] = True
    target['thumbnail_locked'] = True
    # Keep the explicitly chosen representative rather than changing it on this merge.
    for pid in person_ids:
        if pid != target_id: del state['people'][pid]
    history = state.setdefault('merge_history', [])
    entry = {'id': uuid.uuid4().hex, 'created_at': time.time(), 'target_id': target_id,
             'people': snapshots, 'changes': changes,
             'previous_aliases': copy.deepcopy(state.get('aliases', {}))}
    history.append(entry)
    aliases = state.setdefault('aliases', {})
    for pid in person_ids:
        if pid != target_id: aliases[pid] = target_id
    state.setdefault('admin_history', []).append({'id': entry['id'], 'kind': 'merge',
                                                  'created_at': entry['created_at']})
    advance(state)
    return {'merge_id': entry['id'], 'person_id': target_id, 'count': len(target['photo_ids']),
            'merged_count': len(person_ids)}


def _rebuild_person(state, pid, fallback):
    import copy
    person = copy.deepcopy(fallback)
    person['photo_ids'] = []
    # Snapshot anchors may predate the observations kept by this index. Never discard them on undo.
    person['samples'] = copy.deepcopy(fallback.get('samples', []))
    trusted = representatives(fallback, pid) if 'representatives' in fallback else None
    limit = person.get('sample_limit', 5)
    for photo_id, photo in state['photos'].items():
        for i, face in enumerate(photo['faces']):
            if face['person_id'] != pid: continue
            if photo_id not in person['photo_ids']: person['photo_ids'].append(photo_id)
            # Restore snapshot anchors, but never promote rejected/known-bad observations on undo.
            can_add = face.get('matching', {}).get('sample_eligible',
                        eligible(face) if 'quality_metrics' in face else True)
            if can_add and len(person['samples']) < limit:
                if not any(np.array_equal(face['embedding'], v) for v in person['samples']):
                    person['samples'].append(face['embedding'])
            if trusted is not None and face.get('matching', {}).get('sample_eligible'):
                trusted.append(sample_record(face, face['matching'].get('source_group', pid)))
            quality = face.get('quality', face['bbox'][2] * face['bbox'][3])
            if 'thumbnail_rank' in person:
                rank = sample_record(face, pid)['rank'] if eligible(face) else -1.
                better = rank > person['thumbnail_rank']
            else:
                rank = None
                better = quality > person.get('quality', 0)
            if not person.get('thumbnail_locked') and better:
                person['quality'] = quality
                person['thumbnail'] = face.get('thumbnail') or f'{photo_id}_{i}.jpg'
                if rank is not None:
                    person['thumbnail_rank'] = rank
    if trusted is not None:
        person['representatives'] = select_representatives(trusted)
    person['empty'] = not bool(person['photo_ids'])
    state['people'][pid] = person


def undo_merge(state, merge_id):
    """Undo only the latest merge. New photos remain on the kept person."""
    history = state.get('merge_history', [])
    if not history or history[-1]['id'] != merge_id:
        raise ValueError('請從最近一次合併開始復原；資料可能已更新，請重新整理')
    require_latest(state, merge_id, 'merge')
    entry = history[-1]
    for photo_id, i, old_id in entry['changes']:
        if state['photos'][photo_id]['faces'][i]['person_id'] != entry['target_id']:
            raise ValueError('人物資料不一致，無法復原')
    for photo_id, i, old_id in entry['changes']:
        state['photos'][photo_id]['faces'][i]['person_id'] = old_id
    state['aliases'] = entry['previous_aliases']
    for pid, original in entry['people'].items():
        _rebuild_person(state, pid, original)
    history.pop()
    if state.get('admin_history') and state['admin_history'][-1]['id'] == merge_id:
        state['admin_history'].pop()
    advance(state)
    return {'restored_count': len(entry['people']), 'person_ids': list(entry['people'])}
