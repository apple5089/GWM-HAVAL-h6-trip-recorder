# 安装与使用说明

## 1. 环境和首次启动

推荐使用持续联网的 NAS 或 Linux 主机，提供 Docker Engine、Compose v2，以及至少 384 MB 可用容器内存。本地开发需要 Python 3.13+。

按 [README](../README.md#快速启动) 启动容器。首次构建会从 Python 镜像和 PyPI 安装依赖，使用仓库自带的中文字体。镜像构建不会读取你的账号、API 密钥或车辆客户端。

默认地址为 `http://localhost:18770`，端口只绑定本机。NAS 内网访问示例：

```dotenv
BIND_IP=NAS_LAN_IP
HOST_PORT=18770
PUBLIC_ORIGIN=http://NAS_LAN_IP:18770
```

将 `NAS_LAN_IP` 替换成自己的地址；`PUBLIC_ORIGIN` 必须与浏览器地址的协议、主机和端口一致。Host 错误会返回 403，写操作还会核对浏览器 Origin。通过反向代理访问时，代理应提供访问认证并正确传递 Host。

## 2. 车辆登录与记录

先完成 [车辆客户端接入](VEHICLE_CLIENT.md)。在管理页输入本人车辆账号手机号，主动请求短信，再输入验证码。登录后选择车辆、设置 60–3600 秒采样间隔并开启持续记录。

账号和会话保存在私有数据卷中，重启后尝试恢复。服务只保存有效、时间新鲜的新样本；重复、倒序、陈旧定位和明显跳点会过滤。官方云端不更新定位时，管理页保留末次观测，不补造过去轨迹。

停车使用位置稳定规则；已验证的车辆可通过服务的停车校准接口配置联合规则。开源包不带任何车主的发动机校准、门窗基准或车辆标识。门窗提醒需本人确认全部关闭后，在页面建立当前车辆的关闭基准。

## 飞书推送

### 应用机器人：文字和图片

1. 在 [飞书开放平台](https://open.feishu.cn/app) 创建企业自建应用并启用机器人。
2. 为应用开通发送机器人消息及图片上传所需权限，通常涉及 `im:message:send_as_bot` 和 `im:resource`；以平台实际展示的 API 权限要求为准。完成租户要求的发布或审批。
3. 将机器人加入接收群聊，取得该群聊的 `chat_id`，格式为 `oc_...`。可按 [群聊 ID 获取说明](https://open.feishu.cn/document/server-docs/group/chat/chat-id-description) 操作。
4. 在管理页填写 App ID、App Secret、群聊 ID，点击“保存应用机器人配置”。
5. 开启行程通知和轨迹图片推送，并保存设置。需要验证通道时，主动点击“发送一条通道测试”。

App ID、App Secret 和群聊 ID 都保存在本地加密配置中，API 状态响应不回传这些值。更新配置时留空可保留原值；变更接收群时重新填写群聊 ID。

图片上传出现 `99991672` 时，检查资源权限是否已生效，随后点击页面的图片重试按钮。发送消息和上传图片分别参考飞书 [消息 API](https://open.feishu.cn/document/server-docs/im-v1/message/create) 与 [图片 API](https://open.feishu.cn/document/server-docs/im-v1/image/create)。

### 自定义机器人 Webhook：文字

在页面填写自己的飞书 HTTPS Webhook；机器人启用签名时一并填写签名密钥。Webhook 通道用于文字摘要和提醒；图片推送使用上述应用机器人。配置应用机器人后，它会成为优先通知通道。

行程的定位移动观测累计严格超过 60 秒才进入推送流程。通知写入持久队列，失败退避重试，事件去重；补发按钮只恢复当前图片版本尚未送达的行程。

## 4. 腾讯地图、Kimi 和天气

### 腾讯道路底图与地名

在 [腾讯位置服务控制台](https://lbs.qq.com/) 创建自己的 Web 服务 Key，按服务要求启用静态地图与地名查询；使用签名校验时填写 SK。页面中勾选道路底图、地名查询并保存。

腾讯静态图请求只包含中心点、缩放和图片尺寸，轨迹在本机叠加。地名查询会发送起点、终点及需要标注的停留点坐标。原始坐标系应按实际来源选择，图片不执行道路吸附，也不补断档。

静态图缓存 30 天，每日本机最多请求 100 张新图；地名相关请求每日本机最多 64 次。平台自身配额、价格和授权范围以各自账号为准，见 [静态地图文档](https://lbs.qq.com/service/staticV2/staticGuide/staticDoc)。

### Kimi 游戏风格标题

填写自己的 Kimi Key 和可用模型名，开启标题生成。程序仅发送城市/商圈概括、固定地点类别、距离、用时和天气，不发送详细地址、校名、轨迹坐标或车辆账号。标题每日最多请求 20 次并缓存；不可用时使用本地规则生成游戏风格标题。

### 天气

开启天气显示后，按北京时间的行程日期和时段匹配终点粗网格天气、高低温，使用 [Open-Meteo](https://open-meteo.com/) 的个人免费接口，每日本机最多 64 次。经纬度各保留一位小数。天气结果为模型数据，失败不影响轨迹记录。商业用途或更高额度请按服务方方案配置。

网页交互底图来自 OpenStreetMap，自动推送 PNG 的道路底图来自腾讯，两者独立。关闭网页道路底图可停止后续浏览器瓦片请求。

## 5. 图片、长途和导出

在行程列表选择记录，点击“预览轨迹 PNG”。图片包括距离、用时、均速、剩余油量、油表估算用油、近期估算油耗和天气。定位断档保留断线，停车待确认时使用中断状态横幅。

50 km 以上行程生成约 5 km 的可选分段；自动发送总览及最多 3 张精选放大图。持续约 3 分钟且之后恢复移动的位置停留会标注；缓行也可能表现为停留，因此标签保持推定表述。

CSV 导出包含原始坐标和采样时间，应作为私有车辆数据保管。当前没有自动历史删除策略，请根据使用频率安排备份和存储空间。

## 备份与升级

数据位于 Compose 的 `h6_data` 命名卷，保留历史轨迹、通知队列、图片、设置与加密会话。使用以下步骤做一致备份；命令在项目目录执行：

```sh
umask 077
docker compose stop h6-recorder
mkdir -p backups
docker compose run --rm -T --no-deps --user 0:0 --entrypoint tar h6-recorder -C /data -czf - . > backups/h6-data-backup.tar.gz
docker compose up -d
```

备份包含 `.secrets/master.key` 与 `account.enc`，必须一起保管。备份文件具有账号访问能力，请限制文件权限，不要上传 GitHub。运行中的 SQLite 不能只复制主文件而忽略 WAL；上述停机导出包含整个数据卷。

升级前备份，随后执行：

```sh
git pull --ff-only
docker compose up -d --build
docker compose ps
```

升级不删除数据卷。`docker compose down -v` 会删除持久数据，请仅在明确要清空时使用。

## 7. 常见问题

| 现象 | 检查 |
| --- | --- |
| 网页提示未接入客户端 | `GWM_CLIENT_HOST_PATH` 是否指向包的父目录，挂载后是否重启 |
| 页面或 API 返回 403 | 浏览器地址、`PUBLIC_ORIGIN`、端口及代理 Host 是否一致 |
| 定位不增加 | 云端时间是否变化；数据是否超过 5 分钟、重复或无效 |
| 无道路图片 | 腾讯 Key、签名、配额、网络，以及页面道路状态 |
| 文字能发，图片不能发 | 应用机器人和资源权限；Webhook 仅支持文字 |
| 标题仍为默认 | Kimi 是否启用、Key 与模型是否可用，是否达到本机日配额 |
| 油耗显示积累中 | 近期有效里程是否达到 10 km，油量是否下降，加油后是否重新积累 |
| 重启后登录未恢复 | 数据卷、主密钥、加密会话是否配套，云端是否已撤销会话 |

## 8. HTTP 接口

管理页和 API 共用访问边界。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /health` | 健康检查 |
| `GET /api/state` | 状态、设置、行程摘要 |
| `GET /api/points?trip=ID` | 行程原始点 |
| `GET /api/trip-image.png?trip=ID` | 行程图片，分段可加 `segment=N` |
| `GET /api/trip-pages?trip=ID` | 分段目录 |
| `GET /api/export.csv?trip=ID` | 私有 CSV 导出 |
| `POST /api/feishu_app` | 保存应用机器人配置 |
| `POST /api/trip_services` | 保存地图、标题和天气配置 |

POST 使用 JSON，且要求与 `PUBLIC_ORIGIN` 一致的 `Origin`。API 不回显保存的密钥。
