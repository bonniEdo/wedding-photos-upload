"""Reproducible synthetic search-only benchmark, with exact legacy-output assertions."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np

from core import similarity
from evaluate_faces import digest, safe_destination, write_new_json
from search_index import SearchSnapshot


def scalar(state, query, threshold):
    result=[]
    for pid, photo in state['photos'].items():
        score=max((similarity(query,f['embedding']) for f in photo['faces']),default=-1)
        if score>=threshold:
            result.append({'id':pid,'name':photo['name'],'score':round(score,4),'url':f'/api/photos/{pid}/image'})
    result.sort(key=lambda r:r['score'],reverse=True)
    return result


def run(output, photo_count=3000, query_count=12, seed=42):
    target=safe_destination(output)
    if target.exists():raise ValueError('Choose a new report path; existing reports are not overwritten')
    rng=np.random.default_rng(seed)
    identities=rng.normal(size=(100,128)).astype(np.float32)
    identities/=np.linalg.norm(identities,axis=1,keepdims=True)
    vectors=identities[rng.integers(100,size=(photo_count,3))]+rng.normal(0,.05,size=(photo_count,3,128)).astype(np.float32)
    vectors/=np.linalg.norm(vectors,axis=2,keepdims=True)
    queries=identities[:query_count]+rng.normal(0,.05,size=(query_count,128)).astype(np.float32)
    queries/=np.linalg.norm(queries,axis=1,keepdims=True)
    state={'photos':{str(i):{'name':str(i),'faces':[{'embedding':v.tolist()} for v in rows]} for i,rows in enumerate(vectors)}}
    started=time.perf_counter();snapshot=SearchSnapshot.build(state);build_ms=(time.perf_counter()-started)*1000
    snapshot.search(queries[0],.3);scalar(state,queries[0],.3)
    timings={'scalar':[],'vectorized':[]};counts=[]
    for i,query in enumerate(queries):
        outputs={}
        for name in (('scalar','vectorized') if i%2 else ('vectorized','scalar')):
            start=time.perf_counter()
            outputs[name]=scalar(state,query,.3) if name=='scalar' else snapshot.search(query,.3)
            timings[name].append((time.perf_counter()-start)*1000)
        if outputs['scalar']!=outputs['vectorized']:raise AssertionError(f'Output mismatch on query {i}')
        counts.append(len(outputs['scalar']))
    summary={k:{'p50_ms':float(np.percentile(v,50)),'p95_ms':float(np.percentile(v,95))} for k,v in timings.items()}
    report={'kind':'synthetic-search-only','seed':seed,'photos':photo_count,'faces':photo_count*3,'dimension':128,
            'queries':query_count,'threshold':.3,'matches_per_query':counts,'exact_outputs_equal':True,
            'build_ms':build_ms,'timings':summary,'p50_speedup':summary['scalar']['p50_ms']/summary['vectorized']['p50_ms'],
            'matrix_bytes':sum(a.nbytes for arrays in snapshot.groups.values() for a in arrays),
            'fixture_sha256':hashlib.sha256(vectors.tobytes()+queries.tobytes()).hexdigest(),
            'source_sha256':{name:digest(Path(__file__).with_name(name)) for name in ('search_index.py','core.py','benchmark_search.py')},
            'platform':platform.platform(),'numpy':np.__version__,
            'limitations':'Cached comparison only; excludes image decode/model inference/network. Synthetic vectors do not measure recognition accuracy. Matrix bytes exclude Python objects and peak process memory.'}
    write_new_json(target,report);print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--photos',type=int,default=3000);parser.add_argument('--queries',type=int,default=12)
    args=parser.parse_args()
    if args.photos<1 or not 1<=args.queries<=100:parser.error('photos must be positive and queries between 1 and 100')
    run(args.output,args.photos,args.queries)
