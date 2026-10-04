"""启动时初始化演示数据（仅在库为空时执行）。

演示：两片 60x25 mm 的陶片，B 需旋转 -90°（顺时针）并平移 (60,25) mm
移动到 A 右侧共享竖边，两组边缘标记误差为 0，构成可预览/采纳的候选关系。
"""
from __future__ import annotations

import json
import sqlite3

from . import store


def seed_if_empty(conn: sqlite3.Connection) -> bool:
    n = conn.execute("SELECT COUNT(*) c FROM fragments").fetchone()["c"]
    if n:
        return False

    # A: 矩形 (0,0)-(60,25)，开口/拼接边在右侧 x=60
    contour_a = [[0, 0], [60, 0], [60, 25], [0, 25]]
    # B: 局部同为 60x25 矩形，但其拼接边在“局部”的底边 y=0；
    # 采纳时旋转 90° 使该边变为竖边并贴合 A 的右侧。
    contour_b = [[0, 0], [60, 0], [60, 25], [0, 25]]

    aid = store.create_fragment(conn, "陶片A（演示）", contour_a)
    bid = store.create_fragment(conn, "陶片B（演示）", contour_b)

    # A 右边缘的两个标记
    store.add_marker(conn, aid, "A1", 60, 5)
    store.add_marker(conn, aid, "A2", 60, 20)
    # B 底边缘的两个标记：采纳时刚体变换为 θ=-90°、平移 (60,25)，
    # 即 (x,y)->(60+y, 25-x)：B1(5,0)->(60,20)=A2，B2(20,0)->(60,5)=A1
    store.add_marker(conn, bid, "B1", 5, 0)
    store.add_marker(conn, bid, "B2", 20, 0)
    markers = {m["label"]: m["id"]
               for m in store.fragment_markers(conn, aid)}
    markers.update({m["label"]: m["id"]
                    for m in store.fragment_markers(conn, bid)})
    pairs = [[markers["A2"], markers["B1"]],
             [markers["A1"], markers["B2"]]]
    store.create_candidate(conn, aid, bid, pairs)

    # 第三片独立孤片（演示组重算/孤片状态）
    contour_c = [[0, 0], [20, 0], [10, 15]]
    store.create_fragment(
        conn, "陶片C（孤片）", contour_c,
        [{"label": "C1", "x": 0, "y": 0},
         {"label": "C2", "x": 10, "y": 15}])
    conn.commit()
    return True
