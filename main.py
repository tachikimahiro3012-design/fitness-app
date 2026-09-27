import calendar
import datetime
import json
import sqlite3
import pandas as pd
from PIL import Image
import streamlit as st
from google import genai

# --------------------------------------------------
# ページ基本設定
# --------------------------------------------------
st.set_page_config(
    page_title="FITNESS & NUTRITION TRACKER",
    layout="centered",
    initial_sidebar_state="collapsed",
)


# --------------------------------------------------
# データベース初期化
# --------------------------------------------------
def init_db():
    conn = sqlite3.connect("workout.db", check_same_thread=False)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS workout_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            part TEXT,
            exercise TEXT,
            weight REAL,
            reps INTEGER,
            duration_min INTEGER,
            intensity TEXT,
            burned_calories REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part TEXT,
            name TEXT,
            UNIQUE(part, name)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS food_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            breakfast_cal REAL DEFAULT 0,
            lunch_cal REAL DEFAULT 0,
            dinner_cal REAL DEFAULT 0,
            snack_cal REAL DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            gender TEXT,
            age INTEGER,
            height REAL,
            weight REAL,
            activity_level TEXT,
            steps INTEGER,
            goal_phase TEXT,
            api_key TEXT
        )
    """)

    # カラム追加のフォールバック
    try:
        c.execute("ALTER TABLE user_profile ADD COLUMN api_key TEXT")
    except sqlite3.OperationalError:
        pass

    default_exercises = [
        ("胸", "ベンチプレス"),
        ("胸", "インクラインダンベルプレス"),
        ("胸", "ダンベルプレス"),
        ("胸", "スミスインクラインベンチプレス"),
        ("胸", "チェストプレス"),
        ("胸", "ペックフライ"),
        ("胸", "ケーブルフライ"),
        ("二頭", "インクラインダンベルカール"),
        ("二頭", "ダンベルハンマーカール"),
        ("二頭", "ケーブルカール"),
        ("二頭", "プリチャーハンマーカール"),
        ("三頭", "フレンチプレス"),
        ("三頭", "ケーブルプレスダウン"),
        ("背中", "チンニング"),
        ("背中", "ベントオーバーローイング"),
        ("背中", "ワンハンドローイング"),
        ("背中", "ラットプルダウン"),
        ("背中", "シーテッドローイング"),
        ("肩", "ダンベルショルダープレス"),
        ("肩", "スミスショルダープレス"),
        ("肩", "サイドレイズ"),
        ("肩", "インクラインサイドレイズ"),
        ("肩", "ケーブルフェイスプル"),
        ("肩", "ケーブルフロントレイズ"),
        ("脚", "スクワット"),
        ("脚", "45度レッグプレス"),
        ("脚", "レッグプレス"),
        ("脚", "ブルガリアンスクワット"),
        ("脚", "レッグエクステンション"),
        ("脚", "レッグカール"),
        ("脚", "インナーサイ"),
    ]

    c.execute("SELECT COUNT(*) FROM exercises")
    exercise_count = c.fetchone()[0]
    if exercise_count == 0:
        c.executemany(
            "INSERT OR IGNORE INTO exercises (part, name) VALUES (?, ?)",
            default_exercises,
        )

    conn.commit()
    return conn


# --------------------------------------------------
# Gemini AIによる画像解析関数
# --------------------------------------------------
def analyze_food_image(image: Image.Image, api_key: str):
    try:
        client = genai.Client(api_key=api_key)
        prompt = """
        添付された食事の画像を解析し、おおよそのカロリー（kcal）と料理の名称・内訳を推定してください。
        回答は必ず以下の純粋なJSONフォーマットのみで出力してください（Markdownのバックトウや装飾は不要です）。

        {
            "dish_name": "推定される料理名や内容",
            "total_calories": 推定合計カロリー(数値のみ),
            "meal_type": "朝食", "昼食", "夕食", "間食" のいずれか最も可能性が高いもの,
            "description": "内訳や理由の短い補足コメント"
        }
        """
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=[image, prompt]
        )

        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        return json.loads(text), None
    except Exception as e:
        return None, str(e)


# BMR / TDEE / 目標カロリー計算
def calculate_nutrition_targets(
    gender, age, height, weight, activity_level, steps, goal_phase
):
    if gender == "男性":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    act_multipliers = {
        "デスクワーク中心": 1.2,
        "週1〜2回運動": 1.375,
        "週3〜4回運動": 1.55,
        "週5回以上運動": 1.725,
    }
    base_tdee = bmr * act_multipliers.get(activity_level, 1.2)
    step_burn = steps * 0.03
    tdee = base_tdee + step_burn

    phase_offsets = {
        "積極的減量 (-500 kcal)": -500,
        "標準減量 (-400 kcal)": -400,
        "軽い減量 (-250 kcal)": -250,
        "維持・安定期 (±0 kcal)": 0,
        "控えめ増量 (+200 kcal)": 200,
        "標準増量 (+300 kcal)": 300,
    }
    offset = phase_offsets.get(goal_phase, 0)
    target_cal = tdee + offset

    return round(bmr), round(tdee), round(target_cal), offset


# METs計算
def calculate_workout_burn(weight, duration_min, intensity):
    mets_map = {
        "標準 (通常のウェイトトレーニング)": 6.0,
        "軽度 (ストレッチ/自重/休憩長め)": 3.5,
        "高強度 (サーキット/高密度/スーパーセット)": 8.0,
    }
    mets = mets_map.get(intensity, 6.0)
    if duration_min <= 0:
        return 0.0
    burn = (mets - 1.0) * weight * (duration_min / 60.0) * 1.05
    return round(burn, 1)


# --------------------------------------------------
# UI用CSS
# --------------------------------------------------
def inject_theme_css():
    st.markdown(
        """
        <style>
        :root {
            --brand-red: #e63946;
            --brand-red-dark: #c92a3d;
        }

        .section-banner {
            background: var(--brand-red);
            color: #ffffff;
            font-weight: 700;
            font-size: 1.05rem;
            padding: 10px 14px;
            border-radius: 10px;
            margin: 18px 0 10px;
        }

        .app-header {
            background: var(--brand-red);
            color: #ffffff;
            border-radius: 12px;
            padding: 14px 16px;
            margin-bottom: 14px;
        }
        .app-header .app-title { font-size: 1.15rem; font-weight: 800; }
        .app-header .app-date { font-size: 0.8rem; opacity: 0.9; margin-top: 2px; }

        button[kind="primary"] {
            background-color: var(--brand-red) !important;
            border-color: var(--brand-red) !important;
            color: #ffffff !important;
            font-weight: 700 !important;
        }
        button[kind="primary"]:hover {
            background-color: var(--brand-red-dark) !important;
            border-color: var(--brand-red-dark) !important;
        }
        
        button[kind="secondary"] {
            background-color: #f3f4f6 !important;
            border: 1px solid #d1d5db !important;
            color: #1f2937 !important;
            font-weight: 600 !important;
        }
        button[kind="secondary"]:hover {
            background-color: #e5e7eb !important;
            color: #111827 !important;
        }

        [data-testid="stAppViewContainer"], .main {
            background-color: #ffffff !important;
            color: #111827 !important;
        }

        div[data-testid="stHorizontalBlock"] {
            flex-direction: row !important;
            flex-wrap: nowrap !important;
        }
        div[data-testid="stHorizontalBlock"] > div {
            width: 100% !important;
            flex: 1 1 0 !important;
            min-width: 0 !important;
        }

        .ex-card { border: 1px solid #e5e7eb; border-radius: 12px; overflow: hidden; margin-bottom: 14px; }
        .ex-card-header { background: var(--brand-red); color: #fff; font-weight: 700;
                           padding: 8px 12px; font-size: 0.95rem; }
        .ex-set-row { display: grid; grid-template-columns: 0.5fr 1fr 1fr 1fr; padding: 6px 12px;
                      font-size: 0.85rem; border-top: 1px solid #e5e7eb; color: #111827; }
        .ex-set-row.head { font-weight: 700; color: #6b7280; font-size: 0.72rem; border-top: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def red_banner(text: str):
    st.markdown(f'<div class="section-banner">{text}</div>', unsafe_allow_html=True)


def main():
    conn = init_db()
    c = conn.cursor()

    profile = c.execute(
        "SELECT gender, age, height, weight, activity_level, steps, goal_phase, api_key FROM user_profile WHERE id = 1"
    ).fetchone()

    if profile:
        p_gender, p_age, p_height, p_weight, p_act, p_steps, p_goal, p_api_key = (
            profile
        )
    else:
        (
            p_gender,
            p_age,
            p_height,
            p_weight,
            p_act,
            p_steps,
            p_goal,
            p_api_key,
        ) = (
            "男性",
            25,
            170.0,
            65.0,
            "週3〜4回運動",
            8000,
            "標準増量 (+300 kcal)",
            "",
        )

    # サイドバー：設定
    st.sidebar.title("ユーザー設定")
    with st.sidebar.form("profile_form"):
        api_key_input = st.text_input(
            "Gemini API Key",
            value=p_api_key if p_api_key else "",
            type="password",
            help="Google AI Studioで取得したAPIキーを入力してください",
        )
        gender = st.selectbox(
            "性別", ["男性", "女性"], index=0 if p_gender == "男性" else 1
        )
        age = st.number_input("年齢", min_value=10, max_value=100, value=p_age)
        height = st.number_input(
            "身長 (cm)", min_value=100.0, max_value=230.0, value=float(p_height)
        )
        weight = st.number_input(
            "体重 (kg)", min_value=30.0, max_value=200.0, value=float(p_weight)
        )
        act_level = st.selectbox(
            "日常活動レベル",
            [
                "デスクワーク中心",
                "週1〜2回運動",
                "週3〜4回運動",
                "週5回以上運動",
            ],
            index=[
                "デスクワーク中心",
                "週1〜2回運動",
                "週3〜4回運動",
                "週5回以上運動",
            ].index(p_act)
            if p_act in ["デスクワーク中心", "週1〜2回運動", "週3〜4回運動", "週5回以上運動"]
            else 2,
        )
        steps = st.number_input(
            "1日の平均歩数", min_value=0, max_value=50000, value=p_steps, step=500
        )
        goal_phase = st.selectbox(
            "現在の目的",
            [
                "積極的減量 (-500 kcal)",
                "標準減量 (-400 kcal)",
                "軽い減量 (-250 kcal)",
                "維持・安定期 (±0 kcal)",
                "控えめ増量 (+200 kcal)",
                "標準増量 (+300 kcal)",
            ],
            index=[
                "積極的減量 (-500 kcal)",
                "標準減量 (-400 kcal)",
                "軽い減量 (-250 kcal)",
                "維持・安定期 (±0 kcal)",
                "控えめ増量 (+200 kcal)",
                "標準増量 (+300 kcal)",
            ].index(p_goal)
            if p_goal
            in [
                "積極的減量 (-500 kcal)",
                "標準減量 (-400 kcal)",
                "軽い減量 (-250 kcal)",
                "維持・安定期 (±0 kcal)",
                "控えめ増量 (+200 kcal)",
                "標準増量 (+300 kcal)",
            ]
            else 5,
        )

        if st.form_submit_button("設定を保存"):
            c.execute(
                """
                INSERT OR REPLACE INTO user_profile (id, gender, age, height, weight, activity_level, steps, goal_phase, api_key)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    gender,
                    age,
                    height,
                    weight,
                    act_level,
                    steps,
                    goal_phase,
                    api_key_input.strip(),
                ),
            )
            conn.commit()
            st.sidebar.success("設定を更新しました。")
            st.rerun()

    # 種目追加
    st.sidebar.divider()
    st.sidebar.title("種目追加")
    new_part = st.sidebar.selectbox(
        "部位", ["胸", "二頭", "三頭", "背中", "肩", "脚"]
    )
    new_ex = st.sidebar.text_input("種目名")
    if st.sidebar.button("種目を追加"):
        if new_ex.strip() != "":
            try:
                c.execute(
                    "INSERT INTO exercises (part, name) VALUES (?, ?)",
                    (new_part, new_ex.strip()),
                )
                conn.commit()
                st.sidebar.success(f"「{new_ex}」を追加しました。")
                st.rerun()
            except sqlite3.IntegrityError:
                st.sidebar.warning("既に登録されています。")

    # 計算実行
    bmr, tdee, target_cal, offset = calculate_nutrition_targets(
        p_gender, p_age, p_height, p_weight, p_act, p_steps, p_goal
    )

    today_str = datetime.date.today().strftime("%Y-%m-%d")

    food_row = c.execute(
        "SELECT breakfast_cal, lunch_cal, dinner_cal, snack_cal FROM food_logs WHERE date = ?",
        (today_str,),
    ).fetchone()
    total_ingested_cal = sum(food_row) if food_row else 0.0

    workout_burn_row = c.execute(
        "SELECT SUM(burned_calories) FROM workout_logs WHERE date = ?",
        (today_str,),
    ).fetchone()
    total_workout_burn = (
        workout_burn_row[0] if workout_burn_row[0] is not None else 0.0
    )

    inject_theme_css()

    st.markdown(
        f"""
        <div class="app-header">
            <div class="app-title">FITNESS & NUTRITION TRACKER</div>
            <div class="app-date">{today_str}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "view" not in st.session_state:
        st.session_state.view = "dashboard"

    nav_items = [
        ("dashboard", "ホーム"),
        ("food", "食事記録"),
        ("workout", "筋トレ記録"),
    ]
    nav_cols = st.columns(3)
    for col, (view_key, label) in zip(nav_cols, nav_items):
        is_active = st.session_state.view == view_key
        with col:
            if st.button(
                label,
                key=f"nav_{view_key}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state.view = view_key
                st.rerun()
    st.divider()

    # --------------------------------------------------
    # 画面1: ホーム
    # --------------------------------------------------
    if st.session_state.view == "dashboard":
        red_banner("今日の状態")

        offset_str = (
            f"+{offset}"
            if offset > 0
            else (f"{offset}" if offset < 0 else "±0")
        )
        st.info(
            f"現在の設定: {p_goal} | 推定維持カロリー(TDEE): {tdee} kcal | 調整幅: {offset_str} kcal"
        )

        red_banner("今日のカロリー状況")
        target_diff = target_cal - total_ingested_cal
        net_balance = total_ingested_cal - (tdee + total_workout_burn)

        if offset < 0:
            balance_class = "green" if net_balance <= 0 else "red"
        elif offset > 0:
            balance_class = "green" if net_balance >= 0 else "red"
        else:
            balance_class = "green" if abs(net_balance) <= 150 else "red"

        target_diff_class = "green" if target_diff >= 0 else "red"
        target_diff_str = (
            f"{int(target_diff)}"
            if target_diff >= 0
            else f"-{int(abs(target_diff))}"
        )
        net_balance_str = (
            f"+{int(net_balance)}" if net_balance >= 0 else f"{int(net_balance)}"
        )

        st.markdown(
            """
            <style>
            .stat-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-top: 6px; }
            .stat-card { background: #f8f9fa; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 12px; }
            .stat-card.wide { grid-column: span 2; }
            .stat-label { font-size: 0.72rem; color: #6b7280; margin-bottom: 4px; }
            .stat-value { font-size: 1.3rem; font-weight: 700; color: #111827; line-height: 1.15; }
            .stat-value.green { color: #16a34a; }
            .stat-value.red { color: #dc2626; }
            .stat-value.orange { color: #ea580c; }
            </style>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="stat-grid">
                <div class="stat-card">
                    <div class="stat-label">目標摂取カロリー</div>
                    <div class="stat-value">{target_cal} kcal</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">現在の摂取カロリー</div>
                    <div class="stat-value">{int(total_ingested_cal)} kcal</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">目標まで</div>
                    <div class="stat-value {target_diff_class}">{target_diff_str} kcal</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">筋トレ推定消費</div>
                    <div class="stat-value orange">約 {int(total_workout_burn)} kcal</div>
                </div>
                <div class="stat-card wide">
                    <div class="stat-label">実質エネルギー収支(摂取 − 消費)</div>
                    <div class="stat-value {balance_class}">{net_balance_str} kcal</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.divider()

        now = datetime.date.today()
        today_str_display = now.strftime("%Y-%m-%d")
        st.subheader(f"{now.year}年 {now.month}月")

        month_start = f"{now.year}-{now.month:02d}-01"
        next_month = now.month + 1 if now.month < 12 else 1
        next_month_year = now.year if now.month < 12 else now.year + 1
        month_end = f"{next_month_year}-{next_month:02d}-01"

        recorded_df = pd.read_sql_query(
            "SELECT DISTINCT date FROM workout_logs WHERE date >= ? AND date < ?",
            conn,
            params=(month_start, month_end),
        )
        recorded_dates = (
            set(recorded_df["date"].tolist())
            if not recorded_df.empty
            else set()
        )

        food_month_df = pd.read_sql_query(
            "SELECT date, (breakfast_cal + lunch_cal + dinner_cal + snack_cal) AS total_cal "
            "FROM food_logs WHERE date >= ? AND date < ?",
            conn,
            params=(month_start, month_end),
        )
        food_cal_by_date = (
            dict(zip(food_month_df["date"], food_month_df["total_cal"]))
            if not food_month_df.empty
            else {}
        )

        st.markdown(
            """
            <style>
            .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; margin-top: 6px; }
            .cal-head { text-align: center; font-weight: 700; font-size: 0.8rem; color: #6b7280; padding-bottom: 2px; }
            .cal-cell { border-radius: 10px; padding: 6px 4px 8px; min-height: 62px; background: #f8f9fa;
                        border: 1px solid #e5e7eb; display: flex; flex-direction: column; align-items: center; }
            .cal-cell.empty { background: transparent; border: none; }
            .cal-cell.today { border: 2px solid var(--brand-red); }
            .cal-day-num { font-weight: 700; font-size: 0.85rem; color: #111827; }
            .cal-workout-dot { width: 7px; height: 7px; border-radius: 50%; background: #f97316; margin-top: 3px; }
            .cal-no-dot { width: 7px; height: 7px; margin-top: 3px; }
            .cal-cal-num { margin-top: 4px; font-size: 0.68rem; font-weight: 700; line-height: 1; }
            .cal-cal-num.under { color: #16a34a; }
            .cal-cal-num.over { color: #dc2626; }
            .cal-cal-num.none { color: #9ca3af; }
            .cal-legend { display: flex; gap: 16px; margin-top: 10px; font-size: 0.75rem; color: #6b7280; align-items: center; flex-wrap: wrap; }
            .cal-legend-dot { width: 8px; height: 8px; border-radius: 50%; background: #f97316; display: inline-block; margin-right: 4px; }
            .cal-legend-num.under { color: #16a34a; font-weight: 700; }
            .cal-legend-num.over { color: #dc2626; font-weight: 700; }
            </style>
            """,
            unsafe_allow_html=True,
        )

        days_abbr = ["日", "月", "火", "水", "木", "金", "土"]
        head_html = "".join(f'<div class="cal-head">{d}</div>' for d in days_abbr)

        cells_html = ""
        month_cal = calendar.monthcalendar(now.year, now.month)
        for week in month_cal:
            for day in week:
                if day == 0:
                    cells_html += '<div class="cal-cell empty"></div>'
                    continue

                date_str = f"{now.year}-{now.month:02d}-{day:02d}"
                is_today = date_str == today_str_display
                has_workout = date_str in recorded_dates
                cal_total = food_cal_by_date.get(date_str)

                today_class = " today" if is_today else ""
                dot_html = (
                    '<div class="cal-workout-dot"></div>'
                    if has_workout
                    else '<div class="cal-no-dot"></div>'
                )

                if cal_total is None:
                    cal_html = '<div class="cal-cal-num none">-</div>'
                elif cal_total <= target_cal:
                    cal_html = f'<div class="cal-cal-num under">{int(cal_total)}</div>'
                else:
                    cal_html = f'<div class="cal-cal-num over">{int(cal_total)}</div>'

                cells_html += (
                    f'<div class="cal-cell{today_class}">'
                    f'<div class="cal-day-num">{day}</div>'
                    f'{dot_html}'
                    f'{cal_html}'
                    f'</div>'
                )

        st.markdown(
            f'<div class="cal-grid">{head_html}{cells_html}</div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            """
            <div class="cal-legend">
                <span><span class="cal-legend-dot"></span>筋トレした日</span>
                <span><span class="cal-legend-num under">数字</span> = 摂取カロリー(目標以内)</span>
                <span><span class="cal-legend-num over">数字</span> = 摂取カロリー(目標オーバー)</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # --------------------------------------------------
    # 画面2: 食事記録（AIカメラ追加）
    # --------------------------------------------------
    elif st.session_state.view == "food":
        red_banner("食事カロリー入力")

        food_date = st.date_input("記録日", value=datetime.date.today())
        f_date_str = food_date.strftime("%Y-%m-%d")

        f_row = c.execute(
            "SELECT breakfast_cal, lunch_cal, dinner_cal, snack_cal FROM food_logs WHERE date = ?",
            (f_date_str,),
        ).fetchone()

        # Session Stateの初期化
        if "ai_b" not in st.session_state:
            st.session_state.ai_b = f_row[0] if f_row else 0.0
        if "ai_l" not in st.session_state:
            st.session_state.ai_l = f_row[1] if f_row else 0.0
        if "ai_d" not in st.session_state:
            st.session_state.ai_d = f_row[2] if f_row else 0.0
        if "ai_s" not in st.session_state:
            st.session_state.ai_s = f_row[3] if f_row else 0.0

        tab1, tab2 = st.tabs(["手入力", "📸 AIカメラ/写真解析"])

        with tab2:
            st.markdown("#### 写真からカロリーを推定")
            if not p_api_key:
                st.warning(
                    "左側のサイドバーメニュー（ユーザー設定）に Gemini API キーを入力してください。"
                )
            else:
                img_source = st.radio(
                    "入力方法",
                    ["カメラ撮影", "画像ファイル選択"],
                    horizontal=True,
                )
                uploaded_img = None

                if img_source == "カメラ撮影":
                    uploaded_img = st.camera_input("料理を撮影してください")
                else:
                    uploaded_img = st.file_uploader(
                        "画像を選択してください",
                        type=["jpg", "jpeg", "png", "webp"],
                    )

                if uploaded_img is not None:
                    image = Image.open(uploaded_img)
                    st.image(
                        image, caption="解析対象の画像", use_container_width=True
                    )

                    if st.button("AIでカロリーを判定", type="primary"):
                        with st.spinner("AIが食事内容とカロリーを解析中..."):
                            result, error = analyze_food_image(image, p_api_key)
                            if error:
                                st.error(f"解析エラー: {error}")
                            else:
                                st.session_state.ai_result = result

                if "ai_result" in st.session_state:
                    res = st.session_state.ai_result
                    st.success("解析が完了しました！")
                    st.write(f"**料理名:** {res.get('dish_name')}")
                    st.write(
                        f"**推定カロリー:** 約 {res.get('total_calories')} kcal"
                    )
                    st.write(f"**推奨区分:** {res.get('meal_type')}")
                    st.caption(f"メモ: {res.get('description')}")

                    if st.button("この結果を入力欄に反映する"):
                        m_type = res.get("meal_type")
                        c_val = float(res.get("total_calories", 0))

                        if m_type == "朝食":
                            st.session_state.ai_b = c_val
                        elif m_type == "昼食":
                            st.session_state.ai_l = c_val
                        elif m_type == "夕食":
                            st.session_state.ai_d = c_val
                        else:
                            st.session_state.ai_s = c_val

                        st.success("入力欄に反映しました！「手入力」タブで保存を行ってください。")

        with tab1:
            col_b, col_l, col_d, col_s = st.columns(4)
            with col_b:
                in_b = st.number_input(
                    "朝食 (kcal)",
                    min_value=0.0,
                    value=float(st.session_state.ai_b),
                    step=50.0,
                )
            with col_l:
                in_l = st.number_input(
                    "昼食 (kcal)",
                    min_value=0.0,
                    value=float(st.session_state.ai_l),
                    step=50.0,
                )
            with col_d:
                in_d = st.number_input(
                    "夕食 (kcal)",
                    min_value=0.0,
                    value=float(st.session_state.ai_d),
                    step=50.0,
                )
            with col_s:
                in_s = st.number_input(
                    "間食 (kcal)",
                    min_value=0.0,
                    value=float(st.session_state.ai_s),
                    step=50.0,
                )

            sub_total = in_b + in_l + in_d + in_s
            st.metric("本日の摂取合計", f"{int(sub_total)} kcal")

            if st.button("食事記録を保存", type="primary"):
                c.execute(
                    """
                    INSERT OR REPLACE INTO food_logs (date, breakfast_cal, lunch_cal, dinner_cal, snack_cal)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (f_date_str, in_b, in_l, in_d, in_s),
                )
                conn.commit()
                st.success("食事記録を保存しました。")
                st.rerun()

    # --------------------------------------------------
    # 画面3: 筋トレ記録
    # --------------------------------------------------
    elif st.session_state.view == "workout":
        red_banner("筋トレログ & 消費カロリー推定")

        red_banner("① セッション全体の設定")
        col_dur, col_int = st.columns(2)
        with col_dur:
            duration = st.number_input(
                "全体実施時間 (分)", min_value=1, max_value=300, value=60, step=5
            )
        with col_int:
            intensity = st.selectbox(
                "運動強度",
                [
                    "標準 (通常のウェイトトレーニング)",
                    "軽度 (ストレッチ/自重/休憩長め)",
                    "高強度 (サーキット/高密度/スーパーセット)",
                ],
            )

        estimated_burn = calculate_workout_burn(p_weight, duration, intensity)
        st.info(f"このセッションの推定純消費カロリー: 約 {estimated_burn} kcal")

        st.divider()
        red_banner("② 種目の記録")

        col_date, col_part = st.columns(2)
        with col_date:
            work_date = st.date_input(
                "日付", value=datetime.date.today(), key="w_date"
            )
            w_date_str = work_date.strftime("%Y-%m-%d")
        with col_part:
            part = st.selectbox(
                "部位", ["胸", "二頭", "三頭", "背中", "肩", "脚"]
            )

        ex_df = pd.read_sql_query(
            "SELECT name FROM exercises WHERE part = ?", conn, params=(part,)
        )
        ex_list = (
            ex_df["name"].tolist()
            if not ex_df.empty
            else ["（種目がありません）"]
        )
        exercise = st.selectbox("種目", ex_list)

        col1, col2 = st.columns(2)
        with col1:
            weight_val = st.number_input(
                "重量 (kg)", min_value=0.0, step=0.5, value=40.0
            )
        with col2:
            reps_val = st.number_input(
                "回数 (レップ)", min_value=0, step=1, value=10
            )

        if st.button("筋トレ記録を保存", type="primary"):
            if exercise != "（種目がありません）":
                c.execute(
                    """
                    INSERT INTO workout_logs (date, part, exercise, weight, reps, duration_min, intensity, burned_calories)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        w_date_str,
                        part,
                        exercise,
                        weight_val,
                        reps_val,
                        duration,
                        intensity,
                        estimated_burn,
                    ),
                )
                conn.commit()
                st.success(f"【{part}】{exercise} ({weight_val}kg × {reps_val}回) を記録しました！")
                st.rerun()

        st.divider()
        red_banner("本日の筋トレ記録")
        logs_df = pd.read_sql_query(
            "SELECT id, exercise, weight, reps FROM workout_logs "
            "WHERE date = ? ORDER BY id",
            conn,
            params=(w_date_str,),
        )
        if not logs_df.empty:
            cards_html = ""
            for exercise_name, group in logs_df.groupby("exercise", sort=False):
                rows_html = (
                    '<div class="ex-set-row head">'
                    "<div>セット</div><div>重さ</div><div>回数</div><div>RM</div></div>"
                )
                for set_no, (_, row) in enumerate(group.iterrows(), start=1):
                    estimated_rm = row["weight"] * (1 + 0.025 * row["reps"])
                    rows_html += (
                        '<div class="ex-set-row">'
                        f"<div>{set_no}</div>"
                        f"<div>{row['weight']:g} kg</div>"
                        f"<div>{int(row['reps'])} 回</div>"
                        f"<div>{estimated_rm:.1f} kg</div>"
                        "</div>"
                    )
                cards_html += (
                    '<div class="ex-card">'
                    f'<div class="ex-card-header">{exercise_name}</div>'
                    f"{rows_html}"
                    "</div>"
                )
            st.markdown(cards_html, unsafe_allow_html=True)
        else:
            st.info("本日の記録はまだありません。")


if __name__ == "__main__":
    main()