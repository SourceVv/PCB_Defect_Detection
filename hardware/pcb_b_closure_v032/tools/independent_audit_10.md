# 在普通电脑独立复核 v032 结果

`verify_closure_results.py` 读取已经下载的板端证据，重新检查采样、mask、统计、帧包数、DMA安全和时间汇总。它只依赖标准库与 NumPy，不导入 worker、service 或 PYNQ，不重新加载 FPGA，也不产生新的 DMA。

三个模式都要等对应板端报告 `state=complete, passed=true` 后才能作为完成证据。运行中、缺文件或失败报告均拒绝，不提前填写30分钟通过。

## 三种调用

在工作区根目录运行，`<运行目录>` 替换为已经下载的完整目录：

```text
python -B outputs/pcb_b_closure_v032/tools/verify_closure_results.py profile <profile运行目录> --package outputs/pcb_b_roi_v03 --closure-root outputs/pcb_b_closure_v032

python -B outputs/pcb_b_closure_v032/tools/verify_closure_results.py validate <validate运行目录> --package outputs/pcb_b_roi_v03 --closure-root outputs/pcb_b_closure_v032 --previous outputs/pcb_b_integration_v031

python -B outputs/pcb_b_closure_v032/tools/verify_closure_results.py stability <stability运行目录> --package outputs/pcb_b_roi_v03 --closure-root outputs/pcb_b_closure_v032 --previous outputs/pcb_b_integration_v031
```

所有来源目录都显式提供，避免误用其他版本。默认在运行目录写 `independent_profile_audit.json`、`independent_validate_audit.json` 或 `independent_stability_audit.json`，也可用 `--output <另一个.json>` 指定位置。工具拒绝覆盖原 `report.json/profile_report.json/records.jsonl`、冻结清单和源目录。

退出0表示该份保存证据通过独立复核；退出1会写 `audit_passed:false` 和具体原因。电脑缺 NumPy、缺完整文件、摘要不符或记录缺失都会停止，不用软件结果冒充硬件成功。

## 共同核对的来源

工具逐项核对冻结 ABI3 包、v031 包和本次5份 runtime 清单的大小/SHA256。板端报告里的 manifest 和8份关键源/bit/hwh摘要要与本机显式指定的目录一致。

不导入旧软件，直接读取冻结灰度和 ROI，重新计算 `gray<135` 的17框并集输出与各框面积；再绑定冻结 mask 和 case signature。此结果作为所有计数、raw SHA及已保存样本 BIN 的答案。

硬件证据需记录 ARM Linux、管理员运行环境；微测记录也需来自 ARM Linux，不能把普通PC计时当成同板微测。环境字段是源报告记录，不等于工具再次登录板卡检查环境。

## profile：65次严格 helper 调用

确认5候选各3次预热、10次测量，共65条记录，固定随机种子混排次序正确。核对每候选256个数值检查、首中尾污染检查的结果记录、每次白点数、输入未改变标志及源 SHA。

独立用 AST 提取函数原文并计算摘要，不执行或导入 helper。按10个原始测量样本重算 min/max/mean/median/p95/总体标准差和最低中位数候选。时间只代表 CPU 上的严格二值证明与白点统计，不是 PL 或 DMA 时间。

## validate：33轮、66次 DMA

确认3轮预热和30轮正式测量，每轮 CPU/旧服务/新服务各一次，固定种子打乱顺序。CPU不产生DMA，因此总共66次DMA。报告数组和 JSONL 要逐条完全一致，不允许丢一轮或选择性删掉慢样本。

逐调用核 frozen latest17 的 raw SHA、17计数、有效像素与总白点。硬件调用还核连续包数、收发长度、IOC/Idle/错误位、DMA复位后双 Halted、安全空闲与无保留缓冲区。新服务必须确实返回 `frozen_driver_owning_base`，最终数组 owning、C连续且 base=None。

独立重算30个样本的全部分项计时汇总及 CPU/新、旧/新的成对比值。3轮预热排除、额外离群样本剔除必须为0。首末正式轮的 CPU/旧/新六份完整 BIN 都再次逐字节比对。

完整调用计时仍包括严格检查、输入快照、DMA分配/缓存/复位/等待/释放及统计读取。速度比只适用于这组同输入同功能对照，不能叫纯 PL 延迟、持续FPS或整机相机吞吐。

## stability：至少1800秒的 B 链路

流式读取 JSONL 全部帧，核帧号和唯一ID连续、逐帧输出摘要/17计数、包数16位回绕、收发长度与安全状态。单调耗时严格递增，墙上时间不得倒退；最终实际时长和请求时长都必须达到1800秒。不能靠把最终时长字段改大来补齐一段没有帧记录的时间。

按 worker 的60秒检查点触发规则，从全量帧独立推导每个 checkpoint；帧数、时间、内存、包数和 seen-ID 数均要一致。末帧状态、安全快照、帧数、耗时 min/mean/max 和初末完整 mask 也要匹配。

RSS、RSS峰值、CMA总量/空闲量只报告可用样本数、首末、最小/最大与变化，缺失项明确统计。帧 ID 集合按设计增长；看到RSS变化不能直接断言泄漏，看到CMA回升或稳定也不能自动宣布“无泄漏”。本项是重复固定 golden 输入的 B 硬件链路稳定性，不包括摄像头、UI、C整机或缺陷分类全面验收。

每帧原始 mask 并未全部保存：逐帧记录的 hash/比较通过标志、统计、协议与安全状态会全量复核，并绑定已冻结的执行源。只有保存的首末样本 BIN 能在 PC 再次进行完整字节比较，报告会明确这个证据范围。

## 审计器自身测试

9项复合测试使用合成记录，覆盖有效来源、坏聚合/p95、少样本/轮/帧、缩短时长、包序错误、Halted缺失、非owning输出、unsafe/poisoned、错误 checkpoint、样本污染、缺探针及PC微测冒充ARM。测试日志在 `evidence/closure_auditor_tests.log`。

测试中的1800秒是模拟记录，只验证审计门禁，不代表真的等了30分钟；真实持续运行报告必须另外完整下载并通过本工具。
