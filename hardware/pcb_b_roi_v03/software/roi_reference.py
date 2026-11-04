from pathlib import Path
import hashlib
import json
from numbers import Integral
import numpy as np
ROOT = Path(__file__).resolve().parents[1]

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def reference(image, rois, *, threshold=120, invert=True, roi_enable=True, bypass=False):
    pixels = np.asarray(image)
    if pixels.dtype != np.uint8 or pixels.ndim != 2:
        raise ValueError('Reference image must be a uint8 (height,width) array')
    if not isinstance(threshold, Integral) or isinstance(threshold, bool) or (not 0 <= threshold <= 255):
        raise ValueError('threshold must be an integer from 0 to 255')
    if any((type(flag) is not bool for flag in (invert, roi_enable, bypass))):
        raise ValueError('Mode flags must be bool')
    if not isinstance(rois, (list, tuple)) or len(rois) > 17:
        raise ValueError('At most 17 ROI dictionaries are supported')
    foreground = pixels < 255 - int(threshold) if invert else pixels > int(threshold)
    union = np.zeros(pixels.shape, dtype=bool)
    counts, by_index = ({}, [0] * 17)
    names = set()
    for index, roi in enumerate(rois):
        if not isinstance(roi, dict) or not isinstance(roi.get('name'), str) or (not roi['name']) or (roi['name'] in names):
            raise ValueError('ROI names must be unique nonempty strings')
        names.add(roi['name'])
        box = roi.get('box')
        if not isinstance(box, (list, tuple)) or len(box) != 4 or any((not isinstance(v, Integral) or isinstance(v, bool) for v in box)):
            raise ValueError('ROI box must contain four integer coordinates')
        if type(roi.get('enabled', True)) is not bool:
            raise ValueError('enabled must be bool')
        x0, y0, x1, y1 = map(int, box)
        if not (0 <= x0 < x1 <= pixels.shape[1] and 0 <= y0 < y1 <= pixels.shape[0]):
            raise ValueError('Reference ROI out of bounds')
        if roi.get('enabled', True):
            union[y0:y1, x0:x1] = True
            count = 0 if bypass else int(np.count_nonzero(foreground[y0:y1, x0:x1]))
            counts[roi['name']] = count
            by_index[index] = count
    if bypass:
        output = np.array(pixels, copy=True)
        white = 0
    else:
        keep = foreground & union if roi_enable else foreground
        output = np.where(keep, 255, 0).astype(np.uint8)
        white = int(np.count_nonzero(keep))
    return (output, {'roi_counts': counts, 'roi_counts_by_index': by_index, 'output_white_pixels': white, 'valid_pixels': int(pixels.size), 'roi_union_pixels': int(np.count_nonzero(union))})

def load_inputs(root=ROOT):
    root = Path(root)
    manifest = json.loads((root / 'fixtures/sha256.json').read_text(encoding='utf-8'))
    for name, expected in manifest.items():
        path = root / 'fixtures' / name
        if not path.resolve().is_relative_to((root / 'fixtures').resolve()) or sha256(path) != expected:
            raise ValueError('Fixture hash mismatch: ' + name)
    gray = np.load(root / 'fixtures/input_gray.npy', allow_pickle=False)
    original = np.load(root / 'fixtures/original_frozen_mask.npy', allow_pickle=False)
    for name, expected in json.loads((root / 'config/sha256.json').read_text(encoding='utf-8')).items():
        path = root / 'config' / name
        if not path.resolve().is_relative_to((root / 'config').resolve()) or sha256(path) != expected:
            raise ValueError('Configuration hash mismatch: ' + name)
    profiles = json.loads((root / 'config/roi_profiles.json').read_text(encoding='utf-8'))
    if gray.dtype != np.uint8 or original.dtype != np.uint8 or gray.shape != (1900, 2533) or (original.shape != gray.shape):
        raise ValueError('Unexpected frozen image dimensions')
    return (gray, original, profiles)

def build_cases(root=ROOT):
    gray, _, profiles = load_inputs(root)
    tiny = np.array([0, 134, 135, 136, 255, 120, 121, 1] * 4, dtype=np.uint8).reshape(4, 8)
    overlap = [{'name': 'left', 'box': [0, 1, 5, 3], 'enabled': True}, {'name': 'right', 'box': [3, 0, 8, 2], 'enabled': True}, {'name': 'disabled', 'box': [1, 1, 7, 4], 'enabled': False}]
    specifications = [('bypass_32', np.arange(32, dtype=np.uint8).reshape(4, 8), [], False, False, True), ('bypass_64', np.arange(64, dtype=np.uint8).reshape(8, 8), [], False, False, True), ('roi_boundary_8x4', tiny, overlap, True, True, False), ('bypass_640x480', (np.arange(307200, dtype=np.uint32) % 256).astype(np.uint8).reshape(480, 640), [], False, False, True), ('bypass_full_golden', gray, profiles['latest17']['rois'], True, True, True), ('threshold_full_golden', gray, profiles['latest17']['rois'], True, False, False), ('roi_latest17_full', gray, profiles['latest17']['rois'], True, True, False), ('roi_frozen16_full', gray, profiles['frozen16']['rois'], True, True, False)]
    cases = []
    for name, image, rois, invert, enabled, bypass in specifications:
        output, stats = reference(image, rois, invert=invert, roi_enable=enabled, bypass=bypass)
        cases.append({'name': name, 'image': image, 'rois': rois, 'invert': invert, 'roi_enable': enabled, 'bypass': bypass, 'threshold': 120, 'expected': output, 'expected_stats': stats})
    return cases

def compare(actual, expected):
    actual, expected = (np.asarray(actual), np.asarray(expected))
    if actual.dtype != np.uint8 or actual.shape != expected.shape:
        raise ValueError('Actual image shape/dtype mismatch')
    changed = np.flatnonzero(actual.reshape(-1) != expected.reshape(-1))
    first = None
    if changed.size:
        index = int(changed[0])
        first = {'byte_offset': index, 'index': [int(v) for v in np.unravel_index(index, expected.shape)], 'actual': int(actual.reshape(-1)[index]), 'expected': int(expected.reshape(-1)[index])}
    return {'match': not bool(changed.size), 'mismatches': int(changed.size), 'bytes': int(expected.nbytes), 'first_mismatch': first}

def audit(root=ROOT):
    root = Path(root)
    cases = build_cases(root)
    _, original, _ = load_inputs(root)
    frozen = cases[-1]
    check = compare(frozen['expected'], original)
    if not check['match']:
        raise ValueError('Frozen16 CPU reference differs from original A golden')
    stored = json.loads((root / 'fixtures/expected_stats.json').read_text(encoding='utf-8'))
    for label, case in (('full_threshold120', cases[-3]), ('latest17', cases[-2]), ('frozen16', cases[-1])):
        saved_mask = np.load(root / 'fixtures' / ('mask_' + label + '.npy'), allow_pickle=False)
        if not np.array_equal(saved_mask, case['expected']) or stored[label]['stats'] != case['expected_stats']:
            raise ValueError('Frozen ROI profile reference mismatch: ' + label)
    fixed_cases = json.loads((root / 'fixtures/cases_expected.json').read_text(encoding='utf-8'))
    if len(fixed_cases) != len(cases):
        raise ValueError('Frozen acceptance case count mismatch')
    for fixed, case in zip(fixed_cases, cases):
        live = case_signature(case)
        if fixed != live:
            raise ValueError('Frozen acceptance case mismatch: ' + case['name'])
    return {'scope': 'CPU reference only', 'hardware_executed': False, 'frozen16_vs_original_A': check, 'cases': [{'name': c['name'], 'shape': list(c['image'].shape), 'bytes': int(c['image'].nbytes), 'roi_count': len(c['rois']), 'invert': c['invert'], 'roi_enable': c['roi_enable'], 'bypass': c['bypass'], 'expected_stats': c['expected_stats'], 'expected_raw_sha256': hashlib.sha256(c['expected'].tobytes()).hexdigest()} for c in cases]}

def case_signature(case):
    return {'name': case['name'], 'shape': list(case['image'].shape), 'bytes': int(case['image'].nbytes), 'threshold': case['threshold'], 'invert': case['invert'], 'roi_enable': case['roi_enable'], 'bypass': case['bypass'], 'rois': case['rois'], 'expected_stats': case['expected_stats'], 'input_raw_sha256': hashlib.sha256(case['image'].tobytes()).hexdigest(), 'output_raw_sha256': hashlib.sha256(case['expected'].tobytes()).hexdigest()}
if __name__ == '__main__':
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
