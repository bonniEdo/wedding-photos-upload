"""JSON persistence and conservative incremental face grouping; no database."""
import json, os, uuid
from pathlib import Path
import numpy as np

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

def add_photo(state, photo_id, name, faces, threshold=.50, margin=.06):
    if photo_id in state['photos']:
        return False
    used = set()
    observations = []
    for face in faces:
        candidates = []
        for pid, person in state['people'].items():
            if pid in used: continue
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
        observations.append({'person_id': pid, 'embedding': face['embedding'], 'bbox': face['bbox'],
                             'thumbnail': face.get('thumbnail'), 'quality': face.get('quality', 0)})
    state['photos'][photo_id] = {'name': name, 'faces': observations}
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
    # Merging retains every prior exemplar; total exemplars do not grow simply by merging.
    target['samples'] = [e for person in snapshots.values() for e in person['samples']]
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
    return {'merge_id': entry['id'], 'person_id': target_id, 'count': len(target['photo_ids']),
            'merged_count': len(person_ids)}


def _rebuild_person(state, pid, fallback):
    import copy
    person = copy.deepcopy(fallback)
    person['photo_ids'] = []
    person['samples'] = []
    limit = person.get('sample_limit', 5)
    for photo_id, photo in state['photos'].items():
        for i, face in enumerate(photo['faces']):
            if face['person_id'] != pid: continue
            if photo_id not in person['photo_ids']: person['photo_ids'].append(photo_id)
            if len(person['samples']) < limit: person['samples'].append(face['embedding'])
            quality = face.get('quality', face['bbox'][2] * face['bbox'][3])
            if not person.get('thumbnail_locked') and quality > person.get('quality', 0):
                person['quality'] = quality
                person['thumbnail'] = face.get('thumbnail') or f'{photo_id}_{i}.jpg'
    state['people'][pid] = person


def undo_merge(state, merge_id):
    """Undo only the latest merge. New photos remain on the kept person."""
    history = state.get('merge_history', [])
    if not history or history[-1]['id'] != merge_id:
        raise ValueError('請從最近一次合併開始復原；資料可能已更新，請重新整理')
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
    return {'restored_count': len(entry['people']), 'person_ids': list(entry['people'])}
