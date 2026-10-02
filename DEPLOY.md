# 部署手册

从一台干净机器开始，到能在浏览器里提问为止。**照着做就行，不需要懂 Docker。**

- 全程只需要两样东西：**装了 Docker**、**有一个模型 API Key**。
- 预计耗时 15–30 分钟，其中大部分是第一遍拉镜像 / 构建镜像的等待。
- 真正要你手填的只有 **1 行配置**（第 3 步）。

> 卡住了先跑 `./scripts/doctor.sh`——它会直接告诉你缺什么、怎么补，不用先会看日志。
> 它报 `✓` 就能启动；报 `✗` 就照着它给的 `→` 提示做。

---

## 0. 先确认你的机器够用

| 项 | 要求 | 不够会怎样 |
|---|---|---|
| 内存 | **≥ 8 GB**（默认部署实际约用 3–4 GB） | 容器无报错直接消失，或整机卡死 |
| 磁盘 | **≥ 20 GB 空闲** | 构建到一半失败 |
| 系统 | macOS / Windows / Linux，x86_64 或 arm64 | — |
| 网络 | 能拉 Docker 镜像，且能访问模型服务 | 镜像拉不下来 / 提问报网络错误 |

> Windows 用户：建议装 **WSL2**（Docker Desktop 会提示），本项目在 Linux 容器里跑，WSL2 下最顺。

---

## 1. 装 Docker

### macOS / Windows
1. 打开 https://www.docker.com/products/docker-desktop/ 下载 **Docker Desktop**，安装。
2. 启动它，等左上角鲸鱼图标**不再跳动**（表示引擎已就绪）。首次启动会要求授权，按提示给。

### Linux
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"     # 之后要重新登录一次，否则每次都要 sudo
```

### 验证装好了
```bash
docker --version
docker compose version
docker run --rm hello-world        # 看到 "Hello from Docker!" 即成功
```

三条都通过 → 进第 2 步。任何一条失败 → 先解决它，后面都会依赖 Docker。

---

## 2. 拿到代码

```bash
git clone https://github.com/lululu811/weknora-chenliitaz.git
cd weknora-chenliitaz
```

没有 `git`？在仓库页面点 **Code → Download ZIP**，解压后进入该目录，效果一样。

---

## 3. 填一个模型 API Key（唯一必填项）

```bash
cp .env.example .env
```

用任意文本编辑器打开刚生成的 `.env`，找到 **`D2. 内置模型声明式配置`** 那一段，把下面这行的
`#` 去掉并填上你的 key：

```bash
DASHSCOPE_API_KEY=sk-你的百炼key
```

**去哪里申请**：阿里云百炼控制台 https://bailian.console.aliyun.com/ → API-KEY → 创建。
有免费额度，够试。

> **为什么这步不能跳**：仓库里内置了 4 个模型（`config/builtin_models.yaml`），它们的 key
> 全部读这个变量，其中 3 个是**默认模型**（对话 qwen-plus、向量 text-embedding-v3、重排 gte-rerank）。
> 不填的话，它们会带着**字面量** `${DASHSCOPE_API_KEY}` 存进数据库——不报错、不提示，
> 直到你提问时才失败。`./scripts/doctor.sh` 会专门检查这一项。
>
> **不想用百炼**？两条路：① 用本地 Ollama（见 `.env.example` 的 `D1` 段与
> `config/builtin_models.yaml.example` 里的 local 示例）；② 先跳过这步启动，进界面后
> 在「设置 → 模型」里加任意 OpenAI 兼容模型。两条路都行，但初次部署建议先走上面这条最省事。

---

## 4. 启动

```bash
./scripts/doctor.sh      # 自检：环境、内存、磁盘、端口、compose 配置
docker compose up -d     # 启动
```

- `doctor.sh` 有 `✗` 就先别启动，照提示修。
- **第一次 `up` 会构建镜像**（`docker compose` 会用本仓库的源码本地构建，不会去拉上游镜像）。
  屏幕上会滚动大量日志，**10–25 分钟属正常**，取决于网速和 CPU。
- 看到各服务陆续 `Started` / `Healthy` 即可。

启动的是 **5 个服务**：

| 服务 | 作用 | 宿主机端口 |
|---|---|---|
| `frontend` | Web 界面 | **80** |
| `app` | 后端 API | 8080 |
| `docreader` | 文档解析 | 仅容器内（50051） |
| `postgres` | 数据库 | 仅容器内 |
| `redis` | 缓存与队列 | 仅容器内 |

---

## 5. 确认起来了

```bash
docker compose ps
```

期望：`app`、`postgres`、`docreader` 三行末尾出现 **(healthy)**。
`frontend` 没有健康检查，`Up` 即可。

`app` 首次启动要跑数据库迁移，**耐心等 1–2 分钟**（健康检查的 `start_period` 是 60 秒）。

再直接问一句后端：

```bash
curl -f http://localhost/health     # 应返回 200 与健康信息
```

通过 → 打开浏览器访问 **http://localhost** 。

> 打不开或健康检查一直不通过 → 跳到 **第 9 节**。

---

## 6. 第一次使用

打开 **http://localhost** 后会看到**登录页**：

1. 点 **「创建账户」**，用邮箱 + 密码注册（**第一个注册的用户就是管理员**）。
2. 按页面引导**创建工作区**。
3. 进入「知识库」→ **新建知识库**（向导里选模型与分块方式，默认值可直接用）。
4. **上传一个文档**（PDF / Word / txt / Markdown 都行），等状态变成"已完成"。
5. 在对话框里**提问**，答案应带引用出处。

到这一步，部署就成功了。

---

## 7. 日常操作

```bash
docker compose ps                 # 看状态
docker compose logs -f app        # 跟后端日志（Ctrl+C 退出，不影响服务）
docker compose stop               # 停止（保留数据）
docker compose start              # 再启动
docker compose down               # 停止并删除容器（数据仍在卷里）
docker compose up -d              # 重新拉起
```

### 备份与恢复

数据全在两个 Docker 卷里：`postgres-data`（数据库）、`data-files`（上传的文件）。

```bash
# 备份数据库（用户名/库名默认值见 .env：DB_USER / DB_NAME）
docker compose exec postgres pg_dump -U postgres WeKnora > backup-$(date +%F).sql

# 查看卷
docker volume ls | grep weknora
```

> ⚠️ **`docker compose down -v` 会删掉这两个卷，数据不可恢复。** 只有确实想清库重来时才用。

### 升级

```bash
git pull
docker compose build      # 用新代码重新构建镜像
docker compose up -d      # 重建容器（数据保留）
```

> ⚠️ 不要用 `docker compose pull`：本仓库的镜像名与上游相同（`wechatopenai/weknora-*`），
> `pull` 会把**上游的镜像**拉下来覆盖本地构建的版本，fork 的增量功能会静默消失。

升级前建议先备份数据库（见上）。

### 彻底重置

```bash
docker compose down       # 停服务，保留数据
docker compose down -v    # ⚠️ 连数据一起删
docker compose up -d      # 重新拉起并跑迁移
```

---

## 8. 改端口

默认占用宿主机 **80**（界面）与 **8080**（API）。被占用时在 `.env` 里改：

```bash
FRONTEND_PORT=8081
APP_PORT=8090
```

改完 `docker compose up -d`。**注意访问地址也要跟着改**：`http://localhost:8081`。

---

## 9. 常见问题

### 9.1 `Bind for 0.0.0.0:80 failed: port is already in use`

80 端口被占（常见：本机已有 nginx/Apache，或旧容器没停）。

```bash
lsof -i :80                # macOS/Linux 看谁占着
docker compose down        # 停本项目容器
# 或在 .env 里改 FRONTEND_PORT=8081（见第 8 节）
```

### 9.2 容器反复重启 / 一直不 healthy

看日志，别猜：

```bash
docker compose ps                       # 先看是哪个服务
docker compose logs --tail=100 app      # 后端
docker compose logs --tail=100 postgres # 数据库
```

`app` 首次启动慢是正常的（跑迁移），等 1–2 分钟再看。

### 9.3 `could not find .env` / compose 报错说找不到环境文件

第 3 步没做：

```bash
cp .env.example .env
```

### 9.4 界面能打开，但**提问报鉴权错误** / 模型列表里的 key 长得像 `${DASHSCOPE_API_KEY}`

第 3 步的模型 Key 没填（或填错）。回去把 `DASHSCOPE_API_KEY` 填上，然后：

```bash
docker compose up -d --force-recreate app
```

也可以在界面「设置 → 模型」里直接加一个你自己的模型（界面里配的会写进数据库，优先于内置项）。

### 9.5 文档上传后一直"解析中"

`docreader` 没起来或内存不足：

```bash
docker compose ps
docker compose logs --tail=50 docreader
docker stats --no-stream      # 看内存占用
```

内存紧张时可以在 `.env` 里减负：`WEKNORA_SANDBOX_MODE=disabled`、`ENABLE_GRAPH_RAG=false`。

### 9.6 部署完想省内存

- 确认没有用 `--profile full`（那是可选的 Milvus/Doris 全家桶，默认不需要）
- 向量库保持 `RETRIEVE_DRIVER=sqlite`（默认，零依赖、占用最低）

---

## 10. 可选：投研栈（A 股工具）

> **不关心 A 股数据的话，这一节整节可以跳过。** 默认部署完全不需要它。

这一栈提供行情 / 财报 / 指数查询、HALO 基本面评分、技术形态识别与 K 线复盘终端。
它需要一份**本仓库不分发**的本地 DuckDB 数据，所以：

- **不配置也能正常部署**：容器照常启动，投研工具返回「底层数据源不可用」的中文提示，
  知识库与对话主流程完全不受影响。（这是有意设计：工具注册是懒加载的。）
- 想启用：

```bash
# 1) 指定你的数据目录（含 *.duckdb 文件），写进 .env 或直接 export
export HITHINK_DB_DIR=/path/to/your/hithink-db

# 2) 带上 finance profile 启动
docker compose --profile finance up -d

# 3) 确认容器能看到数据（容器内路径固定为 /data/hithink）
docker compose --profile finance exec python-service ls -la /data/hithink
```

未设置 `HITHINK_DB_DIR` 时，会挂载仓库内的空目录 `.hithink-placeholder`——占位、不报错、查不到数据。

### 10.1 工具说「底层数据源不可用」

**预期行为，不是故障**：`HITHINK_DB_DIR` 没配，或目录里没有 `.duckdb` 文件。按上面第 1 步配好即可。

### 10.2 某个股票代码查不到

部分标的无数据或已退市，工具会明确说明，属正常。

### 10.3 想同时开联网搜索

```bash
docker compose --profile searxng up -d
# 可组合：docker compose --profile finance --profile searxng up -d
```

---

## 11. 还是解决不了

把下面这些一并贴出来提问，最快定位：

```bash
./scripts/doctor.sh
docker compose version
docker version --format '{{.Server.Version}}'
docker compose ps
docker compose logs --tail=200 app
uname -a
```
