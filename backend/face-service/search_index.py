"""Immutable exact-cosine search snapshots; no dependence on person grouping."""
from dataclasses import dataclass
import threading

import numpy as np


@dataclass(frozen=True)
class SearchSnapshot:
    photos: tuple
    groups: dict
    invalid_faces: int = 0

    @classmethod
    def build(cls, state):
        photos = tuple((pid, photo['name']) for pid, photo in state['photos'].items())
        groups = {}; invalid = 0
        for photo_index, photo in enumerate(state['photos'].values()):
            for face in photo['faces']:
                try:
                    vector = np.asarray(face['embedding'], dtype=np.float32)
                    if vector.ndim != 1 or not vector.size or not np.isfinite(vector).all():
                        raise ValueError('Invalid embedding')
                    groups.setdefault(len(vector), []).append((photo_index, vector))
                except (ValueError, TypeError, KeyError):
                    invalid += 1
        frozen = {}
        for dimension, rows in groups.items():
            matrix = np.stack([v for _, v in rows])
            # The original scalar cosine uses np.linalg.norm on each individual vector.
            norms = np.asarray([np.linalg.norm(v) for v in matrix], dtype=np.float32)
            indices = np.asarray([i for i, _ in rows], dtype=np.intp)
            if not np.isfinite(norms).all():
                raise ValueError('Embedding norms overflowed')
            for array in (matrix, norms, indices):
                array.setflags(write=False)
            frozen[dimension] = (matrix, norms, indices)
        return cls(photos, frozen, invalid)

    def search(self, embedding, threshold=.30):
        if not np.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('Invalid threshold')
        query = np.asarray(embedding, dtype=np.float32)
        if query.ndim != 1 or not query.size or not np.isfinite(query).all():
            raise ValueError('Invalid query embedding')
        if len(query) not in self.groups:
            return []
        matrix, norms, indices = self.groups[len(query)]
        query_norm = np.linalg.norm(query)
        if not np.isfinite(query_norm):
            raise ValueError('Query norm overflowed')
        denominators = np.maximum(norms * query_norm, np.float32(1e-12))
        scores = np.einsum('ij,j->i', matrix, query, optimize=False) / denominators
        # Recompute boundary values and per-photo contenders with the original scalar arithmetic.
        # This preserves membership and rounded scores despite SIMD rounding.
        epsilon = max(1e-6, len(query) * np.finfo(np.float32).eps * 4)
        def exact(index):
            return np.dot(query, matrix[index]) / max(float(query_norm * norms[index]), 1e-12)
        for index in np.flatnonzero(np.abs(scores-threshold) <= epsilon):
            scores[index] = exact(index)
        maxima = np.full(len(self.photos), -np.inf, dtype=np.float32)
        np.maximum.at(maxima, indices, scores)
        contender = (maxima[indices] >= threshold) & (scores >= maxima[indices] - epsilon)
        exact_maxima = np.full(len(self.photos), -np.inf, dtype=np.float32)
        for index in np.flatnonzero(contender):
            exact_maxima[indices[index]] = max(exact_maxima[indices[index]], exact(index))
        matches = []
        for index, (photo_id, name) in enumerate(self.photos):
            score = float(exact_maxima[index])
            if score >= threshold:
                matches.append({'id': photo_id, 'name': name, 'score': round(score, 4),
                                'url': f'/api/photos/{photo_id}/image'})
        # The existing API sorts rounded scores; stable ties retain photo insertion order.
        matches.sort(key=lambda row: row['score'], reverse=True)
        return matches


class SearchCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.snapshot = None

    def get(self, state):
        # Legacy indexes have no embedding revision until the first new photo.
        key = ('revision', state['embedding_revision']) if 'embedding_revision' in state else ('legacy', id(state['photos']))
        with self.lock:
            if self.key != key:
                self.snapshot = SearchSnapshot.build(state)
                self.key = key
            return self.snapshot
