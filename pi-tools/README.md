# Windows 侧工具链

> **这些脚本不在树莓派上跑。** 它们在本地 PowerShell 里执行，通过 SSH 驱动树莓派。
> 树莓派上跑的是 `runtime/`（部署到 `/home/pi/robogame-runtime`）。
>
> **换电脑、换用户名都不用改这里的代码** —— 脚本里没有本机路径，
> Pi 侧路径全部写死为 `/home/pi/...`。要改的只有环境变量。

---

## 三条调用纪律（先读这个）

1. **必须用 PowerShell，不要用 Git Bash。** MSYS 会把 `/home/pi/...` 改写成 Windows 路径 ——
   实测把文件传到了树莓派上的 `/home/pi/D:/Git/home/pi/_pi_strafe.py`，而 `install` 装上去的是
   **树莓派上的旧版本**，靠 `grep` 结果不符才发现。Git Bash 下必须 `MSYS_NO_PATHCONV=1`。
2. **PowerShell 的 shell 状态不跨调用保持**，每次都要重新设 `$env:RG_PI_PW`。
3. **该机 SFTP 写入返回 ENOENT，不可用。** 传文件只能用 `_pi_put_file.py`。
   **但 SFTP 读取是可用的**（2026-09-16 实测 1.4 MB/s）—— 取录像/图片用
   `_pi_get_binary.py`，**不要**用 `_pi_get_file.py`：那个走 base64 的 exec 通道，
   整个文件先变成一个内存字符串，33 MB 的录像会膨胀成约 45 MB。

```powershell
$env:RG_PI_PW = '<口令>'          # RG_PI_HOST 默认 172.20.10.11，RG_PI_USER 默认 pi
python _pi_ready.py               # 第一条命令永远先跑这个（只读）
```

> **`exit=-1` 且完全没有输出 = 连接断了，不是脚本失败。** 脚本跑在 Pi 本地、走蓝牙控制底盘，
> **WiFi 断了它照样跑完**，输出文件还在 Pi 上。重连上去看结果，不要重跑。

---

## 首要工具

| 工具 | 用途 |
|---|---|
| `_pi_ready.py` | **只读就绪检查**：设备在不在 + 6~8 帧巡线读数。**上手第一条** |
| `_pi_ssh.py` | 执行任意远程命令。**远程命令里不能有双引号**（PowerShell 会剥掉，`argv[0]` 被截断）——多条 `grep -e` 代替引号里的 `\|` |
| `_pi_put_file.py` | 上传单文件（base64 走 exec 通道，CRLF 归一化成 LF） |
| `_pi_get_file.py` | 取回单文件（base64 走 exec 通道，**只适合文本**） |
| `_pi_get_binary.py` | **取回文件或整个目录，走 SFTP**（录像/图片/任何二进制）。⚠️ SFTP **写**在这台机器上是坏的，**读是好的** |
| `_pi_run_file.py` | 在 Pi 上跑一个本地脚本 |

## 巡线

| 工具 | 用途 |
|---|---|
| `_pi_line_watch.py` | 实时打印 mask。**只读，不发运动命令** |
| `_pi_line_trace.py` | 逐帧记录，160 Hz，含 mask 跳变时序 |
| `_pi_line_probe.py` / `_pi_line_sanity.py` / `_pi_line_diag.py` | 巡线链路与极性诊断 |

## 底盘与编码器

| 工具 | 用途 |
|---|---|
| `_pi_cmd_probe.py` / `_pi_cmd_syntax.py` | 固件命令语法探针 |
| `_pi_dir_test.py` | 单轴短脉冲方向测试（编码器 + mask 双证据） |
| `_pi_enc_probe.py` / `_pi_enc_read.py` | 编码器读数与固件行为 |
| `_pi_chassis_probe.py` / `_pi_chassis_verify.py` / `_pi_chassis_acceptance.py` | 底盘验收 |
| `_pi_strafe.py` | 单次/分段横移，逐段读 `ENC` 与 mask（停止机制见 `route_v2.md` §9.3，**核心假设未验证**） |
| `_pi_junction_probe.py` | 手推 + 编码器测距，`--axis forward\|lateral` |
| `_pi_shift_search.py` | 横移距离搜索 |
| `_pi_turn_probe.py` | 转向角度与符号标定 |
| `_pi_rapid_test.py` / `_pi_tick_cost.py` | 时序与 tick 开销 |

## 蓝牙 / RFCOMM

| 工具 | 用途 |
|---|---|
| `_pi_bt_verify.py` / `_pi_bt_diag.sh` / `_pi_bt_bind.sh` | 配对、绑定、诊断 |
| `_pi_rfcomm_probe.sh` / `_pi_rfcomm_service.sh` | RFCOMM 通道发现与服务 |
| `_pi_link_test.py` / `_pi_link_hold.py` / `_pi_keepalive_probe.py` | 链路保持与掉线特性 |
| `_pi_find_owners.sh` / `_pi_trace_real.sh` | 谁占着端口 / 真实设备追踪 |

## 相机 / AprilTag

| 工具 | 用途 |
|---|---|
| `_pi_cam_probe.py` | 相机探测，含 bearing 计算 |
| `_pi_cam_setup_check.py` | 相机配置自检 |
| `_pi_cam_config_probe.py` | 标定文件与实机是否一致 |
| `_pi_tag2_search.py` | 边横移边找 tag 2，三路数据对齐记录 |

## 遥测

在**本地**读 `handoff/measurements/` 里的 JSONL：

| 工具 | 用途 |
|---|---|
| `_pi_telem.py` | 只打印发生变化的行 |
| `_pi_telem_state.py` | 按状态分组 |
| `_pi_telem_trace.py` | 逐帧追踪 |
| `_pi_telem_summary.py` | 汇总 |

## 其它

`_pi_halt.py` / `_pi_halt_wait.py`（急停）、`_pi_calib.py`（标定）、`_pi_bt_*.sh`。
