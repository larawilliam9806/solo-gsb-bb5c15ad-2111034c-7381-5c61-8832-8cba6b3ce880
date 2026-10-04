"""二维毫米制轮廓的几何工具。

所有计算均在“毫米”单位下进行，仅允许刚体变换（平移 + 旋转，不允许缩放）。
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Point = Tuple[float, float]

# 两点距离小于该值视为同一点（毫米）
POINT_EPS = 1e-6
# 标记点对源/目标质心距离小于该值视为退化（毫米）
DEGEN_EPS = 1e-6


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# 轮廓预处理
# ---------------------------------------------------------------------------

def clean_polygon(points: Sequence[Point]) -> List[Point]:
    """删除首尾重合点及相邻重复点；不改变点序。"""
    pts: List[Point] = []
    for p in points:
        q = (float(p[0]), float(p[1]))
        if not pts or dist(pts[-1], q) > POINT_EPS:
            pts.append(q)
    if len(pts) >= 2 and dist(pts[0], pts[-1]) <= POINT_EPS:
        pts.pop()
    return pts


def polygon_area(poly: Sequence[Point]) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - y1 * x2
    return s / 2.0


def bbox(poly: Sequence[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _tol(poly: Sequence[Point]) -> float:
    """根据轮廓尺度给出数值容差。"""
    minx, miny, maxx, maxy = bbox(poly)
    scale = max(maxx - minx, maxy - miny, 1.0)
    return max(1e-7 * scale, 1e-9)


# ---------------------------------------------------------------------------
# 自交检测
# ---------------------------------------------------------------------------

def _on_segment(p: Point, a: Point, b: Point, tol: float) -> bool:
    """p 是否在线段 ab 上（含端点）。"""
    if abs(_cross(b[0] - a[0], b[1] - a[1], p[0] - a[0], p[1] - a[1])) > tol:
        return False
    return (min(a[0], b[0]) - tol <= p[0] <= max(a[0], b[0]) + tol
            and min(a[1], b[1]) - tol <= p[1] <= max(a[1], b[1]) + tol)


def _segments_proper_cross(a: Point, b: Point, c: Point, d: Point,
                           tol: float) -> bool:
    """两线段是否在非端点处相交（真穿越）。"""
    d1x, d1y = b[0] - a[0], b[1] - a[1]
    d2x, d2y = d[0] - c[0], d[1] - c[1]
    cc = _cross(d2x, d2y, a[0] - c[0], a[1] - c[1])
    c1 = _cross(d1x, d1y, c[0] - a[0], c[1] - a[1])
    c2 = _cross(d1x, d1y, d[0] - a[0], d[1] - a[1])
    c3 = _cross(d2x, d2y, b[0] - c[0], b[1] - c[1])
    if abs(cc) <= tol:
        return False  # 共线情况由重叠段检查处理
    # c、d 严格位于直线 ab 两侧，且 a、b 严格位于直线 cd 两侧
    return ((c1 > tol and c2 < -tol) or (c1 < -tol and c2 > tol)) and \
           ((cc > tol and c3 < -tol) or (cc < -tol and c3 > tol))


def find_self_intersection(poly: Sequence[Point]) -> str | None:
    """返回自交原因；闭合简单多边形返回 None。

    检查：非相邻边真穿越/端点接触、相邻边回头、共线边重叠。
    """
    n = len(poly)
    tol = _tol(poly)
    edges = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        a, b = edges[i]
        # 相邻边：若共享顶点之外还接触，说明轮廓回头（自交）
        pa, pb = edges[(i - 1) % n]
        if _on_segment(b, pa, a, tol) and dist(b, a) > tol and dist(pa, a) > tol:
            # b 落在前一条边上（b 不是该边端点 a 本身）
            if dist(b, pa) > tol:
                return f"边 {i + 1} 的端点落在相邻边上，轮廓自交"
        for j in range(i + 1, n):
            c, d = edges[j]
            adjacent = (j == i + 1) or (i == 0 and j == n - 1)
            if adjacent:
                # 相邻边只允许在共享端点接触；非端点真穿越仍算自交
                if _segments_proper_cross(a, b, c, d, tol):
                    return f"相邻边 {i + 1} 与 {j + 1} 互相穿越"
                continue
            if _segments_proper_cross(a, b, c, d, tol):
                return f"边 {i + 1} 与边 {j + 1} 在内部相交"
            # 端点落在另一条边上（T 接）也算自交
            for p, name in ((a, f"边{i + 1}起点"), (b, f"边{i + 1}终点")):
                if _on_segment(p, c, d, tol) and dist(p, c) > tol and dist(p, d) > tol:
                    return f"{name} 落在边 {j + 1} 上，轮廓自交"
            for p, name in ((c, f"边{j + 1}起点"), (d, f"边{j + 1}终点")):
                if _on_segment(p, a, b, tol) and dist(p, a) > tol and dist(p, b) > tol:
                    return f"{name} 落在边 {i + 1} 上，轮廓自交"
            # 共线重叠
            if _collinear_overlap(a, b, c, d, tol):
                return f"边 {i + 1} 与边 {j + 1} 共线重叠"
    return None


def _collinear_overlap(a: Point, b: Point, c: Point, d: Point,
                       tol: float) -> bool:
    if abs(_cross(b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1])) > tol:
        return False

    def overlap_len1d(lo1, hi1, lo2, hi2):
        return max(0.0, min(hi1, hi2) - max(lo1, lo2))

    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) >= abs(dy):
        ov = overlap_len1d(min(a[0], b[0]), max(a[0], b[0]),
                           min(c[0], d[0]), max(c[0], d[0]))
    else:
        ov = overlap_len1d(min(a[1], b[1]), max(a[1], b[1]),
                           min(c[1], d[1]), max(c[1], d[1]))
    return ov > tol


# ---------------------------------------------------------------------------
# 点与轮廓
# ---------------------------------------------------------------------------

def point_on_segment(p: Point, a: Point, b: Point, tol: float) -> bool:
    return _on_segment(p, a, b, tol)


def point_on_polygon_boundary(p: Point, poly: Sequence[Point],
                              tol: float) -> bool:
    n = len(poly)
    for i in range(n):
        if _on_segment(p, poly[i], poly[(i + 1) % n], tol):
            return True
    return False


def point_in_polygon_interior(p: Point, poly: Sequence[Point],
                              tol: float) -> bool:
    """严格内部判定（边界不算内部）。射线法。"""
    if point_on_polygon_boundary(p, poly, tol):
        return False
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xint > x:
                inside = not inside
    return inside


# ---------------------------------------------------------------------------
# 刚体变换
# ---------------------------------------------------------------------------

def apply_pose(p: Point, x: float, y: float, theta: float) -> Point:
    c, s = math.cos(theta), math.sin(theta)
    return (x + c * p[0] - s * p[1], y + s * p[0] + c * p[1])


def compose_pose(pose_a: Tuple[float, float, float],
                 pose_b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """先应用 pose_a 再应用 pose_b 得到的复合位姿。"""
    ax, ay, ath = pose_a
    bx, by, bth = pose_b
    c, s = math.cos(bth), math.sin(bth)
    return (bx + c * ax - s * ay,
            by + s * ax + c * ay,
            ath + bth)


def rigid_fit(src: Sequence[Point], dst: Sequence[Point]
              ) -> Tuple[float, float, float, List[float]]:
    """Kabsch 二维刚体拟合：求 T（平移+旋转，无缩放）使 T(src)≈dst。

    返回 (x, y, theta, residuals)，residuals 为各对应点拟合后的毫米误差。
    标记点全部重合（无法确定旋转）时抛出 ValueError。
    """
    n = len(src)
    smx = sum(p[0] for p in src) / n
    smy = sum(p[1] for p in src) / n
    dmx = sum(p[0] for p in dst) / n
    dmy = sum(p[1] for p in dst) / n
    sxx = syy = sxy = syx = 0.0
    for (sx, sy), (dx, dy) in zip(src, dst):
        ax, ay = sx - smx, sy - smy
        bx, by = dx - dmx, dy - dmy
        sxx += ax * bx
        syy += ay * by
        sxy += ax * by   # Σ ax*by
        syx += ay * bx   # Σ ay*bx
    # R = [[c,-s],[s,c]]
    # theta = atan2(Σ(ax*by - ay*bx), Σ(ax*bx + ay*by))
    sin_num = sxy - syx
    cos_num = sxx + syy
    if math.hypot(sin_num, cos_num) <= DEGEN_EPS:
        raise ValueError("对应标记点全部重合，无法唯一确定旋转角度")
    theta = math.atan2(sin_num, cos_num)
    c, s = math.cos(theta), math.sin(theta)
    x = dmx - (c * smx - s * smy)
    y = dmy - (s * smx + c * smy)
    residuals = [dist(apply_pose(p, x, y, theta), q)
                 for p, q in zip(src, dst)]
    return x, y, theta, residuals


# ---------------------------------------------------------------------------
# 多边形内部不重叠
# ---------------------------------------------------------------------------

def _interior_point(poly: Sequence[Point], tol: float) -> Point | None:
    """求简单多边形的一个严格内点（沿顶点角平分线向内取极小步长）。"""
    n = len(poly)
    best: Point | None = None
    best_len = -1.0
    for i in range(n):
        v = poly[i]
        prev = poly[(i - 1) % n]
        nxt = poly[(i + 1) % n]
        e1 = (prev[0] - v[0], prev[1] - v[1])
        e2 = (nxt[0] - v[0], nxt[1] - v[1])
        l1 = math.hypot(e1[0], e1[1])
        l2 = math.hypot(e2[0], e2[1])
        if l1 <= tol or l2 <= tol:
            continue
        d = (e1[0] / l1 + e2[0] / l2, e1[1] / l1 + e2[1] / l2)
        dl = math.hypot(d[0], d[1])
        step = min(l1, l2)
        if dl <= tol:
            continue
        delta = step * 1e-3
        for sign in (1.0, -1.0):
            q = (v[0] + sign * d[0] / dl * delta,
                 v[1] + sign * d[1] / dl * delta)
            if point_in_polygon_interior(q, poly, tol):
                if step > best_len:
                    best_len = step
                    best = q
                break
    return best


def polygons_interiors_overlap(poly_a: Sequence[Point],
                               poly_b: Sequence[Point]) -> bool:
    """两个简单多边形的内部是否相交（允许共边/边界接触）。"""
    tol = max(_tol(poly_a), _tol(poly_b))
    na, nb = len(poly_a), len(poly_b)

    # 1) 非相邻边真穿越
    for i in range(na):
        a, b = poly_a[i], poly_a[(i + 1) % na]
        for j in range(nb):
            c, d = poly_b[j], poly_b[(j + 1) % nb]
            if _segments_proper_cross(a, b, c, d, tol):
                return True

    # 2) 顶点严格位于对方内部（含完全包含/全等多边形/内部 T 接）
    for p in poly_a:
        if point_in_polygon_interior(p, poly_b, tol):
            return True
    for p in poly_b:
        if point_in_polygon_interior(p, poly_a, tol):
            return True

    # 3) 顶点都在边界上时（全等多边形等），用严格内点判断包含关系
    qa = _interior_point(poly_a, tol)
    qb = _interior_point(poly_b, tol)
    if qa is not None and point_in_polygon_interior(qa, poly_b, tol):
        return True
    if qb is not None and point_in_polygon_interior(qb, poly_a, tol):
        return True
    return False
