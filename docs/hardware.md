# 硬件文件与重建说明

硬件目录来自原仓库 FPGA 交付分支，提交号见 provenance.json。保留 bit、hwh、驱动、RTL、测试平台及构建脚本。Python、HDL及脚本注释按本次仓库要求移除；本仓库说明文档承担操作说明。原目录中的历史校验文件描述原始交付，整理后的源码校验以新发布清单为准。

运行已发布的 bit 文件不需要重新综合。目标平台为 PYNQ-Z2，器件为 xc7z020clg400-1。软件依赖 PYNQ 提供的 DMA、MMIO 和内存分配能力。

重新构建前，需要安装 Vivado 和匹配的 PYNQ-Z2 board preset，并设置 `PYNQ_BOARD_REPO` 指向实际 board files 目录。创建脚本位于 `hardware/pcb_b_roi_v03/hardware/tcl/create_project.tcl`，构建脚本在同目录。应在独立构建副本中操作；构建脚本会拒绝覆盖已有发布目录，避免旧发布文件混入新结果。

本次未重新运行 Vivado 综合、实现、时序分析或JTAG下载。原交付中的报告和记录仅用于追溯，不应写成新仓库已重新取得的硬件成绩。PYNQ版本、Vivado版本、工具IP版本和板级预设应在每次比赛交付前实际记录。
