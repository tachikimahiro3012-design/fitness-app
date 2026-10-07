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


@st.cache_resource
def get_gemini_client(api_key: str):
    return genai.Client(api_key=api_key)


# --------------------------------------------------
# データ取得(キャッシュ付き)
#   書き込み(保存・削除)のたびに invalidate_cache() で破棄する
# --------------------------------------------------
CACHE_TTL = 600
PART_LIST = ["胸", "二頭", "三頭", "背中", "肩", "脚"]


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_profile(user_id):
    res = supabase.table("user_profile").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_food_row(user_id, date_str):
    res = (
        supabase.table("food_logs")
        .select("*")
        .eq("user_id", user_id)
        .eq("date", date_str)
        .execute()
    )
    return res.data[0] if res.data else None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_summary(user_id, date_str):
    # テーブルが無い等の失敗はキャッシュされないよう、例外はそのまま投げる
    res = (
        supabase.table("daily_summaries")
        .select("duration_min, intensity, workout_burned_calories")
        .eq("user_id", user_id)
        .eq("date", date_str)
        .execute()
    )
    return res.data[0] if res.data else None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_month_logs(user_id, month_start, month_end):
    w_res = (
        supabase.table("workout_logs")
        .select("date")
        .eq("user_id", user_id)
        .gte("date", month_start)
        .lt("date", month_end)
        .execute()
    )
    recorded_dates = sorted({r["date"] for r in (w_res.data or [])})

    f_res = (
        supabase.table("food_logs")
        .select("date, breakfast_cal, lunch_cal, dinner_cal, snack_cal")
        .eq("user_id", user_id)
        .gte("date", month_start)
        .lt("date", month_end)
        .execute()
    )
    food_cal_by_date = {}
    for r in (f_res.data or []):
        food_cal_by_date[r["date"]] = (
            (r.get("breakfast_cal") or 0)
            + (r.get("lunch_cal") or 0)
            + (r.get("dinner_cal") or 0)
            + (r.get("snack_cal") or 0)
        )
    return recorded_dates, food_cal_by_date


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_exercises(part):
    res = supabase.table("exercises").select("name").eq("part", part).execute()
    return [r["name"] for r in res.data] if res.data else []


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_part_last_dates(user_id):
    # 全履歴を取得せず、部位ごとに最新1件だけ取得する
    # (全履歴取得は Supabase の既定上限1000行で打ち切られるため、古い部位が「記録なし」になる問題もあった)
    result = {}
    for p in PART_LIST:
        res = (
            supabase.table("workout_logs")
            .select("date")
            .eq("user_id", user_id)
            .eq("part", p)
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            result[p] = res.data[0]["date"]
    return result


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_last_record(user_id, exercise):
    res = (
        supabase.table("workout_logs")
        .select("weight, reps, date")
        .eq("user_id", user_id)
        .eq("exercise", exercise)
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_food_items(user_id, date_str):
    res = (
        supabase.table("food_items")
        .select("*")
        .eq("user_id", user_id)
        .eq("date", date_str)
        .order("created_at")
        .execute()
    )
    return res.data if res.data else []


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_presets(user_id):
    res = (
        supabase.table("user_presets")
        .select("*")
        .eq("user_id", user_id)
        .order("id")
        .execute()
    )
    return res.data if res.data else []


def invalidate_cache():
    for fn in (
        fetch_profile, fetch_food_row, fetch_summary, fetch_month_logs,
        fetch_exercises, fetch_part_last_dates, fetch_last_record,
        fetch_food_items, fetch_presets,
    ):
        fn.clear()

# CSSでStreamlitのカラム自動折り返し＆ロード中の曇り（オーバーレイ）を無効化する
st.markdown(
    """
    <style>
    /* 1. 画面更新・ロード時の白曇り（Stale / 透過処理）を完全に無効化 */
    [data-stale="true"],
    [data-stale="true"] * {
        opacity: 1 !important;
        filter: none !important;
        transition: none !important;
        pointer-events: auto !important;
    }

    [data-testid="stAppViewContainer"],
    [data-testid="stMainBlockContainer"],
    [data-testid="stApp"] {
        opacity: 1 !important;
        filter: none !important;
        transition: none !important;
    }

    /* 右上の「Running...」ステータスウィジェットおよびオーバーレイ要素を完全に非表示 */
    [data-testid="stStatusWidget"],
    [data-testid="stOverlay"],
    div[aria-live="polite"][role="status"] {
        visibility: hidden !important;
        display: none !important;
    }

    /* ヘッダー背景の透明化 */
    .stApp > header {
        background-color: transparent !important;
    }

    /* 2. スマホ端末でもカラム（Column）の横並びを強制的に維持 */
    [data-testid="stHorizontalBlock"] {
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        overflow-x: auto !important;
    }

    [data-testid="stColumn"] {
        min-width: 42px !important;
    }

    div[data-testid="stNumberInput"] {
        min-width: 60px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

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
        client = get_gemini_client(api_key)
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
    client = get_gemini_client(api_key)
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

        /* --- 【追加】スマホ用カレンダーレイアウト最適化 --- */
        div[data-testid="stHorizontalBlock"] {
            gap: 2px !important; /* 横並びカラムの隙間を小さく */
        }

        div[data-testid="stColumn"] {
            padding: 0px 1px !important;
        }

        /* カレンダー日付ボタンのサイズを調整 */
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
def calculate_cardio_burn(weight_kg, speed_kmh, incline_percent, duration_min):
    if weight_kg is None or speed_kmh is None or incline_percent is None or duration_min is None:
        return 0.0
    
    if duration_min <= 0 or speed_kmh <= 0 or weight_kg <= 0:
        return 0.0

    # 時速(km/h)を分速(m/min)に変換
    speed_m_min = (speed_kmh * 1000) / 60.0
    grade = incline_percent / 100.0

    # ACSM公式によるVO2算出 (mL/kg/min)
    if speed_kmh <= 8.0:
        # ウォーキング公式
        vo2 = (0.1 * speed_m_min) + (1.8 * speed_m_min * grade) + 3.5
    else:
        # ランニング公式
        vo2 = (0.2 * speed_m_min) + (0.9 * speed_m_min * grade) + 3.5

    # 安静時(3.5 mL/kg/min)を除いた運動による純酸素消費量
    net_vo2 = max(0.0, vo2 - 3.5)

    # 1Lの酸素消費 ≒ 約 5 kcal
    # (mL/kg/min * kg / 1000) * 5 kcal * 分
    burned_kcal = (net_vo2 * weight_kg / 1000.0) * 5.0 * duration_min

    return round(burned_kcal, 1)

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
    profile = fetch_profile(user_id)

    if profile:
        p_gender = profile.get("gender", "男性")
        p_age = profile.get("age", 20)
        p_height = profile.get("height", 170.0)
        p_weight = profile.get("weight", 65.0)
        p_act = profile.get("activity_level", "週3〜4回運動")
        p_steps = profile.get("steps", 5000)
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
        invalidate_cache()
        st.session_state.user = None
        st.rerun()

    st.sidebar.divider()

    # --- APIキーの設定（ハイブリッド型：入力があれば優先、無ければsecretsを使用） ---
    user_api_key = st.sidebar.text_input(
        "Gemini API Key（任意）",
        value="",
        type="password",
        help="空欄の場合はシステムの共通キーが使用されます。ご自身のキーを使用したい場合のみ入力してください。",
    )

    active_api_key = user_api_key.strip() if user_api_key.strip() else st.secrets.get("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY"))

    if not active_api_key:
        st.sidebar.warning("APIキーが設定されていません。secrets.tomlに登録するかキーを入力してください。")

    st.sidebar.divider()

    # --- プロフィール設定フォーム ---
    with st.sidebar.form("profile_form"):
        gender = st.selectbox("性別", ["男性", "女性"], index=0 if p_gender == "男性" else 1)
        age = st.number_input("年齢", min_value=10, max_value=100, value=int(p_age))
        
        # 身長入力（0.5cm刻み）
        height = st.number_input("身長 (cm)", min_value=50.0, max_value=250.0, value=float(p_height), step=0.5, format="%.1f")
        weight = st.number_input("体重 (kg)", min_value=20.0, max_value=300.0, value=float(p_weight), step=0.5, format="%.1f")
        
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
            }
            supabase.table("user_profile").upsert(profile_data, on_conflict="user_id").execute()
            invalidate_cache()
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
    food_row = fetch_food_row(user_id, today_str)
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
        summary_row = fetch_summary(user_id, today_str)
        total_workout_burn = (
            (summary_row.get("workout_burned_calories", 0) or 0) if summary_row else 0.0
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

        # 該当月の日付範囲の計算
        month_start = f"{cal_y}-{cal_m:02d}-01"
        next_m = cal_m + 1 if cal_m < 12 else 1
        next_y = cal_y if cal_m < 12 else cal_y + 1
        month_end = f"{next_y}-{next_m:02d}-01"

        # 当月の筋トレログと食事ログを取得(キャッシュ)
        recorded_dates_list, food_cal_by_date = fetch_month_logs(user_id, month_start, month_end)
        recorded_dates = set(recorded_dates_list)

        # --------------------------------------------------
        # カレンダー用CSS（iOS風：日曜始まり／今日は赤丸／筋トレ日は青い点）
        # --------------------------------------------------
        st.markdown(
            """
            <style>
            /* ===== スマホ向けカレンダー（iOS風）===== */
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

            /* 曜日ヘッダー */
            .cal-dow-row {
                display: grid;
                grid-template-columns: repeat(7, 1fr);
                width: 100%;
                /* st.markdown は下に -1rem の余白を持ち、次の行が上に食い込むため、その分を相殺 */
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

            /* 日付セル（ボタンを枠なしのセルとして描画） */
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
                color: #9ca3af !important;               /* 摂取カロリー無しの色 */
            }
            /* 1行目＝日付の数字 */
            .st-key-cal_area div[data-testid="stColumn"] button p::first-line {
                font-size: 15px;
                font-weight: 500;
                color: #111827;
            }

            /* 日曜＝赤 / 土曜＝青 */
            .st-key-cal_area div[data-testid="stColumn"] [class*="_sun_"] button p::first-line { color: var(--cal-sun); }
            .st-key-cal_area div[data-testid="stColumn"] [class*="_sat_"] button p::first-line { color: var(--cal-sat); }

            /* 前月・翌月の日付（薄く・押せない） */
            .st-key-cal_area div[data-testid="stColumn"] [class*="_out_"] button { opacity: 1 !important; cursor: default; }
            .st-key-cal_area div[data-testid="stColumn"] [class*="_out_"] button:hover { background: transparent !important; }
            .st-key-cal_area div[data-testid="stColumn"] [class*="_out_"] button p::first-line { color: #d1d5db; }

            /* 今日＝赤い丸 */
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

            /* 筋トレした日＝数字の下に青い点 */
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

            /* 摂取カロリー（目標以内＝緑 / オーバー＝赤） */
            .st-key-cal_area div[data-testid="stColumn"] [class*="_un_"] button p { color: #16a34a !important; }
            .st-key-cal_area div[data-testid="stColumn"] [class*="_ov_"] button p { color: #dc2626 !important; }

            /* 凡例 */
            .cal-legend { display: flex; gap: 14px; margin-top: 10px; font-size: 0.75rem; color: #6b7280; align-items: center; flex-wrap: wrap; }
            .cal-legend-dot { width: 8px; height: 8px; border-radius: 50%; background: #1a73e8; display: inline-block; margin-right: 4px; }
            .cal-legend-today { width: 14px; height: 14px; border-radius: 50%; background: #e63946; color: #fff; display: inline-block; margin-right: 4px; font-size: 0.6rem; line-height: 14px; text-align: center; font-weight: 700; }
            .cal-legend-num.under { color: #16a34a; font-weight: 700; }
            .cal-legend-num.over { color: #dc2626; font-weight: 700; }
            </style>
            """,
            unsafe_allow_html=True,
        )

        # --------------------------------------------------
        # カレンダー描画（日付ボタンを枠なしのセルとして表示）
        #   キー末尾のフラグ(_sun_ / _sat_ / _out_ / _td_ / _wk_ / _un_ / _ov_)を
        #   CSS側で拾って、色・点・丸を切り替えている
        # --------------------------------------------------
        days_abbr = ["日", "月", "火", "水", "木", "金", "土"]
        NBSP = "\u00a0"

        with st.container(key="cal_area"):
            # 曜日見出し（列ではなく1つのHTML行として描画し、見切れを防ぐ）
            dow_html = "".join(
                f"<div class='cal-dow {'sun' if i == 0 else 'sat' if i == 6 else ''}'>{n}</div>"
                for i, n in enumerate(days_abbr)
            )
            st.markdown(
                f"<div class='cal-dow-row'>{dow_html}</div>",
                unsafe_allow_html=True,
            )

            # 日曜始まり（前月・翌月の日付も含めて週ごとに取得）
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
                        # 前月・翌月の日付は薄く表示するだけ（押せない）
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

                    # 1行目=日付 / 2行目=青い点の置き場（空行） / 3行目=摂取カロリー
                    kcal_line = f"{int(cal_total)}k" if cal_total is not None else NBSP
                    btn_label = f"{date_obj.day}\n{NBSP}\n{kcal_line}"
                    btn_key = f"cal_btn_{date_str}_{'_'.join(flags)}_"

                    if w_cols[idx].button(btn_label, key=btn_key, use_container_width=True, disabled=is_out):
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
                <span><span class="cal-legend-today"></span>今日</span>
                <span><span class="cal-legend-dot"></span>筋トレした日</span>
                <span><span class="cal-legend-num under">数字</span> = 摂取カロリー(目標以内)</span>
                <span><span class="cal-legend-num over">数字</span> = 摂取カロリー(目標オーバー)</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


        # --------------------------------------------------
    # 画面2: 食事記録（AI解析 / 手入力 / 定番履歴 / 個別修正・消去）
    # --------------------------------------------------
    elif st.session_state.view == "food":
        red_banner("食事カロリー記録")

        food_date = st.date_input("記録日", value=st.session_state.target_date)
        st.session_state.target_date = food_date
        f_date_str = food_date.strftime("%Y-%m-%d")

        # 該当日の既存ログを取得
        f_row = fetch_food_row(user_id, f_date_str) or {}

        current_b = float(f_row.get("breakfast_cal", 0.0) or 0.0)
        current_l = float(f_row.get("lunch_cal", 0.0) or 0.0)
        current_d = float(f_row.get("dinner_cal", 0.0) or 0.0)
        current_s = float(f_row.get("snack_cal", 0.0) or 0.0)

        # DB更新用のヘルパー関数
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
            invalidate_cache()

        # 明細を追加して合計も更新するヘルパー
        def add_food_item(meal_type, dish_name, calories, source):
            # 1. 明細を保存
            supabase.table("food_items").insert({
                "user_id": user_id,
                "date": f_date_str,
                "meal_type": meal_type,
                "dish_name": dish_name,
                "calories": float(calories),
                "source": source,
            }).execute()

            # 2. 合計を更新
            new_b, new_l, new_d, new_s = current_b, current_l, current_d, current_s
            if meal_type == "朝食":
                new_b += float(calories)
            elif meal_type == "昼食":
                new_l += float(calories)
            elif meal_type == "夕食":
                new_d += float(calories)
            else:
                new_s += float(calories)
            update_food_log(new_b, new_l, new_d, new_s)

        # --- 1. 入力タブ ---
        food_tab1, food_tab2, food_tab3 = st.tabs(
            ["AI解析", "手入力・微調整", "定番・ショートカット"]
        )

        # =============================================
        # タブ1: AI解析（写真 + 文章）
        # =============================================
        with food_tab1:
            if not active_api_key:
                st.warning("左側のサイドバー（ユーザー設定）に Gemini API キーを入力してください。")
            else:
                analysis_mode = st.radio(
                    "解析方法",
                    ["写真で解析", "文章で解析"],
                    horizontal=True,
                    key="ai_analysis_mode",
                )

                # ---------- 写真で解析 ----------
                if analysis_mode == "写真で解析":
                    uploaded_img = st.file_uploader(
                        "画像を選択またはカメラで撮影",
                        type=["jpg", "jpeg", "png", "webp"],
                        key="food_img_uploader",
                    )

                    if uploaded_img is not None:
                        image = Image.open(uploaded_img)
                        st.image(image, caption="解析対象", use_container_width=True)

                        if st.button("AIでカロリーを推定する", type="primary", use_container_width=True, key="btn_analyze_image"):
                            with st.spinner("AIが料理とカロリーを解析中..."):
                                result, error = analyze_food_image(image, active_api_key)
                                if error:
                                    st.error(f"解析エラー: {error}")
                                else:
                                    st.session_state["ai_image_result"] = result

                    # 推定結果の表示
                    if "ai_image_result" in st.session_state and st.session_state["ai_image_result"]:
                        result = st.session_state["ai_image_result"]
                        dish_name = result.get("dish_name", "食事")
                        added_cal = float(result.get("total_calories", 0))
                        description = result.get("description", "")

                        st.markdown("---")
                        st.markdown("**推定結果**")
                        st.markdown(f"- 料理名: **{dish_name}**")
                        st.markdown(f"- 推定カロリー: **{int(added_cal)} kcal**")
                        if description:
                            st.caption(description)

                        st.markdown("#### 追加先を選択")
                        cols = st.columns(4)
                        meal_types = ["朝食", "昼食", "夕食", "間食"]
                        for i, mt in enumerate(meal_types):
                            with cols[i]:
                                if st.button(f"{mt}に追加", key=f"add_img_{mt}", use_container_width=True):
                                    add_food_item(mt, dish_name, added_cal, "ai_image")
                                    st.session_state["ai_image_result"] = None
                                    st.success(f"【{mt}】「{dish_name}」（{int(added_cal)} kcal）を追加しました！")
                                    st.rerun()

                        if st.button("★ 定番メニューに保存", key="save_img_preset", use_container_width=True):
                            supabase.table("user_presets").insert({
                                "user_id": user_id,
                                "name": dish_name,
                                "calories": added_cal,
                            }).execute()
                            invalidate_cache()
                            st.toast(f"「{dish_name}」を定番メニューに保存しました！")

                # ---------- 文章で解析 ----------
                else:
                    text_input = st.text_area(
                        "食べたものを文章で入力",
                        placeholder="例: プロテイン1スクープとバナナ1本、ゆで卵2個",
                        height=100,
                        key="food_text_input",
                    )

                    if st.button("AIでカロリーを推定する", type="primary", use_container_width=True, key="btn_analyze_text"):
                        if not text_input.strip():
                            st.warning("文章を入力してください。")
                        else:
                            with st.spinner("AIがカロリーを推定中..."):
                                result, error = analyze_food_text(text_input.strip(), active_api_key)
                                if error:
                                    st.error(f"解析エラー: {error}")
                                else:
                                    st.session_state["ai_text_result"] = result

                    # 推定結果の表示
                    if "ai_text_result" in st.session_state and st.session_state["ai_text_result"]:
                        result = st.session_state["ai_text_result"]
                        dish_name = result.get("dish_name", "食事")
                        added_cal = float(result.get("total_calories", 0))

                        st.markdown("---")
                        st.markdown("**推定結果**")
                        st.markdown(f"- 料理名: **{dish_name}**")
                        st.markdown(f"- 推定カロリー: **{int(added_cal)} kcal**")

                        st.markdown("#### 追加先を選択")
                        cols = st.columns(4)
                        meal_types = ["朝食", "昼食", "夕食", "間食"]
                        for i, mt in enumerate(meal_types):
                            with cols[i]:
                                if st.button(f"{mt}に追加", key=f"add_text_{mt}", use_container_width=True):
                                    add_food_item(mt, dish_name, added_cal, "ai_text")
                                    st.session_state["ai_text_result"] = None
                                    st.success(f"【{mt}】「{dish_name}」（{int(added_cal)} kcal）を追加しました！")
                                    st.rerun()

                        if st.button("定番メニューに保存", key="save_text_preset", use_container_width=True):
                            supabase.table("user_presets").insert({
                                "user_id": user_id,
                                "name": dish_name,
                                "calories": added_cal,
                            }).execute()
                            invalidate_cache()
                            st.toast(f"「{dish_name}」を定番メニューに保存しました！")

        # =============================================
        # タブ2: 手入力・微調整
        # =============================================
        with food_tab2:
            st.caption("数値の直接入力ができます。加算時は明細にも残ります。")
            m_cat_manual = st.selectbox("対象の食事区分", ["朝食", "昼食", "夕食", "間食"], key="manual_meal_cat")

            col_m1, col_m2 = st.columns(2)
            with col_m1:
                manual_cal = st.number_input("カロリー (kcal)", min_value=0, step=10, value=0)
            with col_m2:
                input_mode = st.radio("記録方法", ["上書き設定", "現在の記録に加算"], index=1)

            manual_name = st.text_input(
                "料理名（任意・明細に残したい場合）",
                placeholder="例: コンビニ弁当、プロテイン",
                key="manual_dish_name",
            )

            if st.button("手入力で反映する", type="primary", use_container_width=True):
                dish = manual_name.strip() if manual_name.strip() else "手入力"

                if input_mode == "上書き設定":
                    # 該当区分の明細を全削除してから、新しい値で1件作る
                    supabase.table("food_items").delete().eq(
                        "user_id", user_id
                    ).eq("date", f_date_str).eq("meal_type", m_cat_manual).execute()

                    target_val = float(manual_cal)
                    new_b = target_val if m_cat_manual == "朝食" else current_b
                    new_l = target_val if m_cat_manual == "昼食" else current_l
                    new_d = target_val if m_cat_manual == "夕食" else current_d
                    new_s = target_val if m_cat_manual == "間食" else current_s
                    update_food_log(new_b, new_l, new_d, new_s)

                    if target_val > 0:
                        supabase.table("food_items").insert({
                            "user_id": user_id,
                            "date": f_date_str,
                            "meal_type": m_cat_manual,
                            "dish_name": dish,
                            "calories": target_val,
                            "source": "manual",
                        }).execute()

                    st.toast(f"【{m_cat_manual}】を {int(target_val)} kcal に上書きしました！")
                else:
                    # 加算 → 明細にも残す
                    if manual_cal > 0:
                        add_food_item(m_cat_manual, dish, float(manual_cal), "manual")
                        st.toast(f"【{m_cat_manual}】に {int(manual_cal)} kcal を加算しました！")
                    else:
                        st.warning("カロリーを入力してください。")

                st.rerun()

        # =============================================
        # タブ3: 定番・ショートカット
        # =============================================
        with food_tab3:
            st.caption("よく食べるメニューをワンタップで追加したり、自分の定番メニューを登録・管理できます。")

            custom_presets = fetch_presets(user_id)

            p_cat = st.selectbox("追加先", ["朝食", "昼食", "夕食", "間食"], key="preset_cat")

            if custom_presets:
                cols_p = st.columns(2)
                for idx, item in enumerate(custom_presets):
                    p_id = item["id"]
                    p_name = item["name"]
                    p_cal = float(item["calories"])

                    with cols_p[idx % 2]:
                        col_btn, col_del = st.columns([4, 1])
                        with col_btn:
                            if st.button(f"+ {p_name} ({int(p_cal)}kcal)", key=f"preset_btn_{p_id}", use_container_width=True):
                                add_food_item(p_cat, p_name, p_cal, "manual")
                                st.toast(f"【{p_cat}】に {p_name} を追加しました！")
                                st.rerun()
                        with col_del:
                            if st.button("×", key=f"del_preset_{p_id}", help="このプリセットを削除"):
                                supabase.table("user_presets").delete().eq("id", p_id).execute()
                                invalidate_cache()
                                st.toast("定番メニューを削除しました。")
                                st.rerun()
            else:
                st.info("登録された定番メニューがありません。下のフォームから登録してください。")

            st.divider()

            with st.expander("＋ 新しい定番メニューを登録する"):
                with st.form("add_preset_form", clear_on_submit=True):
                    new_p_name = st.text_input("メニュー名", placeholder="例: プロテイン1杯 + バナナ")
                    new_p_cal = st.number_input("カロリー (kcal)", min_value=0, step=10, value=200)
                    submit_preset = st.form_submit_button("定番メニューに保存", type="primary")

                    if submit_preset:
                        if new_p_name.strip():
                            supabase.table("user_presets").insert({
                                "user_id": user_id,
                                "name": new_p_name.strip(),
                                "calories": new_p_cal
                            }).execute()
                            invalidate_cache()
                            st.toast(f"「{new_p_name.strip()}」を定番メニューに登録しました！")
                            st.rerun()
                        else:
                            st.warning("メニュー名を入力してください。")

        st.divider()

        # =============================================
        # 本日の記録一覧（カード表示）
        # =============================================
        st.markdown("#### 本日の記録一覧")

        categories = [
            ("朝食", current_b, "breakfast"),
            ("昼食", current_l, "lunch"),
            ("夕食", current_d, "dinner"),
            ("間食", current_s, "snack"),
        ]

        st.markdown(
            """
            <style>
            .food-stat-grid {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 6px;
                margin-bottom: 8px;
            }
            .food-stat-card {
                background: #f8f9fa;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                padding: 10px 6px;
                text-align: center;
            }
            .food-stat-label {
                font-size: 0.72rem;
                color: #6b7280;
                margin-bottom: 4px;
            }
            .food-stat-value {
                font-size: 1.05rem;
                font-weight: 700;
                color: #111827;
                line-height: 1.2;
                white-space: nowrap;
            }
            @media (max-width: 480px) {
                .food-stat-grid {
                    grid-template-columns: repeat(2, 1fr);
                }
                .food-stat-value {
                    font-size: 1.1rem;
                }
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        cards = []
        for label, val, _ in categories:
            display_val = f"{int(val)} kcal" if val > 0 else "-"
            cards.append(
                f'<div class="food-stat-card">'
                f'<div class="food-stat-label">{label}</div>'
                f'<div class="food-stat-value">{display_val}</div>'
                f'</div>'
            )
        html = f'<div class="food-stat-grid">{"".join(cards)}</div>'
        st.markdown(html, unsafe_allow_html=True)

        # クリアボタン
        grid_cols = st.columns(4)
        for idx, (label, val, key_prefix) in enumerate(categories):
            with grid_cols[idx]:
                if val > 0:
                    if st.button("クリア", key=f"clear_{key_prefix}", use_container_width=True):
                        new_b = 0.0 if key_prefix == "breakfast" else current_b
                        new_l = 0.0 if key_prefix == "lunch" else current_l
                        new_d = 0.0 if key_prefix == "dinner" else current_d
                        new_s = 0.0 if key_prefix == "snack" else current_s
                        update_food_log(new_b, new_l, new_d, new_s)
                        # 該当区分の明細も削除
                        supabase.table("food_items").delete().eq("user_id", user_id).eq("date", f_date_str).eq("meal_type", label).execute()
                        st.toast(f"{label}の記録を消去しました。")
                        st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)
        sub_total = current_b + current_l + current_d + current_s
        st.metric("本日の合計摂取カロリー", f"{int(sub_total)} kcal")

        # =============================================
        # 本日の明細履歴
        # =============================================
        st.markdown("#### 本日の明細履歴")

        items = fetch_food_items(user_id, f_date_str)

        if items:
            for item in items:
                item_id = item["id"]
                col1, col2, col3 = st.columns([5, 2, 1])
                with col1:
                    source_icon = {"ai_image": "📷", "ai_text": "✏️", "manual": "📌"}.get(item["source"], "•")
                    st.markdown(f"{source_icon} **{item['dish_name']}**（{item['meal_type']}）")
                with col2:
                    st.markdown(f"**{int(item['calories'])} kcal**")
                with col3:
                    if st.button("×", key=f"del_item_{item_id}", help="この明細を削除"):
                        # 合計から差し引き
                        cal = float(item["calories"])
                        new_b, new_l, new_d, new_s = current_b, current_l, current_d, current_s
                        mt = item["meal_type"]
                        if mt == "朝食": new_b = max(0.0, new_b - cal)
                        elif mt == "昼食": new_l = max(0.0, new_l - cal)
                        elif mt == "夕食": new_d = max(0.0, new_d - cal)
                        else: new_s = max(0.0, new_s - cal)
                        update_food_log(new_b, new_l, new_d, new_s)
                        # 明細削除
                        supabase.table("food_items").delete().eq("id", item_id).execute()
                        st.toast("明細を削除しました。")
                        st.rerun()
        else:
            st.info("本日の明細はまだありません。")

        with st.expander("本日の記録をすべてリセット"):
            if st.button("全区分の食事記録をクリアする", type="secondary"):
                supabase.table("food_logs").delete().eq("user_id", user_id).eq("date", f_date_str).execute()
                supabase.table("food_items").delete().eq("user_id", user_id).eq("date", f_date_str).execute()
                invalidate_cache()
                st.toast("本日の食事記録をすべてクリアしました。")
                st.rerun()

    # --------------------------------------------------
    # 画面3: 筋トレ記録
    # --------------------------------------------------
    elif st.session_state.view == "workout":
        red_banner("筋トレログ & 消費カロリー推定")
        # 筋トレ / 有酸素 の切り替えセクション
        workout_type = st.radio(
            "運動の種類を選択",
            ["筋トレ", "有酸素運動"],
            horizontal=True,
            key="workout_type_selector"
        )
        # --------------------------------------------------
        # 【パターンA】有酸素運動の記録
        # --------------------------------------------------
        if workout_type == "有酸素運動":
            red_banner("有酸素運動 記録")

            c_date, c_dummy = st.columns(2)
            with c_date:
                cardio_date = st.date_input("日付", value=st.session_state.target_date, key="cardio_date")
                st.session_state.target_date = cardio_date
                c_date_str = cardio_date.strftime("%Y-%m-%d")

            col_s, col_inc, col_dur = st.columns(3)
            with col_s:
                speed = st.number_input("時速 (km/h)", min_value=1.0, max_value=25.0, value=None, step=0.5)
            with col_inc:
                incline = st.number_input("傾斜 (%)", min_value=0.0, max_value=20.0, value=None, step=0.5)
            with col_dur:
                cardio_dur = st.number_input("時間 (分)", min_value=1, max_value=300, value=None, step=5)


            # 入力がすべて揃っている場合のみ計算、未入力時は 0.0
            if speed is not None and incline is not None and cardio_dur is not None:
                cardio_burn = calculate_cardio_burn(p_weight, speed, incline, cardio_dur)
            else:
                cardio_burn = 0.0
            st.info(f"推定純消費カロリー (ACSM公式): **約 {cardio_burn} kcal**")

            if st.button("有酸素運動を記録", type="primary", use_container_width=True):
                # daily_summaries または workout_logs へ消費カロリーを加算保存
                # （既存の daily_summaries テーブルに加算、あるいは workout_logs に種目名="トレッドミル" で登録）
                try:
                    # 既存の消費カロリーを取得して加算
                    summary_res = supabase.table("daily_summaries").select("workout_burned_calories").eq("user_id", user_id).eq("date", c_date_str).execute()
                    current_burn = summary_res.data[0].get("workout_burned_calories", 0.0) if summary_res.data else 0.0

                    new_total_burn = current_burn + cardio_burn

                    supabase.table("daily_summaries").upsert({
                        "user_id": user_id,
                        "date": c_date_str,
                        "workout_burned_calories": new_total_burn
                    }, on_conflict="user_id,date").execute()

                    # ログ明細としても保存する場合
                    supabase.table("workout_logs").insert({
                        "user_id": user_id,
                        "date": c_date_str,
                        "part": "有酸素",
                        "exercise": f"トレッドミル ({speed}km/h, 傾斜{incline}%)",
                        "weight": incline,       # 傾斜を保持
                        "reps": cardio_dur,       # 実施時間を保持
                        "burned_calories": cardio_burn
                    }).execute()

                    invalidate_cache()
                    st.toast(f"トレッドミル ({cardio_dur}分, 約{cardio_burn}kcal) を記録しました！")
                    st.rerun()
                except Exception as e:
                    st.error(f"保存エラー: {e}")

        # --------------------------------------------------
        # 【パターンB】従来の筋トレ記録
        # --------------------------------------------------
        else:

            red_banner("① セッション全体の設定")

            # 今日の日付（デフォルト）を取得
            work_date_default = st.session_state.get("target_date", datetime.date.today())
            w_date_str_default = work_date_default.strftime("%Y-%m-%d")

            # 既存のセッション設定（実施時間・運動強度）を DB から取得
            try:
                existing_row = fetch_summary(user_id, w_date_str_default)
            except Exception:
                existing_row = None
            saved_duration = None
            saved_intensity_idx = 0
            intensity_options = [
                "標準 (通常のウェイトトレーニング)",
                "軽度 (ストレッチ/自重/休憩長め)",
                "高強度 (サーキット/高密度/スーパーセット)",
            ]

            if existing_row:
                dur_val = existing_row.get("duration_min")
                if dur_val is not None:
                    saved_duration = int(dur_val)
                
                intent_val = existing_row.get("intensity")
                if intent_val in intensity_options:
                    saved_intensity_idx = intensity_options.index(intent_val)

            col_dur, col_int = st.columns(2)
            with col_dur:
                duration_input = st.number_input(
                    "全体実施時間 (分)",
                    min_value=0,
                    step=5,
                    value=saved_duration,
                    placeholder="0",
                    key="session_duration_input"
                )
                duration = duration_input or 0
            with col_int:
                intensity = st.selectbox(
                    "運動強度",
                    intensity_options,
                    index=saved_intensity_idx,
                    key="session_intensity_select"
                )

            estimated_burn = calculate_workout_burn(p_weight, duration, intensity)
            st.info(f"このセッションの推定純消費カロリー: 約 {estimated_burn} kcal")

            # 【追加】セッション設定の保存ボタン
            if st.button("セッション設定を保存", type="primary", key="save_session_setting"):
                try:
                    supabase.table("daily_summaries").upsert({
                        "user_id": user_id,
                        "date": w_date_str_default,
                        "duration_min": duration,
                        "intensity": intensity,
                        "workout_burned_calories": estimated_burn,
                    }, on_conflict="user_id,date").execute()
                    invalidate_cache()
                    st.toast("セッション時間と消費カロリーを保存しました！")
                    st.rerun()
                except Exception as e:
                    st.error(f"保存時にエラーが発生しました: {e}")

        st.divider()
        red_banner("② 種目の記録")

        col_date, col_part = st.columns(2)
        with col_date:
            work_date = st.date_input("日付", value=st.session_state.target_date, key="w_date")
            st.session_state.target_date = work_date
            w_date_str = work_date.strftime("%Y-%m-%d")

        # --- DB（user_profile）から前回選択した部位・種目を取得 ---
        db_last_part = profile.get("last_part", "胸") if profile else "胸"
        db_last_ex = profile.get("last_exercise", "ベンチプレス") if profile else "ベンチプレス"

        part_list = ["胸", "二頭", "三頭", "背中", "肩", "脚"]
        part_index = part_list.index(db_last_part) if db_last_part in part_list else 0

        # ① 部位セレクトボックスの変更検知コールバック
        def on_part_change():
            new_p = st.session_state["part_select_key"]
            supabase.table("user_profile").upsert(
                {"user_id": user_id, "last_part": new_p}, on_conflict="user_id"
            ).execute()
            fetch_profile.clear()

        with col_part:
            part = st.selectbox(
                "部位", 
                part_list, 
                index=part_index, 
                key="part_select_key", 
                on_change=on_part_change
            )

        # --------------------------------------------------
        # 【追加】選択部位の種目別「最終実施日・経過日数」一覧
        # --------------------------------------------------
        # 1. 選択された部位の種目一覧を取得
        ex_list = fetch_exercises(part)


        # --------------------------------------------------
        # 部位ごとの「最終実施日・経過日数」一覧
        # --------------------------------------------------
        # 各部位の最新実施日を DB から取得(キャッシュ化)
        all_parts = ["胸", "二頭", "三頭", "背中", "肩", "脚"]
        
        part_last_dates = fetch_part_last_dates(user_id)

        # 部位ごとの経過日数をすっきり表示
        with st.expander("各部位の前回実施からの経過日数", expanded=False):
            today_obj = datetime.date.today()
            
            # 2列（または3列）でコンパクトに並べる
            cols = st.columns(3)
            for idx, p_item in enumerate(all_parts):
                col = cols[idx % 3]
                with col:
                    if p_item in part_last_dates:
                        last_d_obj = datetime.datetime.strptime(part_last_dates[p_item], "%Y-%m-%d").date()
                        days_ago = (today_obj - last_d_obj).days
                        
                        if days_ago == 0:
                            days_str = "本日実施"
                        else:
                            days_str = f"{days_ago}日前"
                            
                        st.markdown(f"**{p_item}**: {days_str}")
                    else:
                        st.markdown(f"**{p_item}**: 記録なし")
            
        # --------------------------------------------------
        # 種目の選択ドロップダウン
        # --------------------------------------------------
        add_option_text = "+ 新しい種目を追加..."
        options = ex_list + [add_option_text]

        ex_index = options.index(db_last_ex) if db_last_ex in options else 0

        def on_exercise_change():
            new_ex = st.session_state["exercise_select_key"]
            if new_ex != add_option_text:
                supabase.table("user_profile").upsert(
                    {"user_id": user_id, "last_exercise": new_ex}, on_conflict="user_id"
                ).execute()
                fetch_profile.clear()

        col_select, col_del = st.columns([5, 1])
        with col_select:
            exercise = st.selectbox(
                "種目", 
                options, 
                index=ex_index, 
                key="exercise_select_key", 
                on_change=on_exercise_change
            )

        with col_del:
            st.write("")
            st.write("")
            if exercise != add_option_text and ex_list:
                if st.button("削除", key=f"btn_delete_start_{exercise}"):
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
                invalidate_cache()
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
                    "user_id", user_id
                ).eq("exercise", exercise).execute()
                # 2. 種目自体の削除（exercises）
                supabase.table("exercises").delete().eq(
                    "name", exercise
                ).eq("part", part).execute()
                invalidate_cache()

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

        # --------------------------------------------------
        # 前回記録(Last Record)を表示
        # --------------------------------------------------
        
        if exercise != add_option_text:
            last_record = fetch_last_record(user_id, exercise)
            if last_record:
                st.info(
                    f"**前回記録**: {last_record['date']} に {last_record['weight']}kg × {last_record['reps']}回"
                )
            else:
                st.info(f"**{exercise}**の記録はまだありません")
        
            col1, col2 = st.columns(2)
            with col1:
                weight_val = st.number_input("重量 (kg)", min_value=0.0, step=2.5, value=None, placeholder="0.0") or 0.0
            with col2:
                reps_val = st.number_input("回数 (レップ)", min_value=0, step=1, value=None, placeholder="0") or 0
            
        if st.button("筋トレ記録を保存", type="primary"):
            if exercise != add_option_text:
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
                    "burned_calories": 0,
                }
                supabase.table("workout_logs").insert(workout_data).execute()

                # 2. その日の全体消費カロリーを daily_summaries に UPSERT
                try:
                    supabase.table("daily_summaries").upsert({
                        "user_id": user_id,
                        "date": w_date_str,
                        "workout_burned_calories": estimated_burn,
                    }, on_conflict="user_id,date").execute()
                except Exception:
                    pass

                # キャッシュを最新化し、画面を即時更新
                invalidate_cache()
                st.session_state["refresh_today_workout"] = True
                st.success(f"【{part}】{exercise} ({weight_val}kg × {reps_val}回) を記録しました！")
                st.rerun()
            else:
                st.warning("種目が選択されていません。先に種目を選ぶか追加してください。")

        st.divider()
        red_banner("本日の筋トレ記録")
        # --- 本日の筋トレ記録データ取得(セット保存または日付変更時のみ再取得) ---
        # フラグが立っていたり、日付が変わっていれば再取得
        if "last_workout_date" not in st.session_state or st.session_state.last_workout_date != w_date_str or st.session_state.get("refresh_today_workout", False):
            w_res = supabase.table("workout_logs").select("*").eq("user_id", user_id).eq("date", w_date_str).order("id").execute()
            st.session_state.workout_data_cache = w_res.data if w_res.data else []
            st.session_state.last_workout_date = w_date_str
            st.session_state.refresh_today_workout = False
        
        workout_df = pd.DataFrame(st.session_state.workout_data_cache) if st.session_state.workout_data_cache else pd.DataFrame()

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
                    c1, c2, c3, c4, c5 = st.columns([0.8, 2.5, 2.5, 2.0, 1.0])

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
                        st.session_state["refresh_today_workout"] = True
                        invalidate_cache()
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
                        invalidate_cache()
                        st.session_state["refresh_today_workout"] = True
                        st.toast("セットを削除しました。")
                        st.rerun()
                    

                st.divider()
        else:
            st.info("本日の記録はまだありません。")

if __name__ == "__main__":
    main()