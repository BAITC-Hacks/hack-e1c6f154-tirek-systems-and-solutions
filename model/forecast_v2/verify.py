"""Independently re-read XLSX targets and verify saved evaluation, cuts and weights."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .data import load_panels, sha256
from .run import metrics, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    def read(name):
        return json.loads((args.artifacts/name).read_text())
    selection, evaluation, manifest, validation = [read(n) for n in ('selection.json', 'evaluation.json', 'manifest.json', 'validation.json')]
    expected_hash = sha256(args.artifacts/'selection.json')
    assert expected_hash == read('selection.lock.json')['selection_sha256']
    assert expected_hash == evaluation['selection_sha256'] == manifest['selection_sha256']
    for relative, digest in selection['source_hashes'].items():
        assert sha256(args.inputs/relative) == digest, relative
    panels, _ = load_panels(args.inputs, tuple(sorted({x['supplier'] for x in selection['panels'].values()})))
    checks = {'selection_sha256': expected_hash, 'source_hashes_verified': True,
              'raw_xlsx_targets_re_read': True, 'target_rows_verified': 0, 'model_files_verified': 0,
              'training_cutoffs_verified': 0, 'v1_baseline_reproduced': False}
    for panel in panels:
        for stage, suffix in [('validation', 'validation-pairs'), ('evaluation', 'evaluation')]:
            table = pd.read_csv(args.private/f'{panel.name}-{suffix}.csv', dtype={'sku': str})
            reports = validation['panels'][panel.name]['candidates'] if stage=='validation' else evaluation['panels'][panel.name]['methods']
            for origin, rows in table.groupby('origin'):
                dates = pd.date_range(pd.Timestamp(origin)+pd.Timedelta(days=1), periods=28)
                raw = panel.daily.loc[:, dates].sum(axis=1)
                assert rows.sku.is_unique
                np.testing.assert_allclose(rows.target.to_numpy(), raw.reindex(rows.sku).to_numpy(), atol=1e-10, rtol=0)
                np.testing.assert_allclose(rows.target.sum(), raw.sum(), atol=1e-8, rtol=0)
                checks['target_rows_verified'] += len(rows)
            for method, report in reports.items():
                actual = metrics(table.target, table[method])
                for key, value in report['overall'].items():
                    if value is None:
                        assert actual[key] is None
                    else:
                        np.testing.assert_allclose(actual[key], value, rtol=1e-10, atol=1e-8)
        for entry in validation['panels'][panel.name]['fits'] + evaluation['panels'][panel.name]['fits']:
            assert entry['label_end_max'] <= entry['cutoff']
            checks['training_cutoffs_verified'] += 1
        for entry in manifest['panels'][panel.name]['models'].values():
            assert sha256(args.artifacts/entry['file']) == entry['sha256']
            assert entry['fit']['label_end_max'] <= entry['fit']['cutoff']
            checks['model_files_verified'] += 1
    previous = Path(__file__).resolve().parents[1]/'artifacts'/'evaluation.json'
    if previous.exists() and 'SE__pieces' in evaluation['panels']:
        old = json.loads(previous.read_text())
        new = evaluation['panels']['SE__pieces']['methods']['v1_baseline']
        np.testing.assert_allclose(new['overall']['wape'], old['test_aggregate']['baseline']['wape'], atol=1e-12)
        for fold in old['test_folds']:
            np.testing.assert_allclose(new['by_origin'][fold['origin']]['wape'], fold['baseline']['wape'], atol=1e-12)
        checks['v1_baseline_reproduced'] = True
    core_files = ['data.py', 'features.py', 'methods.py', 'run.py']
    for name in core_files:
        assert sha256(Path(__file__).parent/name) == selection['implementation_sha256'][name], name
    checks['forecast_implementation_unchanged_since_freeze'] = True
    checks['passed'] = True
    write_json(args.artifacts/'verification.json', checks)
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == '__main__':
    main()
