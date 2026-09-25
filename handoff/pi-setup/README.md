# 树莓派环境恢复

> **状态：未完成。** 这里目前只有一块 SD 卡 `/boot` 备份和分析结论。
> 完整的 `install.sh` 和恢复流程要等**拿到 SSH 跑一次只读快照**之后再写 —— **不要靠猜写安装脚本。**

---

## 1. 出事了按这个顺序

1. **换上预刷好的备用卡**（最快，约 5 分钟）。所以**值得常备一张**。
2. 没有备用卡 → 重刷 Raspberry Pi OS（64 位）→ 按下面第 3 节恢复 → 约 20 分钟。
3. 有整卡镜像 → `dd` 写回去。**整卡镜像不进仓库**，放外部硬盘/网盘。

---

## 2. 已知的树莓派侧改动

### 2.1 `/boot/firmware/config.txt` —— 只在末尾加了 3 行

`sd-boot-20260912/config.txt` 的**唯一自定义内容**就是文件最后这一段：

```ini
[all]
enable_uart=1
dtoverlay=miniuart-bt
force_turbo=1
```

**其余全部是 Raspberry Pi OS 原版默认值** —— `camera_auto_detect=1`、`arm_64bit=1`、
`dtoverlay=vc4-kms-v3d`、`arm_boost=1` 等等，重刷就有，不用手抄。

`config.txt` 的其余 300 多个文件（`kernel8.img`、`start*.elf`、`overlays/*.dtbo`、`*.dtb`）
**同样是原版固件**，重刷就回来。**这就是为什么不需要留着那 80M。**

> ⚠️ **两个待查项**（拿到 SSH 后确认）：
> - `force_turbo=1` **关掉了 CPU 调频**。这是有意为之（追求时序稳定？）还是某次实验的遗留？
>   它会让 SoC 一直跑最高频、发热和功耗都更高。**没搞清楚之前不要动它。**
> - `dtoverlay=miniuart-bt` 把蓝牙挪到 mini-UART，好把 PL011 让给 GPIO14/15。
>   但巡线**已经改用 GPIO23/24 的 pigpio 软件串口**了，所以这个 overlay 可能已经不需要。
>   **同样：搞清之前不要动。** 动了可能反而把蓝牙搞坏。

### 2.2 `cmdline.txt`

原版 + Raspberry Pi Imager 加的标记（`ds=nocloud;i=rpi-imager-fix-20260910d`）。
**没有手工改动**，重刷时用 Imager 配好主机名/用户名/WiFi 即可。

### 2.3 系统文件（这三份在仓库里，部署时拷过去）

| 仓库内 | 树莓派目标 | 作用 |
|---|---|---|
| `runtime/deploy/robogame-chassis-rfcomm` | `/usr/local/sbin/robogame-chassis-rfcomm` | RFCOMM 链路常驻维护脚本（`chmod +x`） |
| `runtime/deploy/robogame-chassis-rfcomm.service` | `/etc/systemd/system/` | systemd unit，`systemctl enable --now` |
| `runtime/deploy/99-robogame-chassis.rules` | `/etc/udev/rules.d/` | udev 建立 `/dev/robogame-chassis` 别名 |

维护脚本会**从 `/home/pi/robogame-runtime/config/runtime.yaml` 读 MAC 和 SDP 通道**
（读到就用，读不到用写死的默认值），所以 `runtime/` 要先部署。

### 2.4 需要手工做的（无法用文件覆盖）

- **蓝牙配对**：`JDY-31-SPP`，MAC `6E:53:BD:74:00:A7`。配对凭据由现场保管，不写入仓库。
  ```bash
  bluetoothctl
  power on
  agent on
  default-agent
  pair 6E:53:BD:74:00:A7
  trust 6E:53:BD:74:00:A7
  quit
  ```
  **不要假定 SDP 通道是 1**，用 `sdptool browse 6E:53:BD:74:00:A7` 查 `JL_SPP` 的实际通道。
  （维护脚本自己会 `trust`，但 `pair` 必须手工做一次。）
- **Python 虚拟环境** `~/rg-venv`：`python3 -m venv ~/rg-venv` +
  `~/rg-venv/bin/pip install -r runtime/requirements.txt`。需要系统包 `pigpio`。
- **pigpiod 开机启动**：用 `-s 1` 参数（1 µs 采样）。

### 2.5 还不知道的

- pigpiod 到底是怎么启的（systemd unit 名？`/etc/rc.local`？）
- 有没有仓库之外的自建服务、定时任务、`/etc/rc.local` 内容
- `~/rg-venv` 里到底装了哪些包（`requirements.txt` 是否完整）
- 蓝牙配对状态、各服务的 `is-enabled`

---

## 3. 下一轮拿到 SSH 后要做的事

1. 写 `pi-tools/_pi_setup_snapshot.py` —— **只读，不发任何运动命令**，一次性 dump：
   - `/boot/firmware/config.txt`、`cmdline.txt`、`/etc/rc.local`
   - `/etc/systemd/system/` 下所有 robogame/pigpio 相关 unit + `is-enabled` / `is-active`
   - `/etc/udev/rules.d/`、`/usr/local/sbin/` 下的 robogame 文件
   - `~/rg-venv/bin/pip freeze`
   - `bluetoothctl info 6E:53:BD:74:00:A7`（配对状态）
   - `systemctl list-units --type=service --state=running`
2. 把快照结果存进本目录
3. 用**真实输出**写 `install.sh`（幂等）和 `verify.sh`（只读）
4. 把这节标成"已完成"，删掉上面所有"不知道"

---

## 4. 目录内容

```
sd-boot-20260912/     2026-09-12 的 /boot 整份拷贝（80M）
                      绝大多数是原版固件，自定义内容只有 config.txt 末尾 3 行
                      等 install.sh 写完、确认没别的东西之后，这个目录可以删掉换成几 KB 的差异文件
```
