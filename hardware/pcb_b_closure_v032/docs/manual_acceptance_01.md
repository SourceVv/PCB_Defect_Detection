# B剩下三项怎样完成：新手操作手册

归档日期：2026-11-02。板卡性能与连续运行的实测批次为2026-11-01。以下是尚需真实人员补齐的动作；本文件是说明，不代表这些动作已经完成。

## 1. 先确认哪些不用重做

B的六类核心交付物均有证据，66次新旧DMA对照通过，固定黄金图连续1800.478687秒、3439帧、0错误。你已确认做过JTAG点灯并保留照片或视频，不要求为了补文档重新点灯。

新服务中位数275.906ms，旧服务437.332ms，减少36.91%；CPU参考235.175ms。报告必须保留“仍慢于本次CPU参考”的结论。原开发数据有1个SAD误报，不能写成已经全部分类正确。

## 2. 第一项：归档已有点灯照片或视频

1. 在电脑打开完整包解压目录，进入 `pcb_b_closure_v032/evidence/manual_led_archive_pending/`。已有user_confirmation.json是你的文字确认，不是照片本身。
2. 将已有原始照片/视频复制进来。不要修改原拍摄内容。若有SW0两个位置的照片，分别保留；若是连续视频，保留能看清SW0变化和LD0响应的一段及原文件。没有的画面不要补写为已有。
3. 新建 `实物记录说明.txt`，填写：板卡型号；实际操作日期（不确定就写不确定）；所用bit文件；由谁操作；SW0在哪两个位置；LD0实际如何变化；对应照片/视频文件名。实际高低电平应以所用RTL和画面为准，不凭开关方向猜测。
4. 由本人或队友打开原始媒体，确认能看清板卡、开关和灯；记录谁核对、何时核对。如果看不清，原样说明证据不足，不把它标为独立验收通过。
5. 原ZIP是冻结快照。补入文件后另存补充包或新的交付版本，保留本次ZIP和SHA；不要只替换媒体而继续沿用旧的最终清单。

完成标准：原始媒体+实际操作说明+查看核对记录齐全。不会因为只有构建成功日志就自动完成这一项。

## 3. 第二项：留存原project_1或明确替代依据

1. 在最初保存工程的电脑找原 `project_1` 文件夹，其中通常有 `.xpr` 工程文件以及 `.srcs` 源文件目录。你此前说明相关工程在另一台电脑上，先向保管人取得原文件。
2. 整个工程复制到英文路径留档，保留原件。用Vivado选择“Open Project”打开副本中的 `.xpr`，记录原版本和器件。如果提示升级，先保留完整原副本，再在副本里操作，记录版本变化。
3. 保存能说明工程名称、器件和源文件的截图，另写来源、用途、打开日期和操作人。这里只追溯原工程，不必为了截图重新综合正式ROI硬件。
4. 归档到 `pcb_b_closure_v032/evidence/original_project_1_archive_pending/`，大工程也可单独保存压缩包并写真实位置和SHA。
5. 若原工程确实拿不到，记录“原工程未取得”及原因，交队内确认是否接受现有自检工程替代。替代应写清名称、源文件和实际验证范围，不能把正式ROI工程改名后声称已经打开过原project_1。

完成标准：原工程的留存/打开记录；或团队明确接受的替代记录。当前本包不含“已经打开原project_1”的证据。

## 4. 第三项：由另一位队友实际复现

必须由A或C本人操作并记录；阅读已有输出与重新进行硬件验证是两件不同的事。先把完整ZIP及旁边的 `.sha256` 文件交给队友，保持解压后的几个工程目录相邻。

### 4.1 先看结果，熟悉目录

打开板上Jupyter，进入 `pcb_b_closure_v032/software/`，打开 `acceptance_results_07.ipynb` 看已保存输出，或打开 `acceptance_results_08.ipynb` 用Shift+Enter逐格读取。两个查看器都不加载FPGA、不访问DMA，所以这一步只是了解历史结果，不算队友硬件复现。

### 4.2 准备真正硬件复现

让其他正在控制FPGA/DMA的Notebook停止操作。保持板卡供电和网络稳定。以下命令会重新加载配套bit/hwh并使用DMA，应由实际复现者在独占板卡时执行。原测试已经结束，毋须同时开另一份长时任务。

Jupyter首页点右上角New，再选Terminal。先输入：

```text
cd /home/xilinx/jupyter_notebooks/pcb_b_closure_v032
/usr/local/share/pynq-venv/bin/python3 -c "import uuid; print('20261101_' + uuid.uuid4().hex[:8])"
```

第二行只产生一个新的编号。把打印出来的编号复制下来。编号前缀是冻结脚本的实验系列日期，不是本次实际操作日期；实际日期应另填复现表。

下面先查看执行计划。把 `20261101_替换为刚才8位字符` 整段换成实际编号，不要保留中文占位符：

```text
/usr/local/share/pynq-venv/bin/python3 tools/board_closure_worker.py --mode validate --candidate bytes_count --run-id 20261101_替换为刚才8位字符
```

应看到 `state: plan_only`、`hardware_executed: false` 和计划66次传输。若提示文件摘要不符或编号错误，先解决具体报错，不修改冻结清单来绕过。

### 4.3 再实际执行66次对照

使用同一个新编号执行下一条；整条在终端中输入一行：

```text
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 tools/board_closure_worker.py --mode validate --candidate bytes_count --run-id 20261101_替换为刚才8位字符 --execute
```

若要求密码，输入板卡管理员密码后回车，终端不显示输入字符是正常现象。运行过程中不要再启动控制DMA的Notebook。程序包含3轮预热和30轮正式对照，每轮旧新各一次实际DMA，共66次；CPU参考另行计算，不计入DMA次数。

程序会建立新的 `evidence/closure_validate_实际编号/`。不应覆盖旧run；如果目录已存在，重新生成编号。

### 4.4 看懂并保存结果

在Jupyter文件列表找到本次新目录，打开 `report.json`，检查：`state` 为 `complete`、`passed` 为 `true`、`validated_hardware_frames` 为66；同时保留 `records.jsonl` 和程序保存的六个mask样本。失败时保留原文件和报错，不修改passed值。

把完整新run目录复制到电脑。用本包的 `tools/verify_closure_results.py` 的validate模式复核，明确指定相邻的ROI、closure和integration目录；输出应 `audit_passed: true`。可参考同目录的independent_audit_10.md。在板上不方便下载目录时，可从Jupyter的Terminal用zip归档，再从文件列表下载；不要只下载report.json而漏掉样本和JSONL。

填写 `docs/reproduction_template_06.md`：实际操作人、真实日期和时区、版本、收到的ZIP和摘要、本次新编号、原始结果、电脑审计结果和遇到的问题。队友不能只在已有3439帧记录旁签字就当成新的实测。

完成标准：另一位成员本人操作的新run+完整原始证据+独立复核+本人填写记录。一般不需要为了这项交接再跑一遍B的30分钟固定图测试；C的相机整机30分钟仍单独安排。

## 5. B交给A和C的材料

将 `architecture_report_05.md` 交C合并到正式报告。新入口是 `software/final_roi_api.py`；使用方法在性能说明第5节。A仍需处理s01_b03_3.jpg/U1的已知SAD误报，C仍需完成相机、触发、界面和整机验收。这里未向任何队友自动发送文件或消息。

11月3日前以修复与冻结为主；11月4-5日合并报告和演示；11月6-7日交叉复现与最终核对；11月8日按原计划上传并留凭证。上述三项人工证据补齐后，再更新完成矩阵和发布清单。
