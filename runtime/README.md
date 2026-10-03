# Runtime

`runtime/` 是部署到树莓派 `/home/pi/robogame-runtime` 的自动化程序。完整架构、视觉实现、动作包格式和部署流程见仓库根目录的 [README.md](../README.md)。

## 运行

```powershell
python run_route_v2.py --dry-run --full --config config/route_v2.yaml
python run_route_v2.py --full --config config/route_v2.yaml
```

现场参数位于 `config/route_v2.yaml` 和 `config/runtime.yaml`。动作包唯一来源是 `data/route_v2_actions/catalog.json`。

## 运行时约束

- 库存固定为左槽、右槽和吸盘槽，最多三个方块。
- 路线状态机负责路线与停止条件，`RouteRunner` 负责设备 I/O、动作确认、遥测和安全清理。
- 视觉采用独立相机/识别线程；AprilTag 用连续帧确认，方块识别使用配置化颜色 profile、ROI 和轮廓 gate。
- 搭建区当前位置按里程定位；动作切换不会隐式关闭吸盘。
- `logs/`、`data/control_hub/` 和 `data/route_capture/` 是现场运行产物，不应提交到 Git。
