import calendar
import datetime
import pandas as pd
import streamlit as st
from supabase import create_client, Client

# --------------------------------------------------
# ページ基本設定
# --------------------------------------------------
st.set_page_config(
    page_title="FITNESS & NUTRITION TRACKER",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# --------------------------------------------------
# Supabase クライアント初期化
# --------------------------------------------------
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

# デフォルト種目の初期登録処理
def init_default_exercises():
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
    for part, name in default_exercises:
        try:
            supabase.table("exercises").insert({"part": part, "name": name}).execute()
        except Exception:
            pass  # 登録済みならスキップ

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
    if "user" not in st.session_state:
        st.session_state.user = None

    # --- 1. 未ログイン時：ログイン / 新規登録画面 ---
    if st.session_state.user is None:
        st.title("FITNESS & NUTRITION TRACKER")
        auth_mode = st.radio("機能を選択", ["ログイン", "新規アカウント登録"], horizontal=True)
        
        email = st.text_input("メールアドレス")
        password = st.text_input("パスワード", type="password")

        if auth_mode == "新規アカウント登録":
            if st.button("アカウント作成"):
                try:
                    res = supabase.auth.sign_up({"email": email, "password": password})
                    st.success("アカウントが作成されました！ログインしてください。")
                except Exception as e:
                    st.error(f"登録エラー: {e}")
        else:
            if st.button("ログイン"):
                try:
                    res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                    st.session_state.user = res.user
                    init_default_exercises()
                    st.success("ログインに成功しました！")
                    st.rerun()
                except Exception as e:
                    st.error("ログインエラー: メールアドレスまたはパスワードを確認してください。")
        return

    # --- 2. ログイン後：メインアプリ画面 ---
    user_id = st.session_state.user.id

    # プロフィール取得
    res = supabase.table("user_profile").select("*").eq("user_id", user_id).execute()
    profile = res.data[0] if res.data else None

    if profile:
        p_gender = profile.get("gender", "男性")
        p_age = profile.get("age", 25)
        p_height = profile.get("height", 170.0)
        p_weight = profile.get("weight", 65.0)
        p_act = profile.get("activity_level", "週3〜4回運動")
        p_steps = profile.get("steps", 8000)
        p_goal = profile.get("goal_phase", "標準増量 (+300 kcal)")
    else:
        p_gender, p_age, p_height, p_weight, p_act, p_steps, p_goal = (
            "男性", 25, 170.0, 65.0, "週3〜4回運動", 8000, "標準増量 (+300 kcal)"
        )

    # サイドバー：設定
    st.sidebar.title("ユーザー設定")
    st.sidebar.caption(f"ログイン中: {st.session_state.user.email}")
    if st.sidebar.button("ログアウト"):
        supabase.auth.sign_out()
        st.session_state.user = None
        st.rerun()

    st.sidebar.divider()
    with st.sidebar.form("profile_form"):
        gender = st.selectbox(
            "性別",
            ["男性", "女性"],
            index=0 if p_gender == "男性" else 1,
        )
        age = st.number_input("年齢", min_value=10, max_value=100, value=int(p_age))
        height = st.number_input(
            "身長 (cm)", min_value=100.0, max_value=230.0, value=float(p_height)
        )
        weight = st.number_input(
            "体重 (kg)", min_value=30.0, max_value=200.0, value=float(p_weight)
        )
        act_options = ["デスクワーク中心", "週1〜2回運動", "週3〜4回運動", "週5回以上運動"]
        act_index = act_options.index(p_act) if p_act in act_options else 2
        act_level = st.selectbox("日常活動レベル", act_options, index=act_index)
        
        steps = st.number_input(
            "1日の平均歩数", min_value=0, max_value=50000, value=int(p_steps), step=500
        )
        goal_options = [
            "積極的減量 (-500 kcal)",
            "標準減量 (-400 kcal)",
            "軽い減量 (-250 kcal)",
            "維持・安定期 (±0 kcal)",
            "控えめ増量 (+200 kcal)",
            "標準増量 (+300 kcal)",
        ]
        goal_index = goal_options.index(p_goal) if p_goal in goal_options else 5
        goal_phase = st.selectbox("現在の目的", goal_options, index=goal_index)

        if st.form_submit_button("設定を保存"):
            data = {
                "user_id": user_id,
                "gender": gender,
                "age": age,
                "height": height,
                "weight": weight,
                "activity_level": act_level,
                "steps": steps,
                "goal_phase": goal_phase,
                "updated_at": "now()",
            }
            supabase.table("user_profile").upsert(data).execute()
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
                supabase.table("exercises").insert({"part": new_part, "name": new_ex.strip()}).execute()
                st.sidebar.success(f"「{new_ex}」を追加しました。")
                st.rerun()
            except Exception:
                st.sidebar.warning("既に登録されています。")

    # 計算実行
    bmr, tdee, target_cal, offset = calculate_nutrition_targets(
        p_gender, p_age, p_height, p_weight, p_act, p_steps, p_goal
    )

    # 今日のデータ取得
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    food_res = supabase.table("food_logs").select("*").eq("user_id", user_id).eq("date", today_str).execute()
    food_data = food_res.data[0] if food_res.data else {}
    total_ingested_cal = (
        food_data.get("breakfast_cal", 0) +
        food_data.get("lunch_cal", 0) +
        food_data.get("dinner_cal", 0) +
        food_data.get("snack_cal", 0)
    )

    workout_res = supabase.table("workout_logs").select("burned_calories").eq("user_id", user_id).eq("date", today_str).execute()
    total_workout_burn = sum(item["burned_calories"] for item in workout_res.data if item.get("burned_calories"))

    # アプリタイトル
    st.title("FITNESS & NUTRITION TRACKER")

    # メイン画面のタブ切り替え
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

        recorded_res = supabase.table("workout_logs").select("date").eq("user_id", user_id).execute()
        recorded_dates = {item["date"] for item in recorded_res.data} if recorded_res.data else set()

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

        f_res = supabase.table("food_logs").select("*").eq("user_id", user_id).eq("date", f_date_str).execute()
        f_row = f_res.data[0] if f_res.data else {}

        init_b = f_row.get("breakfast_cal", 0.0)
        init_l = f_row.get("lunch_cal", 0.0)
        init_d = f_row.get("dinner_cal", 0.0)
        init_s = f_row.get("snack_cal", 0.0)

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
            data = {
                "user_id": user_id,
                "date": f_date_str,
                "breakfast_cal": in_b,
                "lunch_cal": in_l,
                "dinner_cal": in_d,
                "snack_cal": in_s,
            }
            supabase.table("food_logs").upsert(data, on_conflict="user_id,date").execute()
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

        ex_res = supabase.table("exercises").select("name").eq("part", part).execute()
        ex_list = [item["name"] for item in ex_res.data] if ex_res.data else ["（種目がありません）"]
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
                data = {
                    "user_id": user_id,
                    "date": w_date_str,
                    "part": part,
                    "exercise": exercise,
                    "weight": weight_val,
                    "reps": reps_val,
                    "duration_min": duration,
                    "intensity": intensity,
                    "burned_calories": estimated_burn,
                }
                supabase.table("workout_logs").insert(data).execute()
                st.success("筋トレ記録を保存しました。")
                st.rerun()

        st.divider()
        st.subheader("本日の筋トレ記録")
        w_logs_res = supabase.table("workout_logs").select("part, exercise, weight, reps, duration_min, burned_calories").eq("user_id", user_id).eq("date", w_date_str).execute()
        if w_logs_res.data:
            df = pd.DataFrame(w_logs_res.data)
            df.columns = ["部位", "種目", "重量(kg)", "回数", "時間(分)", "消費(kcal)"]
            st.dataframe(df, use_container_width=True)
        else:
            st.info("本日の記録はまだありません。")

if __name__ == "__main__":
    main()