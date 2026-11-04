import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import traceback
ROOT = Path(__file__).resolve().parents[1]
RUN_NAMESPACE = {}

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch('20261031_[a-f0-9]{8}', args.run_id):
        raise ValueError('Unexpected run ID')
    manifest = json.loads((ROOT / 'package_manifest.json').read_text())
    for item in manifest['files']:
        path = ROOT / item['path']
        if not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError('Manifest path escapes project')
        if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise ValueError('Package changed: ' + item['path'])
    if not args.execute:
        print(json.dumps({'plan': 'run reviewed ABI3 six notebook cells and eight DMA cases', 'manifest_files_verified': len(manifest['files']), 'hardware_executed': False, 'uid': os.geteuid(), 'run_id': args.run_id}, indent=2))
        return 0
    if os.geteuid() != 0:
        raise PermissionError('Authorized hardware execution requires root on the board')
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    run_dir = ROOT / 'evidence' / ('board_run_' + args.run_id)
    notebook_path = ROOT / 'software' / ('ROI_live_' + args.run_id + '.ipynb')
    status_path = ROOT / 'evidence' / ('worker_' + args.run_id + '.json')
    log_path = ROOT / 'evidence' / ('worker_' + args.run_id + '.log')
    if run_dir.exists() or notebook_path.exists() or status_path.exists():
        raise FileExistsError('Refusing to overwrite a previous run')
    notebook = json.loads((ROOT / 'software/reference_document_16.ipynb').read_text())
    status = {'client_request_date': '2026-10-31', 'board_utc': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(), 'uid': os.geteuid(), 'run_id': args.run_id, 'run_directory': str(run_dir), 'notebook': str(notebook_path), 'engine': 'same ABI3 notebook cells in dedicated board Python process', 'expected_dma_cases': 8, 'XILINX_XRT': os.environ.get('XILINX_XRT'), 'state': 'starting', 'code_cell': 0, 'hardware_attempted': False}

    def save():
        notebook['metadata']['board_execution'] = dict(status)
        notebook_path.write_text(json.dumps(notebook, ensure_ascii=False, indent=2), encoding='utf-8')
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    log = log_path.open('x', encoding='utf-8', buffering=1)

    class Capture(io.StringIO):

        def write(self, text):
            log.write(text)
            return super().write(text)
    count = 0
    try:
        for cell in notebook['cells']:
            if cell['cell_type'] != 'code':
                continue
            count += 1
            source = ''.join(cell['source'])
            if count == 1:
                source = source.replace('RUN_ON_BOARD = False', 'RUN_ON_BOARD = True')
            if count == 5:
                source = source.replace("run_name = 'board_run_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')", 'run_name = ' + repr(run_dir.name))
            cell['source'] = source.splitlines(keepends=True)
            cell['execution_count'] = count
            status.update(state='running', code_cell=count, hardware_attempted=count >= 4)
            save()
            print('CELL_START', count, flush=True)
            captured = Capture()
            try:
                with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                    exec(compile(source, 'board-notebook-cell-' + str(count), 'exec'), RUN_NAMESPACE)
            except BaseException as exc:
                cell['outputs'] = [{'output_type': 'stream', 'name': 'stdout', 'text': captured.getvalue().splitlines(keepends=True)}, {'output_type': 'error', 'ename': type(exc).__name__, 'evalue': str(exc), 'traceback': traceback.format_exc().splitlines()}]
                raise
            cell['outputs'] = [{'output_type': 'stream', 'name': 'stdout', 'text': captured.getvalue().splitlines(keepends=True)}]
            status['state'] = 'cell_complete'
            save()
            print('CELL_COMPLETE', count, flush=True)
        result = RUN_NAMESPACE.get('board_result', {})
        if not result.get('passed') or result.get('state') != 'complete' or len(result.get('records', [])) != 8:
            raise AssertionError('Missing successful board result')
        status.update(state='complete', passed=True, code_cells_completed=count)
        save()
        print('BOARD_RUN_COMPLETE', status_path, flush=True)
        return 0
    except BaseException as exc:
        driver = RUN_NAMESPACE.get('driver')
        snapshot = vars(driver) if driver is not None else {}
        retained = len(snapshot.get('_retained_buffers', []))
        unsafe = bool(snapshot.get('poisoned') or snapshot.get('_active') or retained or (snapshot.get('_buffers') and (not snapshot.get('_dma_safe'))))
        status.update(state='held_for_recovery' if unsafe else 'failed_stopped', passed=False, error=type(exc).__name__ + ': ' + str(exc), driver_poisoned=bool(snapshot.get('poisoned')), driver_active=bool(snapshot.get('_active')), retained_buffers=retained, last_transfer=snapshot.get('last_transfer'))
        try:
            log.write(traceback.format_exc())
            save()
            print('BOARD_RUN_FAILED', status_path, 'state=' + status['state'], flush=True)
        except BaseException:
            pass
        finally:
            if unsafe:
                while True:
                    try:
                        time.sleep(30)
                    except BaseException:
                        pass
        return 1
    finally:
        try:
            log.flush()
        except BaseException:
            pass
if __name__ == '__main__':
    raise SystemExit(main())
