# OnePlus 15 / 真我 GT8 Pro 内核崩溃问题：解决方案

## 一、核心结论

问题可以通过修改内核解决，不需要放弃 PID Namespace 或容器支持。

根因是 `oplus_bsp_midas.ko` 在特定进程命名空间场景下，对空指针进行了访问，导致 Kernel Panic。

## 二、问题根因

- **故障模块：** `oplus_bsp_midas.ko`
- **触发条件：** 在 PID Namespace 中运行进程，例如启动容器。
- **崩溃原因：** 模块调用 `find_task_by_vpid()` 时，使用全局 TGID 查找任务，却没有检查返回值是否为 `NULL`，随后解引用空指针，导致 Kernel Panic。

## 三、解决方法

参考 WildKernels 中用于 OnePlus 15 的修复方案。

- **参考补丁：** `0001-Return-ghost-task-if-task-is-null-and-is-requested-b.patch`
- **参考位置：** `.github/actions/build-kernel/action.yml`，约第 1715 行
- **补丁用途：** 修复 `oplus_bsp_midas` 引发的 Kernel Panic，并在启用 `PID_NS` 前应用该补丁。

### 实施步骤

1. 从 WildKernels 获取上述补丁。
2. 将补丁加入当前内核源码，命名为 `patches/007`，并确认补丁适配当前内核版本。
3. 调整 CI 构建流程，确保补丁在启用 `PID_NS` 前应用。
4. 重新构建内核，检查补丁是否成功应用，以及是否引入编译错误。
5. 重新执行 ABI 检查，重点关注核心内核代码变更带来的兼容性影响。
6. 刷入设备并验证容器和 PID Namespace 场景，确认不再发生 Kernel Panic。

> 注意：补丁名称暗示它会在特定情况下返回占位的 ghost task，而不是直接返回 `NULL`。具体行为必须以补丁实际代码为准，不能仅凭文件名判断。

## 四、已经完成的工作

- 内核成功构建，ABI 检查通过：**0 个 CRC 变化、0 个符号移除，仅增加 8 个预期符号**。
- 结构体布局与模块签名检查通过。
- 已刷入并测试基础内核，Wi-Fi、蓝牙、移动数据、相机、指纹、充电、KernelSU 等功能正常，665 个模块与原厂匹配。
- 已确认临时 `fastboot boot` 在该设备上不可用，因此需要通过实际刷写测试验证修复效果。

## 五、后续任务清单

- [ ] 获取 WildKernels 修复补丁并加入 `patches/007`。
- [ ] 重新运行 CI 构建及 ABI 检查。
- [ ] 确认检查结果无异常后，再刷入设备（刷写前先征求确认）。
- [ ] 实测容器、PID Namespace 及设备日常功能。
- [ ] 修正 `FINDINGS.md` 中错误的“设备不可行”结论。
- [ ] 清理遗留的实验分支。

## 六、源码仓库

[gt8pro-droidspaces-gki](https://github.com/ssyclr/gt8pro-droidspaces-gki)

## 最终目标

保留 PID Namespace 功能，同时修复 `oplus_bsp_midas.ko` 引起的内核崩溃。

目前已有明确的根因和可参考的修复补丁，剩余关键工作是补丁移植、重新构建、ABI 复核及真机验证。
