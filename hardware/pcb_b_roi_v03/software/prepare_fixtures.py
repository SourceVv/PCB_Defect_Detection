import argparse
import hashlib
import json
from pathlib import Path
import shutil
import numpy as np
from roi_reference import build_cases, case_signature, reference
ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--a-config', type=Path, required=True)
    parser.add_argument('--frozen', type=Path, required=True)
    args = parser.parse_args()
    fixtures, config = (ROOT / 'fixtures', ROOT / 'config')
    fixtures.mkdir(exist_ok=True)
    config.mkdir(exist_ok=True)
    sources = {'latest17': args.a_config, 'frozen16': args.frozen / 'config.json'}
    profiles = {}
    for label, path in sources.items():
        cfg = json.loads(path.read_text(encoding='utf-8'))
        name = 'a_roi_config_' + ('v02.json' if label == 'latest17' else 'v01.json')
        shutil.copy2(path, config / name)
        rois = [{'name': name, 'box': roi['box'], 'enabled': True, 'source_feature': roi.get('feature', 'area_inv'), 'source_type': roi.get('type')} for name, roi in cfg['rois'].items() if roi.get('active', True)]
        profiles[label] = {'a_config_version': cfg['version'], 'input_size_width_height': cfg['input_size'], 'source_config_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'pixel_threshold': 120, 'invert': True, 'rois': rois, 'scope': 'foreground counts only; not SAD/hcorr/cd40 or classification'}
    assert len(profiles['latest17']['rois']) == 17 and len(profiles['frozen16']['rois']) == 16
    (config / 'roi_profiles.json').write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding='utf-8')
    (config / 'SHA256.json').write_text(json.dumps({name: hashlib.sha256((config / name).read_bytes()).hexdigest() for name in ('roi_profiles.json', 'a_roi_config_v01.json', 'a_roi_config_v02.json')}, indent=2), encoding='utf-8')
    shutil.copy2(args.frozen / 'input_gray.npy', fixtures / 'input_gray.npy')
    shutil.copy2(args.frozen / 'expected_mask.npy', fixtures / 'original_frozen_mask.npy')
    gray = np.load(fixtures / 'input_gray.npy', allow_pickle=False)
    original = np.load(fixtures / 'original_frozen_mask.npy', allow_pickle=False)
    summaries = {}
    for label, profile in profiles.items():
        image, stats = reference(gray, profile['rois'])
        np.save(fixtures / ('mask_' + label + '.npy'), image)
        summaries[label] = {'stats': stats, 'mask_raw_sha256': hashlib.sha256(image.tobytes()).hexdigest()}
        if label == 'frozen16':
            assert np.array_equal(image, original), 'Frozen16 differs from original A mask'
    full, full_stats = reference(gray, profiles['latest17']['rois'], invert=True, roi_enable=False)
    np.save(fixtures / 'mask_full_threshold120.npy', full)
    summaries['full_threshold120_white_pixels'] = int(np.count_nonzero(full))
    summaries['full_threshold120'] = {'stats': full_stats, 'mask_raw_sha256': hashlib.sha256(full.tobytes()).hexdigest()}
    (fixtures / 'expected_stats.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding='utf-8')
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(fixtures.iterdir()) if p.is_file() and p.name != 'SHA256.json'}
    (fixtures / 'SHA256.json').write_text(json.dumps(checksums, indent=2), encoding='utf-8')
    (fixtures / 'cases_expected.json').write_text(json.dumps([case_signature(c) for c in build_cases(ROOT)], ensure_ascii=False, indent=2), encoding='utf-8')
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(fixtures.iterdir()) if p.is_file() and p.name != 'SHA256.json'}
    (fixtures / 'SHA256.json').write_text(json.dumps(checksums, indent=2), encoding='utf-8')
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
if __name__ == '__main__':
    main()
