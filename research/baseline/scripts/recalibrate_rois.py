import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from pcb_inspection.core import load_config, prepare_frame, process_reference, reference_path
from pcb_inspection.golden import public_config
from pcb_inspection.io import read_image

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def separation(present, missing):
    p0, p1, m0, m1 = (min(present), max(present), min(missing), max(missing))
    op, threshold, gap = (None, None, None)
    if m0 > p1:
        op, threshold, gap = ('>', (m0 + p1) / 2, m0 - p1)
    elif m1 < p0:
        op, threshold, gap = ('<', (m1 + p0) / 2, p0 - m1)
    return dict(present_range=[p0, p1], missing_range=[m0, m1], op=op, threshold=threshold, separation_gap=gap)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-config', 'annotations', 'input', 'truth', 'out-config', 'report'):
        parser.add_argument(f'--{name}', required=True, type=Path)
    args = parser.parse_args()
    config = load_config(args.source_config)
    annotation = json.loads(args.annotations.read_text(encoding='utf-8'))
    if sha256(reference_path(config)) != annotation['reference_sha256']:
        raise ValueError('Manual annotations refer to a different reference image')
    if config['input_size'] != annotation['input_size']:
        raise ValueError('Annotation dimensions differ from config')
    if set(config['rois']) != set(annotation['boxes']):
        raise ValueError('Annotation ROI names differ from config')
    for name, box in annotation['boxes'].items():
        x0, y0, x1, y1 = box
        w, h = config['input_size']
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise ValueError(f'Invalid annotation: {name}')
        config['rois'][name]['box'] = box
        config['rois'][name]['source'] = 'manual_body_and_pads_review_v0.4.0'
    with args.truth.open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file))
    truth = {(r['filename'], r['refdes']): r['state'] for r in rows}
    names = [n for n, r in config['rois'].items() if r.get('active', True) and r['type'] == 'missing']
    images = sorted((p for p in args.input.iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png')))
    if not images or len(rows) != len(truth) or set(truth) != {(p.name, n) for p in images for n in names}:
        raise ValueError('Truth coverage mismatch')
    if any((state not in ('present', 'missing') for state in truth.values())):
        raise ValueError('Only present/missing labels can calibrate these tasks')
    candidates = {n: [config['rois'][n]['feature']] + annotation.get('fallback_features', {}).get(n, []) for n in names}
    values = {n: {f: {'present': [], 'missing': []} for f in candidates[n]} for n in names}
    for path in images:
        gray, meta = prepare_frame(read_image(path), config)
        if not meta['image_ok']:
            raise ValueError(f'Alignment failed: {path.name}')
        for name in names:
            rc = config['rois'][name]
            previous_feature = rc['feature']
            for feature in candidates[name]:
                rc['feature'] = feature
                feats, _ = process_reference(gray, config, name, meta['H'])
                values[name][feature][truth[path.name, name]].append(feats['feat'])
            rc['feature'] = previous_feature
    report = dict(config_version='0.4.0', dataset='s01_development_calibration', images=len(images), decisions=len(truth), independent_test=False, annotations_sha256=sha256(args.annotations), source_config_sha256=sha256(args.source_config), reference_sha256=sha256(reference_path(config)), photos_sha256={p.name: sha256(p) for p in images}, truth_sha256=sha256(args.truth), items={})
    for name in names:
        ranges = {}
        for feature, samples in values[name].items():
            if not samples['present'] or not samples['missing']:
                raise ValueError(f'Both classes required for {name}')
            ranges[feature] = separation(**samples)
        feature = next((f for f in candidates[name] if ranges[f]['op'] is not None), None)
        if feature is None:
            raise ValueError(f'No allowed feature separates development samples for {name}; review manually')
        selected = ranges[feature]
        rc = config['rois'][name]
        old = {k: rc[k] for k in ('feature', 'op', 'threshold')}
        rc.update(feature=feature, op=selected['op'], threshold=selected['threshold'])
        report['items'][name] = dict(box=rc['box'], previous=old, feature=feature, **selected, candidate_ranges=ranges, small_margin=feature == 'cd40' and selected['separation_gap'] < 0.02)
    config['version'] = '0.4.0'
    config['note'] = 'Manually corrected body/pad ROIs; thresholds recalibrated on s01 development data, not independent accuracy. R12 has a small margin.'
    config['roi_revision'] = dict(reference='s01_b01_1.jpg', exif_oriented=True, annotation_version=annotation['version'])
    result = public_config(config)
    result['alignment']['reference_image'] = Path(os.path.relpath(reference_path(config), args.out_config.resolve().parent)).as_posix()
    args.out_config.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out_config.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {args.out_config} and {args.report}. Development calibration only.')
if __name__ == '__main__':
    main()
