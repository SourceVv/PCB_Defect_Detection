# PCB视觉算法子模块

本目录是完整项目中的`pcbinspection`部分，不是独立Git仓库。首次安装和日常运行优先按照[整仓库README](../readme.md)在根目录执行。本页后续的相对命令以`pcbinspection`为当前目录，适合单独维护算法时参考；不要在这里重复初始化Git或创建远端仓库。

面向固定板型的PC软件检测基线，供三人团队复现、标定和与PYNQ硬件联调。

当前版本 **0.3.0**：在18张s01开发照片上检查14个缺件位置。支持ORB/RANSAC定位、配置化ROI、四种缺件特征和批量评价。焊反、倾斜、摄像头实时采集与FPGA后端尚未完成；倾斜位置显示`uncertain`，定位失败返回`invalid`，`backend="fpga"`会明确报未实现。

## 五分钟开始

以下命令适用于Windows PowerShell。在项目根目录执行，每行单独运行，不需要激活虚拟环境。

本次已验证环境：**Windows、Python 3.14.6、NumPy 2.5.3、OpenCV 5.0.0.93**。`requirements-pc.txt`锁定后两个版本。建议三台电脑先统一Python版本；`pyproject.toml`允许的其他Python版本仅代表兼容范围，不代表已在每个平台验证。PC环境与PYNQ板端环境分别管理，不要直接把PC锁定文件安装到ARM板。

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-pc.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pcb_inspection doctor --config configs/roi_config.json
```

第一行应确认运行的是你安装的Python。如果`python`打开微软商店或找不到命令，使用Python启动器或实际解释器路径创建环境。不要把别人的`.venv`复制到自己的电脑；每个人各建一个。

### 运行一张图

```powershell
.\.venv\Scripts\python.exe -m pcb_inspection detect --config configs/roi_config.json --image data/raw/s01/s01_b04_1.jpg --out outputs/single
```

打开`outputs/single/annotated.png`查看检测框，`result.json`查看每个位号的状态、特征值和原因。绿框为存在，红框为缺件，黄色为未实现或无效。`present`只表示器件存在，不能证明方向、焊接或电气功能正常。

### 复现全部开发照片

```powershell
.\.venv\Scripts\python.exe -m pcb_inspection evaluate --config configs/roi_config.json --input data/raw/s01 --truth data/labels/component_truth.csv --out outputs/s01
```

期望结果：18张照片、252项缺件判定，TP=42、TN=210、FP=0、FN=0、invalid=0。输出是`decisions.csv`、`summary.json`和`annotated/`。

**这是开发集回归，不是独立测试准确率。** 同一批图已参与选择ROI、特征和阈值，六块板重复拍摄也不是18块独立板。新拍S02后可替换输入目录与标签路径，但应冻结配置后再评价；不要把新照片的失败结果用于调参后仍称其独立测试。

### 运行测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

或者执行`scripts/check.ps1`，依次跑测试、252项回归、生成与核对黄金参考。若本机策略不允许运行脚本，直接执行本文的命令即可，无需改变全局系统策略。

Linux/macOS使用`python3 -m venv .venv`，把后续解释器路径改成`.venv/bin/python`。目前本地验证为Windows；GitHub Actions配置了Windows和Linux，上传后以实际CI结果为准。

## 目录

```text
configs/roi_config.json          主配置和参数版本
data/raw/s01/                   18张固定开发样例，随仓库共享
data/labels/photos.csv          照片、板号、用途和SHA256
data/labels/board_defects.csv    原实物缺陷记录的统一英文列名副本
data/labels/component_truth.csv  252项明确的缺件任务真值
src/pcb_inspection/              正式软件代码及命令行入口
tests/                          行为、数据和黄金包回归
golden/generated/               本机生成，Git忽略大数组
outputs/                        本机运行结果，Git忽略
scripts/check.ps1               一次执行复现验证
docs/                           迁移记录、接口和GitHub协作说明
.github/                        CI、任务和PR模板
```

历史`work/`、下载副本、PDF生成脚本、数百张探索图没有迁入正式目录，原压缩包及解压档案保留在项目之外。这些研究脚本不是本版运行依赖。

## 路径规则

`--config`等命令行路径相对于你执行命令的位置；配置文件内部`alignment.reference_image`相对于配置文件所在目录。程序不依赖某个用户名或盘符。

在其他工作目录运行时，给命令行参数绝对路径，例如：

```powershell
C:\项目位置\pcb-inspection\.venv\Scripts\python.exe -m pcb_inspection doctor --config C:\项目位置\pcb-inspection\configs\roi_config.json
```

含空格的路径要加双引号。Python图像读写已兼容中文路径；Vivado工程仍建议放在较短的英文路径。

## 给B和C的接口

```python
from pcb_inspection import detect, load_config
from pcb_inspection.io import read_image

config = load_config("configs/roi_config.json")
result, annotated = detect(read_image("data/raw/s01/s01_b04_1.jpg"), config)
```

输入为uint8 BGR或灰度图。配置尺寸为2533×1900，内部按此尺寸定位；返回的`box`属于配置坐标，`original_box`和标注图属于输入原图坐标。允许缩放只是坐标支持，不能保证低分辨率摄像头检测效果，需要用最终采集链路重新标定。

详细黄金包与硬件接口见[接口说明](docs/hardware_handoff.md)。

## 如何上传及分工

按[整项目GitHub协作说明](../docs/github_guide.md)创建项目仓库并推送。三个人克隆同一整个仓库，分别负责pcbinspection、hardware、board；不用分别创建三个仓库。

本版没有替团队选择开源许可证；需要公开授权复用时，由团队确认源码、照片和外部材料的授权范围后增加LICENSE。仓库可见性与许可证是两件不同的事。

## 已知限制

- ROI映射后采用外接矩形裁图，没有完成逐ROI透视矫正，较大旋转或透视变化可能影响SAD。
- `cd40`保留旧数学定义：实际取中间约三分之一宽高的区域，名称没有改，以免阈值失效。
- 标签由原包实物状态表整理，尚不能代替实物复核；C24倾斜和C32翘起的历史记载不一致，本版不据此计算成绩。
- LED5和LED7未纳入14个缺件ROI；焊反未加入主检测，倾斜仅返回`uncertain`，翘起停用。
- `.bit/.hwh`和DMA工程尚未提供。不能把软件黄金包自检通过写成上板通过。
