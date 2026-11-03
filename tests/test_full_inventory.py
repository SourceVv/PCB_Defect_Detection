from pathlib import Path
import json
import tempfile
import unittest
import cv2
import numpy as np
from pcb_app.general import GeneralSession,geometry
from pcb_app.backend import BoardThreshold


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
REFERENCE = (DATA/'reference/B01_reference.png').read_bytes()


class Driver:
    def read_status(self):
        return dict(abi=3,busy=False,inpacket=False,cfg_error=False,frame_error=False)

    def recover(self):
        pass


class Service:
    calls = []
    def __init__(self,driver,**kwargs):
        self.kwargs = kwargs

    def configure(self):
        assert len(self.kwargs['rois']) == 1

    def process(self,mosaic,frame_id):
        Service.calls.append(mosaic.shape)
        return dict(frame_id=frame_id,mask=np.where(mosaic>self.kwargs['threshold'],255,0).astype(np.uint8))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = GeneralSession(self.temp.name)
        self.session.register('reference.png',REFERENCE)

    def tearDown(self):
        self.temp.cleanup()

    def test_inventory_and_known_defects(self):
        result = self.session.inspect((DATA/'development/S01/B02_S01_001.png').read_bytes())
        self.assertEqual(len(result['items']),len(self.session.rows))
        self.assertGreater(len(result['items']),90)
        names = {i['refdes']:i for i in result['items']}
        for name in ('U5','X2','SW2','LED5','LED7'):
            self.assertEqual(names[name]['presence'],'缺件')
        self.assertEqual(names['LED4']['presence'],'参考为空焊盘')
        self.assertEqual(names['LED6']['presence'],'参考为空焊盘')
        self.assertIn('U7',names)
        self.assertIn('R25',names)

    def test_profile_roundtrip(self):
        path = self.session.save_profile()
        restored = GeneralSession(self.temp.name)
        restored.load_profile(path)
        self.assertEqual(restored.rows,self.session.rows)

    def test_body_ignores_lettering(self):
        p = np.full((64,64,3),(20,110,20),np.uint8)
        cv2.rectangle(p,(8,8),(56,56),(25,25,25),-1)
        original = geometry(p,'U')['occupancy']
        cv2.putText(p,'NEW',(11,35),0,.45,(240,240,240),1)
        changed = geometry(p,'U')['occupancy']
        self.assertGreater(changed/original,.8)

    def test_capacitor_angle(self):
        def patch(angle):
            image = np.full((64,64,3),(20,110,20),np.uint8)
            points = cv2.boxPoints(((32,32),(36,14),angle)).astype(np.int32)
            cv2.fillPoly(image,[points],(100,120,140))
            return image
        a,b = geometry(patch(0),'C'),geometry(patch(25),'C')
        delta = abs(a['angle']-b['angle'])%180
        self.assertGreater(min(delta,180-delta),20)

    def test_dynamic_board_interface(self):
        hwroot = ROOT / 'hardware'
        backend = BoardThreshold(hwroot,bindings=(lambda *a,**k:Driver(),Service))
        self.session.backend = backend
        result = self.session.inspect((DATA/'development/S01/B02_S01_001.png').read_bytes())
        self.assertEqual(len(result['items']),len(self.session.rows))
        self.assertEqual(result['backend'],'FPGA threshold with ARM geometry')
        self.assertIsNotNone(result['operator']['mask'])
        self.assertGreaterEqual(len(Service.calls),2)


if __name__ == '__main__':
    unittest.main()

