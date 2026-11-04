import csv
from pathlib import Path
import json
from .core import detect
from .io import read_image, write_image

def evaluate(config, input_dir, truth_path, output_dir):
    input_dir, output_dir = (Path(input_dir), Path(output_dir))
    truth = {}
    with Path(truth_path).open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            key = (row['filename'], row['refdes'])
            if key in truth:
                raise ValueError(f'Duplicate truth row: {key}')
            if row['state'] not in ('present', 'missing'):
                raise ValueError(f'Invalid missing-task label: {key}')
            truth[key] = row['state']
    images = sorted((p for p in input_dir.iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png')))
    if not images:
        raise ValueError(f'No images found in {input_dir}')
    names = [n for n, r in config['rois'].items() if r.get('active', True) and r['type'] == 'missing']
    wanted = {(p.name, n) for p in images for n in names}
    if set(truth) != wanted:
        raise ValueError(f'Truth coverage mismatch: missing={len(wanted - set(truth))}, extra={len(set(truth) - wanted)}')
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = dict(TP=0, TN=0, FP=0, FN=0, invalid=0, uncertain=0)
    rows = []
    image_failures = 0
    for path in images:
        result, annotated = detect(read_image(path), config)
        image_failures += not result['image_ok']
        for item in result['items']:
            if item['task'] != 'missing':
                continue
            expected = truth[path.name, item['refdes']]
            got = item['status']
            if got in ('invalid', 'uncertain'):
                counts[got] += 1
            elif expected == 'missing':
                counts['TP' if got == 'missing' else 'FN'] += 1
            else:
                counts['TN' if got == 'present' else 'FP'] += 1
            rows.append(dict(filename=path.name, refdes=item['refdes'], expected=expected, got=got, correct=got == expected, image_ok=result['image_ok'], feature_value=item['feature_value'], reason=item['reason']))
        write_image(output_dir / 'annotated' / f'{path.stem}.png', annotated)
    with (output_dir / 'decisions.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(config_version=config['version'], dataset='development_replay', images=len(images), decisions=len(rows), invalid_images=image_failures, **counts, correct=sum((r['correct'] for r in rows)), note='s01 was used for calibration; this is not independent accuracy.')
    (output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary
