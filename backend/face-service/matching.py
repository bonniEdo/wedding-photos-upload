"""Conservative whole-photo matching and bounded, quality-aware representatives."""
from dataclasses import dataclass
import copy
import hashlib
import json
import math

import numpy as np


@dataclass(frozen=True)
class MatchConfig:
    # Initial conservative EXPERIMENT settings; calibrate on labelled photos before enabling.
    threshold: float = .60
    margin: float = .08
    single_threshold: float = .65
    sample_threshold: float = .70
    sample_margin: float = .10
    support_floor: float = .35
    representative_limit: int = 5

    def __post_init__(self):
        scores = (self.threshold, self.margin, self.single_threshold, self.sample_threshold,
                  self.sample_margin, self.support_floor)
        if (any(not math.isfinite(v) or not 0 <= v <= 1 for v in scores)
                or self.representative_limit < 1 or self.single_threshold < self.threshold
                or self.sample_threshold < self.threshold or self.sample_margin < self.margin):
            raise ValueError('Invalid enhanced matching thresholds')


def cosine(a, b):
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    if (a.ndim != 1 or a.shape != b.shape or not np.isfinite(a).all()
            or not np.isfinite(b).all() or np.linalg.norm(a) < 1e-12 or np.linalg.norm(b) < 1e-12):
        return -1.
    return float(np.clip(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1))


def eligible(face):
    # Legacy observations remain readable; absence of measured quality is not evidence of good quality.
    return face.get('quality_metrics', {}).get('sample_eligible', False) is True


def sample_record(face, source_group):
    metrics = face.get('quality_metrics', {})
    sharpness = min(float(metrics.get('sharpness', 0)), 1000.)
    size = min(metrics.get('original_face_size', [0, 0]))
    rank = float(metrics.get('detector_score', 0)) * min(size, 512) + sharpness * .1
    return {'face_id': face['face_id'], 'embedding': list(face['embedding']),
            'source_group': source_group, 'rank': rank}


def representatives(person, person_id):
    if 'representatives' in person:
        return copy.deepcopy(person['representatives'])
    # Never infer that multiple samples of an old manual merge belong to one appearance mode.
    return [{'face_id': f'legacy:{person_id}:{i}', 'embedding': list(vector), 'rank': 0.,
             'source_group': f'legacy:{person_id}:{i}' if person.get('manually_merged') else person_id}
            for i, vector in enumerate(person.get('samples', []))]


def select_representatives(records, limit=5):
    """Keep each manually preserved source group, picking quality then angular diversity."""
    groups = {}
    for record in records:
        groups.setdefault(record['source_group'], {})[record['face_id']] = record
    selected = []
    for group_id in sorted(groups):
        pool = sorted(groups[group_id].values(), key=lambda r: (-r['rank'], r['face_id']))
        chosen = pool[:1]; pool = pool[1:]
        while pool and len(chosen) < limit:
            # Nearly identical samples add no new support, even when they arrived first.
            diverse = [r for r in pool if max(cosine(r['embedding'], c['embedding']) for c in chosen) < .995]
            if not diverse:
                break
            candidate = max(diverse, key=lambda r: (min(1-cosine(r['embedding'], c['embedding']) for c in chosen)
                                                    + min(r['rank'], 1000) / 10000, r['face_id']))
            chosen.append(candidate); pool.remove(candidate)
        selected.extend(chosen)
    return copy.deepcopy(selected)


def score_person(face, person, person_id, config):
    groups = {}
    for record in representatives(person, person_id):
        groups.setdefault(record['source_group'], []).append(cosine(face['embedding'], record['embedding']))
    candidates = []
    for source_group, scores in groups.items():
        scores.sort(reverse=True)
        supporting = [s for s in scores if s >= config.support_floor]
        count = min(2, len(supporting))
        support = sum(supporting[:2]) / count if count else -1.
        best = scores[0]
        score = .75 * best + .25 * support if count > 1 else best
        candidates.append({'person_id': person_id, 'source_group': source_group,
                           'score': score, 'best': best, 'support_count': count})
    return max(candidates, key=lambda r: (r['score'], r['source_group'])) if candidates else None


def assign_photo(people, faces, config=None):
    """Pure function: all candidates see the SAME pre-photo people and sample sets."""
    config = config or MatchConfig()
    decisions = []
    for face in faces:
        candidates = [score_person(face, person, pid, config) for pid, person in people.items()
                      if not person.get('provisional')]
        candidates = sorted((r for r in candidates if r is not None),
                            key=lambda r: (-r['score'], r['person_id']))
        top = candidates[0] if candidates else None
        second = candidates[1]['score'] if len(candidates) > 1 else -1.
        gap = top['score'] - second if top else 0.
        decision = {'person_id': None, 'reason': 'no_candidate', 'sample_eligible': False,
                    'candidates': candidates[:3], 'gap': gap, 'strategy': 'whole-photo-v1'}
        if not eligible(face):
            decision['reason'] = 'low_quality'
        elif top:
            floor = config.single_threshold if top['support_count'] < 2 else config.threshold
            if top['score'] < floor:
                decision['reason'] = 'below_threshold'
            elif gap < config.margin:
                decision['reason'] = 'ambiguous'
            else:
                decision.update(person_id=top['person_id'], source_group=top['source_group'], reason='matched',
                                sample_eligible=top['score'] >= config.sample_threshold and gap >= config.sample_margin)
        decisions.append(decision)
    contenders = {}
    for i, decision in enumerate(decisions):
        if decision['person_id'] is not None:
            contenders.setdefault(decision['person_id'], []).append(i)
    for indices in contenders.values():
        if len(indices) < 2:
            continue
        ranked = sorted(indices, key=lambda i: -decisions[i]['candidates'][0]['score'])
        first, second = ranked[:2]
        decisive = (decisions[first]['candidates'][0]['score'] - decisions[second]['candidates'][0]['score']
                    >= config.margin)
        for i in ranked[1:] if decisive else ranked:
            decisions[i].update(person_id=None, reason='same_photo_conflict', sample_eligible=False)
    return decisions


def policy_fingerprint(config):
    return hashlib.sha256(json.dumps(config.__dict__, sort_keys=True).encode()).hexdigest()[:16]
