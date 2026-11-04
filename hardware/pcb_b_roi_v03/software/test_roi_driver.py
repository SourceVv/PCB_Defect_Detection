import copy
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import numpy as np
from roi_driver import ABIError, BusyError, ConfigurationError, MetadataError, ROIValidationError, RoiDriver, _validate_roi_description
from roi_reference import audit, reference
from board_validation import validate_board
from transport import DMATimeout, PoisonedError
from test_transport import Runtime, FakeDMA, valid_ip_dict

class RoiControl:

    def __init__(self, runtime):
        self.runtime = runtime
        self.control = 384
        self.abi, self.count, self.flags, self.ack = (3, 0, 0, 0)
        self.cfg_error, self.frame_error = (False, False)

    def read(self, offset):
        self.runtime.events.append(('gpio_read', offset))
        assert offset == 8, 'Output control must not be read for shadow recovery'
        return self.count << 16 | self.abi << 8 | self.flags | self.ack << 2 | int(self.cfg_error) << 3 | int(self.frame_error) << 4

    def write(self, offset, value):
        self.runtime.events.append(('gpio_write', offset, value))
        assert offset == 0 and value & ~16719871 == 0
        self.control = value
        token = value >> 12 & 1
        if value & 512:
            self.count = self.flags = 0
            self.ack = token
            self.cfg_error = self.frame_error = False
            self.runtime.cfg.results = {2: 0, 3: 0}
            return
        if token != self.ack:
            cfg = self.runtime.cfg
            if cfg.drop_ack:
                self.flags |= 1
                return
            self.ack = token
            if self.flags or cfg.reject_next:
                cfg.reject_next = False
                self.cfg_error = True
                return
            address = value >> 16 & 255
            if address in (0, 1) or 16 <= address < 16 + 17 * 4:
                cfg.registers[address] = cfg.data
            else:
                self.cfg_error = True

class RoiConfig:

    def __init__(self, runtime):
        self.runtime = runtime
        self.data = 0
        self.registers = {i: 0 for i in [0, 1] + list(range(16, 16 + 17 * 4))}
        self.results = {2: 0, 3: 0}
        self.drop_ack = self.reject_next = self.corrupt_readback = False

    def write(self, offset, value):
        self.runtime.events.append(('cfg_write', offset, value))
        assert offset == 0
        self.data = value

    def read(self, offset):
        self.runtime.events.append(('cfg_read', offset))
        assert offset == 8, 'Readback comes from cfg_result input, never data output'
        address = self.runtime.gpio.control >> 16 & 255
        value = self.results.get(address, self.registers.get(address, 0))
        return value ^ int(self.corrupt_readback)

class RoiDMA(FakeDMA):

    def write(self, offset, value):
        super().write(offset, value)
        if offset != 40 or not self.complete or self.error_status:
            return
        runtime, cfg = (self.runtime, self.runtime.cfg)
        width, height = (cfg.registers[0] & 65535, cfg.registers[0] >> 16)
        raw = np.array(runtime.buffers_by_address[self.regs[24]], copy=True).reshape(height, width)
        rois = []
        for index in range(17):
            box = [cfg.registers[16 + 4 * index + field] for field in range(4)]
            if box[2] > box[0] and box[3] > box[1]:
                rois.append({'name': str(index), 'box': box, 'enabled': bool(cfg.registers[1] & 1 << index)})
        control = runtime.gpio.control
        output, stats = reference(raw, rois, threshold=control & 255, invert=bool(control & 1024), roi_enable=bool(control & 2048), bypass=bool(control & 256))
        runtime.buffers_by_address[self.regs[72]][:] = output.reshape(-1)
        cfg.results = {2: stats['output_white_pixels'], 3: stats['valid_pixels']}
        for index, count in enumerate(stats['roi_counts_by_index']):
            cfg.results[128 + index] = count
        runtime.gpio.frame_error = runtime.force_frame_error

class RoiRuntime(Runtime):

    def __init__(self):
        super().__init__()
        self.gpio = RoiControl(self)
        self.cfg = RoiConfig(self)
        self.dma = RoiDMA(self)
        self.force_frame_error = False

    def driver(self):
        return RoiDriver(self.dma, self.gpio, self.cfg, self.allocate, timeout=0.05, poll_interval=0.01, clock=self.clock.monotonic, sleep=self.clock.sleep)

def roi_ip_dict():
    result = valid_ip_dict()
    cfg = copy.deepcopy(result['axi_gpio_ctrl'])
    cfg['phys_addr'] = 1092681728
    cfg['parameters']['C_DOUT_DEFAULT'] = '0'
    result['axi_gpio_cfg'] = cfg
    return result

class RoiTests(unittest.TestCase):

    def setUp(self):
        self.runtime = RoiRuntime()
        self.driver = self.runtime.driver()
        self.image = np.array([0, 134, 135, 136, 255, 1, 2, 3] * 4, np.uint8).reshape(4, 8)
        self.rois = [{'name': 'a', 'box': [0, 1, 5, 3]}, {'name': 'b', 'box': [3, 0, 8, 2]}, {'name': 'off', 'box': [1, 1, 7, 4], 'enabled': False}]

    def ready(self):
        self.driver.recover()
        self.driver.configure(8, 4, self.rois)

    def test_requires_recovery_and_configuration_before_allocation(self):
        with self.assertRaises(ConfigurationError):
            self.driver.configure(8, 4, self.rois)
        with self.assertRaises(ConfigurationError):
            self.driver.transfer(self.image)
        self.assertFalse(self.runtime.buffers)

    def test_wrong_abi_rejected_without_allocation(self):
        self.runtime.gpio.abi = 2
        with self.assertRaises(ABIError):
            self.runtime.driver()
        self.assertFalse(self.runtime.buffers)

    def test_geometry_roi_validation_and_transpose(self):
        for w, h, rois in [(0, 4, []), (8, True, []), (65535, 65535, []), (3, 3, []), (8, 4, [{'name': 'bad', 'box': [0, 0, 9, 1]}]), (8, 4, self.rois * 6)]:
            with self.subTest(w=w, h=h):
                with self.assertRaises(ValueError):
                    self.driver.configure(w, h, rois)
        self.ready()
        with self.assertRaises(ValueError):
            self.driver.transfer(self.image.T)
        self.assertFalse(self.runtime.buffers)

    def test_data_then_toggle_ack_readback_and_shadow_preserved(self):
        self.ready()
        writes = [e for e in self.runtime.events if e[0] in ('cfg_write', 'gpio_write')]
        for index, event in enumerate(writes):
            if event[0] == 'cfg_write':
                self.assertEqual(writes[index + 1][0], 'gpio_write')
        self.assertEqual(self.runtime.cfg.registers[0], 8 | 4 << 16)
        self.assertEqual(self.runtime.cfg.registers[1], 3)
        old_token = self.driver._ctrl_shadow & RoiDriver.CFG_TOGGLE
        self.driver._write_control(120, False)
        self.assertEqual(self.driver._ctrl_shadow & RoiDriver.CFG_TOGGLE, old_token)
        self.assertEqual(self.driver._ctrl_shadow & 3072, 3072)

    def test_overlap_counts_mask_and_release_before_return(self):
        self.ready()
        got = self.driver.transfer(self.image)
        stats = self.driver.read_results()
        expected, want = reference(self.image, self.rois)
        np.testing.assert_array_equal(got, expected)
        self.assertEqual(stats['roi_counts'], want['roi_counts'])
        self.assertEqual(stats['output_white_pixels'], want['output_white_pixels'])
        self.assertTrue(all((b.freed for b in self.runtime.buffers)))
        self.assertEqual(self.driver.last_transfer['packet_delta'], 1)

    def test_bypass_ignores_modes_and_counter_zero(self):
        self.ready()
        got = self.driver.transfer(self.image.reshape(-1), bypass=True)
        np.testing.assert_array_equal(got, self.image.reshape(-1))
        stats = self.driver.read_results()
        self.assertEqual(stats['output_white_pixels'], 0)
        self.assertEqual(sum(stats['roi_counts_by_index']), 0)

    def test_full_output_still_counts_enabled_rois(self):
        self.driver.recover()
        self.driver.configure(8, 4, self.rois, roi_enable=False)
        got = self.driver.transfer(self.image)
        stats = self.driver.read_results()
        expected, want = reference(self.image, self.rois, roi_enable=False)
        np.testing.assert_array_equal(got, expected)
        self.assertEqual(stats['roi_counts'], want['roi_counts'])

    def test_soft_reset_preserves_configuration_but_invalidates_results(self):
        self.ready()
        self.driver.transfer(self.image)
        before = dict(self.runtime.cfg.registers)
        self.driver.recover()
        self.assertEqual(self.runtime.cfg.registers, before)
        with self.assertRaises(ConfigurationError):
            self.driver.read_results()
        self.driver.transfer(self.image)
        self.assertEqual(self.driver.read_results()['status']['completed_packets'], 1)

    def test_config_rejection_timeout_readback_disable_transfer(self):
        for mode in ('reject_next', 'drop_ack', 'corrupt_readback'):
            runtime = RoiRuntime()
            driver = runtime.driver()
            driver.recover()
            setattr(runtime.cfg, mode, True)
            with self.subTest(mode=mode):
                with self.assertRaises((ConfigurationError, DMATimeout)):
                    driver.configure(8, 4, self.rois)
                self.assertFalse(driver._configured)
                self.assertFalse(runtime.buffers)

    def test_busy_configuration_writes_nothing(self):
        self.driver.recover()
        self.runtime.gpio.flags = 3
        before = len(self.runtime.events)
        with self.assertRaises(BusyError):
            self.driver.configure(8, 4, self.rois)
        self.assertFalse(any((e[0] in ('gpio_write', 'cfg_write') for e in self.runtime.events[before:])))

    def test_completed_frame_error_retains_actual_copy_and_safe_buffers(self):
        self.ready()
        self.runtime.force_frame_error = True
        with self.assertRaises(ROIValidationError) as caught:
            self.driver.transfer(self.image)
        self.assertEqual(caught.exception.actual_output.shape, self.image.shape)
        self.assertTrue(all((b.freed for b in self.runtime.buffers)))

    def test_poisoned_dma_retains_buffers_and_blocks_configuration(self):
        self.ready()
        self.runtime.dma.complete = False
        self.runtime.dma.stuck_after_launch = True
        with self.assertRaises(PoisonedError):
            self.driver.transfer(self.image)
        self.assertEqual(self.driver.retained_buffer_count, 2)
        self.assertFalse(any((b.freed for b in self.runtime.buffers)))
        with self.assertRaises(PoisonedError):
            self.driver.configure(8, 4, self.rois)

class FactoryAndReferenceTests(unittest.TestCase):

    def test_hwh_extra_gpio_mode_default_and_overlap(self):
        valid = roi_ip_dict()
        self.assertEqual(_validate_roi_description(valid)['axi_gpio_cfg'][0], 1092681728)
        swapped = copy.deepcopy(valid)
        swapped['axi_gpio_ctrl']['phys_addr'], swapped['axi_gpio_cfg']['phys_addr'] = (1092681728, 1092616192)
        self.assertEqual(_validate_roi_description(swapped)['axi_gpio_ctrl'][0], 1092681728)
        self.assertEqual(_validate_roi_description(swapped)['axi_gpio_cfg'][0], 1092616192)
        for field, value in [('C_ALL_INPUTS_2', '0'), ('C_DOUT_DEFAULT', '1')]:
            bad = copy.deepcopy(valid)
            bad['axi_gpio_cfg']['parameters'][field] = value
            with self.assertRaises(MetadataError):
                _validate_roi_description(bad)
        bad = copy.deepcopy(valid)
        bad['axi_gpio_cfg']['phys_addr'] = 1092616192
        with self.assertRaises(MetadataError):
            _validate_roi_description(bad)

    def test_factory_validates_before_download(self):
        runtime, events = (RoiRuntime(), [])
        description = roi_ip_dict()

        class Overlay:

            def __init__(self, filename, download):
                self.ip_dict = description
                events.append(('construct', download))

            def download(self):
                events.append(('download',))

        def mmio(base, size):
            return {1077936128: runtime.dma, 1092616192: runtime.gpio, 1092681728: runtime.cfg}[base]
        fake = types.SimpleNamespace(Overlay=Overlay, MMIO=mmio, allocate=runtime.allocate)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.bit'
            path.write_bytes(b'fixture')
            path.with_suffix('.hwh').write_text('fixture')
            with patch.dict(sys.modules, {'pynq': fake}):
                driver = RoiDriver.from_bitfile(path)
                self.assertIsInstance(driver, RoiDriver)
                self.assertEqual(events, [('construct', False), ('download',)])
                events.clear()
                description['axi_gpio_cfg']['parameters']['C_DOUT_DEFAULT'] = '1'
                with self.assertRaises(MetadataError):
                    RoiDriver.from_bitfile(path)
                self.assertEqual(events, [('construct', False)])

    def test_real_profiles_and_original_mask(self):
        result = audit()
        self.assertEqual(len(result['cases']), 8)
        latest = result['cases'][-2]
        frozen = result['cases'][-1]
        self.assertEqual(latest['expected_stats']['output_white_pixels'], 326860)
        self.assertEqual(latest['expected_stats']['roi_union_pixels'], 389266)
        self.assertEqual(frozen['expected_stats']['output_white_pixels'], 314801)
        self.assertTrue(result['frozen16_vs_original_A']['match'])

    def test_reference_rejects_invalid_pixel_contract(self):
        image = np.zeros((4, 8), dtype=np.uint8)
        for threshold in (True, 120.0, -1, 256):
            with self.assertRaises(ValueError):
                reference(image, [], threshold=threshold)
        with self.assertRaises(ValueError):
            reference(image, [{'name': str(i), 'box': [0, 0, 1, 1]} for i in range(18)])
        with self.assertRaises(ValueError):
            reference(image, [{'name': 'x', 'box': [0.0, 0, 1, 1]}])

    def test_eight_case_controller_with_mock_not_physical_board(self):
        runtime = RoiRuntime()
        driver = runtime.driver()
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            report = validate_board(driver, root, Path(directory) / 'mock_run')
            self.assertTrue(report['passed'])
            self.assertEqual(len(report['records']), 8)
            self.assertEqual(report['final_status']['completed_packets'], 8)
            self.assertEqual(report['records'][-1]['original_A_mask_check']['mismatches'], 0)
            self.assertTrue(all((r['pixel_check']['mismatches'] == 0 for r in report['records'])))

    def test_config_failure_does_not_label_previous_transfer_current(self):
        import json
        runtime = RoiRuntime()
        driver = runtime.driver()
        configure = driver.configure
        calls = []

        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise ConfigurationError('synthetic second configure failure')
            return configure(*args, **kwargs)
        driver.configure = fail_second
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mock_run'
            with self.assertRaises(ConfigurationError):
                validate_board(driver, root, path)
            report = json.loads((path / 'board_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['state'], 'failed')
            self.assertEqual(report['current_attempt']['case'], 'bypass_64')
            self.assertEqual(len(report['records']), 1)
            self.assertFalse(report['last_driver_transfer_is_current_attempt'])
if __name__ == '__main__':
    unittest.main(verbosity=2)
