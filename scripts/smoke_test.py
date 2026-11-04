from pathlib import Path
import json
import tempfile
from pcb_app.general import GeneralSession

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    session = GeneralSession(directory)
    session.register("reference.png",(root/"data/reference/b01_reference.png").read_bytes())
    result = session.inspect((root/"data/development/s01/b02_s01_001.png").read_bytes())
    items = {item["refdes"]:item for item in result["items"]}
    assert len(items) == len(session.rows) and len(items) > 90
    for name in ("U5","X2","SW2","LED5","LED7"):
        assert items[name]["presence"] == "缺件", name
    assert items["LED4"]["presence"] == "参考为空焊盘"
    print(json.dumps({"passed":True,"positions":len(items),"backend":"CPU","physical_fpga_tested":False},ensure_ascii=False))
