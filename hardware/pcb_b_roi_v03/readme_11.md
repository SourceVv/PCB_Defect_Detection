# B：ROI与面积统计候选Overlay ABI3

本包把反相、严格阈值、17路ROI面积和ROI并集mask放进PL，接续已实板通过的ABI2基线。原灰度直接输入；PS负责几何对齐、配置、分类和日志。

## 先看什么

- docs/reference_document_14.pdf：7页设计与操作说明；同名Markdown可编辑。
- config/interface_v03.md：寄存器、ACK、包边界和统计含义。
- software/reference_document_16.ipynb：6代码格，初始空输出，默认RUN_ON_BOARD=False。
- release/roi_v0_3/readme_build_audit.md：真实100MHz时序、资源、地址和保留的告警。
- evidence/rtl：按源码版本分开的真实仿真日志。

## 本版已完成的离线结果

最终RTL SHA256：f65df400554246f70438c02f9ca4c2dd79f08d02ee8c6b4bccd9a326671d994f。

单元、pipeline复位附加、窄宽/超长边界附加和三整图仿真均通过；三整图共14,438,100字节，每帧17路统计对照正确。36项Python测试通过，包含原安全DMA与新配置/统计测试。参考结果还与不导入本包软件的积分图实现逐ROI交叉核对。

最终100MHz构建：WNS +0.565ns，WHS +0.030ns，setup/hold失败为0；11202个可布线网络全部完成，0布线错误、0黑盒。bit/hwh和输入源码均已审计。仍保留16项DRC Warning、4项Advisory及生成互连FIFO的3项LUTAR-1；详细记录在构建摘要。

首次单段和初次pipeline的时序失败记录作为历史保留，不能拿它们替代当前f65d版本结果。板卡成绩单独保存在实际board_run/worker记录和本次实测说明中；这些离线成绩不代替板上结论。

## 使用边界

latest17对应A的v0.2.0配置，参考mask白像素326860；frozen16对应A的v0.1.0原答案，白像素314801。两版索引不同，按ROI名称配对。像素门限120与A的SAD/比例/面积分类门限分开定义。

每拍最多4个字节，内部流水分段，最小接收间隔3个时钟；AXIS允许反压，整图末尾一次TLAST，无逐行填充。所有图像/统计必须来自同一个已完成包。

控制GPIO本版为0x41210000，配置GPIO为0x41200000；软件按HWH实例名解析，避免沿用旧手写地址。新驱动要求ABI3，不能与ABI2驱动混用。

## 复现入口

普通电脑先运行软件和RTL检查；以下从完整包根目录执行：

```powershell
python -m unittest discover -s software -p 'test_*.py' -v
python software/roi_reference.py
& hardware/sim/run_modelsim.ps1 -Test All -PythonPath python
```

本机若使用Codex自带Python，可把python替换为已记录的python.exe路径。CPU参考需要NumPy。ModelSim入口默认本机已安装工具路径，可用-ToolBin指定。

Vivado构建采用单进程标准全局综合/实现，以避免本环境Windows WMI子任务启动限制：

```powershell
& hardware/tcl/run_vivado_build.ps1 -Stage AllInProcess -Jobs 4
```

从干净包重建；脚本拒绝覆盖已有工程或发布目录。源目录包含历史证据与测试平台，应使用创建脚本选择正式源文件。板卡库路径可通过-BoardRepo指定。

## 上板

完整包默认放在/home/xilinx/jupyter_notebooks/pcb_b_roi_v03，打开software/reference_document_16.ipynb。依次运行前两格离线检查，再改True并重跑1→2→3，然后4→5→6。第4格会替换FPGA配置，第5格自动执行8例。

tools/board_live_worker.py是板上自动执行器：普通用户默认只做计划/整包校验；经授权的--execute才以root执行，预检新进程XRT环境。遇到无法确认DMA停止时保留对象与进程，即使日志保存失败也不会主动退出释放缓冲区。

完整内容摘要由package_manifest.json记录，bit/hwh摘要在release/roi_v0_3/sha256.json。新运行产生的额外证据不会覆盖静态源文件。
