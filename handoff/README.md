# 本地交接入口

这里维护树莓派项目的现场交接资料。根目录 `README.md` 只作为仓库标语；开发和部署请从本页进入。

## 先读

1. [`交接文档.md`](交接文档.md)：当前 Route v2 自动化逻辑、库存规则、停止条件和已知缺口。
2. [`route_v2.md`](route_v2.md)：Route v2 的快速入口和关键约束。
3. [`pi-setup/README.md`](pi-setup/README.md)：树莓派环境恢复、部署文件和待补的只读快照流程。

## 代码对应关系

- 自动化程序：`runtime/run_route_v2.py`
- 路线状态机与策略：`runtime/route_v2/`
- 控制台：`runtime/control_hub/`，入口为 `runtime/run_control_hub.py`
- 树莓派部署文件：`runtime/deploy/`
- Windows 侧树莓派操作工具：`pi-tools/`

## 现场地址

- 树莓派：`172.20.10.11`
- 控制台：`http://172.20.10.11:8080/operate`
- 底盘蓝牙 MAC：`6E:53:BD:74:00:A7`

密码、配对码和其他凭据由现场保管，不写入仓库。
