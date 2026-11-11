"""演示数据：不联网、不登录也能先把页面跑起来看效果。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import database
from .config import Task
from .parse import canonical_url
from .repositories import batch_repo, product_repo, snapshot_repo, task_repo

_TASK = Task(task_id="kids-bottle", name="儿童保温杯", keywords=["儿童保温杯"])

_ITEMS = [
    {
        "id": "610000000001",
        "title": "富全密码锁保温杯儿童男学生上学专用水杯大容量316不锈钢密码杯",
        "shop": "oktp旗舰店", "rating": 4.8, "rate": "96%",
        "skus": ["星墨黑570ML【密码解锁+316不锈钢】", "云奶白570ML【密码解锁+316不锈钢】",
                 "桃粉570ML【密码解锁+316不锈钢】", "礼盒款 星墨黑570ML【密码+杯套】"],
        "params": {"杯盖材质": "PP材质", "品牌": "oktp", "功能": "便携,保温",
                   "杯盖类型": "双饮盖", "材质": "304不锈钢+316不锈钢", "净重": "390g",
                   "保温时长": "8小时", "型号": "FQ-1693维希密码保温杯", "杯子种类": "保温杯"},
        "price": (179, 149), "sold": (500, 640), "cart": (600, 830),
    },
    {
        "id": "610000000002",
        "title": "智能显温儿童保温杯316不锈钢大容量女孩上学专用双饮便携水杯",
        "shop": "皇家洛克旗舰店", "rating": 4.9, "rate": "98%",
        "skus": ["可妮粉 400ML【显温+吸管】", "海盐蓝 400ML【显温+吸管】", "奶咖 500ML【显温+直饮】"],
        "params": {"杯盖材质": "PP", "品牌": "皇家洛克", "功能": "显温,保温,便携",
                   "材质": "316不锈钢", "净重": "350g", "保温时长": "12小时", "容量": "400/500ML"},
        "price": (99, 49.9), "sold": (20000, 26000), "cart": (3100, 4200),
    },
    {
        "id": "610000000003",
        "title": "moc儿童保温杯学生上学专用保冷吸管水杯男孩新款大容量",
        "shop": "moc旗舰店", "rating": 4.8, "rate": "95%",
        "skus": ["天空蓝 600ML", "奶油白 600ML", "抹茶绿 600ML", "600ML+杯套套装"],
        "params": {"杯盖材质": "PP", "品牌": "moc", "功能": "保冷,保温,吸管",
                   "材质": "316不锈钢", "净重": "380g", "保温时长": "10小时"},
        "price": (79, 59.9), "sold": (40000, 47000), "cart": (5200, 6100),
    },
    {
        "id": "610000000004",
        "title": "保温杯大容量316L不锈钢水杯男生女高颜值少女生上学专用水杯",
        "shop": "晖礼旗舰店", "rating": 4.7, "rate": "94%",
        "skus": ["曜石黑 800ML", "雾霾蓝 800ML", "象牙白 800ML"],
        "params": {"杯盖材质": "pp", "品牌": "晖礼", "功能": "保温", "材质": "316L不锈钢",
                   "净重": "420g", "保温时长": "8小时", "容量": "800ML"},
        "price": (89, 52.21), "sold": (300, 420), "cart": (150, 260),
    },
    {
        "id": "610000000005",
        "title": "儿童保温杯女高颜值初高中学生上学专用吸管水杯子婴儿幼儿园小学生",
        "shop": "雅非奇旗舰店", "rating": 4.8, "rate": "97%",
        "skus": ["樱粉 500ML【吸管+直饮】", "薄荷绿 500ML【吸管+直饮】", "熊猫白 500ML【吸管+直饮】"],
        "params": {"杯盖材质": "PP", "品牌": "雅非奇", "功能": "吸管,保温", "材质": "316不锈钢",
                   "净重": "360g", "保温时长": "12小时", "容量": "500ML"},
        "price": (69, 39.9), "sold": (8000, 9600), "cart": (1200, 1500),
    },
    {
        "id": "610000000006",
        "title": "儿童保温杯2026新款316水杯子女孩婴幼儿幼儿园学生上学专用",
        "shop": "雅非奇旗舰店", "rating": 4.8, "rate": "97%",
        "skus": ["草莓粉 450ML", "芝士黄 450ML", "天空蓝 450ML"],
        "params": {"杯盖材质": "PP", "品牌": "雅非奇", "功能": "吸管,保温", "材质": "316不锈钢",
                   "净重": "340g", "保温时长": "12小时", "容量": "450ML"},
        "price": (69, 39.8), "sold": (20000, 24000), "cart": (2600, 3300),
    },
    {
        "id": "610000000007",
        "title": "4H强悍锁温儿童保温杯800ML大容量户外运动水壶便携提手",
        "shop": "贝尔塔旗舰店", "rating": 4.6, "rate": "92%",
        "skus": ["激光绿 800ML", "岩石灰 800ML", "800ML 两用盖"],
        "params": {"杯盖材质": "PP", "品牌": "贝尔塔", "功能": "锁温,便携", "材质": "316不锈钢",
                   "净重": "450g", "保温时长": "4小时", "容量": "800ML"},
        "price": (129, 99), "sold": (900, 1300), "cart": (210, 350),
    },
    {
        "id": "610000000008",
        "title": "一杯三饮儿童保温杯1000ML大容量免费刻字学生水壶带提绳",
        "shop": "旅途者旗舰店", "rating": 4.7, "rate": "93%",
        "skus": ["香槟金 1000ML", "夜空黑 1000ML", "1000ML+杯套"],
        "params": {"杯盖材质": "PP", "品牌": "旅途者", "功能": "三饮,保温,刻字", "材质": "316不锈钢",
                   "净重": "520g", "保温时长": "6小时", "容量": "1000ML"},
        "price": (158, 128), "sold": (700, 980), "cart": (180, 300),
    },
]


def seed() -> None:
    conn = database.connect()
    task_repo.upsert_task(conn, _TASK)

    now = datetime.now()
    batches = [
        (now.strftime("%Y-%m-%d") + "_demo", 1.0),
        ((now - timedelta(days=1)).strftime("%Y-%m-%d") + "_demo", 0.82),
    ]

    for batch, factor in batches:
        stamp = now.isoformat(timespec="seconds")
        batch_repo.ensure_batch(conn, batch, _TASK.task_id)
        for i, it in enumerate(_ITEMS, 1):
            product_repo.upsert_product(conn, {
                "item_id": it["id"],
                "title": it["title"],
                "shop_name": it["shop"],
                "shop_rating": it["rating"],
                "positive_rate": it["rate"],
                "sku_names": json.dumps(it["skus"], ensure_ascii=False),
                "params_json": json.dumps(it["params"], ensure_ascii=False),
                "image_url": f"https://picsum.photos/seed/{it['id']}/300/300",
                "video_url": None,
                "product_url": canonical_url(it["id"]),
                "first_seen_at": stamp,
                "updated_at": stamp,
            })
            sold = int(it["sold"][1] * factor)
            cart = int(it["cart"][1] * factor)
            snapshot_repo.upsert_snapshot(
                conn, _TASK.task_id, it["id"], batch, stamp, i,
                f"¥{it['price'][0]} 优惠后", f"{sold}+人付款", sold,
                f"超{cart}人加购", cart,
            )
            snapshot_repo.set_detail(conn, _TASK.task_id, it["id"], batch, it["price"][1])
        batch_repo.update_batch(conn, batch, _TASK.task_id, status="done", stage="done",
                                page_done=_TASK.pages, detail_done=len(_ITEMS),
                                items_count=len(_ITEMS), finished_at=stamp)

    print(f"演示数据已生成：{len(_ITEMS)} 个商品 × {len(batches)} 个批次（批次号以 _demo 结尾）")
    print("现在运行：start.bat 打开页面查看")