# 陶片拼接工作台（Pottery Fragment Assembly）

面向文物修复人员的陶片拼接 Web 应用：以**毫米**为单位录入陶片闭合轮廓与边缘标记，
为两片陶片指定至少两处对应标记生成候选拼接，系统用**仅平移 + 旋转**的刚体变换
预览拼接效果，展示各片位置、角度及全部冲突原因；满足约束后方可采纳。

- 后端：FastAPI + SQLite（标准库 `sqlite3`，WAL 模式）
- 前端：浏览器二维画布（原生 Canvas 2D + 原生 JS，无构建步骤）
- 几何：二维 Kabsch 刚体拟合（无缩放）、多边形自交检测、轮廓内部重叠检测
- 部署：Dockerfile + Docker Compose，一键启动并自动初始化演示数据

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

启动后访问：

- 应用页面：<http://localhost:8000/>
- 接口文档（Swagger UI）：<http://localhost:8000/docs>
- 健康/配置接口：<http://localhost:8000/api/config>

停止：

```bash
docker compose down            # 保留数据卷
docker compose down -v         # 同时删除数据库数据卷
```

## 本地直接运行（不使用 Docker）

需要 Python 3.11+：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

首次启动自动建表；数据库为空时写入演示数据（两片 60×25 mm 矩形陶片
与一个可预览/采纳的候选关系，采纳后 B 旋转 -90° 平移到 A 右侧）。

运行测试：

```bash
pip install pytest
python -m pytest tests/ -q
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `POTTERY_DB` | `app/data/pottery.db`（本地）/ `/data/pottery.db`（容器） | SQLite 数据库文件路径 |

服务监听端口：**8000**（Compose 已映射到宿主机同名端口；如要改宿主机端口，
修改 `docker-compose.yml` 的 `ports`，如 `"18000:8000"`）。

业务常量（在 `app/store.py` 顶部，按需修改）：

- `JOIN_TOL_MM = 1.0`：采纳时同组对应点误差上限（毫米）
- `MARKER_TOL_MM = 0.5`：录入标记时判定“点在边缘上”的容差（毫米）

## 页面与端口

| 地址 | 内容 |
| --- | --- |
| `http://localhost:8000/` | 工作台页面（录入 / 候选 / 预览 / 采纳 / 撤销） |
| `http://localhost:8000/api/state` | 全量状态（陶片、世界位姿、标记、关系及版本号） |
| `http://localhost:8000/docs` | OpenAPI 交互文档 |

## 操作流程

1. **录入陶片**：在编辑器画布点击依次添加轮廓顶点（≥3，自动闭合），
   或在文本框粘贴 `x, y` 坐标（每行一个，单位毫米）；切换到“标记模式”后
   点击轮廓边缘（自动吸附到最近边）添加命名标记。提交时服务端校验：
   顶点数、退化面积、**轮廓自交**（整体拒绝）；标记必须落在边缘上且
   **不得重合**。
2. **创建候选拼接**：选择两片陶片 A（固定组）、B（可整体移动组），
   按相同顺序在两侧各选**至少 2 个**对应边缘标记。
3. **预览**：系统求二维刚体变换（仅平移 + 旋转），展示
   - 整体平移量 (Δx, Δy) 与旋转角 θ；
   - 被移动组中**每一片**陶片的当前/预览位置（毫米）与角度（度），
     组内相对位姿保持不变；
   - 每组对应点的毫米误差及是否超过 1 mm；
   - 冲突原因列表：`ERROR_OVER_LIMIT`（误差超限）、`COLLISION`（同组轮廓内部重叠）。
   世界视图以紫色虚线叠加 B 组预览位姿。
4. **采纳**：仅当所有对应点误差 ≤ 1 mm 且同组轮廓**内部不重叠**
   （允许共享边/点接触）时按钮可用。采纳在单个事务内完成：整体移动 B 组、
   合并连通组、关系置为已采纳。任一条件不满足则**整次拒绝，原拼接组不变**。
5. **闭环关系**：两片已在同一连通组时，预览进入 `CLOSURE` 模式——
   **不移动任何陶片**，只校核当前世界位姿是否满足 1 mm 阈值；不满足则拒绝。
6. **撤销关系**：删除已采纳关系后，按其余已采纳关系重新计算连通分量；
   分裂出的孤片恢复独立组（组号 = 陶片 id），**世界位姿保留**。
7. **并发/过期保护**：每个关系带 `version`，全局状态带 `state_version`；
   基于过期版本的采纳或撤销返回 `409 VERSION_CONFLICT / PREVIEW_STALE`，
   前端提示冲突并要求刷新预览。

## REST API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/config` | 阈值、单位、数据库路径 |
| GET | `/api/state` | 全量状态与全局版本号 |
| POST | `/api/fragments` | 录入陶片（含轮廓与标记） |
| POST | `/api/fragments/{id}/markers` | 追加边缘标记 |
| POST | `/api/candidates` | 创建候选关系（≥2 组对应标记），返回即时预览 |
| GET | `/api/relations/{id}/preview` | 重新计算预览（平移+旋转、误差、冲突） |
| POST | `/api/relations/{id}/adopt` | 采纳（body：`{"version":n,"state_version":v}`） |
| POST | `/api/relations/{id}/undo` | 撤销/删除（body：`{"version":n}`），重算连通组 |

## 目录结构

```
app/
  main.py        # FastAPI 入口与 API
  store.py       # 领域逻辑：校验、预览、采纳、撤销、连通组重算
  geometry.py    # 刚体拟合(Kabsch)、自交、多边形内部重叠判定
  db.py          # SQLite 连接、建表、版本号
  seed.py        # 首次启动演示数据
  static/        # 前端（index.html / app.js / style.css）
tests/           # 业务规则集成测试（18 项）
Dockerfile
docker-compose.yml
requirements.txt
```
