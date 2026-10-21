"""Offline, verified snapshots and additive identity maintenance for the face index.

Never start this utility while Node can write DATA_DIR. The existing Python worker
lock is also checked, but cannot protect Node uploads or configuration writers.
"""
import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import time
import uuid

from face_state import face_id


class MaintenanceError(ValueError):
    """A snapshot or index is unsafe to use."""


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def audit_state(state):
    """Return aggregate diagnostics only; never expose embeddings or configuration."""
    errors, warnings = {}, {}
    counts = {'photos': 0, 'faces': 0, 'people': 0, 'aliases': 0,
              'merges': 0, 'operations': 0, 'legacy_face_ids': 0}

    def error(code):
        errors[code] = errors.get(code, 0) + 1

    def warning(code):
        warnings[code] = warnings.get(code, 0) + 1

    def result():
        return {'ok': not errors, 'counts': counts, 'errors': errors, 'warnings': warnings}

    if not isinstance(state, dict):
        error('index_not_object'); return result()
    photos, people = state.get('photos'), state.get('people')
    if not isinstance(photos, dict) or not isinstance(people, dict):
        error('invalid_photo_or_people_table'); return result()
    counts.update(photos=len(photos), people=len(people))
    expected = {pid: set() for pid in people}
    lookup = {}
    for photo_id, photo in photos.items():
        if not isinstance(photo_id, str) or not photo_id or not isinstance(photo, dict) or not isinstance(photo.get('faces'), list):
            error('invalid_photo'); continue
        for index, row in enumerate(photo['faces']):
            counts['faces'] += 1
            if not isinstance(row, dict):
                error('invalid_face'); continue
            if 'face_id' not in row:
                counts['legacy_face_ids'] += 1
            elif not isinstance(row['face_id'], str) or not row['face_id']:
                error('invalid_face_id'); continue
            key = face_id(photo_id, index, row)
            if key in lookup:
                error('duplicate_face_id')
            lookup[key] = (photo_id, index, row)
            pid = row.get('person_id')
            if not isinstance(pid, str) or pid not in people:
                error('missing_face_person')
            else:
                expected[pid].add(photo_id)
            vector = row.get('embedding')
            if not isinstance(vector, list) or not vector or any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
                error('invalid_embedding')
            bbox = row.get('bbox')
            if not isinstance(bbox, list) or len(bbox) != 4 or any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox):
                error('invalid_bbox')
    for pid, person in people.items():
        if not isinstance(pid, str) or not pid or not isinstance(person, dict):
            error('invalid_person'); continue
        photo_ids = person.get('photo_ids')
        if not isinstance(photo_ids, list) or any(not isinstance(p, str) for p in photo_ids):
            error('invalid_person_photo_ids'); continue
        if len(photo_ids) != len(set(photo_ids)):
            warning('duplicate_person_photo_ids')
        if set(photo_ids) != expected[pid]:
            error('person_photo_ids_mismatch')

    def aliases_ok(aliases, person_table):
        if not isinstance(aliases, dict):
            error('invalid_aliases'); return
        for original, target in aliases.items():
            if not isinstance(original, str) or not original or not isinstance(target, str) or not target:
                error('invalid_alias'); continue
            seen = {original}; current = target
            while current in aliases:
                if current in seen:
                    error('alias_cycle'); break
                seen.add(current); current = aliases[current]
                if not isinstance(current, str):
                    error('invalid_alias'); break
            else:
                if current not in person_table:
                    error('alias_missing_person')
            if original in person_table:
                error('alias_shadows_current_person')

    aliases = state.get('aliases', {})
    counts['aliases'] = len(aliases) if isinstance(aliases, dict) else 0
    aliases_ok(aliases, people)
    constraints = state.get('face_constraints', {})
    if not isinstance(constraints, dict):
        error('invalid_constraints')
    else:
        for entry in constraints.values():
            pair = entry.get('face_ids') if isinstance(entry, dict) else None
            if not isinstance(pair, list) or len(pair) != 2 or len(set(str(p) for p in pair)) != 2 or any(
                    not isinstance(fid, str) or fid not in lookup for fid in pair):
                error('invalid_constraint_face_ref')

    merges, operations = state.get('merge_history', []), state.get('admin_history', [])
    if not isinstance(merges, list) or not isinstance(operations, list):
        error('invalid_history'); return result()
    counts.update(merges=len(merges), operations=len(operations))
    merge_map, operation_ids = {}, set()
    for entry in merges:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id']:
            error('invalid_merge_entry'); continue
        if entry['id'] in merge_map:
            error('duplicate_merge_id')
        merge_map[entry['id']] = entry
        snapshots = entry.get('people')
        if (not isinstance(snapshots, dict) or not isinstance(entry.get('target_id'), str) or
                entry.get('target_id') not in snapshots or
                any(not isinstance(pid, str) or not isinstance(person, dict) for pid, person in snapshots.items())):
            error('invalid_merge_snapshot')
        changes = entry.get('changes')
        if not isinstance(changes, list):
            error('invalid_merge_changes'); continue
        for change in changes:
            if not isinstance(change, list) or len(change) != 3:
                error('invalid_merge_position'); continue
            photo_id, position, old_id = change
            photo = photos.get(photo_id) if isinstance(photo_id, str) else None
            if (not isinstance(position, int) or isinstance(position, bool) or position < 0 or
                    not isinstance(photo, dict) or not isinstance(photo.get('faces'), list) or position >= len(photo['faces'])):
                error('missing_merge_position')
            if not isinstance(snapshots, dict) or not isinstance(old_id, str) or old_id not in snapshots:
                error('missing_merge_old_person')
    merge_pointers = []
    allowed = {'merge', 'reassign', 'approve', 'different', 'skip', 'revoke', 'backfill_add'}
    for entry in operations:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id']:
            error('invalid_operation'); continue
        if entry['id'] in operation_ids:
            error('duplicate_operation_id')
        operation_ids.add(entry['id'])
        kind = entry.get('kind')
        if not isinstance(kind, str) or kind not in allowed:
            error('unknown_operation_kind')
            continue
        if kind == 'merge':
            merge_pointers.append(entry['id'])
            if entry['id'] not in merge_map:
                error('missing_operation_merge')
        elif entry['id'] in merge_map:
            error('operation_merge_id_collision')
        if kind in ('reassign', 'approve'):
            before = entry.get('faces_before')
            if not isinstance(before, dict) or not before:
                error('invalid_correction_snapshot')
            else:
                for fid, row in before.items():
                    if fid not in lookup or not isinstance(row, dict):
                        error('missing_correction_face')
                    elif row.get('face_id', fid) != fid:
                        error('correction_identity_mismatch')
            before_people, affected = entry.get('people_before'), entry.get('affected_ids')
            if (not isinstance(before_people, dict) or not isinstance(affected, list) or
                    any(not isinstance(pid, str) for pid in affected) or
                    (isinstance(before_people, dict) and any(not isinstance(pid, str) or not isinstance(person, dict)
                                                           for pid, person in before_people.items()))):
                error('invalid_correction_people')
            elif not set(before_people) <= set(affected):
                error('correction_people_mismatch')
        elif kind in ('different', 'skip', 'revoke'):
            if not isinstance(entry.get('key'), str):
                error('invalid_operation_key')
            if kind == 'revoke':
                previous = entry.get('previous')
                pair = previous.get('face_ids') if isinstance(previous, dict) else None
                if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(fid, str) or fid not in lookup for fid in pair):
                    error('invalid_revoked_constraint')
        elif kind == 'backfill_add' and not isinstance(entry.get('photo_id'), str):
            error('invalid_backfill_photo_ref')
    if merge_pointers and merge_pointers != [entry.get('id') for entry in merges[-len(merge_pointers):] if isinstance(entry, dict)]:
        error('operation_merge_order_mismatch')

    # Replay reference changes, without rebuilding embeddings, to prove historical
    # positional merge references still reach their original observations.
    if not errors:
        replay = copy.deepcopy(state)
        remaining = list(merges)

        def restore_merge(entry):
            for photo_id, position, old_id in entry['changes']:
                row = replay['photos'][photo_id]['faces'][position]
                if row.get('person_id') != entry['target_id']:
                    error('merge_undo_person_mismatch')
                row['person_id'] = old_id
            replay['people'].update(copy.deepcopy(entry['people']))
            aliases_ok(entry.get('previous_aliases', {}), replay['people'])

        for entry in reversed(operations):
            kind = entry['kind']
            if kind == 'merge':
                if not remaining or remaining[-1]['id'] != entry['id']:
                    error('operation_merge_order_mismatch'); break
                restore_merge(remaining.pop())
            elif kind in ('reassign', 'approve'):
                for fid, original in entry['faces_before'].items():
                    photo_id, position, _ = lookup[fid]
                    replay['photos'][photo_id]['faces'][position] = copy.deepcopy(original)
                replay['people'].update(copy.deepcopy(entry['people_before']))
            elif kind == 'backfill_add':
                photo = replay['photos'].get(entry.get('photo_id'))
                count = entry.get('before_count')
                added = entry.get('face_ids')
                person_ids = entry.get('person_ids')
                if (not isinstance(photo, dict) or not isinstance(count, int) or isinstance(count, bool) or count < 0 or
                        not isinstance(added, list) or not added or count > len(photo['faces']) or
                        [face_id(entry['photo_id'], i, row) for i, row in enumerate(photo['faces'][count:], count)] != added):
                    error('backfill_suffix_mismatch')
                elif (not isinstance(person_ids, list) or not person_ids or
                      any(not isinstance(pid, str) or pid not in replay['people'] for pid in person_ids) or
                      any(row.get('person_id') not in person_ids for row in photo['faces'][count:])):
                    error('backfill_person_ref_mismatch')
                else:
                    del photo['faces'][count:]
        for entry in reversed(remaining):
            restore_merge(entry)
    return result()


def audit_data(data):
    path = Path(data) / 'face_index.json'
    if not path.exists():
        report = audit_state({'photos': {}, 'people': {}})
        report['warnings']['index_missing'] = 1
        return report
    if path.is_symlink() or not path.is_file():
        raise MaintenanceError('Index must be a regular file')
    try:
        with path.open(encoding='utf8') as stream:
            return audit_state(json.load(stream))
    except (ValueError, TypeError) as exc:
        raise MaintenanceError('Index is not valid JSON') from exc


@contextlib.contextmanager
def offline_lock(data):
    """Do not create or modify the formal lock during read-only maintenance."""
    path = Path(data) / 'face-worker.lock'
    if not path.exists() and not path.is_symlink():
        yield; return
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise MaintenanceError('Worker lock must be a regular file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MaintenanceError('Face worker is active; stop it before maintenance') from exc
        yield
    finally:
        os.close(descriptor)


def _data_path(value):
    path = Path(value).absolute()
    if path.is_symlink() or not path.is_dir():
        raise MaintenanceError('DATA must be an existing directory, not a symlink')
    return path.resolve()


def _new_output(value, forbidden):
    raw = Path(value).absolute()
    if raw.exists() or raw.is_symlink():
        raise MaintenanceError('Output must be a new directory')
    if not raw.parent.is_dir():
        raise MaintenanceError('Output parent directory must exist')
    path = raw.resolve()
    for root in forbidden:
        root = Path(root).resolve()
        if path == root or path.is_relative_to(root) or root.is_relative_to(path):
            raise MaintenanceError('Output cannot overlap a source or backup directory')
    return path


def _metadata(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode)]


def _file_record(path, archive_path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise MaintenanceError('Source contains a symlink or special file')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        if _metadata(os.fstat(stream.fileno())) != _metadata(before):
            raise MaintenanceError('Source changed during snapshot')
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
        if _metadata(os.fstat(stream.fileno())) != _metadata(before) or _metadata(path.lstat()) != _metadata(before):
            raise MaintenanceError('Source changed during snapshot')
    return {'path': archive_path, 'source': str(path), 'size': before.st_size,
            'sha256': value.hexdigest(), 'source_metadata': _metadata(before)}


def inventory(data, configs=()):
    records = []
    for root, directories, files in os.walk(data, followlinks=False):
        root = Path(root)
        for name in directories:
            if not stat.S_ISDIR((root / name).lstat().st_mode):
                raise MaintenanceError('Source contains a symlink or special directory')
        for name in files:
            path = root / name
            records.append(_file_record(path, 'data/' + path.relative_to(data).as_posix()))
    for index, value in enumerate(configs):
        path = Path(value).absolute()
        records.append(_file_record(path, f'config/{index}/{path.name}'))
    return sorted(records, key=lambda record: record['path'])


def directory_inventory(data):
    result = []
    for root, directories, _ in os.walk(data, followlinks=False):
        path = Path(root)
        for name in directories:
            if not stat.S_ISDIR((path / name).lstat().st_mode):
                raise MaintenanceError('Source contains a symlink or special directory')
        info = path.lstat()
        result.append({'path': 'data' if path == data else 'data/' + path.relative_to(data).as_posix(),
                       'source_metadata': [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode)]})
    return sorted(result, key=lambda row: row['path'])


def _safe_member(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise MaintenanceError('Unsafe archive path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')) or path.parts[0] not in ('data', 'config'):
        raise MaintenanceError('Unsafe archive path')
    return path


def _write_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf8') as stream:
        # Index dictionaries retain their insertion order: public album ordering
        # and equal-score selfie ties depend on the original photo sequence.
        json.dump(value, stream, ensure_ascii=False)
        stream.flush(); os.fsync(stream.fileno())


def verify_backup(backup):
    backup = Path(backup)
    if backup.is_symlink() or not backup.is_dir():
        raise MaintenanceError('Backup must be a directory, not a symlink')
    manifest_path, archive = backup / 'manifest.json', backup / 'snapshot.tar.gz'
    if any(path.is_symlink() or not path.is_file() for path in (manifest_path, archive)):
        raise MaintenanceError('Backup files must be regular files')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        if (not isinstance(manifest, dict) or manifest.get('format') != 'wedding-face-backup-v1' or
                not isinstance(manifest.get('files'), list) or not isinstance(manifest.get('data_source'), str) or
                not Path(manifest['data_source']).is_absolute()):
            raise MaintenanceError('Unsupported backup manifest')
        if digest(archive) != manifest['archive_sha256']:
            raise MaintenanceError('Archive checksum mismatch')
        expected = {}
        for row in manifest['files']:
            if not isinstance(row, dict):
                raise MaintenanceError('Invalid file manifest')
            if len(_safe_member(row['path']).parts) < 2:
                raise MaintenanceError('File path must be below a data or config directory')
            if (row['path'] in expected or not isinstance(row.get('size'), int) or isinstance(row['size'], bool) or
                    row['size'] < 0 or not isinstance(row.get('sha256'), str) or len(row['sha256']) != 64 or
                    any(c not in '0123456789abcdef' for c in row['sha256'])):
                raise MaintenanceError('Invalid file manifest')
            expected[row['path']] = row
        directories = manifest.get('directories', [])
        if not isinstance(directories, list) or len({str(row.get('path')) for row in directories if isinstance(row, dict)}) != len(directories):
            raise MaintenanceError('Invalid directory manifest')
        for row in directories:
            path = str(_safe_member(row['path']))
            if path in expected:
                raise MaintenanceError('File and directory paths overlap')
        for path in [*expected, *(row['path'] for row in directories)]:
            if any(str(parent) in expected for parent in PurePosixPath(path).parents if str(parent) != '.'):
                raise MaintenanceError('File path overlaps another archive path')
        seen, index_bytes = set(), None
        with tarfile.open(archive, 'r:gz') as stream:
            for member in stream:
                _safe_member(member.name)
                if member.name in seen or member.name not in expected:
                    raise MaintenanceError('Archive has unsafe, duplicate, or unexpected members')
                if not member.isfile():
                    raise MaintenanceError('Archive members must be regular files')
                row = expected[member.name]
                if member.size != row['size']:
                    raise MaintenanceError('Archive file size mismatch')
                source = stream.extractfile(member)
                value = hashlib.sha256()
                index_blocks = [] if member.name == 'data/face_index.json' else None
                with source:
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        value.update(block)
                        if index_blocks is not None:
                            index_blocks.append(block)
                if value.hexdigest() != row['sha256']:
                    raise MaintenanceError('Archive file checksum mismatch')
                if index_blocks is not None:
                    index_bytes = b''.join(index_blocks)
                seen.add(member.name)
        if seen != set(expected):
            raise MaintenanceError('Archive is missing files')
        report = audit_state(json.loads(index_bytes)) if index_bytes is not None else audit_state({'photos': {}, 'people': {}})
        if index_bytes is None:
            report['warnings']['index_missing'] = 1
        if not report['ok']:
            raise MaintenanceError('Archived face index failed structural audit')
        if manifest.get('index_sha256') != (expected.get('data/face_index.json') or {}).get('sha256'):
            raise MaintenanceError('Manifest index checksum mismatch')
        return {'ok': True, 'file_count': len(expected), 'bytes': sum(row['size'] for row in expected.values()),
                'archive_sha256': manifest['archive_sha256'], 'audit': report, 'manifest': manifest}
    except (OSError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, tarfile.TarError, EOFError) as exc:
        raise MaintenanceError('Invalid or unreadable backup') from exc


def create_backup(data, output, configs=()):
    data = _data_path(data)
    output = _new_output(output, [data])
    configs = [Path(path).absolute() for path in configs]
    with offline_lock(data):
        report = audit_data(data)
        if not report['ok']:
            raise MaintenanceError('Source face index failed structural audit')
        before = inventory(data, configs)
        directories_before = directory_inventory(data)
        output.mkdir(mode=0o700)
        try:
            archive = output / 'snapshot.tar.gz'
            descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as raw, tarfile.open(fileobj=raw, mode='w:gz') as target:
                for row in before:
                    source_path = Path(row['source'])
                    descriptor = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(descriptor, 'rb') as source:
                        if _metadata(os.fstat(source.fileno())) != row['source_metadata']:
                            raise MaintenanceError('Source changed during snapshot')
                        member = tarfile.TarInfo(row['path'])
                        member.size = row['size']; member.mode = 0o600
                        target.addfile(member, source)
                # tarfile closes after this scope; fsync below includes gzip trailer.
            with archive.open('rb') as stream:
                os.fsync(stream.fileno())
            after = inventory(data, configs)
            if before != after or directories_before != directory_inventory(data):
                raise MaintenanceError('Source changed during snapshot')
            index = next((row for row in before if row['path'] == 'data/face_index.json'), None)
            manifest = {'format': 'wedding-face-backup-v1', 'created_at': time.time(), 'data_source': str(data),
                        'files': before, 'directories': directories_before, 'archive_sha256': digest(archive),
                        'index_sha256': index['sha256'] if index else None}
            _write_json(output / 'manifest.json', manifest)
            checked = verify_backup(output)
            checked.pop('manifest')
            return {**checked, 'backup': str(output)}
        except BaseException:
            shutil.rmtree(output)
            raise


def restore_test(backup, output):
    checked = verify_backup(backup)
    destination = _new_output(output, [Path(backup), Path(checked['manifest']['data_source'])])
    destination.mkdir(mode=0o700)
    try:
        expected = {row['path']: row for row in checked['manifest']['files']}
        seen = set()
        for row in checked['manifest'].get('directories', []):
            path = destination.joinpath(*_safe_member(row['path']).parts)
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
            path.chmod(0o700)
        with tarfile.open(Path(backup) / 'snapshot.tar.gz', 'r:gz') as archive:
            for member in archive:
                relative = _safe_member(member.name)
                if (not member.isfile() or member.name not in expected or member.name in seen or
                        member.size != expected[member.name]['size']):
                    raise MaintenanceError('Restored archive members do not match the verified manifest')
                seen.add(member.name)
                path = destination.joinpath(*relative.parts)
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                # mkdir(parents=True) can apply default mode to intermediate directories.
                for parent in path.parents:
                    if parent == destination:
                        break
                    parent.chmod(0o700)
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, 'wb') as target, archive.extractfile(member) as source:
                    shutil.copyfileobj(source, target, 1024 * 1024)
                    target.flush(); os.fsync(target.fileno())
        if seen != set(expected):
            raise MaintenanceError('Restored archive is missing files')
        manifest = checked['manifest']
        for row in manifest['files']:
            path = destination.joinpath(*PurePosixPath(row['path']).parts)
            if path.stat().st_size != row['size'] or digest(path) != row['sha256']:
                raise MaintenanceError('Restored file checksum mismatch')
        report = audit_data(destination / 'data')
        if not report['ok']:
            raise MaintenanceError('Restored index failed structural audit')
        return {'ok': True, 'output': str(destination), 'file_count': checked['file_count'], 'audit': report}
    except BaseException:
        shutil.rmtree(destination)
        raise


def migrate(data, backup):
    """Add stable identity metadata; never regroup or edit a historical snapshot."""
    data = _data_path(data)
    with offline_lock(data):
        checked = verify_backup(backup)
        manifest = checked['manifest']
        index_path = data / 'face_index.json'
        if manifest.get('data_source') != str(data) or not manifest.get('index_sha256'):
            raise MaintenanceError('Backup does not match the current source index')
        report = audit_data(data)
        if not report['ok']:
            raise MaintenanceError('Source index failed structural audit')
        original = index_path.read_bytes()
        state = json.loads(original)
        receipt = state.get('face_backfill_maintenance', {})
        same_receipt = (isinstance(receipt, dict) and receipt.get('version') == 1 and
                        receipt.get('backup_dir') == str(Path(backup).resolve()) and
                        receipt.get('archive_sha256') == manifest['archive_sha256'] and
                        receipt.get('original_index_sha256') == manifest['index_sha256'])
        if hashlib.sha256(original).hexdigest() != manifest['index_sha256'] and not same_receipt:
            raise MaintenanceError('Backup does not match the current source index')
        added = 0
        for photo_id, photo in state['photos'].items():
            for index, row in enumerate(photo['faces']):
                if 'face_id' not in row:
                    row.setdefault('face_id', face_id(photo_id, index, row)); added += 1
        changed = bool(added) or 'face_identity_version' not in state or 'embedding_revision' not in state or not same_receipt
        if changed and hashlib.sha256(original).hexdigest() != manifest['index_sha256']:
            raise MaintenanceError('Changed migration needs a new backup of the current index')
        state.setdefault('face_identity_version', 1)
        state.setdefault('embedding_revision', 0)
        if changed:
            state['face_backfill_maintenance'] = {'version': 1, 'backup_dir': str(Path(backup).resolve()),
                                                 'archive_sha256': manifest['archive_sha256'],
                                                 'original_index_sha256': manifest['index_sha256'], 'created_at': time.time()}
            state['revision'] = state.get('revision', 0) + 1
        if not audit_state(state)['ok']:
            raise MaintenanceError('Migrated index failed structural audit')
        if changed:
            temporary = index_path.with_name(index_path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                _write_json(temporary, state)
                if index_path.read_bytes() != original:
                    raise MaintenanceError('Source index changed before migration commit')
                os.replace(temporary, index_path)
                directory = os.open(data, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)
        return {'ok': True, 'changed': changed, 'added_face_ids': added, 'audit': audit_state(state)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup', help='Create and verify a new full snapshot')
    backup.add_argument('--data', required=True); backup.add_argument('--output', required=True)
    backup.add_argument('--config', action='append', default=[])
    verify = commands.add_parser('verify', help='Verify all archive hashes and index structure')
    verify.add_argument('--backup', required=True)
    restore = commands.add_parser('restore-test', help='Restore to a NEW directory and audit without overwriting live data')
    restore.add_argument('--backup', required=True); restore.add_argument('--output', required=True)
    audit = commands.add_parser('audit', help='Read-only structural and history-reference audit')
    audit.add_argument('--data', required=True)
    migration = commands.add_parser('migrate', help='Add stable face IDs using a matching verified backup')
    migration.add_argument('--data', required=True); migration.add_argument('--backup', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'backup':
            result = create_backup(args.data, args.output, args.config)
        elif args.command == 'verify':
            result = verify_backup(args.backup); result.pop('manifest')
        elif args.command == 'restore-test':
            result = restore_test(args.backup, args.output)
        elif args.command == 'migrate':
            result = migrate(args.data, args.backup)
        else:
            data = _data_path(args.data)
            with offline_lock(data):
                result = audit_data(data)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result['ok'] else 1
    except (MaintenanceError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
