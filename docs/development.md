# 后续维护

修改界面时编辑 `src/pcb_app/ui.py` 和 `appearance.py`，修改检测逻辑时编辑 `general.py`、`inventory.py` 或 `backend.py`。`legacy.py`保留配准及已标定规则；其中部分代码用于历史兼容，后续拆分时应先保留回归测试。

提交前运行 `python scripts/smoke_test.py` 和 `python -m pytest -q`。代码中不保留注释和文档字符串，新增算法原理、接口含义及操作方法写入docs。不要为了减少待确认项而把它们直接改成正常。

发布前检查 `git status`，确认没有加入虚拟环境、私有OCR令牌或运行结果。通过GitHub创建空仓库后，可添加自己的远程地址再提交推送。本次只创建本地仓库，不代团队发布到GitHub。

现有硬件发布文件与驱动需配套使用。修改RTL后必须重新构建并核对ABI、TLAST、帧字节数及实际输出；不能只替换bit而保留未经确认的hwh和驱动。
