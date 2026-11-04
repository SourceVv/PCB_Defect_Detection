from pathlib import Path
import tempfile
from pcb_app.general import GeneralSession
from pcb_app.ui import InventoryUI

def test_reference_and_result():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as directory:
        session = GeneralSession(directory)
        session.register("normal.png",(root/"data/reference/b01_reference.png").read_bytes())
        result = session.inspect((root/"data/development/s01/b02_s01_001.png").read_bytes())
        assert len(result["items"]) == len(session.rows)
        assert len(result["items"]) > 90
        items = {item["refdes"]:item for item in result["items"]}
        assert all(items[name]["presence"] == "缺件" for name in ("U5","X2","SW2","LED5","LED7"))
        path = session.save_profile()
        restored = GeneralSession(directory)
        restored.load_profile(path)
        assert restored.rows == session.rows
        app = InventoryUI(session)
        app.refresh()
        assert len(app.compare_select.options) == len(result["items"])
