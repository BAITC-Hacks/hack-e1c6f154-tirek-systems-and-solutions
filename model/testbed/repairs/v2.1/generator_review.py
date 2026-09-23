"""Read-only development-seed audit; prints aggregates, never source records."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import types

parser = argparse.ArgumentParser()
parser.add_argument('--worktree', default=os.getcwd())
parser.add_argument('--base', default='771aa8e')
args = parser.parse_args()
os.chdir(args.worktree)
sys.path.insert(0, str(Path.cwd()))
sys.dont_write_bytecode = True
from model.testbed.generator import generate, PROTOCOL
from model.testbed.baseline import forecast
from model.testbed.adapters import validate_request

def old_module(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(Path(path).resolve())
    sys.modules[name] = module
    source = subprocess.check_output(['git', 'show', args.base + ':' + path], text=True)
    exec(compile(source, path + '@' + args.base, 'exec'), module.__dict__)
    return module

old = old_module('audit_base_generator', 'model/testbed/generator.py')
old.PROTOCOL = json.loads(subprocess.check_output(['git', 'show', args.base + ':model/testbed/protocol.json'], text=True))
old_baseline = old_module('audit_base_baseline', 'model/testbed/baseline.py')
counts = Counter()
assert PROTOCOL['development_seeds'] == [101, 202, 303]
for seed in (101, 202, 303):
    for origin in PROTOCOL['origins']:
        for scenario in PROTOCOL['scenarios']:
            previous = old.generate(scenario, seed, origin)
            current = generate(scenario, seed, origin)
            assert current.truth == previous.truth, (seed, origin, scenario, 'truth')
            assert current.projects == previous.projects, (seed, origin, scenario, 'projects')
            counts['truth_and_projects_unchanged_cases'] += 1
            before, after = deepcopy(previous.observed), deepcopy(current.observed)
            for request in (before, after):
                for item in request['items']:
                    item['events'] = sorted((e['date'], e['client_id'], e['quantity']) for e in item['events'])
            assert before == after, (seed, origin, scenario, 'non_ID_observations')
            counts['observations_equal_except_event_ID_and_order'] += 1
            validate_request(current.observed)
            counts['new_requests_validated'] += 1
            expected = forecast(current.observed)
            assert expected == old_baseline.forecast(previous.observed), (seed, origin, scenario, 'baseline_output')
            counts['baseline_equal_old_generated_case'] += 1
            shuffled = deepcopy(current.observed)
            rng = random.Random(seed)
            for item in shuffled['items']:
                rng.shuffle(item['history'])
                rng.shuffle(item['events'])
                for n, event in enumerate(item['events']):
                    event['event_id'] = f'fresh-opaque-{n}'
            validate_request(shuffled)
            assert expected == forecast(shuffled), (seed, origin, scenario, 'permutation')
            counts['opaque_ID_history_and_event_permutation_invariance'] += 1
            ids = [e['event_id'] for item in current.observed['items'] for e in item['events']]
            assert len(ids) == len(set(ids))
            assert all(re.fullmatch('evt_[0-9a-f]{32}', identifier) for identifier in ids)
            counts['opaque_unique_events_within_cases'] += len(ids)
            for item in current.observed['items']:
                if item['events']:
                    counts['event_lists_with_data'] += 1
                    counts['event_lists_not_in_chronological_insertion_order'] += item['events'] != sorted(item['events'], key=lambda e: e['date'])
            original = json.dumps(current.observed, sort_keys=True)
            for truth in current.truth.values():
                truth['regular_realized'][-28:] = [999999] * 28
                truth['regular_expectation'][-28:] = [999999] * 28
            current.projects.clear()
            assert original == json.dumps(current.observed, sort_keys=True)
            assert expected == forecast(current.observed)
            counts['private_future_mutation_does_not_affect_public_or_baseline'] += 1

paths = ('model/testbed/generator.py', 'model/testbed/baseline.py', 'model/testbed/adapters.py', 'model/testbed/protocol.json')
print(json.dumps({
    'audit': 'generator repair comparison; development only; no forecast tuning',
    'base_commit': subprocess.check_output(['git', 'rev-parse', args.base], text=True).strip(),
    'development_seeds': [101, 202, 303],
    'origins': PROTOCOL['origins'],
    'scenarios': PROTOCOL['scenarios'],
    'final_seeds_evaluated': [],
    'counts': dict(counts),
    'source_sha256': {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths},
    'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'critical_findings': [],
    'limitations': ['Trusted local adapter is not an OS sandbox.', 'Fixed SKU demand scales remain a synthetic-generalization limitation.', 'No claim of adversarial secrecy of the public generator or its fixed seeds.']
}, ensure_ascii=False, sort_keys=True, indent=2))
