from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from pcb_inspection import core
from pcb_inspection.evaluation import evaluate
from pcb_inspection.golden import compare_mask, generate, threshold_mask, verify
from pcb_inspection.io import read_image, write_image
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/roi_config.json'
RAW = ROOT / 'data/raw/S01'

class CoreTests(unittest.TestCase):

    def setUp(self):
        self.config = core.load_config(CONFIG)

    def test_reference_is_config_relative(self):
        self.assertEqual(core.reference_path(self.config), (RAW / 'S01_B01_1.jpg').resolve())

    def test_invalid_images_are_not_classified(self):
        result, _ = core.detect(np.full((1900, 2533, 3), 255, np.uint8), self.config)
        self.assertFalse(result['image_ok'])
        self.assertTrue(all((x['status'] == 'invalid' for x in result['items'])))

    def test_unsupported_hardware_never_silently_runs_software(self):
        with self.assertRaises(NotImplementedError):
            core.detect(np.zeros((20, 20), np.uint8), self.config, backend='fpga')

    def test_unimplemented_tasks_are_uncertain(self):
        result, _ = core.detect(read_image(RAW / 'S01_B01_1.jpg'), self.config)
        tilt = [x for x in result['items'] if x['task'] == 'tilt']
        self.assertEqual(len(tilt), 3)
        self.assertTrue(all((x['status'] == 'uncertain' for x in tilt)))

    def test_threshold_boundaries(self):
        config = {'rois': {'R': dict(type='missing', box=[0, 0, 3, 1], feature='area_inv', invert=True, threshold_inv=120)}}
        features, mask = core.process_reference(np.array([[136, 135, 134]], np.uint8), config, 'R')
        np.testing.assert_array_equal(mask, [[0, 0, 255]])
        self.assertEqual(features['feat'], 1)

    def test_clipped_roi_is_rejected(self):
        config = {'rois': {'R': dict(box=[0, 0, 11, 10], feature='area_inv')}}
        with self.assertRaisesRegex(ValueError, 'roi_outside_image'):
            core.process_reference(np.zeros((10, 10), np.uint8), config, 'R')

    def test_resizing_uses_config_coordinates_and_original_annotation(self):
        config = deepcopy(self.config)
        config['rois'] = {'T': dict(box=[10, 20, 110, 120], type='tilt')}
        with patch('pcb_inspection.core.align_homography', return_value=(np.eye(3), 100)):
            result, image = core.detect(np.zeros((950, 5066, 3), np.uint8), config)
        self.assertTrue(result['image_ok'])
        self.assertEqual(result['dx'], 0)
        self.assertEqual(result['dy'], 0)
        self.assertEqual(result['items'][0]['original_box'], [20, 10, 220, 60])
        self.assertEqual(image.shape, (950, 5066, 3))

    def test_unicode_image_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '中文 路径' / '样本.png'
            image = np.full((5, 7, 3), 42, np.uint8)
            write_image(path, image)
            np.testing.assert_array_equal(read_image(path), image)

class GoldenTests(unittest.TestCase):

    def test_mask_black_white_and_roi_boundary(self):
        gray = np.array([[0, 255, 134, 135, 136]], np.uint8)
        config = {'rois': {'R': dict(box=[0, 0, 4, 1], type='missing')}}
        np.testing.assert_array_equal(threshold_mask(gray, config), [[255, 0, 255, 0, 0]])

    def test_changed_pixel_reports_position(self):
        expected = np.zeros((3, 4), np.uint8)
        actual = expected.copy()
        actual[1, 2] = 255
        result = compare_mask(expected, actual)
        self.assertFalse(result['match'])
        self.assertEqual(result['mismatch_pixels'], 1)
        self.assertEqual(result['first_yx'], [1, 2])

    def test_golden_detects_corrupt_feature_and_mask(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            generate(CONFIG, folder)
            result = verify(folder, CONFIG)
            self.assertTrue(result['passed'], result)
            self.assertFalse(result['hardware_validated'])
            stats = json.loads((folder / 'expected_stats.json').read_text(encoding='utf-8'))
            stats['items'][0]['feature_value'] += 50
            (folder / 'expected_stats.json').write_text(json.dumps(stats), encoding='utf-8')
            result = verify(folder, CONFIG)
            self.assertIn('feature_mismatch:U1', result['errors'])
            saved = np.load(folder / 'expected_mask.npy', allow_pickle=False)
            saved[5, 6] ^= 255
            saved.tofile(folder / 'changed.bin')
            result = verify(folder, CONFIG, actual_mask=folder / 'changed.bin')
            self.assertEqual(result['hardware_mask']['first_yx'], [5, 6])
            self.assertIn('hardware_mask_mismatch', result['errors'])

class DataTests(unittest.TestCase):

    def test_development_replay_252_decisions(self):
        with tempfile.TemporaryDirectory() as temp:
            result = evaluate(core.load_config(CONFIG), RAW, ROOT / 'data/labels/component_truth.csv', temp)
        self.assertEqual(result['decisions'], 252)
        self.assertEqual((result['TP'], result['TN'], result['FP'], result['FN']), (42, 210, 0, 0))
        self.assertEqual(result['invalid'], 0)

    def test_data_checksums(self):
        import csv
        with (ROOT / 'data/labels/photos.csv').open(encoding='utf-8', newline='') as file:
            photos = list(csv.DictReader(file))
        self.assertEqual(len(photos), 18)
        for row in photos:
            self.assertEqual(hashlib.sha256((RAW / row['filename']).read_bytes()).hexdigest(), row['sha256'])

    def test_truth_coverage_fails_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            (temp / 'raw').mkdir()
            shutil.copy2(RAW / 'S01_B01_1.jpg', temp / 'raw/S01_B01_1.jpg')
            (temp / 'truth.csv').write_text('filename,refdes,state\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Truth coverage mismatch'):
                evaluate(core.load_config(CONFIG), temp / 'raw', temp / 'truth.csv', temp / 'out')
if __name__ == '__main__':
    unittest.main()
