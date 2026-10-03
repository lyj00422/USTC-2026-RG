# 树莓派维护工具

这些脚本在 Windows PowerShell 上运行，通过 SSH 驱动 `/home/pi/robogame-runtime`。先加载环境变量，再执行只读就绪检查：

```powershell
. .\pi-tools\_pi_env.ps1
python .\pi-tools\_pi_ready.py
```

## 部署与文件传输

| 脚本 | 用途 |
|---|---|
| `_pi_push_runtime.py` | 推送整个 runtime，默认跳过树莓派上的 `data/` 与 `logs/`，推送前自动备份 |
| `_pi_push_dir.py` | 单独推送动作包目录，适合更新 `data/route_v2_actions` |
| `_pi_push_files.py` | 按 manifest 精确推送文件并做哈希校验 |
| `_pi_put_file.py` | 推送单个文本文件 |
| `_pi_get_file.py` | 读取单个文本文件 |
| `_pi_get_binary.py` | 通过 SFTP 读取图片、录像或整个目录 |
| `_pi_run_file.py` | 把本地脚本送到 Pi 临时目录执行 |
| `_pi_ssh.py` | 执行远程命令 |

## 自动化运行

| 脚本 | 用途 |
|---|---|
| `_pi_launch.py` | 在 Pi 上执行启动前检查，或脱离 SSH 会话启动路线 |
| `_pi_stop_route.py` | 用 SIGTERM 安全停止路线并等待清理完成 |
| `_pi_run_status.py` | 只读查看当前路线状态和日志 |
| `_pi_halt.py` | 向底盘发送急停 |
| `_pi_ready.py` | 检查底盘节点和巡线传感器，只读/只发 STOP |
| `_pi_check_action.py` | 加载并编译 catalog 中的全部动作包 |
| `_pi_run_action.py` | 在 Pi 上运行 catalog 中的单个动作包 |
| `_pi_run_actions.py` | 按顺序运行一组动作包 |
| `_pi_holds.py` | 检查动作包中的吸盘锁存与停顿 |
| `_pi_line_watch.py` | 实时观察巡线 mask，不发送运动命令 |
| `_pi_arm_state.py` | 查看机械臂状态 |
| `_pi_arm_home.py` | 机械臂复位到 home |
| `_pi_arm_reset_pkg.py` | 执行 catalog 中的 reset 动作包 |
| `_pi_arm_suction_on.py` | 打开吸盘并保持 |
| `_pi_hub_connect.py` | 连接控制台所需的硬件设备 |

所有脚本都从 `RG_PI_HOST`、`RG_PI_USER` 和 `RG_PI_PW` 读取连接信息；不要把密码写入脚本或命令历史。PowerShell 与 Git Bash 的路径转换规则不同，部署命令统一在 PowerShell 中执行。
