from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import statistics
import sys
import time
ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            value.update(chunk)
    return value.hexdigest()

def manifest_check(base, name):
    base = Path(base).resolve()
    manifest = json.loads((base / name).read_text(encoding='utf-8'))
    seen = set()
    for item in manifest['files']:
        rel = item['path']
        path = (base / rel).resolve()
        if Path(rel).is_absolute() or '..' in Path(rel).parts or rel in seen or (not path.is_relative_to(base)) or (not path.is_file()) or (path.stat().st_size != item['bytes']) or (digest(path) != item['sha256']):
            raise ValueError('Frozen manifest mismatch: ' + str(path))
        seen.add(rel)
    return {'path': str(base / name), 'sha256': digest(base / name), 'files_verified': len(seen)}

def summary(values):
    values = [float(v) for v in values]
    if not values or any((not math.isfinite(v) or v <= 0 for v in values)):
        raise ValueError('Timing samples must be finite and positive')
    ordered = sorted(values)
    position = 0.95 * (len(values) - 1)
    left, right = (math.floor(position), math.ceil(position))
    return {'n': len(values), 'unit': 'seconds', 'min': min(values), 'max': max(values), 'mean': statistics.mean(values), 'median': statistics.median(values), 'p95_linear': ordered[left] + (position - left) * (ordered[right] - ordered[left]), 'std_population': statistics.pstdev(values)}

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    if spec is None or spec.loader is None:
        raise ValueError('Cannot load reviewed service source')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def environment():
    result = {'client_date': '2026-11-01', 'board_utc': datetime.now(timezone.utc).isoformat(), 'python': sys.version, 'platform': platform.platform(), 'machine': platform.machine(), 'uid': os.geteuid() if hasattr(os, 'geteuid') else None, 'clock': vars(time.get_clock_info('perf_counter')), 'cpu_runtime_info': {}}
    for name in ('scaling_governor', 'scaling_cur_freq', 'scaling_min_freq', 'scaling_max_freq'):
        try:
            result['cpu_runtime_info'][name] = (Path('/sys/devices/system/cpu/cpu0/cpufreq') / name).read_text().strip()
        except OSError:
            result['cpu_runtime_info'][name] = None
    return result

def save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))

def correctness_probes(module, mask, expected_white):
    import numpy as np
    results = {}
    for name, validator in module.MASK_VALIDATORS.items():
        valid_values = invalid_values = 0
        for value in range(256):
            tiny = np.array([[0, 255], [value, 0]], dtype=np.uint8)
            if value in (0, 255):
                if validator(tiny) != 1 + int(value == 255):
                    raise AssertionError(name + ' returned wrong white count')
                valid_values += 1
            else:
                try:
                    validator(tiny)
                except module.ResultValidationError:
                    invalid_values += 1
                else:
                    raise AssertionError(name + ' accepted a nonbinary value ' + str(value))
        for index in (0, mask.size // 2, mask.size - 1):
            corrupted = mask.copy()
            corrupted.reshape(-1)[index] = 134
            try:
                validator(corrupted)
            except module.ResultValidationError:
                pass
            else:
                raise AssertionError(name + ' missed a corrupted first/middle/last pixel')
        if validator(mask) != expected_white:
            raise AssertionError(name + ' differs from frozen white count')
        results[name] = {'all_256_values_checked': True, 'valid_values': valid_values, 'invalid_values_rejected': invalid_values, 'large_first_middle_last_corruption_rejected': True}
    return results

def execute_profile(package, run_id, root=ROOT):
    import numpy as np
    package, root = (Path(package).resolve(), Path(root).resolve())
    run_dir = root / 'evidence' / ('profile_run_' + run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    module = load_module(root / 'software/aoi_roi_service.py', 'closure_profile_service')
    mask = np.load(package / 'fixtures/mask_latest17.npy', allow_pickle=False)
    expected = json.loads((package / 'fixtures/expected_stats.json').read_text(encoding='utf-8'))['latest17']['stats']['output_white_pixels']
    if mask.dtype != np.uint8 or mask.shape != (1900, 2533):
        raise ValueError('Unexpected frozen mask dimensions')
    before = hashlib.sha256(mask.tobytes()).hexdigest()
    report = {'schema': 'pcb-b-strict-mask-microprofile/0.3.2', 'run_id': run_id, 'state': 'running', 'passed': False, 'hardware_executed': False, 'environment': environment(), 'numpy': np.__version__, 'mask_bytes': int(mask.nbytes), 'shape': list(mask.shape), 'input_raw_sha256': before, 'expected_white_pixels': expected, 'warmups': 3, 'measured': 10, 'service_source_sha256': digest(root / 'software/aoi_roi_service.py'), 'helper_source_sha256': {name: hashlib.sha256(inspect.getsource(fn).encode()).hexdigest() for name, fn in module.MASK_VALIDATORS.items()}, 'scope': 'Strict binary validation AND white count only; excludes comparisons/probes/hashes/file I/O; no DMA or PL measurement', 'records': []}
    path = run_dir / 'profile_report.json'
    try:
        report['correctness'] = correctness_probes(module, mask, expected)
        save_json(path, report)
        rng = random.Random(20261101)
        for index in range(13):
            order = list(module.MASK_VALIDATORS)
            rng.shuffle(order)
            for name in order:
                start = time.perf_counter_ns()
                white = module.MASK_VALIDATORS[name](mask)
                seconds = (time.perf_counter_ns() - start) / 1000000000.0
                if type(white) is not int or white != expected:
                    raise AssertionError('Candidate white count mismatch: ' + name)
                report['records'].append({'iteration': index, 'phase': 'warmup' if index < 3 else 'measured', 'candidate': name, 'order': order, 'seconds': seconds, 'white_pixels': white})
            save_json(path, report)
        if hashlib.sha256(mask.tobytes()).hexdigest() != before:
            raise AssertionError('A validator modified the input mask')
        report['summary'] = {name: summary([row['seconds'] for row in report['records'] if row['phase'] == 'measured' and row['candidate'] == name]) for name in module.MASK_VALIDATORS}
        report['lowest_median_candidate'] = min(report['summary'], key=lambda name: report['summary'][name]['median'])
        report.update(state='complete', passed=True, input_unchanged=True, completed_utc=datetime.now(timezone.utc).isoformat())
        save_json(path, report)
        print(json.dumps({'profile_report': str(path), 'summary': report['summary'], 'lowest_median_candidate': report['lowest_median_candidate'], 'hardware_executed': False}, ensure_ascii=False, indent=2), flush=True)
        return report
    except BaseException as exc:
        report.update(state='failed', passed=False, error=type(exc).__name__ + ': ' + str(exc))
        save_json(path, report)
        raise

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, default=ROOT.parent / 'pcb_b_roi_v03')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch('20261101_[a-f0-9]{8}', args.run_id):
        raise ValueError('Expected run ID 20261101_<8 lowercase hex>')
    manifests = [manifest_check(ROOT, 'new_package_manifest.json'), manifest_check(args.package, 'package_manifest.json')]
    if not args.execute:
        print(json.dumps({'state': 'plan_only', 'hardware_executed': False, 'environment': environment(), 'warmups': 3, 'measured_per_candidate': 10, 'manifests': manifests}, ensure_ascii=False, indent=2))
        return 0
    if platform.system() != 'Linux' or platform.machine().lower() not in ('armv7l', 'armv8l'):
        raise EnvironmentError('Use the PYNQ ARM Linux CPU for this measured microprofile')
    execute_profile(args.package, args.run_id)
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
