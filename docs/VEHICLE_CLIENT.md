# 车辆客户端接入

记录器与车辆协议实现分开分发。仓库中的 SQLite、网页、绘图、天气和通知功能可独立运行，实时车辆采集需要兼容的 `gwm_client` Python 包。

## 兼容基线

适配层参考 [moryoav/ha-gwm-ev](https://github.com/moryoav/ha-gwm-ev) 的提交 `82883e801b59b025a6a6c21a2b3179643db78040`。该来源的包元数据同时声明 `MIT AND LicenseRef-GWM-Protocol-Materials`，其 `THIRD_PARTY_NOTICES.md` 记录了尚待解决的协议材料再分发条件。因此，本仓库不包含该包，也不在安装、镜像构建或启动时自动下载它。

请使用你有权使用的兼容客户端。仅有本仓库的 MIT 许可证不代表已经取得外部客户端或车辆服务的使用、再分发许可。

## Docker 接入

将客户端包放在本机单独目录：

```text
external/vehicle-client/
└── gwm_client/
    ├── __init__.py
    ├── china_client.py
    ├── china_transport.py
    ├── errors.py
    └── ...客户端自身需要的其他模块
```

`.env` 中的 `GWM_CLIENT_HOST_PATH` 指向 `gwm_client/` 的父目录，默认是 `./external/vehicle-client`。目录通过只读卷挂载到容器中，不会被复制进镜像或 Git 仓库。

```sh
docker compose up -d --force-recreate
docker compose exec h6-recorder python -c "import vehicle_client; print(vehicle_client.available())"
```

输出 `True` 表示包目录可以被发现；实际登录还会检查其模块和接口是否兼容。客户端需要额外依赖时，请在自己的镜像构建配置中安装。

## 本地 Python 接入

将客户端包的父目录加入 `PYTHONPATH`，再启动应用。例如 Linux/macOS：

```sh
PYTHONPATH=/path/to/vehicle-client python app.py
```

Windows PowerShell：

```powershell
$env:PYTHONPATH='C:\path\to\vehicle-client'
python app.py
```

## 适配接口与只读边界

`vehicle_client.py` 懒加载账号状态、认证结果类型、HTTP 传输及兼容发现类。`discovery_compat.py` 依赖兼容基线的发现解析接口；替换实现时应同步调整适配层。

车辆请求通过 `read_guard.py`：仅允许短信请求、登录、刷新、初始化、车辆发现和状态读取。记录器没有开锁、启动、鸣笛、门窗或空调控制入口。云端撤销会话后，需要在管理页重新完成验证；程序重启不会自动发送验证码。

使用自己的车辆和账号。不同车型先验证里程、油量单位、坐标系、发动机枚举和停车样本，再启用相应提醒。
