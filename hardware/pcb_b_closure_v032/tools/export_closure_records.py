from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile
ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile('(?P<kind>profile_run|closure_validate|closure_stability)_(?P<run_id>20261101_[a-f0-9]{8})')

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()

def _json(path):

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique)

def _contained(base, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts or ('\\' in relative):
        raise ValueError('Only contained relative source paths are allowed')
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or path == base.resolve():
        raise ValueError('Source path escapes selected directory')
    return path

def _safe_snapshot(value):
    if not isinstance(value, dict) or value.get('dma_safe') is not True or value.get('active') is not False or (value.get('poisoned') is not False) or (value.get('buffers') != 0) or (value.get('retained_buffers') != 0):
        raise ValueError('Completed hardware report does not show safe stopped buffers')

def collect_run(root, name):
    matched = NAME.fullmatch(name)
    if matched is None:
        raise ValueError('Invalid run directory name: ' + str(name))
    evidence = (root / 'evidence').resolve()
    run = evidence / name
    if run.is_symlink() or not run.is_dir() or run.resolve().parent != evidence:
        raise ValueError('Run must be a real direct child of evidence: ' + name)
    kind = matched['kind']
    report_path = run / ('profile_report.json' if kind == 'profile_run' else 'report.json')
    report = _json(report_path)
    if report.get('state') != 'complete' or report.get('passed') is not True or report.get('run_id') != matched['run_id']:
        raise ValueError('Refusing active, incomplete or failed run: ' + name)
    if kind == 'profile_run':
        if report.get('schema') != 'pcb-b-strict-mask-microprofile/0.3.2' or report.get('hardware_executed') is not False or report.get('warmups') != 3 or (report.get('measured') != 10) or (len(report.get('records', [])) != 65) or (len(report.get('summary', {})) != 5):
            raise ValueError('Microprofile is not complete: ' + name)
    else:
        expected_mode = 'validate' if kind == 'closure_validate' else 'stability'
        if report.get('schema') != 'pcb-b-closure/0.3.2' or report.get('mode') != expected_mode or report.get('hardware_executed') is not True:
            raise ValueError('Hardware report mode/schema differs: ' + name)
        _safe_snapshot(report.get('final_driver_safety'))
        records_path = run / 'records.jsonl'
        lines = 0
        with records_path.open(encoding='utf-8') as stream:
            for line in stream:
                if not line.endswith('\n') or not line.strip():
                    raise ValueError('Truncated or empty JSONL record: ' + name)
                row = json.loads(line)
                if row.get('passed') is not True:
                    raise ValueError('Unsuccessful JSONL record: ' + name)
                lines += 1
        if expected_mode == 'validate':
            if lines != 33 or len(report.get('records', [])) != 33 or report.get('completed_rounds') != 33 or (report.get('validated_hardware_frames') != 66):
                raise ValueError('Incomplete validate records: ' + name)
        elif report.get('requested_duration_seconds', 0) < 1800 or report.get('elapsed_monotonic_seconds', 0) < report.get('requested_duration_seconds', 1800) or report.get('errors') != 0 or (not lines) or (lines != report.get('validated_hardware_frames')):
            raise ValueError('Incomplete stability records or duration: ' + name)
        samples = report.get('samples', {})
        expected_samples = 6 if expected_mode == 'validate' else 2
        if len(samples) != expected_samples:
            raise ValueError('Missing saved masks: ' + name)
        for sample in samples.values():
            path = _contained(run, sample['file'])
            if path.is_symlink() or not path.is_file() or path.stat().st_size != sample.get('bytes'):
                raise ValueError('Missing/truncated saved mask: ' + name)
    files = []
    for path in sorted(run.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlinks are not exportable: ' + str(path))
        if path.is_file():
            if path.name.endswith(('.tmp', '.partial')):
                raise ValueError('A temporary/incomplete file remains in run: ' + str(path))
            if not path.resolve().is_relative_to(run.resolve()):
                raise ValueError('Source file escapes selected run')
            files.append(path)
    return ({'name': name, 'kind': kind, 'run_id': matched['run_id'], 'report_file': str(report_path.relative_to(root)).replace('\\', '/'), 'report_sha256': digest(report_path), 'state': report['state'], 'source_passed': report['passed']}, files)

def export_runs(root, names, output, home=None):
    root = Path(root).resolve()
    home = Path(home or Path.home()).resolve()
    if not names or len(names) != len(set(names)):
        raise ValueError('Provide a nonempty explicit list of distinct run directory names')
    output = Path(output).expanduser()
    if not output.is_absolute():
        output = home / output
    output = output.resolve()
    sidecar = output.with_suffix(output.suffix + '.sha256')
    if output.suffix.lower() != '.zip' or not output.is_relative_to(home) or output.is_relative_to(root) or (not output.parent.is_dir()):
        raise ValueError('New ZIP must be inside the current user home, with an existing parent')
    if output.exists() or sidecar.exists():
        raise FileExistsError('Refusing to overwrite ZIP or SHA256 sidecar')
    selections = []
    files = []
    for name in names:
        info, selected = collect_run(root, name)
        selections.append(info)
        files.extend(selected)
    manifest_path = root / 'new_package_manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('Root frozen new_package_manifest.json is missing')
    _json(manifest_path)
    files.append(manifest_path)
    entries = []
    for path in sorted(files):
        entries.append({'path': root.name + '/' + path.relative_to(root).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest(path)})
    export_manifest = {'schema': 'pcb-b-closure-evidence-export/0.3.2', 'created_utc': datetime.now(timezone.utc).isoformat(), 'package': root.name, 'selected_runs': selections, 'files': entries, 'file_count': len(entries), 'total_uncompressed_source_bytes': sum((entry['bytes'] for entry in entries)), 'root_manifest_sha256': digest(manifest_path), 'compression': 'ZIP_DEFLATED level 1', 'exporter_source_sha256': digest(Path(__file__).resolve()), 'hardware_accessed_by_exporter': False, 'scope': 'Read-only export of named completed source runs; independent PC audit still required'}
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        for path, entry in zip(sorted(files), entries):
            archive.write(path, entry['path'], compress_type=zipfile.ZIP_DEFLATED, compresslevel=1)
        archive.writestr('EXPORT_MANIFEST.json', json.dumps(export_manifest, ensure_ascii=False, indent=2), compress_type=zipfile.ZIP_DEFLATED, compresslevel=1)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('Export ZIP CRC failed')
        if len(archive.infolist()) != len(entries) + 1:
            raise ValueError('Export ZIP entry count differs')
        for entry in entries:
            h = hashlib.sha256()
            size = 0
            with archive.open(entry['path']) as stream:
                for block in iter(lambda: stream.read(1048576), b''):
                    h.update(block)
                    size += len(block)
            if size != entry['bytes'] or h.hexdigest() != entry['sha256']:
                raise ValueError('Source changed while exporting: ' + entry['path'])
    for expected in selections:
        current, _ = collect_run(root, expected['name'])
        if current != expected:
            raise ValueError('Run changed while exporting: ' + expected['name'])
    if digest(manifest_path) != export_manifest['root_manifest_sha256']:
        raise ValueError('Root manifest changed while exporting')
    zip_hash = digest(output)
    with sidecar.open('x', encoding='ascii') as stream:
        stream.write(zip_hash + '  ' + output.name + '\n')
    return {'state': 'export_complete', 'zip': str(output), 'zip_bytes': output.stat().st_size, 'sha256_first32': zip_hash[:32], 'sha256_sidecar': str(sidecar), 'source_files': len(entries), 'runs': names, 'crc_and_all_payload_hashes_verified': True, 'hardware_accessed': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', required=True, help='Explicit completed evidence directory names')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or Path.home() / ('B_closure_export_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ') + '.zip')
    result = export_runs(ROOT, args.runs, output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
