from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import numpy as np
SOFTWARE = Path(__file__).resolve().parents[1]
OUTPUTS = SOFTWARE.parents[1]

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
new = load_module('closure_service_under_test', SOFTWARE / 'aoi_roi_service.py')
baseline = load_module('closure_baseline_test_cases', OUTPUTS / 'pcb_b_integration_v031/software/tests/test_aoi_roi_service.py')
OriginalContractDriver = baseline.ContractDriver

class SafeContractDriver(OriginalContractDriver):

    def __init__(self, counter=0):
        super().__init__(counter)
        self._dma_safe, self._active = (True, False)
        self._buffers, self._retained_buffers = ([], [])

    def transfer(self, image, **kwargs):
        output = super().transfer(image, **kwargs)
        self.last_transfer.update(reset_tx_status=1, reset_rx_status=1)
        return output
baseline.ContractDriver = SafeContractDriver
baseline.SOFTWARE = SOFTWARE
for name in ('AoiRoiService', 'ResultValidationError', 'ServiceBusyError', 'ServiceStateError'):
    setattr(baseline, name, getattr(new, name))

class ExistingServiceContractRegression(baseline.ServiceTests):
    pass

class ExistingFrozenDriverRegression(baseline.FrozenDriverCompatibilityTests):
    pass

class MaskProofTests(unittest.TestCase):

    def test_all_256_values_and_mixed_arrays_exhaustively(self):
        for name, helper in new.MASK_VALIDATORS.items():
            for value in range(256):
                for image in (np.array([value], np.uint8), np.array([0, value, 255, 0], np.uint8)):
                    with self.subTest(helper=name, value=value, size=image.size):
                        if value in (0, 255):
                            self.assertEqual(helper(image), int(np.count_nonzero(image)))
                        else:
                            with self.assertRaises(new.ResultValidationError):
                                helper(image)

    def test_random_binary_and_single_intermediate_corruption(self):
        rng = np.random.default_rng(20261101)
        for trial in range(25):
            valid = rng.integers(0, 2, size=(31, 29), dtype=np.uint8) * np.uint8(255)
            invalid = valid.copy()
            invalid.reshape(-1)[int(rng.integers(0, invalid.size))] = rng.integers(1, 255)
            for name, helper in new.MASK_VALIDATORS.items():
                with self.subTest(helper=name, trial=trial):
                    self.assertEqual(helper(valid), int(np.count_nonzero(valid)))
                    with self.assertRaises(new.ResultValidationError):
                        helper(invalid)

    def test_noncontiguous_fortran_reversed_readonly_and_empty_arrays(self):
        source = np.tile(np.array([0, 255, 255, 0, 255, 0, 0, 255], np.uint8), (7, 1))
        readonly = source.copy()
        readonly.flags.writeable = False
        samples = [source[:, ::2], source.T, source[::-1, ::-1], np.asfortranarray(source), readonly, np.empty((0, 5), np.uint8)]
        for name, helper in new.MASK_VALIDATORS.items():
            for image in samples:
                with self.subTest(helper=name, shape=image.shape, strides=image.strides):
                    self.assertEqual(helper(image), int(np.count_nonzero(image)))

    def test_wrong_dtype_or_non_array_rejected_before_arithmetic(self):
        invalid = [np.array([0, 255], np.int16), np.array([0, -1], np.int8), np.array([0.0, 255.0]), np.array([False, True]), [0, 255]]
        for name, helper in new.MASK_VALIDATORS.items():
            for image in invalid:
                with self.subTest(helper=name), self.assertRaises(new.ResultValidationError):
                    helper(image)

    def test_subclass_cannot_override_binary_proof(self):

        class LyingArray(np.ndarray):

            def __array_ufunc__(self, *args, **kwargs):
                raise AssertionError('Subclass arithmetic must not be used')
        image = np.array([0, 128, 255], np.uint8).view(LyingArray)
        for helper in new.MASK_VALIDATORS.values():
            with self.assertRaises(new.ResultValidationError):
                helper(image)

    def test_validator_choices_cannot_disable_validation(self):
        driver = SafeContractDriver()
        for invalid in ('off', 'none', '', None, False):
            with self.assertRaises(ValueError):
                new.AoiRoiService(driver, width=4, height=4, rois=baseline.ROIS, mask_validation=invalid)
        self.assertEqual(driver.calls, [])
        with self.assertRaises(TypeError):
            new.MASK_VALIDATORS['off'] = lambda value: 0

class ServiceCandidateTests(unittest.TestCase):

    def ready(self, candidate='compare'):
        driver = SafeContractDriver()
        service = new.AoiRoiService(driver, width=4, height=4, rois=deepcopy(baseline.ROIS), mask_validation=candidate)
        service.configure()
        return (driver, service)

    def test_each_candidate_same_results_and_unknown_driver_always_copies(self):
        for candidate in new.MASK_VALIDATORS:
            driver, service = self.ready(candidate)
            result = service.process(baseline.IMAGE, frame_id=candidate)
            self.assertEqual(result['output_white_pixels'], 8)
            self.assertEqual(result['roi_counts'], {'left': 4, 'right': 4})
            self.assertEqual(result['mask_validation'], candidate)
            self.assertEqual(result['output_ownership'], 'copy_fallback')
            self.assertTrue(result['mask'].flags.owndata)
            self.assertTrue(result['mask'].flags.c_contiguous)
            self.assertIsNone(result['mask'].base)
            self.assertFalse(np.shares_memory(result['mask'], driver.mask))
            result['mask'][:] = 0
            self.assertEqual(np.count_nonzero(driver.mask), 8)

    def test_each_candidate_rejects_intermediate_and_wrong_white_statistic(self):
        for candidate in new.MASK_VALIDATORS:
            for corruption in (1, 128, 134, 254, 'wrong_white'):
                driver, service = self.ready(candidate)
                if corruption == 'wrong_white':
                    driver.stats_hook = lambda value: value.stats.update(output_white_pixels=7)
                else:
                    driver.mask[0, 0] = corruption
                with self.subTest(candidate=candidate, corruption=corruption), self.assertRaises(new.ResultValidationError):
                    service.process(baseline.IMAGE, frame_id=str(corruption))
                self.assertEqual(service.state, 'faulted')

    def test_post_dma_unsafe_state_faults_without_retry(self):
        corruptions = {'_dma_safe': False, '_active': True, 'poisoned': True, '_buffers': [object()], '_retained_buffers': [object()]}
        for key, value in corruptions.items():
            driver, service = self.ready()
            driver.transfer_hook = lambda target: setattr(target, key, value)
            with self.subTest(key=key), self.assertRaises(new.ResultValidationError):
                service.process(baseline.IMAGE, frame_id=key)
            before = list(driver.calls)
            with self.assertRaises(new.ServiceStateError):
                service.process(baseline.IMAGE, frame_id='after-' + key)
            self.assertEqual(driver.calls, before)
            self.assertIs(service._driver, driver)

    def test_missing_or_error_halted_record_rejected(self):
        driver, service = self.ready()
        original = driver.read_results

        def corrupt():
            driver.last_transfer['reset_rx_status'] = 113
            return original()
        driver.read_results = corrupt
        with self.assertRaises(new.ResultValidationError):
            service.process(baseline.IMAGE, frame_id='halted-with-error')

class OutputOwnershipTests(unittest.TestCase):

    @staticmethod
    def real_mock_driver(subclass=False):
        sys.path.insert(0, str(OUTPUTS / 'pcb_b_roi_v03/software'))
        from test_roi_driver import RoiRuntime
        from roi_driver import RoiDriver
        runtime = RoiRuntime()
        if subclass:

            class DerivedDriver(RoiDriver):
                pass
            driver = DerivedDriver(runtime.dma, runtime.gpio, runtime.cfg, runtime.allocate, timeout=0.05, poll_interval=0.01, clock=runtime.clock.monotonic, sleep=runtime.clock.sleep)
        else:
            driver = runtime.driver()
        driver.recover()
        return (runtime, driver)

    def create_real(self, subclass=False):
        runtime, driver = self.real_mock_driver(subclass)
        service = new.AoiRoiService(driver, width=8, height=4, rois=[{'name': 'whole', 'box': [0, 0, 8, 4]}])
        service.configure()
        return (runtime, driver, service)

    def test_frozen_driver_transfers_full_owning_base_without_data_copy(self):
        runtime, driver, service = self.create_real()
        self.assertTrue(service._ownership_proof)
        captured = []
        original = service._return_output

        def capture(mask):
            captured.append(mask)
            return original(mask)
        service._return_output = capture
        result = service.process(np.zeros((4, 8), np.uint8), frame_id='zero-copy-mock')
        self.assertEqual(result['output_ownership'], 'frozen_driver_owning_base')
        self.assertTrue(result['mask'].flags.owndata)
        self.assertTrue(result['mask'].flags.c_contiguous)
        self.assertIsNone(result['mask'].base)
        self.assertIs(captured[0].base, result['mask'])
        self.assertTrue(np.shares_memory(captured[0], result['mask']))
        self.assertTrue(driver._dma_safe)
        self.assertFalse(driver._buffers)
        self.assertTrue(all((buffer.freed for buffer in runtime.buffers)))
        self.assertTrue(all((not np.shares_memory(result['mask'], buffer) for buffer in runtime.buffers)))

    def test_subclass_and_source_mismatch_fall_back_to_owned_copy(self):
        _, _, service = self.create_real(subclass=True)
        self.assertIsNone(service._ownership_proof)
        result = service.process(np.zeros((4, 8), np.uint8), frame_id='subclass')
        self.assertEqual(result['output_ownership'], 'copy_fallback')
        _, driver = self.real_mock_driver()
        with patch.dict(new._FROZEN_SOURCE_HASHES, {'roi_driver': '0' * 64}):
            service = new.AoiRoiService(driver, width=8, height=4, rois=[])
        service.configure()
        self.assertIsNone(service._ownership_proof)
        self.assertEqual(service.process(np.zeros((4, 8), np.uint8), frame_id='wrong-source')['output_ownership'], 'copy_fallback')

    def test_method_replacement_and_retained_alias_cannot_bypass_copy(self):
        _, driver, service = self.create_real()
        original = driver.transfer

        def retained(this, image, **kwargs):
            output = original(image, **kwargs)
            this.retained_output_alias = output
            return output
        driver.transfer = types.MethodType(retained, driver)
        result = service.process(np.zeros((4, 8), np.uint8), frame_id='patched-instance')
        self.assertEqual(result['output_ownership'], 'copy_fallback')
        self.assertFalse(np.shares_memory(result['mask'], driver.retained_output_alias))
        result['mask'][:] = 0
        self.assertEqual(np.count_nonzero(driver.retained_output_alias), 32)

    def test_class_method_replacement_after_initial_proof_cannot_bypass_copy(self):
        _, driver, service = self.create_real()
        self.assertTrue(service._ownership_proof)
        cls = type(driver)
        original = cls.transfer

        def replacement(this, image, **kwargs):
            return original(this, image, **kwargs)
        with patch.object(cls, 'transfer', replacement):
            result = service.process(np.zeros((4, 8), np.uint8), frame_id='patched-class')
        self.assertEqual(result['output_ownership'], 'copy_fallback')

    def test_partial_external_fortran_readonly_and_subclass_owners_not_promoted(self):
        owner = np.zeros(33, np.uint8)
        readonly = np.zeros((4, 8), np.uint8)
        readonly.flags.writeable = False

        class DerivedArray(np.ndarray):
            pass
        rejected = [owner[1:].reshape(4, 8), np.frombuffer(bytearray(32), np.uint8).reshape(4, 8), np.asfortranarray(np.zeros((4, 8), np.uint8)), readonly, np.zeros((4, 8), np.uint8).view(DerivedArray)]
        for image in rejected:
            with self.subTest(shape=image.shape, strides=image.strides):
                self.assertIsNone(new._full_owning_base(image))
if __name__ == '__main__':
    unittest.main(verbosity=2)
