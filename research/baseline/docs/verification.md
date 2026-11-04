# 本地验证记录

日期：2026年10月26日。验证对象：本仓库0.3.0的软件基线。

## 环境

- Windows x64
- Python 3.14.6
- NumPy 2.5.3
- opencv-python 5.0.0.93，cv2报告5.0.0
- 项目独立.venv，安装requirements-pc.txt，再执行pip install --no-deps -e .

## 结果

14项unittest通过，覆盖：配置相对路径、无效图像停止分类、FPGA后端拒绝静默软件回退、未实现任务uncertain、阈值边界、越界ROI、缩放坐标、中文路径、二值掩码边界、差异像素位置、黄金特征和掩码篡改识别、s01开发回归、照片校验和、缺少真值时明确报错。

正式命令行evaluate运行结果：18张图、252项判定，TP42、TN210、FP0、FN0、invalid0。未实施硬件，没有给出PYNQ性能或独立测试成绩。

make-golden和verify-golden运行通过，mask差异0像素；硬件验证标记为false，因为尚未提供真实PL输出。

GitHub CI已经配置Windows与Linux，但尚未创建远端仓库，因此没有远端CI通过记录。

## 迁移验证

从精简压缩包重新解压到另一处包含中文和空格的目录，重新创建独立虚拟环境、安装相同依赖及项目，14项测试再次全部通过。再从项目外工作目录，以绝对配置路径执行doctor成功。此结果验证Windows目录迁移，不代表已在其他操作系统实测。
