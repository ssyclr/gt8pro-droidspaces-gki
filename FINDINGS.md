# 真我 GT8 Pro：容器场景下的内核崩溃与修复

## 结论

- 用本仓库的补丁 001–006 编出的 GKI 内核，ABI 与官方构建 ab14541642 完全一致，刷入后开机和日常使用都正常：Wi-Fi、蓝牙、移动数据、相机、指纹、充电、KernelSU 都没问题，665 个模块与原厂逐一对齐。
- 但只要有进程运行在**子 PID 命名空间**里（也就是 Droidspaces 容器），OPLUS 的预编译厂商模块 `oplus_bsp_midas.ko` 就会在下一个调度器 tick 空指针解引用，导致内核 panic。
- 补丁 007 修复了这个问题。它移植自 WildKernels 给一加 15（与 GT8 Pro 同一颗 SoC）做的 Droidspaces 构建所用的补丁，只改内核 `kernel/pid.c`，不需要动 vendor 分区，ABI 不变。
- **状态：** 补丁 007 已通过 CI 构建和 ABI 自检（run 41163521204）：已有符号 CRC 变化 0、删除 0、导出属性变化 0，新增的仍是原来那 8 个符号，结构体只有 `task_struct` 在空隙里新增成员。再加上补丁 008 的构建（run 41163530823）结果相同。**尚未在 GT8 Pro 真机上验证**，需要接上设备后用 `scripts/verify_device.sh --stress` 测试。

之前版本的本文件曾判断「这台设备在只改 boot 的前提下无法修复」，这个判断是错的。

---

## 根因：`oplus_bsp_midas.ko`

复现方式：刷入未带补丁 007 的内核后，以 root 身份执行 `unshare -p -f true`。崩溃日志如下（`/data/persist_log/backup/SYSTEM_LAST_KMSG.txt`）：

```
Unable to handle kernel NULL pointer dereference at virtual address 0000000000000910
pc : strncpy+0xc/0x2c
lr : update_or_create_entry_locked+0x1ac [oplus_bsp_midas]
Call trace:
  strncpy
  midas_record_task_times [oplus_bsp_midas]
  __traceiter_android_vh_cpufreq_acct_update_power
  cpufreq_acct_update_power
  account_system_index_time
  irqtime_account_process_tick
Kernel panic - not syncing: Oops: Fatal exception in interrupt
```

寄存器为 `x1 = 0x910`、`x2 = 0x10`。`0x910` 是 `task_struct.comm` 的偏移，`0x10` 是 `TASK_COMM_LEN`，说明 midas 读的是一个 NULL 任务的 `comm`。

反汇编 `update_or_create_entry_locked`，midas 第一次统计某个进程时会执行：

```c
leader = find_task_by_vpid(task->tgid);         // task->tgid 是全局 tgid
strncpy(entry->leader_comm, leader->comm, 16);  // 没有判空
```

- `find_task_by_vpid()` 在 **`current` 所在的 PID 命名空间**里解析编号，而 midas 传入的却是**全局** tgid。
- 原厂内核关着 `PID_NS`，系统里不存在子命名空间，全局编号和 vpid 永远相同，所以这条查找从不失败，OPLUS 自己也没碰到过。
- 开启 `PID_NS`（Droidspaces 的必需项）之后，子命名空间里的进程被 tick 统计时，查找返回 NULL，于是 panic。
- 这个崩溃和 ABI、补丁 001 改动的 `task_struct` 布局（`comm` 偏移没有变）、模块签名都无关。在 midas 里，`find_task_by_vpid` 只能经由这一个钩子走到；它注册的另外 3 个 binder 钩子都不会走到这里。

## 修复：补丁 007

`patches/007.ghost-task-for-oplus-midas.patch` 只改了 `find_task_by_vpid()`：当查找结果是 NULL、**并且**调用地址落在 `oplus_bsp_midas` 模块里时，返回一个常驻的「哨兵任务」，也就是 `init_task` 的拷贝，`comm` 设为 `ghost-task-sentinel`，pid/tgid 设为 -1，引用计数设为 1000 防止被释放。

- midas 拿到返回值后只读取 `->comm`，读一个哨兵任务是安全的。
- 其他所有调用者行为不变，查不到时照样拿到 NULL。
- 正常路径（能查到任务）没有任何额外开销。
- 函数原型没有变，CRC 不变；新增的对象都是 `static`，不导出；也没有改任何头文件，所以 bindgen 生成的 Rust 绑定不受影响。

与 WildKernels 原补丁（`common/droidspaces/0001-Return-ghost-task-if-task-is-null-and-is-requested-b.patch`，fatalcoder524，取自提交 03bb1d245c）相比的改动：

- `init_ghost_task()` 改为 `static`。这个源码树默认开着 `-Wmissing-prototypes`，配置里又有 `CONFIG_WERROR=y`，原样打上会编译失败。
- 用 `strscpy()` 代替 `strncpy()`。原写法把 19 个字符的名字写进 16 字节的 `comm`，结果没有 NUL 结尾。
- 删掉了 6.12 里早已不存在的 `CONFIG_REFCOUNT_FULL` 分支，以及兼容旧内核的 `_Generic` 写法。

社区的修复过程也可以作为参考：WildKernels 最早（2026-04-23）的做法是让 `find_task_by_vpid` 对**所有**调用者在查不到时返回 `current`。这非常危险，比如对一个不存在的 pid 调 `sched_setaffinity`，就会作用到调用者自己身上。第二天他们就改成了只针对 midas 的版本，此后半年一直如此。

## 补丁 008：限制非特权进程创建用户命名空间

`patches/008.guard-user-ns-for-non-root.patch` 同样来自 WildKernels（`0001-Guard-USER_NS-for-non-root-users.patch`），不过他们的构建脚本并没有接入它。本仓库已合入。

- **为什么需要：** 原厂内核关着 `USER_NS`，任何 App 都创建不了用户命名空间。为 Droidspaces 打开它之后，**所有普通 App** 都可以创建用户命名空间，而很多内核漏洞只有通过非特权用户命名空间才能触达。补丁 008 要求调用者具备 `CAP_SYS_ADMIN`，让普通 App 回到和原厂一样的状态，root（也就是 Droidspaces）不受影响。
- **代价：** 容器内的非 root 用户同样无法创建用户命名空间，依赖它的沙箱（Chromium、bubblewrap/Flatpak）在非 root 用户下无法使用。

## 其他厂商模块

当前加载的厂商模块中，有 19 个用到了 `find_task_by_vpid`/`pid_task`/`find_vpid` 这类按命名空间解析 pid 的函数。

- 绝大多数用法是安全的：解析的是 userspace 写进来的 pid（ioctl、proc/sysfs 写入），而且是在写入方自己的命名空间里解析。
- 静态分析发现 `oplus_secure_guard_new.ko` 的 execve 处理函数 `do_execveat_common_entry_handler`，在 `find_task_by_vpid` 之后也没有判空就调用 `get_task_struct()`，并读取了 `->cred`。没能确认它查找的是哪个 pid，测试时也是 midas 先崩溃的。
- 一加 15 的 Droidspaces 构建只修了 midas，此后一直在用，说明这一处在实际使用中大概率不会触发。真机验证时要重点留意 execve 场景（`--stress` 里每一轮都会 exec）。

## 其他发现

- **临时启动不可用：** 在这台机器上，`fastboot boot` 连原样的原厂 `boot.img` 也会让设备进入高通 900E 转储模式，所以只能正式刷入后测试，回滚方式是 `fastboot flash boot boot_stock.img`。
- **magiskboot 打包：** 6.12 的 Image 内置了一个 72 字节的空 DTB，magiskboot 会把它误认成附加 DTB，把内核切成两段。repack 之前必须删掉 `kernel_dtb`，详见 README。

## 真机验证步骤

1. 用 main 分支（补丁 001–008）构建出的 `Image` 打包 `new-boot.img`，刷入 `boot` 分区。
2. 执行 `bash scripts/verify_device.sh`：检查配置、命名空间、密钥、模块错误和基础探测。
3. 执行 `bash scripts/verify_device.sh --stress`：在新的 PID 命名空间里反复运行进程，这正是此前触发崩溃的场景。
4. 在 Droidspaces App 里依次运行「设置 → 要求 → 检查要求」，然后真正启动一个容器。
5. 如果出现 panic：设备重启后读取 `/data/persist_log/backup/SYSTEM_LAST_KMSG.txt`，按里面的调用栈定位到具体模块。
