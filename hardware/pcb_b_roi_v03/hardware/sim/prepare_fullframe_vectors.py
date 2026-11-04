import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
PACKAGE = Path(__file__).resolve().parents[2]

def write_mem(path, values):
    with path.open('w', encoding='ascii', newline='\n') as stream:
        for start in range(0, len(values), 10000):
            stream.writelines((f'{int(v):08x}\n' for v in values[start:start + 10000]))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    profiles = json.loads((PACKAGE / 'config/roi_profiles.json').read_text(encoding='utf-8'))
    expected = json.loads((PACKAGE / 'fixtures/expected_stats.json').read_text(encoding='utf-8'))
    gray = np.load(PACKAGE / 'fixtures/input_gray.npy', allow_pickle=False)
    assert gray.dtype == np.uint8 and gray.shape == (1900, 2533)
    write_mem(args.output / 'input_gray.mem', np.frombuffer(gray.tobytes(), dtype='<u4'))
    for name in ('latest17', 'frozen16'):
        entries = profiles[name]['rois']
        config = [1900 << 16 | 2533, sum((1 << i for i, r in enumerate(entries) if r.get('enabled', True)))]
        for i in range(17):
            config.extend(entries[i]['box'] if i < len(entries) else [0, 0, 0, 0])
        write_mem(args.output / f'{name}_config.mem', config)
        stats = expected[name]['stats']
        counts = stats['roi_counts_by_index'] + [0] * (17 - len(stats['roi_counts_by_index']))
        write_mem(args.output / f'{name}_stats.mem', [stats['output_white_pixels'], stats['valid_pixels']] + counts)
        mask = np.load(PACKAGE / f'fixtures/mask_{name}.npy', allow_pickle=False)
        write_mem(args.output / f'{name}_mask.mem', np.frombuffer(mask.tobytes(), dtype='<u4'))
    mask = np.load(PACKAGE / 'fixtures/mask_full_threshold120.npy', allow_pickle=False)
    write_mem(args.output / 'full_mask.mem', np.frombuffer(mask.tobytes(), dtype='<u4'))
    stats = expected['latest17']['stats']
    write_mem(args.output / 'full_stats.mem', [int(np.count_nonzero(mask)), gray.size] + stats['roi_counts_by_index'])
    result = {'scope': 'precomputed independent NumPy masks/counts, raw gray input', 'bytes_per_frame': gray.nbytes, 'words_per_frame': gray.nbytes // 4, 'frames': 3, 'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.output.glob('*.mem'))}}
    (args.output / 'vectors_manifest.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}, indent=2))
if __name__ == '__main__':
    main()
