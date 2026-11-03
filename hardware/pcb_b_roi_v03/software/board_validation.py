from datetime import datetime, timezone
import json
from pathlib import Path
import time
import numpy as np
from roi_reference import audit, build_cases, compare, reference

def validate_board(driver, root, run_directory):
    root, run_directory = (Path(root), Path(run_directory))
    offline = audit(root)
    cases = build_cases(root)
    run_directory.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'physical board execution requested by caller', 'utc': datetime.now(timezone.utc).isoformat(), 'run_directory': str(run_directory.resolve()), 'state': 'started', 'passed': False, 'abi': 3, 'offline_audit': offline, 'records': []}

    def save():
        (run_directory / 'board_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    current = {}
    before_transfer_object = None
    try:
        driver.recover()
        report['initial_status'] = driver.read_status()
        for case in cases:
            before_transfer_object = None
            current = {'case': case['name'], 'bytes': int(case['image'].nbytes), 'threshold': 120, 'invert': case['invert'], 'roi_enable': case['roi_enable'], 'bypass': case['bypass'], 'roi_config': case['rois'], 'stage': 'configure', 'passed': False}
            report['current_attempt'] = current
            save()
            print('CASE_START', case['name'], flush=True)
            start = time.perf_counter()
            driver.configure(case['image'].shape[1], case['image'].shape[0], case['rois'], invert=case['invert'], roi_enable=case['roi_enable'])
            current['configuration_seconds'] = time.perf_counter() - start
            start = time.perf_counter()
            expected, expected_stats = reference(case['image'], case['rois'], threshold=120, invert=case['invert'], roi_enable=case['roi_enable'], bypass=case['bypass'])
            current['cpu_reference_seconds_single_call'] = time.perf_counter() - start
            current['stage'] = 'transfer'
            save()
            before_transfer_object = driver.last_transfer
            actual = None
            start = time.perf_counter()
            try:
                actual = driver.transfer(case['image'], threshold=120, bypass=case['bypass'])
                stats = driver.read_results()
            finally:
                current['hardware_transfer_and_stats_seconds_single_call'] = time.perf_counter() - start
                if actual is not None:
                    actual.tofile(run_directory / (case['name'] + '_actual.bin'))
            pixel_check = compare(actual, expected)
            stat_checks = {name: stats[name] == expected_stats[name] for name in ('roi_counts', 'roi_counts_by_index', 'output_white_pixels', 'valid_pixels')}
            current.update(stage='complete', pixel_check=pixel_check, actual_stats=stats, expected_stats=expected_stats, stats_match=stat_checks, driver=dict(driver.last_transfer), passed=pixel_check['match'] and all(stat_checks.values()))
            if case['name'] == 'roi_frozen16_full':
                original = np.load(root / 'fixtures/original_frozen_mask.npy', allow_pickle=False)
                current['original_A_mask_check'] = compare(actual, original)
                current['passed'] = current['passed'] and current['original_A_mask_check']['match']
            report['records'].append(current)
            save()
            if not current['passed']:
                raise AssertionError('Pixel or ROI counter mismatch: ' + case['name'])
            print('CASE_PASS', case['name'], flush=True)
        report['final_status'] = driver.read_status()
        final = report['final_status']
        if final['completed_packets'] != 8 or any((final[k] for k in ('busy', 'inpacket', 'cfg_error', 'frame_error'))):
            raise AssertionError('Unexpected final algorithm status: ' + str(final))
        report.update(state='complete', passed=True)
        report.pop('current_attempt', None)
        save()
        return report
    except BaseException as exc:
        extra = getattr(exc, 'actual_output', None)
        if isinstance(extra, np.ndarray):
            extra.tofile(run_directory / (current.get('case', 'unknown') + '_failed_actual.bin'))
        report.update(state='failed', passed=False, error=type(exc).__name__ + ': ' + str(exc), last_driver_transfer=dict(driver.last_transfer), current_attempt=current, last_driver_transfer_is_current_attempt=current.get('stage') in ('transfer', 'complete') and before_transfer_object is not None and (driver.last_transfer is not before_transfer_object))
        save()
        raise
