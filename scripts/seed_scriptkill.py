"""生成剧本杀连锁门店演示库 data/scriptkill.db(SQLite)。

场景:马鞍山同城 5 家剧本杀门店(花山/雨山/当涂/含山/和县)。
规模:5 门店 / 40 员工(含 25 名 DM)/ 36 个剧本 / 约 110 条门店授权 /
      1200 名玩家 / 约 1.8 万场次 / 约 10 万条玩家到场明细 / 120 条门店月度目标。
真实性:场次营收严格等于玩家消费合计;周末与寒暑假场次更多;含取消场次、
      无 DM 场次、离职员工、NULL 手机号;线下渠道有折扣;数据截至运行当天。
"""
from __future__ import annotations

import bisect
import datetime
import os
import random
import sqlite3

random.seed(42)

CITY = "马鞍山"
TODAY = datetime.date.today()
START = datetime.date(2025, 1, 1)

STORES = [
    (1, "花山万达店", "花山区", "2023-03-18", "王磊"),
    (2, "雨山金鹰店", "雨山区", "2023-09-02", "李静"),
    (3, "当涂步行街店", "当涂县", "2024-05-20", "张敏"),
    (4, "含山中心店", "含山县", "2025-01-11", "陈浩"),
    (5, "和县历阳店", "和县", "2025-06-28", "赵雪"),
]

# (名称, 类型, 最少人数, 最多人数, 时长分钟, 人均价格, 难度)
SCRIPTS = [
    ("雾隐山庄", "情感", 4, 8, 240, 128, "进阶"),
    ("长夜将明", "情感", 6, 8, 300, 158, "进阶"),
    ("雪落无声", "情感", 4, 7, 240, 118, "新手"),
    ("南风知我意", "情感", 6, 8, 270, 148, "进阶"),
    ("旧城来信", "情感", 4, 6, 210, 108, "新手"),
    ("星河渡", "情感", 6, 9, 330, 188, "硬核"),
    ("破产兄弟", "欢乐", 6, 10, 210, 98, "新手"),
    ("老板不在家", "欢乐", 4, 8, 180, 88, "新手"),
    ("今天不加班", "欢乐", 6, 9, 210, 98, "新手"),
    ("疯人院欢乐篇", "欢乐", 6, 10, 240, 108, "进阶"),
    ("相亲大作战", "欢乐", 6, 8, 210, 98, "新手"),
    ("回魂夜", "恐怖", 4, 7, 240, 138, "进阶"),
    ("第七个房间", "恐怖", 5, 7, 270, 148, "进阶"),
    ("纸新娘", "恐怖", 6, 8, 300, 158, "硬核"),
    ("山村诡事", "恐怖", 4, 6, 210, 118, "新手"),
    ("午夜放映厅", "恐怖", 5, 8, 240, 138, "进阶"),
    ("星陨之夜", "机制", 6, 8, 300, 168, "进阶"),
    ("齿轮之城", "机制", 6, 9, 330, 188, "硬核"),
    ("赌局", "机制", 5, 7, 240, 138, "进阶"),
    ("时间囚徒", "机制", 6, 8, 300, 158, "硬核"),
    ("最后的列车", "机制", 6, 9, 330, 178, "硬核"),
    ("无声证词", "硬核", 5, 7, 300, 168, "硬核"),
    ("孤岛邮局", "硬核", 6, 8, 330, 188, "硬核"),
    ("密室13号", "硬核", 4, 6, 240, 128, "进阶"),
    ("双重悖论", "硬核", 6, 8, 330, 198, "硬核"),
    ("迷雾审判", "硬核", 5, 8, 300, 168, "进阶"),
    ("寒江城", "阵营", 6, 9, 300, 158, "进阶"),
    ("权倾天下", "阵营", 6, 10, 330, 178, "进阶"),
    ("江湖客栈", "阵营", 6, 8, 270, 148, "新手"),
    ("王座之争", "阵营", 6, 10, 330, 188, "硬核"),
    ("山海异闻", "阵营", 5, 8, 300, 158, "进阶"),
    ("归途", "还原", 4, 6, 240, 118, "新手"),
    ("孤舟", "还原", 5, 7, 270, 138, "进阶"),
    ("禁区", "还原", 6, 8, 300, 158, "进阶"),
    ("年轮之外", "还原", 5, 8, 300, 168, "硬核"),
    ("记忆碎片", "还原", 4, 7, 240, 128, "进阶"),
]

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英华玉红建国"
NICK_PREFIX = ["小", "阿", "老", "大", "糖", "奶", "可", "柠", "星", "月", "风", "云",
               "半", "不", "一", "木"]
NICK_SUFFIX = ["马", "杰", "婷", "K", "糖", "乐", "哲", "鹿", "月", "阳", "橙", "夏",
               "冬", "青", "白", "七", "九", "川", "舟", "野", "心", "安", "然", "知",
               "言", "北", "南"]
CHANNELS = ["美团", "大众点评", "小程序", "到店", "拼车群"]
CHANNEL_WEIGHTS = [32, 22, 26, 12, 8]
MEMBER_LEVELS = ["普通", "银卡", "金卡", "黑卡"]
MEMBER_WEIGHTS = [60, 25, 12, 3]


def _phone() -> str:
    return "1" + random.choice("3589") + "".join(random.choice("0123456789") for _ in range(9))


def _rand_date(a: datetime.date, b: datetime.date) -> datetime.date:
    return datetime.date.fromordinal(random.randint(a.toordinal(), b.toordinal()))


def _unique_names(n: int, used: set[str]) -> list[str]:
    out = []
    while len(out) < n:
        name = random.choice(SURNAMES) + "".join(random.sample(GIVEN, random.randint(1, 2)))
        if name not in used:
            used.add(name)
            out.append(name)
    return out


def main() -> None:
    os.makedirs("data", exist_ok=True)
    path = "data/scriptkill.db"
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE stores (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        city TEXT NOT NULL,
        district TEXT NOT NULL,
        open_date TEXT NOT NULL,
        manager_name TEXT NOT NULL,
        status TEXT NOT NULL
    );
    CREATE TABLE staff (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        store_id INTEGER NOT NULL REFERENCES stores(id),
        role TEXT NOT NULL,
        dm_level TEXT,
        phone TEXT,
        hire_date TEXT NOT NULL,
        resign_date TEXT
    );
    CREATE TABLE scripts (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL,
        player_min INTEGER NOT NULL,
        player_max INTEGER NOT NULL,
        duration_min INTEGER NOT NULL,
        price_per_player REAL NOT NULL,
        difficulty TEXT NOT NULL,
        launch_date TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE script_licenses (
        id INTEGER PRIMARY KEY,
        store_id INTEGER NOT NULL REFERENCES stores(id),
        script_id INTEGER NOT NULL REFERENCES scripts(id),
        license_date TEXT NOT NULL,
        license_fee REAL NOT NULL
    );
    CREATE TABLE players (
        id INTEGER PRIMARY KEY,
        nickname TEXT NOT NULL UNIQUE,
        phone TEXT,
        member_level TEXT NOT NULL,
        register_date TEXT NOT NULL,
        home_store_id INTEGER NOT NULL REFERENCES stores(id)
    );
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY,
        store_id INTEGER NOT NULL REFERENCES stores(id),
        script_id INTEGER NOT NULL REFERENCES scripts(id),
        dm_id INTEGER REFERENCES staff(id),
        session_date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        status TEXT NOT NULL,
        player_count INTEGER NOT NULL,
        total_amount REAL NOT NULL
    );
    CREATE TABLE session_players (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES sessions(id),
        player_id INTEGER NOT NULL REFERENCES players(id),
        price_paid REAL NOT NULL,
        channel TEXT NOT NULL,
        is_new INTEGER NOT NULL
    );
    CREATE TABLE revenue_targets (
        id INTEGER PRIMARY KEY,
        store_id INTEGER NOT NULL REFERENCES stores(id),
        month TEXT NOT NULL,
        target_amount REAL NOT NULL
    );
    CREATE INDEX idx_sessions_store_date ON sessions(store_id, session_date);
    CREATE INDEX idx_sessions_date ON sessions(session_date);
    CREATE INDEX idx_sessions_script ON sessions(script_id);
    CREATE INDEX idx_sessions_dm ON sessions(dm_id);
    CREATE INDEX idx_sp_session ON session_players(session_id);
    CREATE INDEX idx_sp_player ON session_players(player_id);
    CREATE INDEX idx_staff_store ON staff(store_id);
    CREATE INDEX idx_lic_store ON script_licenses(store_id);
    """)

    # ---------- 门店 ----------
    c.executemany(
        "INSERT INTO stores VALUES (?,?,?,?,?,?,?)",
        [(sid, name, CITY, district, open_date, manager, "营业中")
         for sid, name, district, open_date, manager in STORES],
    )
    store_open = {sid: datetime.date.fromisoformat(d) for sid, _, _, d, _ in STORES}

    # ---------- 员工:每店 1 店长 + 1 前台 + 5 DM + 1 NPC ----------
    staff, sid_staff = [], 0
    used_names: set[str] = set()
    for store_id, _, _, open_date, manager in STORES:
        open_d = datetime.date.fromisoformat(open_date)
        sid_staff += 1
        staff.append((sid_staff, manager, store_id, "店长", None,
                      _phone(), _rand_date(open_d - datetime.timedelta(days=90),
                                           open_d + datetime.timedelta(days=30)).isoformat(), None))
        for role, level in [("前台", None)] + [("DM", lv) for lv in
                                              ["高级", "高级", "中级", "中级", "初级"]] + [("NPC", None)]:
            sid_staff += 1
            name = _unique_names(1, used_names)[0]
            staff.append((sid_staff, name, store_id, role, level,
                          _phone() if random.random() > 0.08 else None,
                          _rand_date(open_d, min(open_d + datetime.timedelta(days=400), TODAY)).isoformat(),
                          None))
    # 新店开业前至少 3 名 DM 已到岗,避免历史场次大面积没有 DM
    for store_id, _, _, open_date, _ in STORES:
        open_d = datetime.date.fromisoformat(open_date)
        store_dms = [s[0] for s in staff if s[3] == "DM" and s[2] == store_id]
        for staff_id in store_dms[:3]:
            row = list(staff[staff_id - 1])
            row[6] = (open_d - datetime.timedelta(days=20)).isoformat()
            staff[staff_id - 1] = tuple(row)

    # 3 名 DM 已离职(不影响历史场次)
    dm_ids = [s[0] for s in staff if s[3] == "DM"]
    for staff_id in random.sample(dm_ids, 3):
        row = list(staff[staff_id - 1])
        hire = datetime.date.fromisoformat(row[6])
        resign_from = min(hire + datetime.timedelta(days=120), TODAY)
        resign_to = max(resign_from, datetime.date(2026, 8, 31))
        row[7] = _rand_date(resign_from, resign_to).isoformat()
        staff[staff_id - 1] = tuple(row)
    c.executemany("INSERT INTO staff VALUES (?,?,?,?,?,?,?,?)", staff)

    # ---------- 剧本 ----------
    scripts, script_launch = [], {}
    for i, (name, cat, pmin, pmax, dur, price, diff) in enumerate(SCRIPTS, 1):
        launch = _rand_date(datetime.date(2023, 6, 1), datetime.date(2026, 8, 31))
        script_launch[i] = launch
        scripts.append((i, name, cat, pmin, pmax, dur, float(price), diff,
                        launch.isoformat(), 0 if random.random() < 0.08 else 1))
    c.executemany("INSERT INTO scripts VALUES (?,?,?,?,?,?,?,?,?,?)", scripts)
    script_by_id = {s[0]: s for s in scripts}
    active_script_ids = [s[0] for s in scripts if s[9] == 1]

    # ---------- 门店购本(授权) ----------
    licenses, lid = [], 0
    licensed: dict[int, list[int]] = {}
    for store_id, _, _, open_date, _ in STORES:
        open_d = datetime.date.fromisoformat(open_date)
        pool = [sid for sid in active_script_ids if script_launch[sid] <= TODAY]
        chosen = random.sample(pool, min(len(pool), random.randint(18, 24)))
        licensed[store_id] = chosen
        for script_id in chosen:
            lid += 1
            lo = max(open_d, script_launch[script_id])
            licenses.append((lid, store_id, script_id,
                             _rand_date(lo, min(TODAY, lo + datetime.timedelta(days=540))).isoformat(),
                             float(random.choice([2280, 2980, 3680, 4280, 5280, 6880, 8800, 10800]))))
    c.executemany("INSERT INTO script_licenses VALUES (?,?,?,?,?)", licenses)

    # ---------- 玩家(会员) ----------
    players, nick_used = [], set()
    for pid in range(1, 1201):
        store_id = random.choices(range(1, 6), weights=[26, 24, 22, 16, 12])[0]
        reg = _rand_date(store_open[store_id], TODAY)
        nick = random.choice(NICK_PREFIX) + random.choice(NICK_SUFFIX)
        if nick in nick_used:
            while nick in nick_used:
                nick = f"{nick[:4]}{random.randint(10, 9999)}"
        nick_used.add(nick)
        players.append((pid, nick, _phone() if random.random() > 0.1 else None,
                        random.choices(MEMBER_LEVELS, weights=MEMBER_WEIGHTS)[0],
                        reg.isoformat(), store_id))
    c.executemany("INSERT INTO players VALUES (?,?,?,?,?,?)", players)
    player_reg = {p[0]: datetime.date.fromisoformat(p[4]) for p in players}
    # 按注册日期排序,便于按场次日期筛选“已注册玩家”
    reg_sorted = sorted(player_reg.items(), key=lambda kv: kv[1])
    reg_dates = [d for _, d in reg_sorted]

    # ---------- 场次 + 玩家到场明细 ----------
    active_dm_by_store: dict[int, list[tuple[int, str | None]]] = {}
    for s in staff:
        s_id, name, store_id, role, level, phone, hire, resign = s
        if role == "DM":
            active_dm_by_store.setdefault(store_id, []).append((s_id, hire, resign))

    sessions, session_players = [], []
    session_id = 0
    sp_id = 0
    d = START
    while d <= TODAY:
        is_weekend = d.weekday() >= 4          # 周五/六/日
        in_holiday = d.month in (1, 2, 7, 8)
        for store_id, _, _, open_str, _ in STORES:
            open_d = datetime.date.fromisoformat(open_str)
            if d < open_d:
                continue
            base = 7 if is_weekend else 4
            if in_holiday:
                base += 1
            if store_id in (4, 5):
                base -= 1
            n_sessions = max(1, base + random.choice([-1, 0, 0, 1]))
            for _ in range(n_sessions):
                session_id += 1
                script_id = random.choice(licensed[store_id])
                _, _, _, pmin, pmax, dur, base_price, _, _, _ = script_by_id[script_id]

                # 当天安排:少量进行中/待开本;历史日期只有已完成/已取消
                if d == TODAY:
                    status = random.choices(["已完成", "进行中", "待开本"],
                                            weights=[45, 25, 30])[0]
                else:
                    status = "已完成" if random.random() > 0.10 else "已取消"

                # 场次开始时间(晚场为主)
                hour = random.choices([13, 14, 15, 16, 18, 19, 20, 21],
                                      weights=[5, 6, 7, 8, 18, 22, 18, 10])[0]
                minute = random.choice([0, 0, 30])
                start_dt = datetime.datetime.combine(d, datetime.time(hour, minute))
                end_dt = start_dt + datetime.timedelta(minutes=dur)

                # 当天在职 DM;1% 场次无归属 DM
                eligible = [
                    s_id for s_id, hire, resign in active_dm_by_store.get(store_id, [])
                    if hire <= d.isoformat() and (resign is None or resign > d.isoformat())
                ]
                dm_id = random.choice(eligible) if eligible and random.random() > 0.01 else None

                if status == "已取消":
                    player_count, total_amount = 0, 0.0
                else:
                    player_count = random.randint(pmin, pmax)
                    # 已注册玩家池(注册日期 <= 场次日期)
                    hi = bisect.bisect_right(reg_dates, d)
                    pool = [pid for pid, _ in reg_sorted[:hi]]
                    chosen = random.sample(pool, min(player_count, len(pool)))
                    player_count = len(chosen)
                    total_amount = 0.0
                    for player_id in chosen:
                        channel = random.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]
                        factor = {
                            "美团": random.uniform(0.75, 1.0),
                            "大众点评": random.uniform(0.8, 1.0),
                            "拼车群": random.uniform(0.7, 0.95),
                        }.get(channel, 1.0)
                        price_paid = round(base_price * factor, 2)
                        total_amount += price_paid
                        is_new = 1 if (d - player_reg[player_id]).days <= 60 else 0
                        sp_id += 1
                        session_players.append((sp_id, session_id, player_id,
                                                price_paid, channel, is_new))
                    total_amount = round(total_amount, 2)
                sessions.append((
                    session_id, store_id, script_id, dm_id, d.isoformat(),
                    start_dt.strftime("%H:%M"), end_dt.strftime("%H:%M"),
                    status, player_count, total_amount,
                ))
        d += datetime.timedelta(days=1)

    c.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)", sessions)
    c.executemany("INSERT INTO session_players VALUES (?,?,?,?,?,?)", session_players)

    # ---------- 门店月度营收目标(2025-01 ~ 2026-12) ----------
    targets, tid = [], 0
    base_target = {1: 175_000, 2: 160_000, 3: 140_000, 4: 115_000, 5: 95_000}
    for y in (2025, 2026):
        for m in range(1, 13):
            for store_id in range(1, 6):
                tid += 1
                boost = 1.35 if m in (1, 2, 7, 8) else 1.0
                targets.append((tid, store_id, f"{y}-{m:02d}",
                                round(base_target[store_id] * boost * random.uniform(0.9, 1.1), 2)))
    c.executemany("INSERT INTO revenue_targets VALUES (?,?,?,?)", targets)

    conn.commit()
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("stores", "staff", "scripts", "script_licenses", "players",
                        "sessions", "session_players", "revenue_targets")}
    bad_amount = c.execute("""
        SELECT COUNT(*) FROM sessions s
        WHERE s.status <> '已取消' AND ABS(s.total_amount -
            (SELECT IFNULL(SUM(price_paid), 0) FROM session_players sp WHERE sp.session_id = s.id)) > 0.01
    """).fetchone()[0]
    bad_count = c.execute("""
        SELECT COUNT(*) FROM sessions s
        WHERE s.status <> '已取消' AND s.player_count <>
            (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id)
    """).fetchone()[0]
    unlicensed = c.execute("""
        SELECT COUNT(*) FROM sessions s
        WHERE NOT EXISTS (SELECT 1 FROM script_licenses l
                          WHERE l.store_id = s.store_id AND l.script_id = s.script_id)
    """).fetchone()[0]
    conn.close()
    print(f"演示库已生成: {path} -> {counts}")
    print(f"一致性校验: 金额不一致={bad_amount}, 人数不一致={bad_count}, "
          f"未授权剧本场次={unlicensed}")
    print(f"数据区间: {START} ~ {TODAY}")


if __name__ == "__main__":
    main()
