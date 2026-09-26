import calendar
import datetime
import sqlite3
import pandas as pd
import streamlit as st

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
            goal_phase TEXT
        )
    """)

    default_exercises = [
        ("胸", "ベンチプレス"),
        ("胸", "インクラインダンベルプレス"),
        ("胸", "ダンベルプレス"),
        ("胸", "チェストプレス"),
        ("二頭", "インクラインダンベルカール"),
        ("二頭", "ケーブルカール"),
        ("三頭", "ケーブルプレスダウン"),
        ("背中", "チンニング"),
        ("背中", "ラットプルダウン"),
        ("背中", "ベントオーバーローイング"),
        ("肩", "ダンベルショルダープレス"),
        ("肩", "サイドレイズ"),
        ("脚", "スクワット"),
        ("脚", "レッグプレス"),
    ]
    c.executemany(
        "INSERT OR IGNORE INTO exercises (part, name) VALUES (?, ?)",
        default_exercises,
    )

    conn.commit()
    return conn


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
        "軽度 (ストレッチ/自重/休憩長め)": 3.5,
        "標準 (通常のウェイトトレーニング)": 6.0,
        "高強度 (サーキット/高密度/スーパーセット)": 8.0,
    }
    mets = mets_map.get(intensity, 6.0)
    if duration_min <= 0:
        return 0.0
    burn = (mets - 1.0) * weight * (duration_min / 60.0) * 1.05
    return round(burn, 1)


def main():
    conn = init_db()
    c = conn.cursor()

    # プロフィール取得
    profile = c.execute(
        "SELECT gender, age, height, weight, activity_level, steps, goal_phase FROM user_profile WHERE id = 1"
    ).fetchone()

    if profile:
        p_gender, p_age, p_height, p_weight, p_act, p_steps, p_goal = profile
    else:
        p_gender, p_age, p_height, p_weight, p_act, p_steps, p_goal = (
            "男性",
            25,
            170.0,
            65.0,
            "週3〜4回運動",
            8000,
            "標準増量 (+300 kcal)",
        )

    # サイドバー：設定
    st.sidebar.title("ユーザー設定")
    with st.sidebar.form("profile_form"):
        gender = st.selectbox(
            "性別",
            ["男性", "女性"],
            index=0 if p_gender == "男性" else 1,
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
                INSERT OR REPLACE INTO user_profile (id, gender, age, height, weight, activity_level, steps, goal_phase)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
                (gender, age, height, weight, act_level, steps, goal_phase),
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

    # 今日のデータ取得
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

    # アプリタイトル
    st.title("FITNESS & NUTRITION TRACKER")

    # メイン画面のタブ切り替え（視認性向上のためst.tabsを採用）
    tab1, tab2, tab3 = st.tabs(
        ["今日の状態（ダッシュボード）", "食事記録", "筋トレ記録"]
    )

    # --------------------------------------------------
    # タブ1: 今日の状態（ダッシュボード）
    # --------------------------------------------------
    with tab1:
        st.header("今日の状態")

        offset_str = (
            f"+{offset}"
            if offset > 0
            else (f"{offset}" if offset < 0 else "±0")
        )
        st.info(
            f"現在の設定: {p_goal} | 推定維持カロリー(TDEE): {tdee} kcal | 調整幅: {offset_str} kcal"
        )

        st.subheader("食事・目標管理")
        target_diff = target_cal - total_ingested_cal

        c1, c2, c3 = st.columns(3)
        c1.metric("目標摂取カロリー", f"{target_cal} kcal")
        c2.metric("現在の摂取カロリー", f"{int(total_ingested_cal)} kcal")
        c3.metric(
            "目標まで",
            f"{int(target_diff)} kcal"
            if target_diff >= 0
            else f"-{int(abs(target_diff))} kcal",
        )

        st.subheader("リアルエネルギー収支")
        net_balance = total_ingested_cal - (tdee + total_workout_burn)

        c4, c5 = st.columns(2)
        c4.metric("筋トレ推定消費", f"約 {int(total_workout_burn)} kcal")
        c5.metric(
            "推定エネルギー収支",
            f"+{int(net_balance)} kcal"
            if net_balance >= 0
            else f"{int(net_balance)} kcal",
        )

        st.divider()

        # カレンダー表示
        now = datetime.date.today()
        st.subheader(f"{now.year}年 {now.month}月")

        recorded_df = pd.read_sql_query(
            "SELECT DISTINCT date FROM workout_logs", conn
        )
        recorded_dates = (
            set(recorded_df["date"].tolist())
            if not recorded_df.empty
            else set()
        )

        cols = st.columns(7)
        days_abbr = ["日", "月", "火", "水", "木", "金", "土"]
        for idx, col in enumerate(cols):
            col.caption(f"**{days_abbr[idx]}**")

        month_cal = calendar.monthcalendar(now.year, now.month)
        for week in month_cal:
            w_cols = st.columns(7)
            for idx, day in enumerate(week):
                if day == 0:
                    w_cols[idx].write("")
                else:
                    date_str = f"{now.year}-{now.month:02d}-{day:02d}"
                    if date_str in recorded_dates:
                        w_cols[idx].markdown(f"**[{day}]**")
                    else:
                        w_cols[idx].write(f"{day}")

    # --------------------------------------------------
    # タブ2: 食事記録
    # --------------------------------------------------
    with tab2:
        st.header("食事カロリー入力")

        food_date = st.date_input("記録日", value=datetime.date.today())
        f_date_str = food_date.strftime("%Y-%m-%d")

        f_row = c.execute(
            "SELECT breakfast_cal, lunch_cal, dinner_cal, snack_cal FROM food_logs WHERE date = ?",
            (f_date_str,),
        ).fetchone()

        init_b = f_row[0] if f_row else 0.0
        init_l = f_row[1] if f_row else 0.0
        init_d = f_row[2] if f_row else 0.0
        init_s = f_row[3] if f_row else 0.0

        col_b, col_l, col_d, col_s = st.columns(4)
        with col_b:
            in_b = st.number_input(
                "朝食 (kcal)", min_value=0.0, value=float(init_b), step=50.0
            )
        with col_l:
            in_l = st.number_input(
                "昼食 (kcal)", min_value=0.0, value=float(init_l), step=50.0
            )
        with col_d:
            in_d = st.number_input(
                "夕食 (kcal)", min_value=0.0, value=float(init_d), step=50.0
            )
        with col_s:
            in_s = st.number_input(
                "間食 (kcal)", min_value=0.0, value=float(init_s), step=50.0
            )

        sub_total = in_b + in_l + in_d + in_s
        st.metric("本日の摂取合計", f"{int(sub_total)} kcal")

        if st.button("食事記録を保存"):
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
    # タブ3: 筋トレ記録
    # --------------------------------------------------
    with tab3:
        st.header("筋トレログ & 消費カロリー推定")

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
                "重量 (kg)", min_value=0.0, step=0.5, value=0.0
            )
        with col2:
            reps_val = st.number_input(
                "回数 (レップ)", min_value=0, step=1, value=0
            )

        col_time, col_int = st.columns(2)
        with col_time:
            duration = st.number_input(
                "実施時間 (分)", min_value=0, max_value=300, value=60, step=5
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
        st.caption(f"このセッションの推定純消費カロリー: 約 {estimated_burn} kcal")

        if st.button("筋トレ記録を保存"):
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
                st.success("筋トレ記録を保存しました。")
                st.rerun()

        st.divider()
        st.subheader("本日の筋トレ記録")
        logs_df = pd.read_sql_query(
            "SELECT id, part as 部位, exercise as 種目, weight as '重量(kg)', reps as 回数, duration_min as '時間(分)', burned_calories as '消費(kcal)' FROM workout_logs WHERE date = ?",
            conn,
            params=(w_date_str,),
        )
        if not logs_df.empty:
            st.dataframe(
                logs_df.drop(columns=["id"]), use_container_width=True
            )
        else:
            st.info("本日の記録はまだありません。")


if __name__ == "__main__":
    main()