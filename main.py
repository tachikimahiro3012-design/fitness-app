import calendar #カレンダー
import datetime #今日の日付
import json #Geminiの回答をPython辞書に変換する
import os #SupabaseのURL/KEYを読む
import pandas as pd #筋トレログを表形式で扱う・集計する
import plotly.express as px #種目分析のグラフ（折れ線・棒グラフ）を描く
import streamlit as st #画面そのもの
from PIL import Image #食事写真を開いてGeminiに渡す
from google import genai #Gemini API
from supabase import create_client, Client #データベース接続


# CSSでStreamlitのカラム自動折り返し＆ロード中の曇り（オーバーレイ）を無効化する
st.markdown(
    """
    <style>
    /* 1. 画面遷移・ロード時の白く曇るオーバーレイとくるくるアニメーションを完全に無効化 */
    div[data-testid="stStatusWidget"],
    div[data-testid="stSpinner"],
    .stSpinner {
        visibility: hidden !important;
        display: none !important;
    }
    
    /* アプリ全体・要素ごとの再描画時の半透明化（グレーアウト）を防止 */
    div[data-testid="stApp"] > div,
    div[data-testid="stAppViewContainer"],
    div[data-testid="stForm"],
    .stApp * {
        opacity: 1 !important;
        transition: none !important;
    }

    .stApp > header {
        background-color: transparent;
    }

    /* 2. 画面幅が狭い端末（スマホ）でもカラムの横並びを維持する */
    [data-testid="stHorizontalBlock"] {
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        overflow-x: auto;
    }
    
    [data-testid="stColumn"] {
        min-width: 42px !important;
    }

    div[data-testid="stNumberInput"] {
        min-width: 60px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

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

# Gemini AIによるテキスト解析関数
def analyze_food_text(text_input: str, api_key: str):
  try:
    client = genai.Client(api_key=api_key)
    prompt = f"""
        以下の食事テキストから、合計カロリー（kcal）と整理された料理名を推定してください。
        テキスト: 「{text_input}」

        回答は必ず以下の純粋なJSONフォーマットのみで出力してください。
        {{
            "dish_name": "簡潔な料理名（例: ゆで卵1個）",
            "total_calories": 推定合計カロリー(数値のみ)
        }}
        """
    response = client.models.generate_content(
        model="gemini-3.6-flash", contents=[prompt]
    )

    text = response.text.strip()
    if text.startswith("```json"):
      text = text[7:]
    if text.startswith("```"):
      text = text[3:]
    if text.endswith("```"):
      text = text[:-3]

    return json.loads(text.strip()), None
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

    # 3. 運動による消費
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

# 筋トレ METs計算
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

# ランニングマシン (ACSM公式) カロリー計算関数
def calculate_treadmill_burn(weight_kg, speed_kmh, incline_percent, duration_min):
    if duration_min <= 0 or speed_kmh <= 0 or weight_kg <= 0:
        return 0.0
    
    speed_m_min = (speed_kmh * 1000.0) / 60.0
    incline_ratio = incline_percent / 100.0
    
    # ACSM公式による VO2 (ml/kg/min) 計算
    if speed_kmh <= 6.0:
        # ウォーキング
        vo2 = (0.1 * speed_m_min) + (1.8 * speed_m_min * incline_ratio) + 3.5
    else:
        # ランニング
        vo2 = (0.2 * speed_m_min) + (0.9 * speed_m_min * incline_ratio) + 3.5
        
    cal_per_min = (vo2 * weight_kg / 1000.0) * 5.0
    total_burn = cal_per_min * duration_min
    return round(total_burn, 1)

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

        .ex-card { border: 1px solid #e5e7eb; border-radius: 12px; overflow: hidden; margin-bottom: 14px; }
        .ex-card-header { background: var(--brand-red); color: #fff; font-weight: 700;
                           padding: 8px 12px; font-size: 0.95rem; }
        .ex-set-row { display: grid; grid-template-columns: 0.5fr 1fr 1fr 1fr; padding: 6px 12px;
                      font-size: 0.85rem; border-top: 1px solid #e5e7eb; color: #111827; }
        .ex-set-row.head { font-weight: 700; color: #6b7280; font-size: 0.72rem; border-top: none; }

        div[data-testid="stHorizontalBlock"] {
            gap: 2px !important;
        }

        div[data-testid="stColumn"] {
            padding: 0px 1px !important;
        }

        div[data-testid="stColumn"] button {
            padding: 4px 0px !important;
            font-size: 0.68rem !important;
            min-height: 40px !important;
            line-height: 1.1 !important;
            white-space: normal !important;
            word-break: break-all !important;
        }
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

  df = df[(df["weight"] > 0) & (df["reps"] > 0)].copy()

  if df.empty:
    st.info("有効なトレーニングデータがありません。")
    return

  df["est_1rm"] = df["weight"] * (1 + 0.025 * df["reps"])
  df["volume"] = df["weight"] * df["reps"]

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

  c1, c2, c3, c4 = st.columns(4)
  c1.metric("Max Weight", f"{daily_summary['max_weight'].max():.1f} kg")
  c2.metric("Max Reps", f"{int(daily_summary['max_reps'].max())} 回")
  c3.metric("Max 1RM", f"{daily_summary['max_1rm'].max():.1f} kg")
  c4.metric("最高1日Volume", f"{int(daily_summary['total_volume'].max()):,} kg")

  st.divider()

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
            supabase.table("user_profile").upsert(profile_data, on_conflict="user_id").execute()
            st.sidebar.success("設定を更新しました。")
            st.rerun()

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
        total_workout_burn = 0.0

    # 【追加】今日の有酸素（トレッドミル）消費カロリーを取得
    try:
        cardio_res = (
            supabase.table("cardio_logs")
            .select("burned_calories")
            .eq("user_id", user_id)
            .eq("date", today_str)
            .execute()
        )
        total_cardio_burn = sum([r.get("burned_calories", 0) for r in cardio_res.data]) if cardio_res.data else 0.0
    except Exception:
        total_cardio_burn = 0.0

    # 総運動消費カロリー
    total_exercise_burn = total_workout_burn + total_cardio_burn

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
        ("workout", "運動記録"),
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
    if "target_date" not in st.session_state:
        st.session_state.target_date = datetime.date.today()
        
    if st.session_state.view == "dashboard":
        red_banner("今日の状態")

        offset_str = f"+{offset}" if offset > 0 else (f"{offset}" if offset < 0 else "±0")
        st.info(f"現在の設定: {p_goal} | 推定維持カロリー(TDEE): {tdee} kcal | 調整幅: {offset_str} kcal")

        red_banner("今日のカロリー状況")
        target_diff = target_cal - total_ingested_cal
        net_balance = total_ingested_cal - (tdee + total_exercise_burn)

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
                    <div class="stat-label">総運動消費 (筋トレ+有酸素)</div>
                    <div class="stat-value orange">約 {int(total_exercise_burn)} kcal</div>
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

        if "cal_year" not in st.session_state:
            st.session_state.cal_year = now.year
        if "cal_month" not in st.session_state:
            st.session_state.cal_month = now.month

        cal_y = st.session_state.cal_year
        cal_m = st.session_state.cal_month

        c_prev, c_title, c_next = st.columns([1, 3, 1])
        with c_prev:
            if st.button("◀", use_container_width=True):
                if cal_m == 1:
                    st.session_state.cal_year -= 1
                    st.session_state.cal_month = 12
                else:
                    st.session_state.cal_month -= 1
                st.rerun()

        with c_title:
            st.markdown(f"<h3 style='text-align: center; margin: 0;'>{cal_y}年 {cal_m}月</h3>", unsafe_allow_html=True)

        with c_next:
            if st.button("▶", use_container_width=True):
                if cal_m == 12:
                    st.session_state.cal_year += 1
                    st.session_state.cal_month = 1
                else:
                    st.session_state.cal_month += 1
                st.rerun()

        month_start = f"{cal_y}-{cal_m:02d}-01"
        next_m = cal_m + 1 if cal_m < 12 else 1
        next_y = cal_y if cal_m < 12 else cal_y + 1
        month_end = f"{next_y}-{next_m:02d}-01"

        w_month_res = supabase.table("workout_logs").select("date").eq("user_id", user_id).gte("date", month_start).lt("date", month_end).execute()
        c_month_res = supabase.table("cardio_logs").select("date").eq("user_id", user_id).gte("date", month_start).lt("date", month_end).execute()
        
        recorded_dates = set([r["date"] for r in w_month_res.data]) if w_month_res.data else set()
        if c_month_res.data:
            recorded_dates.update([r["date"] for r in c_month_res.data])

        f_month_res = supabase.table("food_logs").select("date, breakfast_cal, lunch_cal, dinner_cal, snack_cal").eq("user_id", user_id).gte("date", month_start).lt("date", month_end).execute()
        food_cal_by_date = {}
        if f_month_res.data:
            for r in f_month_res.data:
                tot = (r.get("breakfast_cal") or 0) + (r.get("lunch_cal") or 0) + (r.get("dinner_cal") or 0) + (r.get("snack_cal") or 0)
                food_cal_by_date[r["date"]] = tot

        st.markdown(
            """
            <style>
            .st-key-cal_area {
                --cal-sun: #d93025;
                --cal-sat: #1a62c9;
                --cal-dot: #1a73e8;
                --cal-today: #e63946;
                --cal-line: #e5e7eb;
                gap: 0 !important;
            }
            .st-key-cal_area div[data-testid="stVerticalBlock"],
            .st-key-cal_area div[data-testid="stHorizontalBlock"] {
                gap: 0 !important;
            }
            .st-key-cal_area div[data-testid="stColumn"] {
                padding: 0 !important;
            }

            .cal-dow-row {
                display: grid;
                grid-template-columns: repeat(7, 1fr);
                width: 100%;
                padding-bottom: 1rem;
            }
            .cal-dow {
                text-align: center;
                font-weight: 700;
                font-size: 0.85rem;
                line-height: 1.2;
                color: #374151;
                padding: 8px 0 4px;
            }
            .cal-dow.sun { color: var(--cal-sun); }
            .cal-dow.sat { color: var(--cal-sat); }

            .st-key-cal_area div[data-testid="stColumn"] button {
                position: relative;
                width: 100% !important;
                height: 64px !important;
                min-height: 64px !important;
                padding: 5px 0 0 !important;
                border: none !important;
                border-top: 1px solid var(--cal-line) !important;
                border-radius: 0 !important;
                background: transparent !important;
                box-shadow: none !important;
                display: flex !important;
                align-items: flex-start !important;
                justify-content: center !important;
            }
            .st-key-cal_area div[data-testid="stColumn"] button:hover {
                background: #f3f4f6 !important;
            }
            .st-key-cal_area div[data-testid="stColumn"] button p {
                position: relative;
                z-index: 1;
                margin: 0 !important;
                text-align: center;
                white-space: pre-line !important;
                word-break: normal !important;
                font-size: 10px !important;
                font-weight: 600 !important;
                line-height: 1.35 !important;
                color: #9ca3af !important;
            }
            .st-key-cal_area div[data-testid="stColumn"] button p::first-line {
                font-size: 15px;
                font-weight: 500;
                color: #111827;
            }

            .st-key-cal_area div[data-testid="stColumn"] [class*="_sun_"] button p::first-line { color: var(--cal-sun); }
            .st-key-cal_area div[data-testid="stColumn"] [class*="_sat_"] button p::first-line { color: var(--cal-sat); }

            .st-key-cal_area div[data-testid="stColumn"] [class*="_out_"] button { opacity: 1 !important; cursor: default; }
            .st-key-cal_area div[data-testid="stColumn"] [class*="_out_"] button:hover { background: transparent !important; }
            .st-key-cal_area div[data-testid="stColumn"] [class*="_out_"] button p::first-line { color: #d1d5db; }

            .st-key-cal_area div[data-testid="stColumn"] [class*="_td_"] button::before {
                content: "";
                position: absolute;
                top: 3px;
                left: 50%;
                width: 24px;
                height: 24px;
                margin-left: -12px;
                border-radius: 50%;
                background: var(--cal-today);
            }
            .st-key-cal_area div[data-testid="stColumn"] [class*="_td_"] button p::first-line { color: #ffffff; font-weight: 700; }

            .st-key-cal_area div[data-testid="stColumn"] [class*="_wk_"] button::after {
                content: "";
                position: absolute;
                top: 30px;
                left: 50%;
                width: 5px;
                height: 5px;
                margin-left: -2.5px;
                border-radius: 50%;
                background: var(--cal-dot);
            }

            .st-key-cal_area div[data-testid="stColumn"] [class*="_un_"] button p { color: #16a34a !important; }
            .st-key-cal_area div[data-testid="stColumn"] [class*="_ov_"] button p { color: #dc2626 !important; }

            .cal-legend { display: flex; gap: 14px; margin-top: 10px; font-size: 0.75rem; color: #6b7280; align-items: center; flex-wrap: wrap; }
            .cal-legend-dot { width: 8px; height: 8px; border-radius: 50%; background: #1a73e8; display: inline-block; margin-right: 4px; }
            .cal-legend-today { width: 14px; height: 14px; border-radius: 50%; background: #e63946; color: #fff; display: inline-block; margin-right: 4px; font-size: 0.6rem; line-height: 14px; text-align: center; font-weight: 700; }
            .cal-legend-num.under { color: #16a34a; font-weight: 700; }
            .cal-legend-num.over { color: #dc2626; font-weight: 700; }
            </style>
            """,
            unsafe_allow_html=True,
        )

        days_abbr = ["日", "月", "火", "水", "木", "金", "土"]
        NBSP = "\u00a0"

        with st.container(key="cal_area"):
            dow_html = "".join(
                f"<div class='cal-dow {'sun' if i == 0 else 'sat' if i == 6 else ''}'>{n}</div>"
                for i, n in enumerate(days_abbr)
            )
            st.markdown(
                f"<div class='cal-dow-row'>{dow_html}</div>",
                unsafe_allow_html=True,
            )

            cal = calendar.Calendar(firstweekday=6)
            for week in cal.monthdatescalendar(cal_y, cal_m):
                w_cols = st.columns(7)
                for idx, date_obj in enumerate(week):
                    date_str = date_obj.strftime("%Y-%m-%d")
                    is_out = date_obj.month != cal_m

                    flags = []
                    if idx == 0:
                        flags.append("sun")
                    if idx == 6:
                        flags.append("sat")

                    if is_out:
                        flags.append("out")
                        has_workout = False
                        cal_total = None
                    else:
                        has_workout = date_str in recorded_dates
                        cal_total = food_cal_by_date.get(date_str)
                        if date_obj == now:
                            flags.append("td")
                        if has_workout:
                            flags.append("wk")
                        if cal_total is not None:
                            flags.append("ov" if cal_total > target_cal else "un")

                    kcal_line = f"{int(cal_total)}k" if cal_total is not None else NBSP
                    btn_label = f"{date_obj.day}\n{NBSP}\n{kcal_line}"
                    btn_key = f"cal_btn_{date_str}_{'_'.join(flags)}_"

                    if w_cols[idx].button(btn_label, key=btn_key, use_container_width=True, disabled=is_out):
                        st.session_state.target_date = date_obj
                        if has_workout:
                            st.session_state.view = "workout"
                        else:
                            st.session_state.view = "food"
                        st.rerun()

        st.markdown(
            """
            <div class="cal-legend">
                <span><span class="cal-legend-today"></span>今日</span>
                <span><span class="cal-legend-dot"></span>運動した日</span>
                <span><span class="cal-legend-num under">数字</span> = 摂取カロリー(目標以内)</span>
                <span><span class="cal-legend-num over">数字</span> = 摂取カロリー(目標オーバー)</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # --------------------------------------------------
    # 画面2: 食事記録
    # --------------------------------------------------
    elif st.session_state.view == "food":
        red_banner("食事カロリー記録")

        food_date = st.date_input("記録日", value=st.session_state.target_date)
        st.session_state.target_date = food_date
        f_date_str = food_date.strftime("%Y-%m-%d")

        f_res = (
            supabase.table("food_logs")
            .select("*")
            .eq("user_id", user_id)
            .eq("date", f_date_str)
            .execute()
        )
        f_row = f_res.data[0] if f_res.data else {}

        current_b = float(f_row.get("breakfast_cal", 0.0) or 0.0)
        current_l = float(f_row.get("lunch_cal", 0.0) or 0.0)
        current_d = float(f_row.get("dinner_cal", 0.0) or 0.0)
        current_s = float(f_row.get("snack_cal", 0.0) or 0.0)

        def update_food_log(new_b, new_l, new_d, new_s):
            food_data = {
                "user_id": user_id,
                "date": f_date_str,
                "breakfast_cal": max(0.0, new_b),
                "lunch_cal": max(0.0, new_l),
                "dinner_cal": max(0.0, new_d),
                "snack_cal": max(0.0, new_s),
            }
            supabase.table("food_logs").upsert(
                food_data, on_conflict="user_id,date"
            ).execute()

        def add_food_item(meal_type, dish_name, calories, source):
            supabase.table("food_items").insert({
                "user_id": user_id,
                "date": f_date_str,
                "meal_type": meal_type,
                "dish_name": dish_name,
                "calories": float(calories),
                "source": source,
            }).execute()

            new_b, new_l, new_d, new_s = current_b, current_l, current_d, current_s
            if meal_type == "朝食": new_b += float(calories)
            elif meal_type == "昼食": new_l += float(calories)
            elif meal_type == "夕食": new_d += float(calories)
            else: new_s += float(calories)
            update_food_log(new_b, new_l, new_d, new_s)

        food_tab1, food_tab2, food_tab3 = st.tabs(
            ["AI解析", "手入力・微調整", "定番・ショートカット"]
        )

        with food_tab1:
            if not p_api_key:
                st.warning("左側のサイドバー（ユーザー設定）に Gemini API キーを入力してください。")
            else:
                analysis_mode = st.radio("解析方法", ["写真で解析", "文章で解析"], horizontal=True, key="ai_analysis_mode")

                if analysis_mode == "写真で解析":
                    uploaded_img = st.file_uploader("画像を選択またはカメラで撮影", type=["jpg", "jpeg", "png", "webp"], key="food_img_uploader")
                    if uploaded_img is not None:
                        image = Image.open(uploaded_img)
                        st.image(image, caption="解析対象", use_container_width=True)
                        if st.button("AIでカロリーを推定する", type="primary", use_container_width=True, key="btn_analyze_image"):
                            with st.spinner("AIが料理とカロリーを解析中..."):
                                result, error = analyze_food_image(image, p_api_key)
                                if error: st.error(f"解析エラー: {error}")
                                else: st.session_state["ai_image_result"] = result

                    if "ai_image_result" in st.session_state and st.session_state["ai_image_result"]:
                        result = st.session_state["ai_image_result"]
                        dish_name = result.get("dish_name", "食事")
                        added_cal = float(result.get("total_calories", 0))
                        description = result.get("description", "")

                        st.markdown("---")
                        st.markdown(f"- 料理名: **{dish_name}** / 推定カロリー: **{int(added_cal)} kcal**")
                        if description: st.caption(description)

                        cols = st.columns(4)
                        for i, mt in enumerate(["朝食", "昼食", "夕食", "間食"]):
                            with cols[i]:
                                if st.button(f"{mt}に追加", key=f"add_img_{mt}", use_container_width=True):
                                    add_food_item(mt, dish_name, added_cal, "ai_image")
                                    st.session_state["ai_image_result"] = None
                                    st.success(f"【{mt}】「{dish_name}」を追加しました！")
                                    st.rerun()

                else:
                    text_input = st.text_area("食べたものを文章で入力", placeholder="例: プロテイン1スクープとバナナ1本", height=100, key="food_text_input")
                    if st.button("AIでカロリーを推定する", type="primary", use_container_width=True, key="btn_analyze_text"):
                        if text_input.strip():
                            with st.spinner("AIがカロリーを推定中..."):
                                result, error = analyze_food_text(text_input.strip(), p_api_key)
                                if error: st.error(f"解析エラー: {error}")
                                else: st.session_state["ai_text_result"] = result

                    if "ai_text_result" in st.session_state and st.session_state["ai_text_result"]:
                        result = st.session_state["ai_text_result"]
                        dish_name = result.get("dish_name", "食事")
                        added_cal = float(result.get("total_calories", 0))

                        st.markdown("---")
                        st.markdown(f"- 料理名: **{dish_name}** / 推定カロリー: **{int(added_cal)} kcal**")

                        cols = st.columns(4)
                        for i, mt in enumerate(["朝食", "昼食", "夕食", "間食"]):
                            with cols[i]:
                                if st.button(f"{mt}に追加", key=f"add_text_{mt}", use_container_width=True):
                                    add_food_item(mt, dish_name, added_cal, "ai_text")
                                    st.session_state["ai_text_result"] = None
                                    st.success(f"【{mt}】「{dish_name}」を追加しました！")
                                    st.rerun()

        with food_tab2:
            m_cat_manual = st.selectbox("対象の食事区分", ["朝食", "昼食", "夕食", "間食"], key="manual_meal_cat")
            col_m1, col_m2 = st.columns(2)
            with col_m1: manual_cal = st.number_input("カロリー (kcal)", min_value=0, step=10, value=0)
            with col_m2: input_mode = st.radio("記録方法", ["上書き設定", "現在の記録に加算"], index=1)
            manual_name = st.text_input("料理名（任意）", placeholder="例: コンビニ弁当", key="manual_dish_name")

            if st.button("手入力で反映する", type="primary", use_container_width=True):
                dish = manual_name.strip() if manual_name.strip() else "手入力"
                if input_mode == "上書き設定":
                    supabase.table("food_items").delete().eq("user_id", user_id).eq("date", f_date_str).eq("meal_type", m_cat_manual).execute()
                    target_val = float(manual_cal)
                    new_b = target_val if m_cat_manual == "朝食" else current_b
                    new_l = target_val if m_cat_manual == "昼食" else current_l
                    new_d = target_val if m_cat_manual == "夕食" else current_d
                    new_s = target_val if m_cat_manual == "間食" else current_s
                    update_food_log(new_b, new_l, new_d, new_s)
                    if target_val > 0:
                        supabase.table("food_items").insert({"user_id": user_id, "date": f_date_str, "meal_type": m_cat_manual, "dish_name": dish, "calories": target_val, "source": "manual"}).execute()
                else:
                    if manual_cal > 0:
                        add_food_item(m_cat_manual, dish, float(manual_cal), "manual")
                st.rerun()

        with food_tab3:
            preset_res = supabase.table("user_presets").select("*").eq("user_id", user_id).order("id").execute()
            custom_presets = preset_res.data if preset_res.data else []
            p_cat = st.selectbox("追加先", ["朝食", "昼食", "夕食", "間食"], key="preset_cat")

            if custom_presets:
                cols_p = st.columns(2)
                for idx, item in enumerate(custom_presets):
                    p_id, p_name, p_cal = item["id"], item["name"], float(item["calories"])
                    with cols_p[idx % 2]:
                        col_btn, col_del = st.columns([4, 1])
                        with col_btn:
                            if st.button(f"+ {p_name} ({int(p_cal)}kcal)", key=f"preset_btn_{p_id}", use_container_width=True):
                                add_food_item(p_cat, p_name, p_cal, "manual")
                                st.rerun()
                        with col_del:
                            if st.button("×", key=f"del_preset_{p_id}"):
                                supabase.table("user_presets").delete().eq("id", p_id).execute()
                                st.rerun()

        st.divider()
        st.markdown("#### 本日の記録一覧")
        sub_total = current_b + current_l + current_d + current_s
        st.metric("本日の合計摂取カロリー", f"{int(sub_total)} kcal")

    # --------------------------------------------------
    # 画面3: 運動記録（筋トレ & ランニングマシン有酸素）
    # --------------------------------------------------
    elif st.session_state.view == "workout":
        red_banner("運動ログ & 消費カロリー推定")

        exercise_mode = st.radio("運動種別を選択", ["筋トレ（ウェイト）", "有酸素（ランニングマシン）"], horizontal=True)

        work_date_default = st.session_state.get("target_date", datetime.date.today())
        w_date_str_default = work_date_default.strftime("%Y-%m-%d")

        # ==================================================
        # モード A: 筋トレ記録
        # ==================================================
        if exercise_mode == "筋トレ（ウェイト）":
            red_banner("① セッション全体の設定")

            existing_summary = (
                supabase.table("daily_summaries")
                .select("duration_min, intensity")
                .eq("user_id", user_id)
                .eq("date", w_date_str_default)
                .execute()
            )
            saved_duration = None
            saved_intensity_idx = 0
            intensity_options = [
                "標準 (通常のウェイトトレーニング)",
                "軽度 (ストレッチ/自重/休憩長め)",
                "高強度 (サーキット/高密度/スーパーセット)",
            ]

            if existing_summary.data:
                dur_val = existing_summary.data[0].get("duration_min")
                if dur_val is not None: saved_duration = int(dur_val)
                intent_val = existing_summary.data[0].get("intensity")
                if intent_val in intensity_options: saved_intensity_idx = intensity_options.index(intent_val)

            col_dur, col_int = st.columns(2)
            with col_dur:
                duration_input = st.number_input("筋トレ時間 (分)", min_value=0, step=5, value=saved_duration, placeholder="0", key="session_duration_input")
                duration = duration_input or 0
            with col_int:
                intensity = st.selectbox("運動強度", intensity_options, index=saved_intensity_idx, key="session_intensity_select")

            estimated_burn = calculate_workout_burn(p_weight, duration, intensity)
            st.info(f"この筋トレの推定純消費カロリー: 約 {estimated_burn} kcal")

            if st.button("セッション設定を保存", type="primary", key="save_session_setting"):
                supabase.table("daily_summaries").upsert({
                    "user_id": user_id,
                    "date": w_date_str_default,
                    "duration_min": duration,
                    "intensity": intensity,
                    "workout_burned_calories": estimated_burn,
                }, on_conflict="user_id,date").execute()
                st.toast("筋トレセッション時間を保存しました！")
                st.rerun()

            st.divider()
            red_banner("② 種目の記録")

            col_date, col_part = st.columns(2)
            with col_date:
                work_date = st.date_input("日付", value=st.session_state.target_date, key="w_date")
                st.session_state.target_date = work_date
                w_date_str = work_date.strftime("%Y-%m-%d")

            db_last_part = profile.get("last_part", "胸") if profile else "胸"
            db_last_ex = profile.get("last_exercise", "ベンチプレス") if profile else "ベンチプレス"

            part_list = ["胸", "二頭", "三頭", "背中", "肩", "脚"]
            part_index = part_list.index(db_last_part) if db_last_part in part_list else 0

            def on_part_change():
                new_p = st.session_state["part_select_key"]
                supabase.table("user_profile").upsert({"user_id": user_id, "last_part": new_p}, on_conflict="user_id").execute()

            with col_part:
                part = st.selectbox("部位", part_list, index=part_index, key="part_select_key", on_change=on_part_change)

            ex_res = supabase.table("exercises").select("name").eq("part", part).execute()
            ex_list = [r["name"] for r in ex_res.data] if ex_res.data else []

            add_option_text = "+ 新しい種目を追加..."
            options = ex_list + [add_option_text]
            ex_index = options.index(db_last_ex) if db_last_ex in options else 0

            def on_exercise_change():
                new_ex = st.session_state["exercise_select_key"]
                if new_ex and new_ex != add_option_text:
                    supabase.table("user_profile").upsert(
                        {"user_id": user_id, "last_exercise": new_ex}, 
                        on_conflict="user_id"
                    ).execute()
                    
                    # 選択種目のログ件数確認
                    existing_sets = (
                        supabase.table("workout_logs")
                        .select("id")
                        .eq("user_id", user_id)
                        .eq("date", w_date_str)  # 日付変数を統一
                        .eq("exercise", new_ex)
                        .execute()
                    )
                          
                    current_count = len(existing_sets.data) if existing_sets.data else 0

                    if current_count == 0:
                        new_default_sets = [
                            {
                                "user_id": user_id,
                                "date": w_date_str,
                                "part": part,
                                "exercise": new_ex,
                                "weight": 0.0,
                                "reps": 10,
                                "duration_min": duration,
                                "intensity": intensity,
                                "burned_calories": 0,
                            }
                            for _ in range(3)
                        ]
                        supabase.table("workout_logs").insert(new_default_sets).execute()
            
            col_select, col_del = st.columns([5, 1])
            with col_select:
                exercise = st.selectbox("種目", options, index=ex_index, key="exercise_select_key", on_change=on_exercise_change)
            
            st.divider()
            red_banner("本日の筋トレ記録")

            # 表示クエリの日付変数を w_date_str に統一
            w_res = (
                supabase.table("workout_logs")
                .select("*")
                .eq("user_id", user_id)
                .eq("date", w_date_str)
                .order("id")
                .execute()
            )
            workout_df = pd.DataFrame(w_res.data) if w_res.data else pd.DataFrame()

            if not workout_df.empty:
                for ex_name, group in workout_df.groupby("exercise", sort=False):
                    col_title, col_btn = st.columns([5, 1])
                    with col_title:
                        st.subheader(f"{ex_name}")
                    with col_btn:
                        if st.button("分析", key=f"analytics_{ex_name}", use_container_width=True):
                            show_exercise_analytics(user_id, ex_name, supabase)

                    for set_no, (_, row) in enumerate(group.iterrows(), start=1):
                        log_id = row["id"]
                        c1, c2, c3, c4, c5 = st.columns([0.8, 2.5, 2.5, 2.0, 1.0])
                        c1.write(f"**{set_no}**")

                        # 更新用コールバック関数
                        def update_set_log(l_id=log_id):
                            w_val = st.session_state.get(f"w_{l_id}")
                            r_val = st.session_state.get(f"r_{l_id}")
                            if w_val is not None and r_val is not None:
                                supabase.table("workout_logs").update(
                                    {"weight": w_val, "reps": r_val}
                                ).eq("id", l_id).execute()

                        new_weight = c2.number_input(
                            "重さ",
                            value=float(row["weight"]),
                            step=2.5,
                            key=f"w_{log_id}",
                            label_visibility="collapsed",
                            on_change=update_set_log,
                        )
                        new_reps = c3.number_input(
                            "回数",
                            value=int(row["reps"]),
                            min_value=0,
                            step=1,
                            key=f"r_{log_id}",
                            label_visibility="collapsed",
                            on_change=update_set_log,
                        )

                        if new_reps > 0:
                            c4.write(f"{(new_weight * (1 + 0.025 * new_reps)):.1f} kg")
                        else:
                            c4.write("-")

                        if c5.button("×", key=f"del_{log_id}"):
                            supabase.table("workout_logs").delete().eq("id", log_id).execute()
                            st.rerun()

                    # 「＋ セットを追加」ボタン
                    if st.button(f"＋ セットを追加 ({ex_name})", key=f"add_set_{ex_name}", use_container_width=True):
                        last_row = group.iloc[-1] if not group.empty else None
                        default_weight = float(last_row["weight"]) if last_row is not None else 0.0
                        default_reps = int(last_row["reps"]) if last_row is not None else 10

                        new_set_data = {
                            "user_id": user_id,
                            "date": w_date_str,
                            "part": part,
                            "exercise": ex_name,
                            "weight": default_weight,
                            "reps": default_reps,
                            "duration_min": duration,
                            "intensity": intensity,
                            "burned_calories": 0,
                        }
                        supabase.table("workout_logs").insert(new_set_data).execute()
                        st.rerun()

        # ==================================================
        # モード B: 有酸素記録 (ランニングマシン)
        # ==================================================
        else:
            red_banner("ランニングマシン ログ入力")

            cardio_date = st.date_input("記録日", value=st.session_state.target_date, key="c_date")
            st.session_state.target_date = cardio_date
            c_date_str = cardio_date.strftime("%Y-%m-%d")

            c_col1, c_col2, c_col3 = st.columns(3)
            with c_col1:
                speed_kmh = st.number_input("時速 (km/h)", min_value=0.0, max_value=25.0, value=7.0, step=0.5, format="%.1f")
            with c_col2:
                incline_percent = st.number_input("傾斜 (%)", min_value=0.0, max_value=20.0, value=0.0, step=0.5, format="%.1f")
            with c_col3:
                cardio_duration = st.number_input("時間 (分)", min_value=0, max_value=300, value=20, step=5)

            # リアルタイム消費カロリープレビュー
            calc_burn = calculate_treadmill_burn(p_weight, speed_kmh, incline_percent, cardio_duration)
            st.info(f"推定消費カロリー: **約 {calc_burn} kcal** (ACSM公式推定)")

            if st.button("有酸素記録を保存", type="primary", use_container_width=True):
                if cardio_duration > 0 and speed_kmh > 0:
                    supabase.table("cardio_logs").insert({
                        "user_id": user_id,
                        "date": c_date_str,
                        "speed_kmh": speed_kmh,
                        "incline_percent": incline_percent,
                        "duration_min": cardio_duration,
                        "burned_calories": calc_burn,
                    }).execute()
                    st.success(f"有酸素運動 (時速{speed_kmh}km/h・傾斜{incline_percent}%・{cardio_duration}分 → 約{calc_burn}kcal) を保存しました！")
                    st.rerun()
                else:
                    st.warning("速度と時間を正しく入力してください。")

            st.divider()
            red_banner("本日の有酸素記録一覧")

            c_logs_res = (
                supabase.table("cardio_logs")
                .select("*")
                .eq("user_id", user_id)
                .eq("date", c_date_str)
                .order("created_at")
                .execute()
            )
            c_logs = c_logs_res.data if c_logs_res.data else []

            if c_logs:
                for log in c_logs:
                    cid = log["id"]
                    col_info, col_del = st.columns([5, 1])
                    with col_info:
                        st.markdown(
                            f"**時速 {log['speed_kmh']} km/h** | 傾斜 **{log['incline_percent']} %** | 時間 **{log['duration_min']} 分** → **約 {int(log['burned_calories'])} kcal**"
                        )
                    with col_del:
                        if st.button("×", key=f"del_cardio_{cid}", help="この有酸素記録を削除"):
                            supabase.table("cardio_logs").delete().eq("id", cid).execute()
                            st.toast("有酸素記録を削除しました。")
                            st.rerun()
            else:
                st.info("本日の有酸素記録はまだありません。")

if __name__ == "__main__":
    main()