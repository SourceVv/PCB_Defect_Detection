import argparse
import json
from pathlib import Path
import platform
import sys
import cv2
import numpy as np
from .core import detect, load_config, reference_path
from .evaluation import evaluate
from .golden import generate, verify
from .io import read_image, write_image
from .roi_preview import preview_rois

def emit(value, path=None):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding='utf-8')
    print(text)

def main(argv=None):
    parser = argparse.ArgumentParser(description='Fixed-board PCB software inspection baseline')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('detect', 'preview-rois', 'evaluate', 'doctor', 'make-golden', 'verify-golden'):
        command = commands.add_parser(name)
        command.add_argument('--config', required=True, type=Path)
        if name == 'detect':
            command.add_argument('--image', required=True, type=Path)
            command.add_argument('--out', required=True, type=Path, help='Directory for JSON and annotated PNG')
        elif name == 'preview-rois':
            command.add_argument('--image', type=Path, help='Default: the configured reference image')
            command.add_argument('--out', required=True, type=Path)
        elif name == 'evaluate':
            command.add_argument('--input', required=True, type=Path)
            command.add_argument('--truth', required=True, type=Path)
            command.add_argument('--out', required=True, type=Path)
        elif name == 'make-golden':
            command.add_argument('--out', required=True, type=Path)
        elif name == 'verify-golden':
            command.add_argument('--golden', required=True, type=Path)
            command.add_argument('--actual-mask', type=Path)
            command.add_argument('--actual-stats', type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == 'doctor':
            reference = read_image(reference_path(config))
            emit(dict(python=platform.python_version(), executable=sys.executable, opencv=cv2.__version__, numpy=np.__version__, config_version=config['version'], reference=str(reference_path(config)), reference_shape=list(reference.shape), backend='software'))
        elif args.command == 'detect':
            result, annotated = detect(read_image(args.image), config)
            write_image(args.out / 'annotated.png', annotated)
            emit(result, args.out / 'result.json')
            return 0 if result['image_ok'] else 2
        elif args.command == 'preview-rois':
            result, preview, crops = preview_rois(read_image(args.image or reference_path(config)), config)
            write_image(args.out / 'rois.png', preview)
            write_image(args.out / 'crops.png', crops)
            emit(result, args.out / 'roi_boxes.json')
            return 0 if all((item['inside_image'] for item in result['items'])) else 2
        elif args.command == 'evaluate':
            summary = evaluate(config, args.input, args.truth, args.out)
            emit(summary)
            return 0 if summary['correct'] == summary['decisions'] else 1
        elif args.command == 'make-golden':
            generated = generate(args.config, args.out)
            emit(dict(config_version=generated['config_version'], output=str(args.out)))
        elif args.command == 'verify-golden':
            result = verify(args.golden, args.config, args.actual_mask, args.actual_stats)
            emit(result)
            return 0 if result['passed'] else 1
    except (ValueError, FileNotFoundError, OSError, KeyError, cv2.error) as error:
        parser.exit(2, f'Error: {error}\n')
    return 0
