"""生成生产级演示销售库 data/demo.db(SQLite)。

规模:500 客户 / 40 销售 / 60 产品 / 20000 订单 / ~54000 明细 / ~1600 退货 / 180 目标行。
真实性:金额与明细严格一致;状态按权重分布;11月有促销高峰;含 NULL(行业、离职日期);
含敏感列(客户手机号);订单日期截至 2026-08-05,无未来数据。
"""
import os
import random
import sqlite3

random.seed(42)

REGIONS = ["华东", "华北", "华南", "西南", "东北", "西北"]
LEVELS = ["普通", "银牌", "金牌"]
INDUSTRIES = ["制造业", "零售业", "金融业", "教育", "医疗", "物流", "能源", "互联网", None]
CATEGORIES = {
    "笔记本电脑": (4000, 15000), "台式机": (3000, 10000), "显示器": (800, 4000),
    "键鼠外设": (50, 500), "服务器": (20000, 60000), "网络设备": (2000, 18000),
    "存储设备": (500, 8000), "办公软件": (200, 3000),
}
RETURN_REASONS = ["质量问题", "发错货", "客户取消", "运输损坏", "性能不符"]

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英华玉红建国"

CUSTOMER_PREFIX = ["远大", "宏图", "蓝海", "中原", "东升", "云顶", "华信", "金桥", "天成",
                   "瑞丰", "启明", "康泰", "恒力", "百汇", "新纪元", "众合", "凯旋", "绿洲",
                   "银河", "鼎盛", "长风", "润泽", "旭日", "凌霄", "海纳", "紫金", "白云",
                   "青山", "红帆", "黑石", "金石", "远航", "博雅", "同创", "联城"]
CUSTOMER_MID = ["科技", "实业", "贸易", "集团", "信息", "电子", "物流", "制造", "能源",
                "医疗", "教育", "软件", "建设", "环保", "食品"]
CUSTOMER_SUFFIX = ["有限公司", "股份公司", "集团", ""]

PRODUCT_ADJ = ["凌云", "磐石", "锐视", "疾风", "天权", "星链", "苍穹", "北辰", "极光", "洪流"]
PRODUCT_TYPE = {
    "笔记本电脑": ["本 Pro 14", "本 Air 13", "工作站 15", "本 Ultra 16"],
    "台式机": ["台式机 T3", "台式机 T5", "迷你主机 M1"],
    "显示器": ["27 4K", "24 IPS", "32 曲面", "21 办公"],
    "键鼠外设": ["机械键盘", "静音鼠标", "键鼠套装", "手写板"],
    "服务器": ["服务器 R730", "服务器 R520", "服务器 塔式T110"],
    "网络设备": ["交换机 48口", "路由器 企业版", "防火墙 X1", "AP 面板"],
    "存储设备": ["NAS 4盘位", "企业硬盘 8T", "全闪存阵列"],
    "办公软件": ["协同办公套件", "ERP 基础版", "CRM 标准版"],
}


def _phone() -> str:
    return "1" + random.choice("3589") + "".join(random.choice("0123456789") for _ in range(9))


def _date(y0, m0, d0, y1, m1, d1) -> str:
    import datetime
    a = datetime.date(y0, m0, d0).toordinal()
    b = datetime.date(y1, m1, d1).toordinal()
    return datetime.date.fromordinal(random.randint(a, b)).isoformat()


def main() -> None:
    os.makedirs("data", exist_ok=True)
    path = "data/demo.db"
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE customers (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        region TEXT NOT NULL,
        level TEXT NOT NULL,
        industry TEXT,
        annual_purchase REAL NOT NULL,
        contact_phone TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE employees (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        region TEXT NOT NULL,
        hire_date TEXT NOT NULL,
        resign_date TEXT
    );
    CREATE TABLE products (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price REAL NOT NULL,
        cost REAL NOT NULL,
        launch_date TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        employee_id INTEGER REFERENCES employees(id),
        order_date TEXT NOT NULL,
        amount REAL NOT NULL,
        status TEXT NOT NULL
    );
    CREATE TABLE order_items (
        id INTEGER PRIMARY KEY,
        order_id INTEGER NOT NULL REFERENCES orders(id),
        product_id INTEGER NOT NULL REFERENCES products(id),
        quantity INTEGER NOT NULL,
        unit_price REAL NOT NULL,
        subtotal REAL NOT NULL
    );
    CREATE TABLE returns (
        id INTEGER PRIMARY KEY,
        order_id INTEGER NOT NULL REFERENCES orders(id),
        return_date TEXT NOT NULL,
        refund_amount REAL NOT NULL,
        reason TEXT NOT NULL
    );
    CREATE TABLE sales_targets (
        id INTEGER PRIMARY KEY,
        region TEXT NOT NULL,
        month TEXT NOT NULL,          -- YYYY-MM
        target_amount REAL NOT NULL
    );
    CREATE INDEX idx_orders_date ON orders(order_date);
    CREATE INDEX idx_orders_customer ON orders(customer_id);
    CREATE INDEX idx_items_order ON order_items(order_id);
    CREATE INDEX idx_items_product ON order_items(product_id);
    CREATE INDEX idx_returns_order ON returns(order_id);
    """)

    # 客户 500(保留"远大集团"等既有名称,名称唯一)
    names, seen = [], set()
    fixed = ["远大集团", "宏图科技", "蓝海贸易", "云顶信息", "旭日化工", "凯旋汽车", "鼎盛金融"]
    for n in fixed:
        names.append(n); seen.add(n)
    while len(names) < 500:
        n = random.choice(CUSTOMER_PREFIX) + random.choice(CUSTOMER_MID) + random.choice(CUSTOMER_SUFFIX)
        if n not in seen:
            seen.add(n); names.append(n)
    customers = [
        (i + 1, n, random.choice(REGIONS),
         random.choices(LEVELS, weights=[6, 3, 1])[0],
         random.choice(INDUSTRIES),
         round(random.uniform(100_000, 15_000_000), 2),
         _phone() if random.random() > 0.05 else None,
         _date(2022, 1, 1, 2026, 6, 30))
        for i, n in enumerate(names)
    ]
    c.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)", customers)

    # 销售员 40(15% 已离职)
    emp_names, seen_e = [], set()
    while len(emp_names) < 40:
        n = random.choice(SURNAMES) + "".join(random.sample(GIVEN, random.randint(1, 2)))
        if n not in seen_e:
            seen_e.add(n); emp_names.append(n)
    employees = [
        (i + 1, n, random.choice(REGIONS), _date(2020, 1, 1, 2025, 12, 31),
         _date(2025, 1, 1, 2026, 7, 31) if random.random() < 0.15 else None)
        for i, n in enumerate(emp_names)
    ]
    c.executemany("INSERT INTO employees VALUES (?,?,?,?,?)", employees)

    # 产品 60(约 10% 已下架)
    products, pid = [], 0
    for cat, (lo, hi) in CATEGORIES.items():
        for t in PRODUCT_TYPE[cat]:
            for adj in random.sample(PRODUCT_ADJ, 2):
                pid += 1
                price = round(random.uniform(lo, hi), -1)
                products.append((pid, f"{adj}{t}", cat, price,
                                 round(price * random.uniform(0.55, 0.8), 2),
                                 _date(2021, 1, 1, 2026, 3, 31),
                                 0 if random.random() < 0.1 else 1))
                if pid >= 60:
                    break
            if pid >= 60:
                break
        if pid >= 60:
            break
    c.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", products)
    price_of = {p[0]: p[3] for p in products}

    # 订单 20000 + 明细(金额严格=明细合计;11月促销高峰;日期截至 2026-08-05)
    import datetime
    start = datetime.date(2024, 1, 1).toordinal()
    end = datetime.date(2026, 8, 5).toordinal()
    orders, items, item_id = [], [], 0
    for oid in range(1, 20001):
        while True:
            d = datetime.date.fromordinal(random.randint(start, end))
            if d.month == 11 or random.random() > 0.15:   # 11月促销:非11月订单有15%概率重掷
                break
        date = d.isoformat()
        recent = (datetime.date(2026, 8, 5) - d).days <= 45
        status = random.choices(
            ["已完成", "进行中", "已取消"],
            weights=[70, 25, 5] if recent else [86, 2, 12])[0]
        emp = random.randint(1, 40) if random.random() > 0.03 else None   # 少量无归属订单
        cust = random.randint(1, 500)
        amount = 0.0
        for p in random.sample(list(price_of), random.randint(1, 5)):
            item_id += 1
            qty = random.choices([1, 2, 3, 5, 10, 20], weights=[30, 25, 20, 15, 7, 3])[0]
            unit = price_of[p]
            sub = round(qty * unit, 2)
            amount += sub
            items.append((item_id, oid, p, qty, unit, sub))
        orders.append((oid, cust, emp, date, round(amount, 2), status))
    c.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", orders)
    c.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)

    # 退货(约 8% 的已完成订单,退款 ≤ 订单金额)
    done = [o for o in orders if o[5] == "已完成"]
    returns = []
    for rid, o in enumerate(random.sample(done, int(len(done) * 0.08)), 1):
        oid, _, _, odate, amount, _ = o
        rdate = datetime.date.fromisoformat(odate) + datetime.timedelta(days=random.randint(3, 30))
        if rdate > datetime.date(2026, 8, 5):
            rdate = datetime.date(2026, 8, 5)
        returns.append((rid, oid, rdate.isoformat(),
                        round(amount * random.uniform(0.1, 1.0), 2),
                        random.choice(RETURN_REASONS)))
    c.executemany("INSERT INTO returns VALUES (?,?,?,?,?)", returns)

    # 销售目标:大区 × 月(2024-01 ~ 2026-12)
    targets, tid = [], 0
    for y in (2024, 2025, 2026):
        for m in range(1, 13):
            for r in REGIONS:
                tid += 1
                base = {"华东": 9.0, "华北": 8.0, "华南": 7.5, "西南": 5.0,
                        "东北": 4.5, "西北": 3.5}[r] * 1e6
                boost = 1.6 if m == 11 else 1.0
                targets.append((tid, r, f"{y}-{m:02d}",
                                round(base * boost * random.uniform(0.9, 1.1), 2)))
    c.executemany("INSERT INTO sales_targets VALUES (?,?,?,?)", targets)

    conn.commit()
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("customers", "employees", "products", "orders",
                        "order_items", "returns", "sales_targets")}
    conn.close()
    print(f"演示库已生成: {path} -> {counts}")


if __name__ == "__main__":
    main()
