import calendar
import datetime
import json
import os
import pandas as pd
import plotly.express as px
from PIL import Image
import streamlit as st
from google import genai
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
# Supabase 接続初期化
# --------------------------------------------------
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")
    
    if not url or not key:
        st.error("【設定エラー】.streamlit/secrets.toml または Streamlit Cloud の Secrets に SUPABASE_URL と SUPABASE_KEY を設定してください。")
        st.stop()
    return create_client(url, key)

supabase = init_supabase()

# デフォルト種目の初期登録処理
def init_default_exercises():
    # 既に種目が1件でも登録されていれば、毎回のログインで31回APIを叩かないよう
    # ここで打ち切る(初回のみ実行されるようにする)
    existing = supabase.table("exercises").select("id").limit(1).execute()
    if existing.data:
        return

    default_exercises = [
        ("胸", "ベンチプレス"), ("胸", "インクラインダンベルプレス"), ("胸", "ダンベルプレス"),
        ("胸", "スミスインクラインベンチプレス"), ("胸", "チェストプレス"), ("胸", "ペックフライ"), ("胸", "ケーブルフライ"),
        ("二頭", "インクラインダンベルカール"), ("二頭", "ダンベルハンマーカール"), ("二頭", "ケーブルカール"), ("二頭", "プリチャーハンマーカール"),
        ("三頭", "フレンチプレス"), ("三頭", "ケーブルプレスダウン"),
        ("背中", "チンニング"), ("背中", "ベントオーバーローイング"), ("背中", "ワンハンドローイング"), ("背中", "ラットプルダウン"), ("背中", "シーテッドローイング"),
        ("肩", "ダンベルショルダープレス"), ("肩", "スミスショルダープレス"), ("肩", "サイドレイズ"), ("肩", "インクラインサイドレイズ"), ("肩", "ケーブルフェイスプル"), ("肩", "ケーブルフロントレイズ"),
        ("脚", "スクワット"), ("脚", "45度レッグプレス"), ("脚", "レッグプレス"), ("脚", "ブルガリアンスクワット"), ("脚", "レッグエクステンション"), ("脚", "レッグカール"), ("脚", "インナーサイ"),
    ]
    rows = [{"part": part, "name": name} for part, name in default_exercises]
    try:
        supabase.table("exercises").insert(rows).execute()
    except Exception:
        pass

# Gemini AIによる画像解析関数
def analyze_food_image(image: Image.Image, api_key: str):
    try:
        client = genai.Client(api_key=api_key)
        prompt = """
        添付された食事の画像を解析し、おおよそのカロリー（kcal）と料理の名称・内訳を推定してください。
        回答は必ず以下の純粋なJSONフォーマットのみで出力してください（Markdownのバッククォートや装飾は不要です）。

        {
            "dish_name": "推定される料理名や内容",
            "total_calories": 推定合計カロリー(数値のみ),
            "meal_type": "朝食", "昼食", "夕食", "間食" のいずれか最も可能性が高いもの,
            "description": "内訳や理由の短い補足コメント"
        }
        """
        response = client.models.generate_content(
            model="gemini-3.6-flash", contents=[image, prompt]
        )

        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        return json.loads(text), None
    except Exception as e:
        return None, str(e)

# BMR / TDEE / 目標カロリー計算
def calculate_nutrition_targets(
    gender, age, height, weight, activity_level, steps, workout_burn, goal_phase
):
    if gender == "男性":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    # 1. 基本消費(日常活動レベルの倍率を反映)
    act_multipliers = {
        "デスクワーク中心": 1.2,
        "週1〜2回運動": 1.375,
        "週3〜4回運動": 1.55,
        "週5回以上運動": 1.725,
    }
    base_tdee = bmr * act_multipliers.get(activity_level, 1.2)

    # 2. 歩数による消費
    step_burn = steps * 0.03

    # 3. 筋トレによる消費
    tdee = base_tdee + step_burn + workout_burn

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

# UI用CSS
def inject_theme_css():
    st.markdown(
        """
        <style>
        :root {
            --brand-red: #e63946;
            --brand-red-dark: #c92a3d;
        }

        html, body, [data-testid="stAppViewContainer"], .main, [data-testid="stHeader"] {
            background-color: #ffffff !important;
            color: #111827 !important;
        }

        p, span, label, h1, h2, h3, h4, h5, h6,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stWidgetLabel"],
        [data-testid="stCaptionContainer"],
        .stSelectbox label, .stNumberInput label, .stDateInput label {
            color: #111827 !important;
        }

        div[data-baseweb="input"] > div,
        div[data-baseweb="select"] > div,
        input {
            background-color: #f9fafb !important;
            color: #111827 !important;
            border-color: #d1d5db !important;
        }

        button[aria-label="Increase value"], button[aria-label="Decrease value"] {
            background-color: #e5e7eb !important;
            color: #111827 !important;
        }

        button[data-baseweb="tab"] {
            color: #4b5563 !important;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            color: var(--brand-red) !important;
            border-bottom-color: var(--brand-red) !important;
        }

        .section-banner {
            background: var(--brand-red);
            color: #ffffff !important;
            font-weight: 700;
            font-size: 1.05rem;
            padding: 10px 14px;
            border-radius: 10px;
            margin: 18px 0 10px;
        }
        .section-banner * {
            color: #ffffff !important;
        }

        .app-header {
            background: var(--brand-red);
            color: #ffffff !important;
            border-radius: 12px;
            padding: 14px 16px;
            margin-bottom: 14px;
        }
        .app-header .app-title { font-size: 1.15rem; font-weight: 800; color: #ffffff !important; }
        .app-header .app-date { font-size: 0.8rem; opacity: 0.9; margin-top: 2px; color: #ffffff !important; }

        button[kind="primary"] {
            background-color: var(--brand-red) !important;
            border-color: var(--brand-red) !important;
            color: #ffffff !important;
            font-weight: 700 !important;
        }
        button[kind="primary"] p {
            color: #ffffff !important;
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
        button[kind="secondary"] p {
            color: #1f2937 !important;
        }
        button[kind="secondary"]:hover {
            background-color: #e5e7eb !important;
            color: #111827 !important;
        }

        div[data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
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

# --------------------------------------------------
# 種目別パフォーマンス分析ダイアログ
# --------------------------------------------------
@st.dialog("種目別パフォーマンス分析", width="large")
def show_exercise_analytics(user_id, exercise_name, supabase):
  st.write(f"### {exercise_name}")

  # 該当種目の全履歴を取得
  res = (
      supabase.table("workout_logs")
      .select("*")
      .eq("user_id", user_id)
      .eq("exercise", exercise_name)
      .order("date")
      .execute()
  )
  df = pd.DataFrame(res.data) if res.data else pd.DataFrame()

  if df.empty or "weight" not in df or df["weight"].isnull().all():
    st.info("この種目の過去データがまだありません。")
    return

  # 0kg・0回のデータを除外
  df = df[(df["weight"] > 0) & (df["reps"] > 0)].copy()

  if df.empty:
    st.info("有効なトレーニングデータがありません。")
    return

  # 各セットの推定1RMと総挙上重量を計算
  df["est_1rm"] = df["weight"] * (1 + 0.025 * df["reps"])
  df["volume"] = df["weight"] * df["reps"]

  # 日付ごとのMAX値を抽出
  daily_summary = (
      df.groupby("date")
      .agg(
          max_weight=("weight", "max"),
          max_reps=("reps", "max"),
          max_1rm=("est_1rm", "max"),
          total_volume=("volume", "sum"),
      )
      .reset_index()
  )

  # 数値（メトリクス）表示
  c1, c2, c3, c4 = st.columns(4)
  c1.metric("Max Weight", f"{daily_summary['max_weight'].max():.1f} kg")
  c2.metric("Max Reps", f"{int(daily_summary['max_reps'].max())} 回")
  c3.metric("Max 1RM", f"{daily_summary['max_1rm'].max():.1f} kg")
  c4.metric("最高1日Volume", f"{int(daily_summary['total_volume'].max()):,} kg")

  st.divider()

  # グラフ表示タブ
  tab1, tab2 = st.tabs(["重量・1RM 推移", "総挙上量 (Volume)"])

  with tab1:
    fig = px.line(
        daily_summary,
        x="date",
        y=["max_weight", "max_1rm"],
        labels={"value": "重量 (kg)", "date": "日付", "variable": "指標"},
        title="最高重量・推定1RM の推移",
        markers=True,
    )
    fig.for_each_trace(
        lambda t: t.update(
            name="最高重量 (kg)" if t.name == "max_weight" else "推定1RM (kg)"
        )
    )
    st.plotly_chart(fig, use_container_width=True)

  with tab2:
    fig_vol = px.bar(
        daily_summary,
        x="date",
        y="total_volume",
        labels={"total_volume": "総挙上量 (kg)", "date": "日付"},
        title="日別の総トレーニング負荷 (Volume)",
    )
    st.plotly_chart(fig_vol, use_container_width=True)

def red_banner(text: str):
    st.markdown(f'<div class="section-banner">{text}</div>', unsafe_allow_html=True)

def main():
    inject_theme_css()

    if "user" not in st.session_state:
        st.session_state.user = None

    # 【追加】保存されているログイン情報があれば自動で復元
    if st.session_state.user is None:
        session_res = supabase.auth.get_session()
        if session_res and session_res.user:
            st.session_state.user = session_res.user

    # --- 1. 未ログイン時：ログイン / 新規登録画面 ---
    if st.session_state.user is None:
        st.title("FITNESS & NUTRITION TRACKER")
        auth_mode = st.radio("機能を選択", ["ログイン", "新規アカウント登録"], horizontal=True)
        
        with st.form("auth_form"):
            email = st.text_input("メールアドレス")
            password = st.text_input("パスワード", type="password")
            
            submit_label = "アカウント作成" if auth_mode == "新規アカウント登録" else "ログイン"
            submitted = st.form_submit_button(submit_label, type="primary")

        if submitted:
            if not email or not password:
                st.error("メールアドレスとパスワードを両方入力してください。")
            elif auth_mode == "新規アカウント登録":
                try:
                    res = supabase.auth.sign_up({"email": email.strip(), "password": password})
                    st.success("アカウントが作成されました！「ログイン」に切り替えてログインしてください。")
                except Exception as e:
                    st.error(f"登録エラー: {e}")
            else:
                try:
                    res = supabase.auth.sign_in_with_password({"email": email.strip(), "password": password})
                    st.session_state.user = res.user
                    init_default_exercises()
                    st.success("ログインに成功しました！")
                    st.rerun()
                except Exception as e:
                    st.error("ログインエラー: メールアドレスまたはパスワードを確認してください。")
        return

    # --- 2. ログイン後：メインアプリ画面 ---
    user_id = st.session_state.user.id

    # ユーザープロフィール取得
    res = supabase.table("user_profile").select("*").eq("user_id", user_id).execute()
    profile = res.data[0] if res.data else None

    if profile:
        p_gender = profile.get("gender", "男性")
        p_age = profile.get("age", 20)
        p_height = profile.get("height", 170.0)
        p_weight = profile.get("weight", 65.0)
        p_act = profile.get("activity_level", "週3〜4回運動")
        p_steps = profile.get("steps", 5000)
        p_goal = profile.get("goal_phase", "標準増量 (+300 kcal)")
        p_api_key = profile.get("api_key", "")
    else:
        p_gender, p_age, p_height, p_weight, p_act, p_steps, p_goal, p_api_key = (
            "男性", 25, 170.0, 65.0, "週3〜4回運動", 8000, "標準増量 (+300 kcal)", ""
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
        api_key_input = st.text_input(
            "Gemini API Key",
            value=p_api_key if p_api_key else "",
            type="password",
            help="Google AI Studioで取得したAPIキーを入力してください",
        )
        gender = st.selectbox("性別", ["男性", "女性"], index=0 if p_gender == "男性" else 1)
        age = st.number_input("年齢", min_value=10, max_value=100, value=int(p_age))
        # 身長入力（0.5cm刻み）
        height = st.number_input("身長 (cm)",min_value=50.0,max_value=250.0,value=float(p_height),step=0.5,format="%.1f",)
        weight = st.number_input("体重 (kg)",min_value=20.0,max_value=300.0,value=float(p_weight),step=0.5,format="%.1f",)
        act_options = ["デスクワーク中心", "週1〜2回運動", "週3〜4回運動", "週5回以上運動"]
        act_index = act_options.index(p_act) if p_act in act_options else 2
        act_level = st.selectbox("日常活動レベル", act_options, index=act_index)
        
        steps = st.number_input("1日の平均歩数", min_value=0, max_value=50000, value=int(p_steps), step=500)
        
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
            profile_data = {
                "user_id": user_id,
                "gender": gender,
                "age": age,
                "height": height,
                "weight": weight,
                "activity_level": act_level,
                "steps": steps,
                "goal_phase": goal_phase,
                "api_key": api_key_input.strip(),
            }
            supabase.table("user_profile").upsert(profile_data).execute()
            st.sidebar.success("設定を更新しました。")
            st.rerun()


    # 計算実行
    # 以前は引数の並び順が関数定義とズレていて(活動レベルの文字列が
    # steps の位置に入り、文字列×0.03でクラッシュしていた)、
    # ここではキーワード引数にして取り違えが起きないようにしています。
    # workout_burn は意図的に0固定にしています。目標摂取カロリー(TDEE)には
    # 筋トレ消費を含めず、代わりに「実質エネルギー収支」側でのみ筋トレ消費を
    # 差し引く方針にしたためです(目標値が一日の途中で動かないようにするため)。
    bmr, tdee, target_cal, offset = calculate_nutrition_targets(
        gender=p_gender,
        age=p_age,
        height=p_height,
        weight=p_weight,
        activity_level=p_act,
        steps=p_steps,
        workout_burn=0,
        goal_phase=p_goal,
    )

    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # 今日の食事記録を取得
    food_res = supabase.table("food_logs").select("*").eq("user_id", user_id).eq("date", today_str).execute()
    food_row = food_res.data[0] if food_res.data else None
    total_ingested_cal = (
        (food_row.get("breakfast_cal", 0) or 0) +
        (food_row.get("lunch_cal", 0) or 0) +
        (food_row.get("dinner_cal", 0) or 0) +
        (food_row.get("snack_cal", 0) or 0)
    ) if food_row else 0.0

    # 今日の筋トレ消費カロリーを取得
    # workout_logs.burned_calories は保存時に常に0で入るようになったため、
    # セット保存のたびにUPSERTされる daily_summaries.workout_burned_calories を使う
    try:
        summary_res = (
            supabase.table("daily_summaries")
            .select("workout_burned_calories")
            .eq("user_id", user_id)
            .eq("date", today_str)
            .execute()
        )
        total_workout_burn = (
            (summary_res.data[0].get("workout_burned_calories", 0) or 0)
            if summary_res.data
            else 0.0
        )
    except Exception:
        # daily_summaries テーブルがまだ無い場合などのフォールバック
        total_workout_burn = 0.0

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
    # 選択された日付の保持（初期値は今日）
    if "target_date" not in st.session_state:
        st.session_state.target_date = datetime.date.today()
        
    if st.session_state.view == "dashboard":
        red_banner("今日の状態")

        offset_str = f"+{offset}" if offset > 0 else (f"{offset}" if offset < 0 else "±0")
        st.info(f"現在の設定: {p_goal} | 推定維持カロリー(TDEE): {tdee} kcal | 調整幅: {offset_str} kcal")

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
        target_diff_str = f"{int(target_diff)}" if target_diff >= 0 else f"-{int(abs(target_diff))}"
        net_balance_str = f"+{int(net_balance)}" if net_balance >= 0 else f"{int(net_balance)}"

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

        # 表示する年月をsession_stateで管理（初期値は当月）
        if "cal_year" not in st.session_state:
            st.session_state.cal_year = now.year
        if "cal_month" not in st.session_state:
            st.session_state.cal_month = now.month

        cal_y = st.session_state.cal_year
        cal_m = st.session_state.cal_month

        # 月切り替えヘッダーとボタン
        c_prev, c_title, c_next = st.columns([1, 3, 1])
        with c_prev:
            if st.button("◀ 前月", use_container_width=True):
                if cal_m == 1:
                    st.session_state.cal_year -= 1
                    st.session_state.cal_month = 12
                else:
                    st.session_state.cal_month -= 1
                st.rerun()

        with c_title:
            st.markdown(f"<h3 style='text-align: center; margin: 0;'>{cal_y}年 {cal_m}月</h3>", unsafe_allow_html=True)

        with c_next:
            if st.button("次月 ▶", use_container_width=True):
                if cal_m == 12:
                    st.session_state.cal_year += 1
                    st.session_state.cal_month = 1
                else:
                    st.session_state.cal_month += 1
                st.rerun()

        # 該当月の日付範囲の計算
        month_start = f"{cal_y}-{cal_m:02d}-01"
        next_m = cal_m + 1 if cal_m < 12 else 1
        next_y = cal_y if cal_m < 12 else cal_y + 1
        month_end = f"{next_y}-{next_m:02d}-01"

        # 当月の筋トレログと食事ログを取得
        w_month_res = supabase.table("workout_logs").select("date").eq("user_id", user_id).gte("date", month_start).lt("date", month_end).execute()
        recorded_dates = set([r["date"] for r in w_month_res.data]) if w_month_res.data else set()

        f_month_res = supabase.table("food_logs").select("date, breakfast_cal, lunch_cal, dinner_cal, snack_cal").eq("user_id", user_id).gte("date", month_start).lt("date", month_end).execute()
        food_cal_by_date = {}
        if f_month_res.data:
            for r in f_month_res.data:
                tot = (r.get("breakfast_cal") or 0) + (r.get("lunch_cal") or 0) + (r.get("dinner_cal") or 0) + (r.get("snack_cal") or 0)
                food_cal_by_date[r["date"]] = tot

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

        # --------------------------------------------------
        # カレンダー描画（ボタン形式へ変更）
        # --------------------------------------------------
        days_abbr = ["日", "月", "火", "水", "木", "金", "土"]
        h_cols = st.columns(7)
        for idx, d_name in enumerate(days_abbr):
            h_cols[idx].markdown(f"**<div style='text-align:center;'>{d_name}</div>**", unsafe_allow_html=True)

        month_cal = calendar.monthcalendar(cal_y, cal_m)
        for week in month_cal:
            w_cols = st.columns(7)
            for idx, day in enumerate(week):
                if day == 0:
                    w_cols[idx].write("")
                    continue

                date_obj = datetime.date(cal_y, cal_m, day)
                date_str = date_obj.strftime("%Y-%m-%d")

                has_workout = date_str in recorded_dates
                cal_total = food_cal_by_date.get(date_str)

                # ボタンに表示するテキスト
                label_parts = [f"{day}"]
                if has_workout:
                    label_parts.append("●")
                if cal_total is not None:
                    label_parts.append(f"{int(cal_total)}k")

                btn_label = " ".join(label_parts)

                # 日付ボタンを配置
                if w_cols[idx].button(btn_label, key=f"cal_btn_{date_str}", use_container_width=True):
                    st.session_state.target_date = date_obj
                    # 筋トレ記録があれば「筋トレ記録」画面へ、なければ「食事記録」画面へ直接ジャンプ
                    if has_workout:
                        st.session_state.view = "workout"
                    else:
                        st.session_state.view = "food"
                    st.rerun()
  
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
    # 画面2: 食事記録（AIカメラ機能付き）
    # --------------------------------------------------
    elif st.session_state.view == "food":
        red_banner("食事カロリー入力")

        food_date = st.date_input("記録日", value=st.session_state.target_date)
        st.session_state.target_date = food_date
        f_date_str = food_date.strftime("%Y-%m-%d")

        f_res = supabase.table("food_logs").select("*").eq("user_id", user_id).eq("date", f_date_str).execute()
        f_row = f_res.data[0] if f_res.data else None

        if "last_food_date" not in st.session_state or st.session_state.last_food_date != f_date_str:
            st.session_state.last_food_date = f_date_str
            st.session_state.ai_b = f_row.get("breakfast_cal", 0.0) if f_row else 0.0
            st.session_state.ai_l = f_row.get("lunch_cal", 0.0) if f_row else 0.0
            st.session_state.ai_d = f_row.get("dinner_cal", 0.0) if f_row else 0.0
            st.session_state.ai_s = f_row.get("snack_cal", 0.0) if f_row else 0.0

        tab1, tab2 = st.tabs(["手入力", "AIカメラ/写真解析"])

        with tab2:
            st.markdown("#### 写真からカロリーを推定")
            if not p_api_key:
                st.warning("左側のサイドバーメニュー（ユーザー設定）に Gemini API キーを入力してください。")
            else:
                img_source = st.radio("入力方法", ["カメラ撮影", "画像ファイル選択"], horizontal=True)
                uploaded_img = None

                if img_source == "カメラ撮影":
                    uploaded_img = st.camera_input("料理を撮影してください")
                else:
                    uploaded_img = st.file_uploader("画像を選択してください", type=["jpg", "jpeg", "png", "webp"])

                if uploaded_img is not None:
                    image = Image.open(uploaded_img)
                    st.image(image, caption="解析対象の画像", use_container_width=True)

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
                    st.write(f"**推定カロリー:** 約 {res.get('total_calories')} kcal")
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
                in_b = st.number_input("朝食 (kcal)", min_value=0.0, value=float(st.session_state.ai_b), step=50.0, key=f"input_b_{f_date_str}")
            with col_l:
                in_l = st.number_input("昼食 (kcal)", min_value=0.0, value=float(st.session_state.ai_l), step=50.0, key=f"input_l_{f_date_str}")
            with col_d:
                in_d = st.number_input("夕食 (kcal)", min_value=0.0, value=float(st.session_state.ai_d), step=50.0, key=f"input_d_{f_date_str}")
            with col_s:
                in_s = st.number_input("間食 (kcal)", min_value=0.0, value=float(st.session_state.ai_s), step=50.0, key=f"input_s_{f_date_str}")

            st.session_state.ai_b = in_b
            st.session_state.ai_l = in_l
            st.session_state.ai_d = in_d
            st.session_state.ai_s = in_s

            sub_total = in_b + in_l + in_d + in_s
            st.metric("本日の摂取合計", f"{int(sub_total)} kcal")

            if st.button("食事記録を保存", type="primary"):
                food_data = {
                    "user_id": user_id,
                    "date": f_date_str,
                    "breakfast_cal": in_b,
                    "lunch_cal": in_l,
                    "dinner_cal": in_d,
                    "snack_cal": in_s,
                }
                supabase.table("food_logs").upsert(food_data, on_conflict="user_id,date").execute()
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
            duration_input = st.number_input("全体実施時間 (分)", min_value=0, step=5, value=None, placeholder="0")
            duration = duration_input or 0
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
            work_date = st.date_input("日付", value=st.session_state.target_date, key="w_date")
            st.session_state.target_date = work_date
            w_date_str = work_date.strftime("%Y-%m-%d")
        with col_part:
            part = st.selectbox("部位", ["胸", "二頭", "三頭", "背中", "肩", "脚"])


        ex_res = (
            supabase.table("exercises")
            .select("name")
            .eq("part", part)
            .execute()
        )
        ex_list = (
            [r["name"] for r in ex_res.data] if ex_res.data else []
        )  #[cite: 9]

        # ドロップダウンの選択肢を作成
        add_option_text = "+ 新しい種目を追加..."
        options = ex_list + [add_option_text]

        # 種目選択と削除ボタンを横並びにするレイアウト
        col_select, col_del = st.columns([5, 1])
        with col_select:
          exercise = st.selectbox("種目", options)

        with col_del:
          st.write("")  # 位置調整用の余白
          st.write("")
          # 「新規種目を追加...」以外が選ばれている場合のみ削除ボタンを表示
          if exercise != add_option_text and ex_list:
            if st.button("削除", key=f"btn_delete_start_{exercise}"):
              # 1段階目の確認フラグを立てる
              st.session_state[f"confirm_del_step1_{exercise}"] = True

        # --------------------------------------------------
        # 新規種目の追加処理
        # --------------------------------------------------
        if exercise == add_option_text:
          new_ex_name = st.text_input(
              f"追加する【{part}】の種目名を入力",
              placeholder="例: ダンベルインクラインフライ",
          )
          if st.button("この種目を登録", type="primary"):
            if new_ex_name.strip():
              try:
                supabase.table("exercises").insert(
                    {"part": part, "name": new_ex_name.strip()}
                ).execute()
                st.success(f"「{new_ex_name.strip()}」を登録しました！")
                st.rerun()
              except Exception:
                st.warning("既に登録されているか、エラーが発生しました。")
            else:
              st.warning("種目名を入力してください。")

        # --------------------------------------------------
        # 種目の削除処理（2段階確認）
        # --------------------------------------------------
        # 1段階目の確認（「本当に削除しますか？」）
        if st.session_state.get(f"confirm_del_step1_{exercise}", False):
          st.warning(f"種目「**{exercise}**」を削除しますか？")
          c1, c2 = st.columns(2)
          with c1:
            if st.button(
                "はい（最終確認へ）",
                key=f"btn_del_step1_yes_{exercise}",
                type="primary",
            ):
              st.session_state[f"confirm_del_step1_{exercise}"] = False
              st.session_state[f"confirm_del_step2_{exercise}"] = True
              st.rerun()
          with c2:
            if st.button("キャンセル", key=f"btn_del_step1_no_{exercise}"):
              st.session_state[f"confirm_del_step1_{exercise}"] = False
              st.rerun()

        # 2段階目の確認（「過去の記録も消えますがよろしいですか？」）
        if st.session_state.get(f"confirm_del_step2_{exercise}", False):
          st.error(
              f"**警告：** 「**{exercise}**」に関連する過去のトレーニング記録や分析データもすべて削除されます。本当に削除してよろしいですか？"
          )
          c1, c2 = st.columns(2)
          with c1:
            if st.button(
                "すべてのデータを完全に削除する",
                key=f"btn_del_step2_yes_{exercise}",
                type="primary",
            ):
              try:
                # 1. 過去ログの削除（workout_logs）
                supabase.table("workout_logs").delete().eq(
                    "exercise", exercise
                ).execute()
                # 2. 種目自体の削除（exercises）
                supabase.table("exercises").delete().eq(
                    "name", exercise
                ).eq("part", part).execute()

                st.session_state[f"confirm_del_step2_{exercise}"] = False
                st.success(
                    f"「{exercise}」と関連するデータを削除しました。"
                )
                st.rerun()
              except Exception as e:
                st.error(f"削除処理中にエラーが発生しました: {e}")
          with c2:
            if st.button("やめる", key=f"btn_del_step2_no_{exercise}"):
              st.session_state[f"confirm_del_step2_{exercise}"] = False
              st.rerun()

        col1, col2 = st.columns(2)
        with col1:
            weight_val = st.number_input("重量 (kg)", min_value=0.0, step=2.5, value=None, placeholder="0.0") or 0.0
        with col2:
            reps_val = st.number_input("回数 (レップ)", min_value=0, step=1, value=None, placeholder="0") or 0
        
        if st.button("筋トレ記録を保存", type="primary"):
            if exercise != "（種目がありません）":
            # 1. 各セットの記録（burned_calories は 0 で保存）
                workout_data = {
               "user_id": user_id,
                "date": w_date_str,
                "part": part,
                "exercise": exercise,
                "weight": weight_val,
                "reps": reps_val,
                "duration_min": duration,
                "intensity": intensity,
                "burned_calories": 0,  # ← 0 に変更して重複加算を防ぐ
            }
            supabase.table("workout_logs").insert(workout_data).execute()

            # 2. その日の全体消費カロリーを daily_summaries（日別管理テーブル）に UPSERT 保存
            try:
                supabase.table("daily_summaries").upsert({
                "user_id": user_id,
                "date": w_date_str,
                "workout_burned_calories": estimated_burn,  # その日の総消費カロリー（上書き保存）
            }, on_conflict="user_id,date").execute()
            except Exception as e:
                # まだ daily_summaries テーブルが無い場合などのフォールバック処理
                pass

            st.success(
                f"【{part}】{exercise} ({weight_val}kg × {reps_val}回) を記録しました！"
             )
            st.rerun()
        st.divider()
        red_banner("本日の筋トレ記録")
        # --- 本日の筋トレ記録データ取得 ---
        w_res = supabase.table("workout_logs").select("*").eq("user_id", user_id).eq("date", w_date_str).order("id").execute()
        workout_df = pd.DataFrame(w_res.data) if w_res.data else pd.DataFrame()

        if not workout_df.empty:
            for ex_name, group in workout_df.groupby("exercise", sort=False):
                # 種目名と分析ボタンを横並びに配置
                col_title, col_btn = st.columns([5, 1])
                with col_title:
                    st.subheader(f"{ex_name}")
                with col_btn:
                    if st.button("分析", key=f"analytics_{ex_name}", use_container_width=True):
                        show_exercise_analytics(user_id, ex_name, supabase)

                # テーブルのヘッダー風表示
                h_col1, h_col2, h_col3, h_col4, h_col5 = st.columns([1, 2, 2, 2, 2])
                h_col1.caption("**セット**")
                h_col2.caption("**重さ (kg)**")
                h_col3.caption("**回数**")
                h_col4.caption("**推定RM**")
                h_col5.caption("**操作**")

                for set_no, (_, row) in enumerate(group.iterrows(), start=1):
                    log_id = row["id"]
                    c1, c2, c3, c4, c5 = st.columns([1, 2, 2, 2, 2])

                    c1.write(f"**{set_no}**")

                    # 入力フォーム（変更検知用に on_change や差分チェックを利用）
                    new_weight = c2.number_input(
                        "重さ",
                        value=float(row["weight"]),
                        step=2.5,
                        format="%.1f",
                        key=f"w_{log_id}",
                        label_visibility="collapsed"
                    )
                    new_reps = c3.number_input(
                        "回数", value=int(row["reps"]), min_value=0, step=1, key=f"r_{log_id}", label_visibility="collapsed"
                    )

                    # --- 【ここを追加】数値が変更されたら即座にデータベース上書き ---
                    if new_weight != float(row["weight"]) or new_reps != int(row["reps"]):
                        supabase.table("workout_logs").update({
                            "weight": new_weight,
                            "reps": new_reps
                        }).eq("id", log_id).execute()
                        st.toast("記録を更新しました！")
                        st.rerun()

                    # RMのリアルタイム計算表示
                    if new_reps > 0:
                        est_rm = new_weight * (1 + 0.025 * new_reps)
                        c4.write(f"{est_rm:.1f} kg")
                    else:
                        c4.write("-")

                    # 削除ボタン
                    if c5.button("×", key=f"del_{log_id}", help="このセットを削除"):
                        supabase.table("workout_logs").delete().eq("id", log_id).execute()
                        st.toast("セットを削除しました。")
                        st.rerun() 

                st.divider()
        else:
            st.info("本日の記録はまだありません。")

if __name__ == "__main__":
    main()