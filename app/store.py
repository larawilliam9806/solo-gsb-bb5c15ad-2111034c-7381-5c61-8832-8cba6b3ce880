"""陶片拼接领域逻辑：校验、刚体拟合预览、采纳、撤销重算。"""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from . import db as dbmod
from .geometry import (
    apply_pose,
    clean_polygon,
    compose_pose,
    dist,
    find_self_intersection,
    point_on_polygon_boundary,
    polygons_interiors_overlap,
    rigid_fit,
    polygon_area,
)

MARKER_TOL_MM = 0.5   # 标记点到轮廓边的允许偏差（毫米）
JOIN_TOL_MM = 1.0     # 同组对应点误差上限（毫米）
MIN_POINTS = 3


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422,
                 details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def get_fragment(conn: sqlite3.Connection, frag_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM fragments WHERE id=?", (frag_id,)
    ).fetchone()
    if row is None:
        raise DomainError("FRAGMENT_NOT_FOUND", f"陶片 {frag_id} 不存在", 404)
    return row


def get_relation(conn: sqlite3.Connection, rel_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM relations WHERE id=?", (rel_id,)
    ).fetchone()
    if row is None:
        raise DomainError("RELATION_NOT_FOUND", f"拼接关系 {rel_id} 不存在", 404)
    return row


def fragment_markers(conn: sqlite3.Connection, frag_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM markers WHERE fragment_id=? ORDER BY id", (frag_id,)
    ).fetchall()


def get_marker(conn: sqlite3.Connection, marker_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM markers WHERE id=?", (marker_id,)
    ).fetchone()
    if row is None:
        raise DomainError("MARKER_NOT_FOUND", f"标记 {marker_id} 不存在", 404)
    return row


def group_members(conn: sqlite3.Connection, group_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM fragments WHERE group_id=? ORDER BY id", (group_id,)
    ).fetchall()


def world_polygon(frag: sqlite3.Row) -> list[tuple[float, float]]:
    contour = dbmod.contour_of(frag)
    return [apply_pose(tuple(p), frag["pos_x"], frag["pos_y"], frag["theta"])
            for p in contour]


def world_marker(frag: sqlite3.Row, marker: sqlite3.Row) -> tuple[float, float]:
    return apply_pose((marker["x"], marker["y"]),
                      frag["pos_x"], frag["pos_y"], frag["theta"])


# ---------------------------------------------------------------------------
# 录入
# ---------------------------------------------------------------------------

def validate_contour(raw: list[Any]) -> list[list[float]]:
    if not isinstance(raw, list) or len(raw) < MIN_POINTS:
        raise DomainError(
            "CONTOUR_TOO_SHORT",
            f"闭合轮廓至少需要 {MIN_POINTS} 个顶点", 422)
    pts = []
    for i, p in enumerate(raw):
        if (not isinstance(p, (list, tuple)) or len(p) != 2
                or not all(isinstance(v, (int, float)) for v in p)):
            raise DomainError(
                "CONTOUR_BAD_POINT",
                f"第 {i + 1} 个顶点格式应为 [x, y]（毫米数字）")
        pts.append((float(p[0]), float(p[1])))
    pts = clean_polygon(pts)
    if len(pts) < MIN_POINTS:
        raise DomainError("CONTOUR_TOO_SHORT",
                          f"去重后顶点不足 {MIN_POINTS} 个，无法构成闭合轮廓")
    if abs(polygon_area(pts)) < 1e-6:
        raise DomainError("CONTOUR_DEGENERATE", "轮廓面积为 0，退化为线或点")
    reason = find_self_intersection(pts)
    if reason:
        raise DomainError("CONTOUR_SELF_INTERSECT", f"轮廓自交：{reason}")
    return [[x, y] for x, y in pts]


def _check_marker_on_edge(conn: sqlite3.Connection, frag: sqlite3.Row,
                          x: float, y: float) -> None:
    contour = dbmod.contour_of(frag)
    if not point_on_polygon_boundary((x, y), contour, MARKER_TOL_MM):
        raise DomainError(
            "MARKER_NOT_ON_EDGE",
            f"标记 ({x:g}, {y:g}) 不在陶片 {frag['name']} 的边缘上"
            f"（容差 {MARKER_TOL_MM:g} mm）")


def _check_marker_distinct(conn: sqlite3.Connection, frag_id: int,
                           x: float, y: float,
                           exclude_id: int | None = None) -> None:
    for m in fragment_markers(conn, frag_id):
        if exclude_id is not None and m["id"] == exclude_id:
            continue
        if dist((x, y), (m["x"], m["y"])) < 1e-6:
            raise DomainError(
                "MARKER_DUPLICATE",
                f"标记与同片已有标记“{m['label']}”重合（坐标 "
                f"{x:g}, {y:g}）")


def create_fragment(conn: sqlite3.Connection, name: str,
                    raw_contour: list[Any],
                    raw_markers: list[dict] | None = None) -> int:
    if not name or not str(name).strip():
        raise DomainError("NAME_REQUIRED", "陶片名称不能为空")
    contour = validate_contour(raw_contour)
    with dbmod._lock:
        try:
            cur = conn.execute(
                "INSERT INTO fragments(name, contour, pos_x, pos_y, theta, group_id)"
                " VALUES(?,?,0,0,0,NULL)",
                (str(name).strip(), json.dumps([[round(x, 6), round(y, 6)]
                                                for x, y in contour])))
            frag_id = cur.lastrowid
            # 新建陶片自成一组
            conn.execute("UPDATE fragments SET group_id=id WHERE id=?",
                         (frag_id,))
            for m in raw_markers or []:
                _add_marker(conn, frag_id, m.get("label", ""),
                            float(m["x"]), float(m["y"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return frag_id


def add_marker(conn: sqlite3.Connection, frag_id: int, label: str,
               x: float, y: float) -> int:
    frag = get_fragment(conn, frag_id)
    with dbmod._lock:
        try:
            mid = _add_marker(conn, frag_id, label, float(x), float(y))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return mid


def _add_marker(conn: sqlite3.Connection, frag_id: int, label: Any,
                x: float, y: float) -> int:
    frag = get_fragment(conn, frag_id)
    if not str(label).strip():
        raise DomainError("MARKER_LABEL_REQUIRED", "标记名称不能为空")
    _check_marker_on_edge(conn, frag, x, y)
    _check_marker_distinct(conn, frag_id, x, y)
    cur = conn.execute(
        "INSERT INTO markers(fragment_id, label, x, y) VALUES(?,?,?,?)",
        (frag_id, str(label).strip(), x, y))
    return cur.lastrowid


# ---------------------------------------------------------------------------
# 候选关系
# ---------------------------------------------------------------------------

def create_candidate(conn: sqlite3.Connection, frag_a_id: int,
                     frag_b_id: int, pairs: list[list[int]]) -> int:
    if frag_a_id == frag_b_id:
        raise DomainError("SAME_FRAGMENT", "不能为同一片陶片创建拼接关系")
    frag_a = get_fragment(conn, frag_a_id)
    frag_b = get_fragment(conn, frag_b_id)
    if not isinstance(pairs, list) or len(pairs) < 2:
        raise DomainError("PAIRS_TOO_FEW", "候选拼接至少需要两处对应边缘标记")

    seen_a: set[int] = set()
    seen_b: set[int] = set()
    norm_pairs: list[list[int]] = []
    for i, pair in enumerate(pairs):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise DomainError("PAIR_BAD_FORMAT",
                              f"第 {i + 1} 组对应点格式应为 [标记A, 标记B]")
        ma_id, mb_id = int(pair[0]), int(pair[1])
        ma, mb = get_marker(conn, ma_id), get_marker(conn, mb_id)
        if ma["fragment_id"] != frag_a_id:
            raise DomainError(
                "MARKER_WRONG_FRAGMENT",
                f"第 {i + 1} 组：标记 {ma_id} 不属于陶片 {frag_a['name']}")
        if mb["fragment_id"] != frag_b_id:
            raise DomainError(
                "MARKER_WRONG_FRAGMENT",
                f"第 {i + 1} 组：标记 {mb_id} 不属于陶片 {frag_b['name']}")
        if ma_id in seen_a or mb_id in seen_b:
            raise DomainError(
                "PAIR_MARKER_REPEATED",
                f"第 {i + 1} 组：同一片上的标记在对应点中重复使用（标记重合）")
        seen_a.add(ma_id)
        seen_b.add(mb_id)
        norm_pairs.append([ma_id, mb_id])

    # 源点全部重合时旋转不可定，提前拒绝
    src_world = [world_marker(frag_b, get_marker(conn, mb))
                 for _, mb in norm_pairs]
    if all(dist(src_world[0], p) < 1e-6 for p in src_world[1:]):
        raise DomainError("MARKERS_COINCIDENT",
                          "陶片 B 的对应标记在世界坐标中全部重合，无法确定旋转")

    with dbmod._lock:
        try:
            cur = conn.execute(
                "INSERT INTO relations(frag_a_id, frag_b_id, pairs, status)"
                " VALUES(?,?,?,'proposed')",
                (frag_a_id, frag_b_id, json.dumps(norm_pairs)))
            rel_id = cur.lastrowid
            dbmod.bump_state_version(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return rel_id


# ---------------------------------------------------------------------------
# 预览 / 采纳
# ---------------------------------------------------------------------------

def _evaluate(conn: sqlite3.Connection, rel: sqlite3.Row) -> dict[str, Any]:
    """依据当前世界位姿计算关系的拟合结果与冲突（不写库）。"""
    frag_a = get_fragment(conn, rel["frag_a_id"])
    frag_b = get_fragment(conn, rel["frag_b_id"])
    pairs = dbmod.pairs_of(rel)
    ga_id, gb_id = frag_a["group_id"], frag_b["group_id"]
    closure = ga_id == gb_id

    points_a = [world_marker(frag_a, get_marker(conn, pa)) for pa, _ in pairs]
    points_b = [world_marker(frag_b, get_marker(conn, pb)) for _, pb in pairs]

    conflicts: list[dict[str, str]] = []
    transform: tuple[float, float, float] | None = None

    if closure:
        # 闭环：不允许移动，必须已经满足 1mm
        residuals = [dist(pa, pb) for pa, pb in zip(points_a, points_b)]
    else:
        try:
            transform = rigid_fit(points_b, points_a)
        except ValueError as exc:
            raise DomainError("RIGID_FIT_FAILED", str(exc))
        residuals = transform[3]
        transform = transform[:3]

    pair_results = []
    for (ma_id, mb_id), err in zip(pairs, residuals):
        ma = get_marker(conn, ma_id)
        mb = get_marker(conn, mb_id)
        over = err > JOIN_TOL_MM + 1e-9
        if over:
            conflicts.append({
                "type": "ERROR_OVER_LIMIT",
                "message":
                    f"对应点 {ma['label']} ↔ {mb['label']} 误差 "
                    f"{err:.3f} mm 超过 {JOIN_TOL_MM:g} mm 上限",
            })
        pair_results.append({
            "marker_a": ma_id, "marker_a_label": ma["label"],
            "marker_b": mb_id, "marker_b_label": mb["label"],
            "error_mm": round(err, 6), "over_limit": over,
        })

    moving_fragments: list[dict[str, Any]] = []
    if not closure:
        assert transform is not None
        members_b = group_members(conn, gb_id)
        members_a = group_members(conn, ga_id)
        polys_a = [(f, world_polygon(f)) for f in members_a]
        for f in members_b:
            nx, ny, nth = compose_pose(
                (f["pos_x"], f["pos_y"], f["theta"]), transform)
            new_poly = [apply_pose(tuple(p), nx, ny, nth)
                        for p in dbmod.contour_of(f)]
            moving_fragments.append({
                "id": f["id"], "name": f["name"],
                "current": {"x_mm": round(f["pos_x"], 6),
                            "y_mm": round(f["pos_y"], 6),
                            "angle_deg": round(math.degrees(f["theta"]), 6)},
                "proposed": {"x_mm": round(nx, 6), "y_mm": round(ny, 6),
                             "angle_deg": round(math.degrees(nth), 6)},
            })
            for fa, poly_a in polys_a:
                if fa["id"] == f["id"]:
                    continue
                if polygons_interiors_overlap(poly_a, new_poly):
                    conflicts.append({
                        "type": "COLLISION",
                        "message":
                            f"陶片 {f['name']} 与同组陶片 {fa['name']} "
                            f"轮廓内部重叠（碰撞）",
                        "fragment": f["id"], "with": fa["id"],
                    })

    mode = "CLOSURE" if closure else "MERGE"
    if closure:
        # 闭环不移动任何陶片
        moving_fragments = []
    valid = not conflicts
    return {
        "relation_id": rel["id"],
        "status": rel["status"],
        "version": rel["version"],
        "mode": mode,
        "group_a": ga_id, "group_b": gb_id,
        "valid": valid,
        "tolerance_mm": JOIN_TOL_MM,
        "transform": (
            None if transform is None
            else {"dx_mm": round(transform[0], 6),
                  "dy_mm": round(transform[1], 6),
                  "rotation_deg": round(math.degrees(transform[2]), 6)}
        ),
        "pairs": pair_results,
        "moving_fragments": moving_fragments,
        "conflicts": conflicts,
        "state_version": dbmod.get_state_version(conn),
    }


def preview(conn: sqlite3.Connection, rel_id: int) -> dict[str, Any]:
    rel = get_relation(conn, rel_id)
    return _evaluate(conn, rel)


def adopt(conn: sqlite3.Connection, rel_id: int,
          expected_version: int,
          expected_state_version: int | None = None) -> dict[str, Any]:
    with dbmod._lock:
        rel = get_relation(conn, rel_id)
        if int(expected_version) != int(rel["version"]):
            raise DomainError(
                "VERSION_CONFLICT",
                f"关系 {rel_id} 版本已过期（当前 v{rel['version']}，"
                f"提交基于 v{expected_version}），请刷新后重试",
                409, {"current_version": rel["version"]})
        current_sv = dbmod.get_state_version(conn)
        if (expected_state_version is not None
                and int(expected_state_version) != current_sv):
            raise DomainError(
                "PREVIEW_STALE",
                f"预览基于的全局状态 v{expected_state_version} 已过期"
                f"（当前 v{current_sv}），请重新预览",
                409, {"current_state_version": current_sv})
        if rel["status"] == "adopted":
            raise DomainError("RELATION_ALREADY_ADOPTED",
                              "该关系已采纳，无需重复操作", 409)

        try:
            result = _evaluate(conn, rel)
            if not result["valid"]:
                # 整次拒绝：不做任何写入，原拼接组不变
                raise DomainError(
                    "ADOPT_REJECTED",
                    "拼接不满足约束，整次拒绝，原拼接组保持不变",
                    422, {"preview": result})

            # 提交：移动整组 + 合并组 + 关系生效（同一事务）
            frag_a = get_fragment(conn, rel["frag_a_id"])
            frag_b = get_fragment(conn, rel["frag_b_id"])
            group_b_id = result["group_b"]
            group_a_id = result["group_a"]
            if result["mode"] != "CLOSURE":
                t = result["transform"]
                tx, ty, tth = t["dx_mm"], t["dy_mm"], math.radians(t["rotation_deg"])
                for f in group_members(conn, group_b_id):
                    nx, ny, nth = compose_pose(
                        (f["pos_x"], f["pos_y"], f["theta"]), (tx, ty, tth))
                    conn.execute(
                        "UPDATE fragments SET pos_x=?, pos_y=?, theta=? WHERE id=?",
                        (nx, ny, nth, f["id"]))
                conn.execute("UPDATE fragments SET group_id=? WHERE group_id=?",
                             (group_a_id, group_b_id))
            conn.execute(
                "UPDATE relations SET status='adopted', version=version+1 WHERE id=?",
                (rel_id,))
            new_version = get_relation(conn, rel_id)["version"]
            sv = dbmod.bump_state_version(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        result["state_version"] = sv
        result["adopted"] = True
        result["version"] = new_version
        return result


def undo(conn: sqlite3.Connection, rel_id: int,
         expected_version: int) -> dict[str, Any]:
    with dbmod._lock:
        rel = get_relation(conn, rel_id)
        if int(expected_version) != int(rel["version"]):
            raise DomainError(
                "VERSION_CONFLICT",
                f"关系 {rel_id} 版本已过期（当前 v{rel['version']}，"
                f"提交基于 v{expected_version}），请刷新后重试",
                409, {"current_version": rel["version"]})

        was_adopted = rel["status"] == "adopted"
        try:
            conn.execute("DELETE FROM relations WHERE id=?", (rel_id,))

            recomputed = False
            if was_adopted:
                recompute_groups(conn)
                recomputed = True
            sv = dbmod.bump_state_version(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {"undone": rel_id, "was_adopted": was_adopted,
                "groups_recomputed": recomputed, "state_version": sv}


def recompute_groups(conn: sqlite3.Connection) -> None:
    """按剩余已采纳关系重算连通组；孤片自成一组（世界位姿保留）。"""
    frags = conn.execute("SELECT id FROM fragments ORDER BY id").fetchall()
    parent = {f["id"]: f["id"] for f in frags}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # 以较小 id 为根，结果稳定
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    for rel in conn.execute(
            "SELECT * FROM relations WHERE status='adopted'").fetchall():
        union(rel["frag_a_id"], rel["frag_b_id"])

    # 组号取连通分量内最小陶片 id：孤片组号即自身 id
    for f in frags:
        root = find(f["id"])
        conn.execute("UPDATE fragments SET group_id=? WHERE id=?",
                     (root, f["id"]))


# ---------------------------------------------------------------------------
# 全量状态
# ---------------------------------------------------------------------------

def full_state(conn: sqlite3.Connection) -> dict[str, Any]:
    fragments = []
    for f in conn.execute("SELECT * FROM fragments ORDER BY id").fetchall():
        markers = [
            {"id": m["id"], "label": m["label"],
             "x_mm": m["x"], "y_mm": m["y"],
             "world_x_mm": round(world_marker(f, m)[0], 6),
             "world_y_mm": round(world_marker(f, m)[1], 6)}
            for m in fragment_markers(conn, f["id"])
        ]
        fragments.append({
            "id": f["id"], "name": f["name"],
            "contour_mm": dbmod.contour_of(f),
            "group_id": f["group_id"],
            "pose": {"x_mm": round(f["pos_x"], 6),
                     "y_mm": round(f["pos_y"], 6),
                     "angle_deg": round(math.degrees(f["theta"]), 6)},
            "world_contour_mm": [[round(x, 6), round(y, 6)]
                                 for x, y in world_polygon(f)],
            "markers": markers,
        })
    relations = []
    for r in conn.execute("SELECT * FROM relations ORDER BY id").fetchall():
        relations.append({
            "id": r["id"],
            "frag_a": r["frag_a_id"], "frag_b": r["frag_b_id"],
            "pairs": dbmod.pairs_of(r),
            "status": r["status"], "version": r["version"],
        })
    return {
        "state_version": dbmod.get_state_version(conn),
        "tolerance_mm": JOIN_TOL_MM,
        "marker_tolerance_mm": MARKER_TOL_MM,
        "fragments": fragments,
        "relations": relations,
    }
