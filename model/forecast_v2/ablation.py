"""Fixed-candidate validation ablation: remove 2024 context, never change selected policy."""
import argparse
import json
import pickle
from pathlib import Path
import numpy as np
import pandas as pd
from .features import features_at
from .run import VALIDATION, forecast_methods, history, score_rows, summarize, write_json, log


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.cache.open('rb') as stream:
        state = pickle.load(stream)
    frozen = json.loads((args.output/'selection.json').read_text())
    report = {'status': 'validation-only fixed-candidate diagnostic; does not alter frozen selection', 'panels': {}}
    original_report = json.loads((args.output/'validation.json').read_text())
    for panel in state['panels']:
        hist, simple_hist = history(panel, use2024=False)
        policy = frozen['panels'][panel.name]
        names = sorted({policy['selected'], policy['best_ml'], 'monthlyblend_0.0', 'v1_baseline'})
        tables = []
        for origin in VALIDATION:
            current = features_at(panel, origin, True, history2024=False)
            pred, _ = forecast_methods(panel, current, names, hist, simple_hist, frozen['iterations'], frozen['threads'])
            tables.append(score_rows(panel, current, pred))
        table = pd.concat(tables, ignore_index=True)
        report['panels'][panel.name] = {
            'monthly2024_matched_skus': int(panel.daily.index.isin(panel.monthly_2024.index).sum()),
            'monthly2024_missing_skus': int((~panel.daily.index.isin(panel.monthly_2024.index)).sum()),
            'methods': {name: {'without_2024': summarize(table, name),
                        'with_2024': original_report['panels'][panel.name]['candidates'][name]}
                        for name in names}}
        log(ablation=panel.name, selected=policy['selected'], without2024=summarize(table, policy['selected'])['overall'])
    write_json(args.output/'ablation.json', report)


if __name__ == '__main__':
    main()
