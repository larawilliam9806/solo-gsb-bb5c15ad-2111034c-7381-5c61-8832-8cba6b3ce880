"""业务规则集成测试（FastAPI TestClient + 临时 SQLite）。"""
from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("POTTERY_DB", db_path)
    # 延迟导入以读取环境变量
    import importlib
    from app import main
    importlib.reload(main)
    main.DB_PATH = db_path
    # 启动事件中自动建表；不预置种子数据
    monkeypatch.setattr(main.seed, "seed_if_empty", lambda conn: False)
    with TestClient(main.app) as c:
        yield c


def rect_markers_a(c):
    """A: 60x25 矩形，标记在右边 x=60"""
    r = c.post("/api/fragments", json={
        "name": "A",
        "contour_mm": [[0, 0], [60, 0], [60, 25], [0, 25]],
        "markers": [
            {"label": "a1", "x": 60, "y": 5},
            {"label": "a2", "x": 60, "y": 20},
        ]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def rect_b(c):
    r = c.post("/api/fragments", json={
        "name": "B",
        "contour_mm": [[0, 0], [60, 0], [60, 25], [0, 25]],
        "markers": [
            {"label": "b1", "x": 5, "y": 0},
            {"label": "b2", "x": 20, "y": 0},
        ]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def markers(c, fid):
    st = c.get("/api/state").json()
    f = next(f for f in st["fragments"] if f["id"] == fid)
    return {m["label"]: m["id"] for m in f["markers"]}


# ---------------------------------------------------------------------------

def test_health_and_seed(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["join_tolerance_mm"] == 1.0


def test_self_intersecting_contour_rejected(client):
    # 非退化的蝴蝶结自交五边形（面积非零但边互相穿越）
    r = client.post("/api/fragments", json={
        "name": "bowtie",
        "contour_mm": [[0, 0], [10, 10], [20, 0], [20, 10], [0, 10]]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CONTOUR_SELF_INTERSECT"


def test_degenerate_contour_rejected(client):
    r = client.post("/api/fragments", json={
        "name": "line",
        "contour_mm": [[0, 0], [10, 0], [5, 0]]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("CONTOUR_DEGENERATE", "CONTOUR_TOO_SHORT")


def test_marker_must_be_on_edge(client):
    r = client.post("/api/fragments", json={
        "name": "A",
        "contour_mm": [[0, 0], [60, 0], [60, 25], [0, 25]],
        "markers": [{"label": "m", "x": 30, "y": 12}]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MARKER_NOT_ON_EDGE"


def test_marker_coincident_rejected(client):
    r = client.post("/api/fragments", json={
        "name": "A",
        "contour_mm": [[0, 0], [10, 0], [10, 10], [0, 10]],
        "markers": [
            {"label": "m1", "x": 5, "y": 0},
            {"label": "m2", "x": 5, "y": 0}]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MARKER_DUPLICATE"


def test_candidate_requires_two_pairs(client):
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    r = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b, "pairs": [[ma["a1"], mb["b1"]]]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "PAIRS_TOO_FEW"


def test_marker_wrong_fragment_pair_rejected(client):
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    # 用 A 的标记充当 B 侧
    r = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a1"], ma["a2"]], [ma["a2"], mb["b1"]]]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MARKER_WRONG_FRAGMENT"


def test_preview_rotation_translation_and_adopt(client):
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    r = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a2"], mb["b1"]], [ma["a1"], mb["b2"]]]})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    pv = r.json()["preview"]
    assert pv["valid"] is True, pv
    assert pv["mode"] == "MERGE"
    # -90° 旋转，平移 (60,25)，B 移至 A 右侧共享竖边
    t = pv["transform"]
    assert abs(t["rotation_deg"] - (-90)) < 1e-6
    assert abs(t["dx_mm"] - 60) < 1e-6
    assert abs(t["dy_mm"] - 25) < 1e-6
    for p in pv["pairs"]:
        assert p["error_mm"] < 1e-6

    ver = pv["version"]
    ar = client.post(f"/api/relations/{rid}/adopt",
                     json={"version": ver, "state_version": pv["state_version"]})
    assert ar.status_code == 200, ar.text
    body = ar.json()
    assert body["adopted"] is True
    # 采纳后两片同组、B 位姿被更新
    st = body["state"]
    fa = next(f for f in st["fragments"] if f["id"] == a)
    fb = next(f for f in st["fragments"] if f["id"] == b)
    assert fa["group_id"] == fb["group_id"]
    assert abs(fb["pose"]["angle_deg"] - (-90)) < 1e-6
    assert abs(fb["pose"]["x_mm"] - 60) < 1e-6
    assert abs(fb["pose"]["y_mm"] - 25) < 1e-6


def test_stale_version_adopt_conflict(client):
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    rid = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a2"], mb["b1"]], [ma["a1"], mb["b2"]]]}).json()["id"]
    pv = client.get(f"/api/relations/{rid}/preview").json()
    # 用过期版本号（99）采纳 -> 冲突
    r = client.post(f"/api/relations/{rid}/adopt",
                    json={"version": 99, "state_version": pv["state_version"]})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "VERSION_CONFLICT"
    # 原拼接组不变
    st = client.get("/api/state").json()
    groups = {f["id"]: f["group_id"] for f in st["fragments"]}
    assert groups[a] != groups[b]


def test_stale_state_version_conflict(client):
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    rid = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a2"], mb["b1"]], [ma["a1"], mb["b2"]]]}).json()["id"]
    r = client.post(f"/api/relations/{rid}/adopt",
                    json={"version": 1, "state_version": 999})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PREVIEW_STALE"


def test_error_over_limit_rejected_and_state_unchanged(client):
    # A 的两个标记 15mm 间距；B 的两个标记 10mm 间距 -> 刚体拟合必有残差
    a = client.post("/api/fragments", json={
        "name": "A",
        "contour_mm": [[0, 0], [60, 0], [60, 25], [0, 25]],
        "markers": [{"label": "a1", "x": 60, "y": 5},
                    {"label": "a2", "x": 60, "y": 20}]}).json()["id"]
    b = client.post("/api/fragments", json={
        "name": "B",
        "contour_mm": [[0, 0], [60, 0], [60, 25], [0, 25]],
        "markers": [{"label": "b1", "x": 5, "y": 0},
                    {"label": "b2", "x": 15, "y": 0}]}).json()["id"]
    ma, mb = markers(client, a), markers(client, b)
    rid = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a2"], mb["b1"]], [ma["a1"], mb["b2"]]]}).json()["id"]
    pv = client.get(f"/api/relations/{rid}/preview").json()
    assert pv["valid"] is False
    assert any(c["type"] == "ERROR_OVER_LIMIT" for c in pv["conflicts"])
    max_err = max(p["error_mm"] for p in pv["pairs"])
    assert max_err > 1.0

    r = client.post(f"/api/relations/{rid}/adopt",
                    json={"version": 1, "state_version": pv["state_version"]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ADOPT_REJECTED"
    # 关系仍为 proposed，位姿不变
    st = client.get("/api/state").json()
    rel = next(x for x in st["relations"] if x["id"] == rid)
    assert rel["status"] == "proposed"
    fb = next(f for f in st["fragments"] if f["id"] == b)
    assert fb["pose"] == {"x_mm": 0.0, "y_mm": 0.0, "angle_deg": 0.0}


def test_collision_rejected(client):
    # 两个大正方形，标记对应会导致移动后严重重叠
    a = client.post("/api/fragments", json={
        "name": "bigA",
        "contour_mm": [[0, 0], [50, 0], [50, 50], [0, 50]],
        "markers": [{"label": "a1", "x": 10, "y": 0},
                    {"label": "a2", "x": 40, "y": 0}]}).json()["id"]
    b = client.post("/api/fragments", json={
        "name": "bigB",
        "contour_mm": [[0, 0], [50, 0], [50, 50], [0, 50]],
        # 标记在 B 内部边（底边），对应到底边但朝向相同 -> 重叠
        "markers": [{"label": "b1", "x": 10, "y": 0},
                    {"label": "b2", "x": 40, "y": 0}]}).json()["id"]
    ma, mb = markers(client, a), markers(client, b)
    rid = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a1"], mb["b1"]], [ma["a2"], mb["b2"]]]}).json()["id"]
    pv = client.get(f"/api/relations/{rid}/preview").json()
    assert pv["valid"] is False
    assert any(c["type"] == "COLLISION" for c in pv["conflicts"])
    r = client.post(f"/api/relations/{rid}/adopt",
                    json={"version": 1, "state_version": pv["state_version"]})
    assert r.status_code == 422


def test_shared_edge_is_not_collision(client):
    """相邻拼接（仅共边）必须通过，不能误判碰撞。"""
    a = rect_markers_a(client)
    # B 放在 A 上方：B 的底边两个标记对应 A 顶边，同朝向 -> 平移 (0,25)
    b = client.post("/api/fragments", json={
        "name": "Btop",
        "contour_mm": [[0, 0], [60, 0], [60, 25], [0, 25]],
        "markers": [{"label": "b1", "x": 10, "y": 0},
                    {"label": "b2", "x": 50, "y": 0}]}).json()["id"]
    st0 = client.get("/api/state").json()
    aid_markers = {m["label"]: m["id"]
                   for f in st0["fragments"] if f["id"] == a for m in f["markers"]}
    # A 顶边需要两个标记，单独加
    for x in (10, 50):
        client.post(f"/api/fragments/{a}/markers", json={"label": f"top{x}", "x": x, "y": 25})
    st = client.get("/api/state").json()
    fa = next(f for f in st["fragments"] if f["id"] == a)
    fb = next(f for f in st["fragments"] if f["id"] == b)
    ma = {m["label"]: m["id"] for m in fa["markers"]}
    mb = {m["label"]: m["id"] for m in fb["markers"]}
    rid = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["top10"], mb["b1"]], [ma["top50"], mb["b2"]]]}).json()["id"]
    pv = client.get(f"/api/relations/{rid}/preview").json()
    assert pv["valid"] is True, pv["conflicts"]
    assert abs(pv["transform"]["dy_mm"] - 25) < 1e-6


def test_group_moves_rigidly_and_closure(client):
    # A+B 先拼好；再取 C 与 B 拼接（B 所在的整组固定，C 移动）
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    rid1 = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a2"], mb["b1"]], [ma["a1"], mb["b2"]]]}).json()["id"]
    pv1 = client.get(f"/api/relations/{rid1}/preview").json()
    client.post(f"/api/relations/{rid1}/adopt",
                json={"version": 1, "state_version": pv1["state_version"]})

    # C：与 A 相同形状，但放在右侧 (80,0) 附近、与 A 上边闭环标记对应
    c = client.post("/api/fragments", json={
        "name": "C",
        "contour_mm": [[0, 0], [60, 0], [60, 25], [0, 25]],
        "markers": [{"label": "c1", "x": 60, "y": 5},
                    {"label": "c2", "x": 60, "y": 20}]}).json()["id"]
    st = client.get("/api/state").json()
    fa = next(f for f in st["fragments"] if f["id"] == a)
    fc = next(f for f in st["fragments"] if f["id"] == c)
    ma2 = {m["label"]: m["id"] for m in fa["markers"]}
    mc = {m["label"]: m["id"] for m in fc["markers"]}
    # C 与 A 相同标记几何 -> 平移 0，直接贴合（同位置会碰撞）
    rid2 = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": c,
        "pairs": [[ma2["a1"], mc["c1"]], [ma2["a2"], mc["c2"]]]}).json()["id"]
    pv2 = client.get(f"/api/relations/{rid2}/preview").json()
    # 完全重合 -> 碰撞
    assert any(x["type"] == "COLLISION" for x in pv2["conflicts"])


def test_undo_recomputes_groups_and_singleton(client):
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    rid = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a2"], mb["b1"]], [ma["a1"], mb["b2"]]]}).json()["id"]
    pv = client.get(f"/api/relations/{rid}/preview").json()
    client.post(f"/api/relations/{rid}/adopt",
                json={"version": 1, "state_version": pv["state_version"]})
    st = client.get("/api/state").json()
    assert next(f for f in st["fragments"] if f["id"] == a)["group_id"] == \
           next(f for f in st["fragments"] if f["id"] == b)["group_id"]

    # 撤销（需要用采纳后的新版本号 2）
    r = client.post(f"/api/relations/{rid}/undo", json={"version": 2})
    assert r.status_code == 200
    st = r.json()["state"]
    ga = next(f for f in st["fragments"] if f["id"] == a)["group_id"]
    gb = next(f for f in st["fragments"] if f["id"] == b)["group_id"]
    assert ga != gb
    # 孤片恢复独立组（组号等于自身 id）
    assert ga == a and gb == b
    # B 的世界位姿保留（没有重置）
    fb = next(f for f in st["fragments"] if f["id"] == b)
    assert abs(fb["pose"]["angle_deg"] - (-90)) < 1e-6


def test_undo_stale_version_conflict(client):
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    rid = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a2"], mb["b1"]], [ma["a1"], mb["b2"]]]}).json()["id"]
    r = client.post(f"/api/relations/{rid}/undo", json={"version": 99})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "VERSION_CONFLICT"
    # 关系仍在
    assert client.get(f"/api/relations/{rid}/preview").status_code == 200


def test_closure_must_fit_existing_pose(client):
    """A-B 已拼（B 在 A 右侧，世界 x∈[60,85],y∈[-35,25]）；
    再把 C 拼到 B 上方（世界 y∈[25,50]）使三片同组。
    随后 A-C 关系成为闭环：闭环不得移动，只能校核已有位姿。"""
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    rid1 = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a2"], mb["b1"]], [ma["a1"], mb["b2"]]]}).json()["id"]
    pv1 = client.get(f"/api/relations/{rid1}/preview").json()
    client.post(f"/api/relations/{rid1}/adopt",
                json={"version": 1, "state_version": pv1["state_version"]})

    # B 局部左边 (0,y) 经 (-90°,(60,25)) -> 世界 (60+y,25)，
    # 即 B 的世界水平顶边 y=25，x∈[60,85]。在该边加两个标记。
    client.post(f"/api/fragments/{b}/markers",
                json={"label": "t0", "x": 0, "y": 0})    # 世界 (60,25)
    client.post(f"/api/fragments/{b}/markers",
                json={"label": "t15", "x": 0, "y": 15})  # 世界 (75,25)

    # C: 25x25，底边标记 (0,0),(15,0)，平移 (60,25) 后位于 B 正上方，
    # 占据 x∈[60,85],y∈[25,50]，仅与 B 共享水平边，无内部重叠。
    c = client.post("/api/fragments", json={
        "name": "C",
        "contour_mm": [[0, 0], [25, 0], [25, 25], [0, 25]],
        "markers": [{"label": "c1", "x": 0, "y": 0},
                    {"label": "c2", "x": 15, "y": 0}]}).json()["id"]
    st = client.get("/api/state").json()
    fb = next(f for f in st["fragments"] if f["id"] == b)
    fc = next(f for f in st["fragments"] if f["id"] == c)
    mt = {m["label"]: m["id"] for m in fb["markers"]}
    mc = {m["label"]: m["id"] for m in fc["markers"]}
    rid2 = client.post("/api/candidates", json={
        "frag_a": b, "frag_b": c,
        "pairs": [[mt["t0"], mc["c1"]], [mt["t15"], mc["c2"]]]}).json()["id"]
    pv2 = client.get(f"/api/relations/{rid2}/preview").json()
    assert pv2["valid"] is True, pv2["conflicts"]
    assert abs(pv2["transform"]["dx_mm"] - 60) < 1e-6
    assert abs(pv2["transform"]["rotation_deg"]) < 1e-6
    r = client.post(f"/api/relations/{rid2}/adopt",
                    json={"version": 1, "state_version": pv2["state_version"]})
    assert r.status_code == 200, r.text
    st = r.json()["state"]
    fa = next(f for f in st["fragments"] if f["id"] == a)
    fc2 = next(f for f in st["fragments"] if f["id"] == c)
    assert fa["group_id"] == fc2["group_id"]

    # A-C 关系：三片同组 -> CLOSURE。C 的标记世界位置为 (45,25),(60,25)，
    # 而 A 的 a1(60,5),a2(60,20) 距离很远 -> 闭环残差超限，且不允许移动。
    rid3 = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": c,
        "pairs": [[ma["a1"], mc["c1"]], [ma["a2"], mc["c2"]]]}).json()["id"]
    pv3 = client.get(f"/api/relations/{rid3}/preview").json()
    assert pv3["mode"] == "CLOSURE"
    assert pv3["transform"] is None
    assert pv3["moving_fragments"] == []
    assert pv3["valid"] is False
    assert any(x["type"] == "ERROR_OVER_LIMIT" for x in pv3["conflicts"])
    r = client.post(f"/api/relations/{rid3}/adopt",
                    json={"version": 1, "state_version": pv3["state_version"]})
    assert r.status_code == 422
    # 位姿保持不变
    st2 = client.get("/api/state").json()
    fc3 = next(f for f in st2["fragments"] if f["id"] == c)
    assert abs(fc3["pose"]["x_mm"] - 60) < 1e-6
    assert abs(fc3["pose"]["y_mm"] - 25) < 1e-6

    # 删除该闭环候选（版本仍为 1）
    r = client.post(f"/api/relations/{rid3}/undo", json={"version": 1})
    assert r.status_code == 200


def test_chain_undo_middle_recomputes(client):
    """A-B-C 链采纳后撤销 A-B，应分裂为两个组：{A} 与 {B,C}，位姿保留。"""
    a = rect_markers_a(client)
    b = rect_b(client)
    ma, mb = markers(client, a), markers(client, b)
    rid1 = client.post("/api/candidates", json={
        "frag_a": a, "frag_b": b,
        "pairs": [[ma["a2"], mb["b1"]], [ma["a1"], mb["b2"]]]}).json()["id"]
    pv1 = client.get(f"/api/relations/{rid1}/preview").json()
    client.post(f"/api/relations/{rid1}/adopt",
                json={"version": 1, "state_version": pv1["state_version"]})

    # B 局部左边点映射到世界水平顶边 y=25（x∈[60,85]）
    client.post(f"/api/fragments/{b}/markers",
                json={"label": "t0", "x": 0, "y": 0})
    client.post(f"/api/fragments/{b}/markers",
                json={"label": "t15", "x": 0, "y": 15})
    c = client.post("/api/fragments", json={
        "name": "C",
        "contour_mm": [[0, 0], [25, 0], [25, 25], [0, 25]],
        "markers": [{"label": "c1", "x": 0, "y": 0},
                    {"label": "c2", "x": 15, "y": 0}]}).json()["id"]
    st = client.get("/api/state").json()
    fb = next(f for f in st["fragments"] if f["id"] == b)
    fc = next(f for f in st["fragments"] if f["id"] == c)
    mt = {m["label"]: m["id"] for m in fb["markers"]}
    mc = {m["label"]: m["id"] for m in fc["markers"]}
    rid2 = client.post("/api/candidates", json={
        "frag_a": b, "frag_b": c,
        "pairs": [[mt["t0"], mc["c1"]], [mt["t15"], mc["c2"]]]}).json()["id"]
    pv2 = client.get(f"/api/relations/{rid2}/preview").json()
    assert pv2["valid"] is True, pv2["conflicts"]
    client.post(f"/api/relations/{rid2}/adopt",
                json={"version": 1, "state_version": pv2["state_version"]})

    r = client.post(f"/api/relations/{rid1}/undo", json={"version": 2})
    assert r.status_code == 200, r.text
    st = r.json()["state"]
    groups = {f["id"]: f["group_id"] for f in st["fragments"]}
    assert groups[a] != groups[b]
    assert groups[b] == groups[c]
    # B、C 位姿保留
    fb2 = next(f for f in st["fragments"] if f["id"] == b)
    assert abs(fb2["pose"]["angle_deg"] - (-90)) < 1e-6
    fc2 = next(f for f in st["fragments"] if f["id"] == c)
    assert abs(fc2["pose"]["x_mm"] - 60) < 1e-6
