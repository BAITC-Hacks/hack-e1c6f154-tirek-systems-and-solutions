"""Validation-only convex pairs: refine the selection before opening evaluation."""
import argparse
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import pandas as pd
from .data import sha256
from .methods import MODEL_SPECS
from .run import summarize, write_json, log


def refine(output, private):
    output, private = Path(output), Path(private)
    if (output/'evaluation.json').exists():
        raise ValueError('Refinement is forbidden once this run has an evaluation artifact; use a new output directory')
    selection_path = output/'selection.json'
    parent_hash = sha256(selection_path)
    lock = json.loads((output/'selection.lock.json').read_text())
    if lock['selection_sha256'] != parent_hash:
        raise ValueError('Selection lock mismatch')
    frozen = json.loads(selection_path.read_text())
    report = json.loads((output/'validation.json').read_text())
    bases = sorted(MODEL_SPECS) + ['mean364']
    frozen['convex_pair_search'] = {'base_methods': bases, 'weights': [.25, .5, .75],
        'selection_stage': 'validation only; before evaluation', 'parent_selection_sha256': parent_hash,
        'private_validation_sha256': {}}
    for panel, policy in frozen['panels'].items():
        path = private/f'{panel}-validation.csv'
        frozen['convex_pair_search']['private_validation_sha256'][panel] = sha256(path)
        table = pd.read_csv(path, dtype={'sku': str})
        table = table.drop(columns=['static_group_leave_one_window_out'], errors='ignore')
        diagnostic = report['panels'][panel]['static_group_diagnostic']
        diagnostic.pop('leave_one_window_out', None)
        diagnostic['note'] = 'Group mapping fitted on validation; apparent score is not prospective accuracy.'

        extra = {}
        for left, right in itertools.combinations(bases, 2):
            for weight in (.25, .5, .75):
                name = f'mix|{left}|{right}|{weight}'
                extra[name] = weight*table[left] + (1-weight)*table[right]
        table = pd.concat([table, pd.DataFrame(extra)], axis=1)
        candidates = report['panels'][panel]['candidates']
        candidates.update({name: summarize(table, name) for name in extra})
        winner = min(candidates, key=lambda n: candidates[n]['overall']['absolute_error'])
        policy['selected'] = winner
        report['panels'][panel]['selected'] = winner
        log(refined_validation_selection=winner, panel=panel, metrics=candidates[winner]['overall'])
        table.to_csv(private/f'{panel}-validation-pairs.csv', index=False)
    frozen['implementation_sha256'] = {p.name: sha256(p) for p in Path(__file__).parent.glob('*.py') if not p.name.startswith('test_')}
    frozen['frozen_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(selection_path, frozen)
    write_json(output/'selection.lock.json', {'selection_sha256': sha256(selection_path)})
    write_json(output/'validation.json', report)
    return frozen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    args = parser.parse_args()
    refine(args.output, args.private)


if __name__ == '__main__':
    main()
