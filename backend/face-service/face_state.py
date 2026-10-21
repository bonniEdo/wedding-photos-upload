"""Compatible index metadata. Legacy observations/history stay in their original order."""
import uuid


def face_id(photo_id, index, face):
    return face.get('face_id') or uuid.uuid5(uuid.NAMESPACE_URL, f'wedding-face:{photo_id}:legacy:{index}').hex


def observations(state):
    for photo_id, photo in state['photos'].items():
        for index, face in enumerate(photo['faces']):
            yield face_id(photo_id, index, face), photo_id, index, face


def face_map(state):
    result = {}
    for key, photo_id, index, face in observations(state):
        if key in result:
            raise ValueError('人臉識別碼重複，請先檢查索引')
        result[key] = (photo_id, index, face)
    return result


def revision(state):
    return state.get('revision', 0)


def advance(state, embeddings=False):
    state['revision'] = revision(state) + 1
    if embeddings:
        state['embedding_revision'] = state.get('embedding_revision', 0) + 1


def latest_operation(state):
    if state.get('admin_history'):
        return state['admin_history'][-1]
    if state.get('merge_history'):
        entry = state['merge_history'][-1]
        return {'id': entry['id'], 'kind': 'merge', 'created_at': entry['created_at']}
    return None


def require_latest(state, operation_id, kind):
    latest = latest_operation(state)
    if not latest or latest['id'] != operation_id or latest['kind'] != kind:
        raise ValueError('請先復原較新的管理操作，再復原這次操作')


def active_constraints(state, lookup=None):
    lookup = lookup if lookup is not None else face_map(state)
    for key, entry in state.get('face_constraints', {}).items():
        pair = entry['face_ids']
        if all(fid in lookup for fid in pair):
            first, second = (lookup[fid][2]['person_id'] for fid in pair)
            if first != second:
                yield key, entry, (first, second)


def assert_merge_allowed(state, person_ids):
    if not state.get('face_constraints'):
        return
    selected = set(person_ids)
    if any(set(pair) <= selected for _, _, pair in active_constraints(state)):
        raise ValueError('這些人物已有不同人紀錄，請先撤銷排除後再合併')
