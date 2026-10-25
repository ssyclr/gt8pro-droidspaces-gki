# 结论：真我 GT8 Pro 不适合用原厂 vendor 分区跑 Droidspaces 容器

**一句话**：为这台机器编译的 GKI 内核本身是正确的——开机正常、kABI 与官方构建逐符号一致、原厂模块全部正常加载。但这台设备的**原厂厂商模块**在 **PID 命名空间**下会空指针解引用而内核 panic，而 PID 命名空间是 Droidspaces 容器的硬需求。厂商模块在 `vendor_dlkm`（红线不允许改，且无源码），所以在「只动 boot」的前提下无法修复。

实测：内核刷入后开机、日常使用全部正常（WiFi/蓝牙/数据/相机/指纹/充电、KernelSU、665 个模块与原厂逐一对齐）。一旦创建子 PID 命名空间（`unshare -p`，即容器），内核在下一个调度器 tick 就 panic。手机已还原原厂。

---

## 内核侧的成果（达标部分）

用 `main` 分支的补丁 001–006 构建的 `//common:kernel_aarch64_dist`，对比官方构建 `ab14541642`：

- **符号**：已有符号 CRC 变化 **0**、删除 **0**、导出属性变化 **0**；新增 8 个（`USER_NS` 带来的 `from_kuid/from_kgid/make_kuid/...` 共 6 个，补丁 002 的 `init_ipc_ns/put_ipc_ns` 2 个）。
- **配置**：Droidspaces 必需 + 推荐项全部 `=y`（`NETFILTER_XT_TARGET_REJECT` 在该源码树 Kconfig 不存在，REJECT 由原厂已开的 `IP_NF_TARGET_REJECT`/`IP6_NF_TARGET_REJECT` 提供）；`CFS_BANDWIDTH`/`CGROUP_PIDS` 与原厂一致；`MODVERSIONS=y`。
- **结构体布局**：BTF 比对 10920 个结构体，`task_struct` 仅在空隙里新增成员（`sysvsem`/`sysvshm`），零成员移位。
- **模块签名**：原厂一次性签名证书已打进内核（`SYSTEM_TRUSTED_KEYS`），手机上 103 个 system_dlkm 模块全部验签通过。

也就是说，ABI 自检这一关是**通过**的。阻塞点不在内核。

---

## 根因：厂商模块在 PID 命名空间下空指针 panic

### 确认的崩溃：`oplus_bsp_midas.ko`

panic 日志（`fastboot boot` 临时启动后跑 `unshare -p -f` 触发）：

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
  irqtime_account_process_tick   <- 调度器 tick
Kernel panic - not syncing: Oops: Fatal exception in interrupt
```

寄存器：`strncpy(x0=dst, x1=0x910, x2=0x10)`。`x1 = 0x910` 就是故障地址，`0x10 = 16 = TASK_COMM_LEN`。

反汇编 `update_or_create_entry_locked`（创建新条目分支）：

```
bl   find_task_by_vpid          ; x0 = 按 vpid 查到的任务，或 NULL
mov  x20, x0
bl   __rcu_read_unlock
add  x1, x20, #0x910            ; x1 = x20->comm （comm 在 task_struct 偏移 0x910）
mov  w2, #0x10
bl   strncpy                    ; strncpy(entry+0x20, x20->comm, 16) —— x20 未判空
```

还原出的逻辑：

```c
leader = find_task_by_vpid(task->tgid);   // task->tgid 是“全局” tgid（task_struct 偏移 0x70c）
strncpy(entry->leader_comm, leader->comm, 16);  // 不判空 → leader 为 NULL 时 NULL->comm → panic
```

- `find_task_by_vpid(nr)` 在 **`current` 的 PID 命名空间**里解析 `nr`。midas 传的是**全局 tgid**。
- 原厂 `PID_NS=n`，永远没有子命名空间，全局号 == vpid，查找永远成功 —— OPPO 自己从没踩到。
- 我们开了 `CONFIG_PID_NS=y`（Droidspaces 必需）。进程跑在子 PID 命名空间（容器）时，全局 tgid 在该命名空间里不是有效 vpid → 返回 NULL → panic。
- 这条路径在整个 midas 里只被调度器 tick 钩子 `android_vh_cpufreq_acct_update_power` 触发；`task_struct.comm` 偏移在我们的内核里没变（仍 0x910），所以**与补丁 001 无关、与 ABI 无关、与签名无关**。

### 疑似的第二处：`oplus_secure_guard_new.ko`

它的 execve 处理函数 `do_execveat_common_entry_handler`（**每次 execve 触发**）里：

```
bl   find_task_by_vpid
mov  x8, x0
add  x0, x0, #0x40             ; get_task_struct(task)：对 task->usage(偏移 0x40) 原子加 —— 未判空
...
ldr  x8, [x8, #0x900]          ; task->cred（偏移 0x900）
ldr  w4, [x8, #8]              ; cred->uid
```

同样是对 `find_task_by_vpid` 结果不判空就解引用。容器内 execve 时 `current` 在子命名空间，若查找返回 NULL 则崩。属于**高度疑似**（未单独确诊，因为测试时 midas 先崩了）。

### 面上的landmine扫描

当前加载的厂商模块里有 **19 个**用到 `find_task_by_vpid` / `pid_task` / `find_vpid` 这类按命名空间解析 pid 的调用。绝大多数是**安全**用法（解析 userspace 从 init 命名空间写入的 pid）。危险的是「从在任意/中断上下文触发的钩子里，拿任务的全局 pid 去按 vpid 查，且不判空」这一类——`midas` 已确诊，`secure_guard_new` 高度疑似，`binder_strategy`/`game_opt` 静态分析未排除。

逐个模块表（挂了调度/tick 钩子、会在容器任务上持续触发的）：

| 模块 | 注册的调度/tick 钩子 | find_task_by_vpid 所在函数 | 风险 |
|---|---|---|---|
| oplus_bsp_midas | `android_vh_cpufreq_acct_update_power` | `update_or_create_entry_locked` | **已确诊崩溃** |
| oplus_secure_guard_new | （execve 路径） | `do_execveat_common_entry_handler` | 高度疑似（每次 execve） |
| oplus_bsp_sched_ext | `android_vh_scheduler_tick` 等 | `ctm_timer_handler`（自带定时器） | 待核（未判空分支待确认） |
| oplus_locking_strategy | `android_rvh_entity_tick` 等 | `android_vh_do_futex_handler` | 待核 |
| oplus_bsp_game_opt | `android_rvh_before_do_sched_yield` 等 | 多为 ioctl/proc 写入路径 | 偏安全 |
| oplus_bsp_schedinfo | `android_rvh_sched_fork_init` 等 | proc 读/`jank_find_get_task_by_vpid` | 偏安全 |
| oplus_bsp_waker_identify | `android_rvh_try_to_wake_up_success` | `*_show`/`*_proc_write` | 偏安全 |

---

## 为什么无法在范围内修复

- **不能改厂商模块**：它们在 `vendor_dlkm`（红线 4：只动 boot），且是无源码的预编译 `.ko`。
- **不能关 PID_NS**：它是 Droidspaces 必需项，关了容器无法获得 PID 隔离，要求检查也不通过。
- **内核侧守卫是打地鼠**：能在内核内把某个钩子对「非 init PID 命名空间任务」跳过的，只有触发点在内核内的那种（如 midas 的 `cpufreq_acct_update_power`）。但每个有问题的模块要单独写一个守卫、单独验证，且无法保证修全——有的模块从自带定时器/kprobe 触发，boot 侧挡不住。
- **关掉 `CONFIG_CPU_FREQ_TIMES`** 能堵住 midas 这一条（该钩子的唯一内核内触发点），且 ABI 安全（钩子的 KMI 符号无条件存在，函数本身非导出符号），但代价是全局丢失按 app 的 CPU time-in-state 电量统计，而且挡不住 secure_guard 等其他路径。

---

## 如果以后想再推进

- 换一台厂商模块对 PID 命名空间友好的设备（或有内核源码、能重编 vendor 的设备）。
- 或在本设备上接受「改 vendor_dlkm」这一前提（超出本任务红线）：给 midas / secure_guard 等模块打二进制补丁或替换为自编版本。
- 本仓库的 workflow 可复用：系统 OTA 后用新构建号重跑即可得到新的 ABI 干净内核；真正的阻塞是 vendor 模块，不是内核。

交付物（`D:\A\out\`）：`Image`、`Image.lz4`、`Image.gz`、`new-boot.img`、最终 `config`、`vmlinux.symvers`、`abi-report.md/json`；原厂备份在 `D:\A\backup\`。
