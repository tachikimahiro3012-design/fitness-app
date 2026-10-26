import calendar
import datetime
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import plotly.express as px
from PIL import Image, ImageOps
import streamlit as st
from google import genai
from supabase import create_client, Client

# サーバーの時刻設定(UTC等)に左右されないよう、「今日」は日本時間で決める
JST = datetime.timezone(datetime.timedelta(hours=9))


def today_jst() -> datetime.date:
    return datetime.datetime.now(JST).date()


# --------------------------------------------------
# 画面と日付をURLに残す
#   接続が切れて新しいセッションになっても、同じ画面・日付に戻れるようにする。
#   古いURL(6時間より前)や不正な値は無視して、ホーム・今日から始める。
# --------------------------------------------------
NAV_VIEWS = ("dashboard", "food", "workout")
NAV_RESTORE_SECONDS = 6 * 60 * 60


def save_nav_state():
    try:
        view = st.session_state.view
        date_iso = st.session_state.target_date.isoformat()
        qp = st.query_params
        # 画面か日付が変わったときだけ書き込む(再実行のたびにURLを書き換えない)
        if qp.get("view") != view or qp.get("date") != date_iso:
            qp["view"] = view
            qp["date"] = date_iso
            qp["t"] = str(int(time.time()))
    except Exception:
        pass


def restore_nav_state():
    view, target = "dashboard", today_jst()
    try:
        qp = st.query_params
        if time.time() - int(qp.get("t", "0")) <= NAV_RESTORE_SECONDS:
            if qp.get("view") in NAV_VIEWS:
                view = qp["view"]
            if qp.get("date"):
                target = datetime.date.fromisoformat(qp["date"])
    except Exception:
        pass
    return view, target


# --------------------------------------------------
# ページ基本設定
# --------------------------------------------------
st.set_page_config(
    page_title="FITNESS & NUTRITION TRACKER",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# --------------------------------------------------
# スマホ操作の補助(親ページ側にJSを1回だけ登録)
#  1) 画面上部の空白(ヘッダー部分)をタップ → 一番上へスクロール
#  2) 種目画面で横スワイプ → 「← 戻る」を押して種目一覧へ戻る
# --------------------------------------------------
import streamlit.components.v1 as components

components.html(
    """
    <script>
    (function () {
        const doc = window.parent.document;
        if (doc.getElementById("mobile-nav-helper-v2")) return;  // 二重登録防止

        // iframeではなく親ページ側にスクリプトを置く(再描画で消えないように)
        const s = doc.createElement("script");
        s.id = "mobile-nav-helper-v2";
        s.textContent = `
            (function () {
                // ---------- 1) ヘッダータップでトップへ ----------
                const style = document.createElement("style");
                style.textContent = '[data-testid="stHeader"] { cursor: pointer; }';
                document.head.appendChild(style);

                document.addEventListener("click", function (e) {
                    const header = document.querySelector('[data-testid="stHeader"]');
                    if (!header || !header.contains(e.target)) return;
                    if (e.target.closest("button, a, [role='button']")) return;  // メニュー等は除外
                    const targets = [
                        document.querySelector('[data-testid="stMain"]'),
                        document.querySelector("section.main"),
                        document.querySelector('[data-testid="stAppViewContainer"]'),
                    ];
                    targets.forEach(function (t) {
                        if (t) t.scrollTo({ top: 0, behavior: "smooth" });
                    });
                }, true);

                // ---------- 2) スワイプで種目一覧へ戻る ----------
                const ALLOW_RIGHT = true;   // 指を 左→右 に動かす
                const ALLOW_LEFT  = true;   // 指を 右→左 に動かす(不要なら false)
                const MIN_DX = 70;          // これ以上横に動いたら有効(px)
                const MAX_DY = 60;          // 縦のズレがこれ以内(px)
                const EDGE_SKIP = 20;       // 画面の端はブラウザ標準の操作と競合するので除外
                const IGNORE = 'input, textarea, select, [role="slider"], [data-baseweb="slider"], ' +
                               '[data-testid="stDataFrame"], [data-testid="stTable"], .js-plotly-plot, [role="dialog"]';
                const PAGE_LEVEL = ["HTML", "BODY"];
                let sx = null, sy = null, lx = 0, ly = 0;

                // 横スクロールできる要素(はみ出している行など)の上から始めたスワイプは無視する
                function inScrollableX(el) {
                    while (el && el !== document.body) {
                        if (el.getAttribute) {
                            const tid = el.getAttribute("data-testid");
                            if (tid === "stMain" || tid === "stAppViewContainer" || tid === "stApp") break;
                        }
                        const ox = getComputedStyle(el).overflowX;
                        if ((ox === "auto" || ox === "scroll") && el.scrollWidth > el.clientWidth + 2) return true;
                        el = el.parentElement;
                    }
                    return false;
                }

                function findBackButton() {
                    const byKey = document.querySelector(".st-key-ex_back button");
                    if (byKey) return byKey;
                    // 古いStreamlitではkeyのclassが付かないため、文字で探す
                    const buttons = document.querySelectorAll("button");
                    for (let i = 0; i < buttons.length; i++) {
                        const txt = (buttons[i].textContent || "").trim();
                        if (txt.length <= 6 && txt.indexOf("戻る") !== -1) return buttons[i];
                    }
                    return null;
                }

                function finish(x, y) {
                    if (sx === null) return;
                    const dx = x - sx;
                    const dy = Math.abs(y - sy);
                    sx = sy = null;
                    const okDir = (dx > 0 && ALLOW_RIGHT) || (dx < 0 && ALLOW_LEFT);
                    if (!okDir || Math.abs(dx) < MIN_DX || dy > MAX_DY || Math.abs(dx) < dy * 2) return;
                    if (document.querySelector('[role="dialog"]')) return;  // ダイアログ表示中は無効
                    const back = findBackButton();  // 種目画面にだけ存在する
                    if (back) back.click();
                }

                document.addEventListener("touchstart", function (e) {
                    sx = sy = null;
                    if (e.touches.length !== 1) return;
                    const t = e.touches[0];
                    if (t.clientX < EDGE_SKIP || t.clientX > window.innerWidth - EDGE_SKIP) return;
                    if (e.target.closest(IGNORE)) return;
                    if (inScrollableX(e.target)) return;
                    sx = lx = t.clientX; sy = ly = t.clientY;
                }, { passive: true });

                document.addEventListener("touchmove", function (e) {
                    if (sx === null || !e.touches.length) return;
                    lx = e.touches[0].clientX; ly = e.touches[0].clientY;
                }, { passive: true });

                document.addEventListener("touchend", function (e) {
                    const t = e.changedTouches[0];
                    finish(t ? t.clientX : lx, t ? t.clientY : ly);
                }, { passive: true });

                // ブラウザがスクロール処理を始めるとtouchendが来ない場合があるので、その時は最後の位置で判定
                document.addEventListener("touchcancel", function () {
                    finish(lx, ly);
                }, { passive: true });
            })();
        `;
        doc.body.appendChild(s);
    })();
    </script>
    """,
    height=0,
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
def fetch_all_exercises():
    # 全部位の種目を1回の問い合わせで取得する(部位ごとに6回問い合わせない)
    res = supabase.table("exercises").select("part, name").order("id").execute()
    out = {}
    for r in (res.data or []):
        out.setdefault(r["part"], []).append(r["name"])
    return out


def _query_last_session(user_id, exercise, before_date):
    # before_date より前で、この種目を最後に実施した日の全セットを返す(空欄のセットは除く)
    # ※ スレッドからも呼べるよう、st の機能は使わない
    res = (
        supabase.table("workout_logs")
        .select("date, weight, reps")
        .eq("user_id", user_id)
        .eq("exercise", exercise)
        .lt("date", before_date)
        .order("date", desc=True)
        .order("id")
        .limit(60)
        .execute()
    )
    target_date = None
    sets = []
    for r in (res.data or []):
        if r.get("weight") is None or r.get("reps") is None:
            continue
        if target_date is None:
            target_date = r["date"]
        if r["date"] != target_date:
            break
        sets.append({"weight": r["weight"], "reps": r["reps"]})
    return {"date": target_date, "sets": sets} if target_date else None


def get_last_session(user_id, exercise, before_date):
    cache = st.session_state.setdefault("last_sess_cache", {})
    key = (exercise, before_date)
    if key not in cache:
        cache[key] = _query_last_session(user_id, exercise, before_date)
    return cache[key]


def _fetch_part_last_date(user_id, part):
    res = (
        supabase.table("workout_logs")
        .select("date")
        .eq("user_id", user_id)
        .eq("part", part)
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    return part, (res.data[0]["date"] if res.data else None)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_part_last_dates(user_id):
    # 全履歴を取得せず、部位ごとに最新1件だけ取得する。
    # 部位ごとの問い合わせは直列だと待ち時間が足し算になるため、同時に実行する
    # (全履歴取得は Supabase の既定上限1000行で打ち切られ、古い部位が「記録なし」になる問題もあった)
    with ThreadPoolExecutor(max_workers=len(PART_LIST)) as ex:
        results = list(ex.map(lambda p: _fetch_part_last_date(user_id, p), PART_LIST))
    return {p: d for p, d in results if d}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_last_record(user_id, exercise):
    res = (
        supabase.table("workout_logs")
        .select("weight, reps, date")
        .eq("user_id", user_id)
        .eq("exercise", exercise)
        .order("date", desc=True)
        .limit(20)
        .execute()
    )
    for r in (res.data or []):
        if r.get("weight") is not None and r.get("reps") is not None:
            return r
    return None


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


def get_cardio_burn_total(user_id, date_str):
    """その日の有酸素運動(part=有酸素)の消費カロリー合計。キャッシュしない"""
    try:
        res = (
            supabase.table("workout_logs")
            .select("burned_calories")
            .eq("user_id", user_id)
            .eq("date", date_str)
            .eq("part", "有酸素")
            .execute()
        )
        return sum(float(r.get("burned_calories") or 0) for r in (res.data or []))
    except Exception:
        return 0.0


def invalidate_workout_cache():
    """筋トレのセットを保存・削除したときに、影響する分だけ破棄する
    (プロフィール・種目一覧・食事などは変わらないので残す)"""
    for fn in (fetch_month_logs, fetch_part_last_dates, fetch_last_record, fetch_summary):
        fn.clear()
    st.session_state.pop("last_sess_cache", None)


def invalidate_cache():
    for fn in (
        fetch_profile, fetch_food_row, fetch_summary, fetch_month_logs,
        fetch_exercises, fetch_all_exercises, fetch_part_last_dates, fetch_last_record,
        fetch_food_items, fetch_presets,
    ):
        fn.clear()
    st.session_state.pop("last_sess_cache", None)

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

    /* 3. 画面全体が横方向にずれる(横スワイプで動く/戻る)のを防ぐ。
          横に長い行は、その行の中だけでスクロールする(上の stHorizontalBlock の設定) */
    html, body,
    [data-testid="stApp"],
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    section.main {
        overflow-x: hidden !important;
        overscroll-behavior-x: none !important;
        max-width: 100vw;
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

# ---------------------------------------------------------
# PFC(たんぱく質・脂質・炭水化物, g)の扱い。値が無い・不正なものは None(=未入力)にする
# ---------------------------------------------------------
def _to_gram(v):
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x or x < 0:
            return None
        return round(x, 1)
    except (TypeError, ValueError):
        return None


def parse_pfc(source):
    return {
        "protein_g": _to_gram(source.get("protein_g")),
        "fat_g": _to_gram(source.get("fat_g")),
        "carbs_g": _to_gram(source.get("carbs_g")),
    }


def pfc_text(source):
    pfc = parse_pfc(source)
    if all(v is None for v in pfc.values()):
        return ""
    def f(v):
        return "-" if v is None else f"{v:g}"
    return f"P {f(pfc['protein_g'])} / F {f(pfc['fat_g'])} / C {f(pfc['carbs_g'])} g"


def sum_pfc(items):
    # 明細からPFCの合計(g)を求める。PFCが1つも入っていない明細は数えず、件数だけ返す
    total = {"protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
    missing = 0
    for it in items or []:
        pfc = parse_pfc(it)
        if all(v is None for v in pfc.values()):
            missing += 1
            continue
        for k, v in pfc.items():
            if v is not None:
                total[k] += v
    return total, missing


def pfc_shares(p, f, c):
    # PFCのカロリー比(%)。P=4kcal/g, F=9kcal/g, C=4kcal/g。合計が0なら None
    kp, kf, kc = p * 4, f * 9, c * 4
    ksum = kp + kf + kc
    if ksum <= 0:
        return None
    sp = round(kp / ksum * 100)
    sf = round(kf / ksum * 100)
    return sp, sf, 100 - sp - sf


def calc_pfc_targets(weight, target_cal, goal, overrides=None):
    # 目標PFC(g)。自動計算の目安:
    #   たんぱく質: 体重1kgあたり 減量系2.0g / それ以外1.8g
    #   脂質: 目標カロリーの25%
    #   炭水化物: 残りのカロリー
    # overrides に手動設定(protein_target_g など)があれば、項目ごとにそちらを優先する
    overrides = overrides or {}
    p_per_kg = 2.0 if "減量" in str(goal) else 1.8
    p = float(weight) * p_per_kg
    f = float(target_cal) * 0.25 / 9
    c = max(0.0, (float(target_cal) - p * 4 - f * 9) / 4)
    auto = {"protein_g": round(p), "fat_g": round(f), "carbs_g": round(c)}
    manual_keys = {
        "protein_g": "protein_target_g",
        "fat_g": "fat_target_g",
        "carbs_g": "carbs_target_g",
    }
    result = {}
    for k, mk in manual_keys.items():
        mv = _to_gram(overrides.get(mk))
        result[k] = mv if mv is not None else auto[k]
    return result


def render_pfc_summary(items, targets=None):
    total, missing = sum_pfc(items)
    p, f, c = total["protein_g"], total["fat_g"], total["carbs_g"]
    shares = pfc_shares(p, f, c)

    def g(v):
        return f"{round(v, 1):g}"

    st.markdown(
        """
        <style>
        .pfc-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin: 6px 0 8px; }
        .pfc-card { background: #f8f9fa; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 6px; text-align: center; }
        .pfc-label { font-size: 0.72rem; color: #6b7280; margin-bottom: 4px; white-space: nowrap; }
        .pfc-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
        .pfc-value { font-size: 1.1rem; font-weight: 700; color: #111827; line-height: 1.2; white-space: nowrap; }
        .pfc-bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #e5e7eb; }
        .pfc-legend { font-size: 0.72rem; color: #6b7280; margin-top: 4px; }
        .pfc-sub { font-size: 0.68rem; color: #6b7280; margin-top: 2px; line-height: 1.3; }
        .pfc-sub.ok { color: #16a34a; font-weight: 700; }
        .pfc-sub.over { color: #dc2626; font-weight: 700; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    colors = ("#ef4444", "#f59e0b", "#3b82f6")

    def sub_html(key, val):
        # 目標に対する残り。たんぱく質は「達成」、脂質・炭水化物は「超過」を目立たせる
        t = (targets or {}).get(key)
        if t is None:
            return ""
        diff = t - val
        if key == "protein_g":
            status = f'<div class="pfc-sub">あと {g(diff)} g</div>' if diff > 0 else '<div class="pfc-sub ok">目標達成</div>'
        else:
            status = f'<div class="pfc-sub">あと {g(diff)} g</div>' if diff >= 0 else f'<div class="pfc-sub over">{g(-diff)} g 超過</div>'
        return f'<div class="pfc-sub">目標 {g(t)} g</div>{status}'

    cards = "".join(
        f'<div class="pfc-card"><div class="pfc-label">'
        f'<span class="pfc-dot" style="background:{col}"></span>{name}</div>'
        f'<div class="pfc-value">{g(val)} g</div>{sub_html(key, val)}</div>'
        for name, val, col, key in (
            ("たんぱく質 P", p, colors[0], "protein_g"),
            ("脂質 F", f, colors[1], "fat_g"),
            ("炭水化物 C", c, colors[2], "carbs_g"),
        )
    )
    html = f'<div class="pfc-grid">{cards}</div>'
    if shares:
        sp, sf, sc = shares
        html += (
            '<div class="pfc-bar">'
            f'<div style="width:{sp}%;background:{colors[0]}"></div>'
            f'<div style="width:{sf}%;background:{colors[1]}"></div>'
            f'<div style="width:{sc}%;background:{colors[2]}"></div>'
            '</div>'
            f'<div class="pfc-legend">PFCのカロリー比　P {sp}% ・ F {sf}% ・ C {sc}%</div>'
        )
    st.markdown(html, unsafe_allow_html=True)
    if not shares:
        st.caption("PFCが入力された記録はまだありません。")
    if missing:
        st.caption(f"PFC未入力の記録が{missing}件あります（合計には含まれません）。")


# Gemini AIによる画像解析関数
GEMINI_MODEL = "gemini-3.6-flash"


def is_temporary_gemini_error(e: Exception) -> bool:
    """混雑・一時的な障害(503/504など)かどうか。再試行すれば通る可能性がある"""
    code = getattr(e, "code", None)
    msg = str(e)
    return code in (503, 504) or any(k in msg for k in ("503", "504", "UNAVAILABLE", "DEADLINE_EXCEEDED"))


def call_gemini_with_retry(client, contents, max_attempts: int = 3):
    """一時的なエラー(混雑など)のときだけ、数秒待って自動でやり直す"""
    for attempt in range(max_attempts):
        try:
            return client.models.generate_content(model=GEMINI_MODEL, contents=contents)
        except Exception as e:
            if is_temporary_gemini_error(e) and attempt < max_attempts - 1:
                time.sleep(2 * (attempt + 1))  # 2秒、4秒と間隔をあける
                continue
            raise


def friendly_gemini_error(e: Exception) -> str:
    """画面に出すエラー文。混雑・上限は短い日本語に、それ以外は元の内容を出す"""
    msg = str(e)
    if is_temporary_gemini_error(e):
        return "AIが混み合っています。少し待ってからもう一度お試しください。(自動で再試行しましたが応答がありませんでした)"
    if getattr(e, "code", None) == 429 or "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return "AIの利用回数の上限に達した可能性があります。しばらく待ってからもう一度お試しください。"
    return msg


def analyze_food_image(image: Image.Image, api_key: str):
    try:
        client = get_gemini_client(api_key)
        prompt = """
        添付された食事の画像を解析し、おおよそのカロリー（kcal）、PFC（たんぱく質・脂質・炭水化物のグラム数）、料理の名称・内訳を推定してください。
        回答は必ず以下の純粋なJSONフォーマットのみで出力してください（Markdownのバッククォートや装飾は不要です）。

        {
            "dish_name": "推定される料理名や内容",
            "total_calories": 推定合計カロリー(数値のみ),
            "protein_g": 推定たんぱく質のグラム数(数値のみ),
            "fat_g": 推定脂質のグラム数(数値のみ),
            "carbs_g": 推定炭水化物のグラム数(数値のみ),
            "meal_type": "朝食", "昼食", "夕食", "間食" のいずれか最も可能性が高いもの,
            "description": "内訳や理由の短い補足コメント"
        }
        """
        response = call_gemini_with_retry(client, [image, prompt])

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
        return None, friendly_gemini_error(e)

# Gemini AIによるテキスト解析関数
def analyze_food_text(text_input: str, api_key: str):
  try:
    client = get_gemini_client(api_key)
    prompt = f"""
        以下の食事テキストから、合計カロリー（kcal）、PFC（たんぱく質・脂質・炭水化物のグラム数）、整理された料理名を推定してください。
        テキスト: 「{text_input}」

        回答は必ず以下の純粋なJSONフォーマットのみで出力してください。
        {{
            "dish_name": "簡潔な料理名（例: ゆで卵1個）",
            "total_calories": 推定合計カロリー(数値のみ),
            "protein_g": 推定たんぱく質のグラム数(数値のみ),
            "fat_g": 推定脂質のグラム数(数値のみ),
            "carbs_g": 推定炭水化物のグラム数(数値のみ)
        }}
        """
    response = call_gemini_with_retry(client, [prompt])

    text = response.text.strip()
    if text.startswith("```json"):
      text = text[7:]
    if text.startswith("```"):
      text = text[3:]
    if text.endswith("```"):
      text = text[:-3]

    return json.loads(text.strip()), None
  except Exception as e:
    return None, friendly_gemini_error(e)
  
# BMR / TDEE / 目標カロリー計算
def calculate_nutrition_targets(
    gender, age, height, weight, steps, workout_burn, goal_phase
):
    if gender == "男性":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    # 1. 基本消費(デスクワーク相当の係数1.2のみ)
    #    運動頻度の係数(1.375〜1.725)は、歩数加算や筋トレ消費と二重カウントになるため使わない
    base_tdee = bmr * 1.2

    # 2. 歩数による消費(日常の活動量はここで反映する)
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
            font-size: 0.95rem;
            padding: 6px 12px;
            border-radius: 8px;
            margin: 12px 0 8px;
            display: flex;
            align-items: center;
            line-height: 1.2;
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
        .ex-card { 
            border: 1px solid #e5e7eb; 
            border-radius: 12px; 
            overflow: hidden; 
            margin-bottom: 14px; 
        }

        .ex-card-header { 
            background: var(--brand-red) !important; 
            color: #ffffff !important; 
            font-weight: 700; 
            padding: 10px 14px; 
            font-size: 1.0rem; 
            display: flex; 
            align-items: center; 
            justify-content: space-between; 
        }

        .ex-set-row { 
            display: grid; 
            grid-template-columns: 0.5fr 1fr 1fr 1fr; 
            padding: 8px 12px; 
            font-size: 0.85rem; 
            border-top: 1px solid #e5e7eb; 
            color: #111827; 
        }

        .ex-set-row.head { 
            font-weight: 700; 
            color: #6b7280; 
            font-size: 0.72rem; 
            border-top: none; 
            background-color: #f9fafb; 
        }


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
  red_banner(exercise_name)

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

  # NULL(空欄)が混ざっても比較できるよう数値に変換(変換できないものはNaN)
  df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
  df["reps"] = pd.to_numeric(df["reps"], errors="coerce")

  # 0kg・0回・空欄のデータを除外
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
    fig.update_xaxes(type="category")
    st.plotly_chart(fig, use_container_width=True)

  with tab2:
    fig_vol = px.bar(
        daily_summary,
        x="date",
        y="total_volume",
        labels={"total_volume": "総挙上量 (kg)", "date": "日付"},
        title="日別の総トレーニング負荷 (Volume)",
    )
    fig_vol.update_xaxes(type="category")
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
        p_steps = profile.get("steps", 5000)
        p_goal = profile.get("goal_phase", "標準増量 (+300 kcal)")
    else:
        p_gender, p_age, p_height, p_weight, p_steps, p_goal = (
            "男性", 25, 170.0, 65.0, 8000, "標準増量 (+300 kcal)"
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

        st.caption("目標PFC(g)・任意。空欄なら体重と目的から自動計算します。")
        _prof = profile or {}
        t_protein = st.number_input("目標たんぱく質 (g)", min_value=0.0, max_value=1000.0, step=5.0, value=_to_gram(_prof.get("protein_target_g")), placeholder="自動", key="pfc_target_p")
        t_fat = st.number_input("目標脂質 (g)", min_value=0.0, max_value=1000.0, step=5.0, value=_to_gram(_prof.get("fat_target_g")), placeholder="自動", key="pfc_target_f")
        t_carbs = st.number_input("目標炭水化物 (g)", min_value=0.0, max_value=2000.0, step=5.0, value=_to_gram(_prof.get("carbs_target_g")), placeholder="自動", key="pfc_target_c")

        if st.form_submit_button("設定を保存"):
            profile_data = {
                "user_id": user_id,
                "gender": gender,
                "age": age,
                "height": height,
                "weight": weight,
                "steps": steps,
                "goal_phase": goal_phase,
                "protein_target_g": t_protein,
                "fat_target_g": t_fat,
                "carbs_target_g": t_carbs,
            }
            supabase.table("user_profile").upsert(profile_data, on_conflict="user_id").execute()
            invalidate_cache()
            st.sidebar.success("設定を更新しました。")
            st.rerun()

    # 計算実行
    # 引数の取り違えが起きないよう、キーワード引数で渡しています。
    # workout_burn は意図的に0固定にしています。目標摂取カロリー(TDEE)には
    # 筋トレ消費を含めず、代わりに「実質エネルギー収支」側でのみ筋トレ消費を
    # 差し引く方針にしたためです(目標値が一日の途中で動かないようにするため)。
    bmr, tdee, target_cal, offset = calculate_nutrition_targets(
        gender=p_gender,
        age=p_age,
        height=p_height,
        weight=p_weight,
        steps=p_steps,
        workout_burn=0,
        goal_phase=p_goal,
    )

    pfc_targets = calc_pfc_targets(p_weight, target_cal, p_goal, profile)
    st.sidebar.caption(
        f"現在のPFC目標: P {pfc_targets['protein_g']:g} / F {pfc_targets['fat_g']:g} / C {pfc_targets['carbs_g']:g} g"
    )

    today_str = today_jst().strftime("%Y-%m-%d")

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

    if "view" not in st.session_state or "target_date" not in st.session_state:
        _restored_view, _restored_date = restore_nav_state()
        st.session_state.setdefault("view", _restored_view)
        st.session_state.setdefault("target_date", _restored_date)

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
                save_nav_state()
                st.rerun()
    st.divider()

    # --------------------------------------------------
    # 画面1: ホーム
    # --------------------------------------------------
    # 選択された日付の保持（初期値は今日）
    if "target_date" not in st.session_state:
        st.session_state.target_date = today_jst()
        
    if st.session_state.view == "dashboard":
        red_banner("今日の状態")

        offset_str = f"+{offset}" if offset > 0 else (f"{offset}" if offset < 0 else "±0")
        st.info(f"現在の設定: {p_goal} | 推定維持カロリー(TDEE): {tdee} kcal | 調整幅: {offset_str} kcal")
        if target_cal < bmr:
            st.warning(
                f"目標摂取カロリー({target_cal} kcal)が基礎代謝({bmr} kcal)を下回っています。"
                "減量幅を小さくすることを検討してください。"
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

        red_banner("今日のPFC")
        try:
            render_pfc_summary(fetch_food_items(user_id, today_str), pfc_targets)
        except Exception:
            st.caption("PFCを取得できませんでした。")

        st.divider()

        now = today_jst()
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
                        save_nav_state()
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
        save_nav_state()
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
        def add_food_item(meal_type, dish_name, calories, source, pfc=None):
            # 1. 明細を保存(PFCは任意。無ければ未入力のまま)
            item_row = {
                "user_id": user_id,
                "date": f_date_str,
                "meal_type": meal_type,
                "dish_name": dish_name,
                "calories": float(calories),
                "source": source,
            }
            if pfc:
                item_row.update(pfc)
            supabase.table("food_items").insert(item_row).execute()

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
                        image = ImageOps.exif_transpose(Image.open(uploaded_img))
                        image.thumbnail((1024, 1024))
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
                        pfc = parse_pfc(result)

                        st.markdown("---")
                        st.markdown("**推定結果**")
                        st.markdown(f"- 料理名: **{dish_name}**")
                        st.markdown(f"- 推定カロリー: **{int(added_cal)} kcal**")
                        if pfc_text(pfc):
                            st.markdown(f"- 推定PFC: **{pfc_text(pfc)}**")
                        if description:
                            st.caption(description)

                        st.markdown("#### 追加先を選択")
                        cols = st.columns(4)
                        meal_types = ["朝食", "昼食", "夕食", "間食"]
                        for i, mt in enumerate(meal_types):
                            with cols[i]:
                                if st.button(f"{mt}に追加", key=f"add_img_{mt}", use_container_width=True):
                                    add_food_item(mt, dish_name, added_cal, "ai_image", pfc)
                                    st.session_state["ai_image_result"] = None
                                    st.success(f"【{mt}】「{dish_name}」（{int(added_cal)} kcal）を追加しました！")
                                    st.rerun()

                        if st.button("★ 定番メニューに保存", key="save_img_preset", use_container_width=True):
                            supabase.table("user_presets").insert({
                                "user_id": user_id,
                                "name": dish_name,
                                "calories": added_cal,
                                **pfc,
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
                        pfc = parse_pfc(result)

                        st.markdown("---")
                        st.markdown("**推定結果**")
                        st.markdown(f"- 料理名: **{dish_name}**")
                        st.markdown(f"- 推定カロリー: **{int(added_cal)} kcal**")
                        if pfc_text(pfc):
                            st.markdown(f"- 推定PFC: **{pfc_text(pfc)}**")

                        st.markdown("#### 追加先を選択")
                        cols = st.columns(4)
                        meal_types = ["朝食", "昼食", "夕食", "間食"]
                        for i, mt in enumerate(meal_types):
                            with cols[i]:
                                if st.button(f"{mt}に追加", key=f"add_text_{mt}", use_container_width=True):
                                    add_food_item(mt, dish_name, added_cal, "ai_text", pfc)
                                    st.session_state["ai_text_result"] = None
                                    st.success(f"【{mt}】「{dish_name}」（{int(added_cal)} kcal）を追加しました！")
                                    st.rerun()

                        if st.button("定番メニューに保存", key="save_text_preset", use_container_width=True):
                            supabase.table("user_presets").insert({
                                "user_id": user_id,
                                "name": dish_name,
                                "calories": added_cal,
                                **pfc,
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
                manual_cal = st.number_input("カロリー (kcal)", min_value=0, step=10, value=None, placeholder="0")
            with col_m2:
                input_mode = st.radio("記録方法", ["上書き設定", "現在の記録に加算"], index=1)

            manual_name = st.text_input(
                "料理名（任意・明細に残したい場合）",
                placeholder="例: コンビニ弁当、プロテイン",
                key="manual_dish_name",
            )

            col_mp, col_mf, col_mc = st.columns(3)
            with col_mp:
                manual_p = st.number_input("たんぱく質 (g)", min_value=0.0, step=1.0, value=None, placeholder="任意", key="manual_p")
            with col_mf:
                manual_f = st.number_input("脂質 (g)", min_value=0.0, step=1.0, value=None, placeholder="任意", key="manual_f")
            with col_mc:
                manual_c = st.number_input("炭水化物 (g)", min_value=0.0, step=1.0, value=None, placeholder="任意", key="manual_c")
            manual_pfc = parse_pfc({"protein_g": manual_p, "fat_g": manual_f, "carbs_g": manual_c})

            manual_clicked = st.button("手入力で反映する", type="primary", use_container_width=True)
            if manual_clicked and manual_cal is None:
                st.warning("カロリーを入力してください。(0 kcal にしたいときは 0 と入力してください)")
            elif manual_clicked:
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
                            **manual_pfc,
                        }).execute()

                    st.toast(f"【{m_cat_manual}】を {int(target_val)} kcal に上書きしました！")
                else:
                    # 加算 → 明細にも残す
                    if manual_cal > 0:
                        add_food_item(m_cat_manual, dish, float(manual_cal), "manual", manual_pfc)
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
                                add_food_item(p_cat, p_name, p_cal, "manual", parse_pfc(item))
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
                    new_p_p = st.number_input("たんぱく質 (g)", min_value=0.0, step=1.0, value=None, placeholder="任意", key="preset_form_p")
                    new_p_f = st.number_input("脂質 (g)", min_value=0.0, step=1.0, value=None, placeholder="任意", key="preset_form_f")
                    new_p_c = st.number_input("炭水化物 (g)", min_value=0.0, step=1.0, value=None, placeholder="任意", key="preset_form_c")
                    submit_preset = st.form_submit_button("定番メニューに保存", type="primary")

                    if submit_preset:
                        if new_p_name.strip():
                            supabase.table("user_presets").insert({
                                "user_id": user_id,
                                "name": new_p_name.strip(),
                                "calories": new_p_cal,
                                **parse_pfc({"protein_g": new_p_p, "fat_g": new_p_f, "carbs_g": new_p_c}),
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
        render_pfc_summary(fetch_food_items(user_id, f_date_str), pfc_targets)

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
                    if pfc_text(item):
                        st.caption(pfc_text(item))
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
                save_nav_state()
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

            # 以降は筋トレ用の入力欄。有酸素モードでは表示しない
            return

        # --------------------------------------------------
        # 【パターンB】筋トレ記録: 種目一覧 → 種目ごとの記録画面
        #   一覧で「何をやったか(セット数)」が見え、種目を押すとその画面だけで
        #   前回記録・今日のセット入力・セット追加まで完結する(上下の行き来をなくす)
        # --------------------------------------------------
        # 開いている種目をURLに残す。接続が切れて新しいセッションになっても同じ画面へ戻る
        if "workout_ex" not in st.session_state:
            restored_ex = None
            try:
                qp = st.query_params
                if time.time() - int(qp.get("t", "0")) <= NAV_RESTORE_SECONDS:
                    restored_ex = qp.get("ex") or None
            except Exception:
                restored_ex = None
            st.session_state["workout_ex"] = restored_ex

        def open_exercise(name):
            st.session_state["workout_ex"] = name
            try:
                if name:
                    st.query_params["ex"] = name
                elif "ex" in st.query_params:
                    del st.query_params["ex"]
            except Exception:
                pass

        ex_by_part = fetch_all_exercises()
        ex_part_map = {}
        for _p, _names in ex_by_part.items():
            for _n in _names:
                ex_part_map.setdefault(_n, _p)

        selected_ex = st.session_state.get("workout_ex")
        if selected_ex and selected_ex not in ex_part_map:
            # 削除済みなど、存在しない種目が残っていたら一覧に戻す
            open_exercise(None)
            selected_ex = None

        # 一覧画面だけ、日付と編集モードを表示する
        edit_mode = False
        if not selected_ex:
            red_banner("種目を選択")
            c_date, c_edit = st.columns([3, 2], vertical_alignment="bottom")
            with c_date:
                work_date = st.date_input(
                    "日付", value=st.session_state.target_date, key="w_date"
                )
                st.session_state.target_date = work_date
            with c_edit:
                edit_mode = st.toggle("編集（種目の削除）", key="ex_edit_mode")
        save_nav_state()
        w_date_str = st.session_state.target_date.strftime("%Y-%m-%d")

        # --- 保存済みのセッション設定(実施時間・運動強度) ---
        intensity_options = [
            "標準 (通常のウェイトトレーニング)",
            "軽度 (ストレッチ/自重/休憩長め)",
            "高強度 (サーキット/高密度/スーパーセット)",
        ]
        try:
            existing_row = fetch_summary(user_id, w_date_str)
        except Exception:
            existing_row = None
        saved_duration = None
        saved_intensity = intensity_options[0]
        if existing_row:
            dur_val = existing_row.get("duration_min")
            if dur_val:  # NULL・0 は空欄のまま扱う
                saved_duration = int(dur_val)
            if existing_row.get("intensity") in intensity_options:
                saved_intensity = existing_row["intensity"]
        saved_burn = calculate_workout_burn(p_weight, saved_duration or 0, saved_intensity)

        # --- 本日の筋トレ記録(セット保存または日付変更時のみ再取得) ---
        if (
            "last_workout_date" not in st.session_state
            or st.session_state.last_workout_date != w_date_str
            or st.session_state.get("refresh_today_workout", False)
        ):
            w_res = (
                supabase.table("workout_logs")
                .select("*")
                .eq("user_id", user_id)
                .eq("date", w_date_str)
                .order("id")
                .execute()
            )
            st.session_state.workout_data_cache = w_res.data if w_res.data else []
            st.session_state.last_workout_date = w_date_str
            st.session_state.refresh_today_workout = False

        if not selected_ex:
            empty_ids = [
                r["id"] for r in st.session_state.workout_data_cache
                if r.get("weight") is None and r.get("reps") is None
                and r.get("exercise") in ex_part_map
            ]
            if empty_ids:
                try:
                    supabase.table("workout_logs").delete().in_("id", empty_ids).execute()
                    st.session_state.workout_data_cache = [
                        r for r in st.session_state.workout_data_cache
                        if r["id"] not in empty_ids
                    ]
                    fetch_month_logs.clear()
                except Exception:
                    pass

        today_by_ex = {}
        for r in st.session_state.workout_data_cache:
            today_by_ex.setdefault(r["exercise"], []).append(r)

        INITIAL_SET_COUNT = 3  # 種目を開いたときに最初から用意する空欄セットの数

        def filled_count(rows):
            return sum(
                1 for r in rows
                if r.get("weight") is not None and r.get("reps") is not None
            )

        def new_set_row(part_name, ex_name, weight=None, reps=None):
            row = {
                "user_id": user_id,
                "date": w_date_str,
                "part": part_name,
                "exercise": ex_name,
                "weight": weight,
                "reps": reps,
                "intensity": saved_intensity,
                "burned_calories": 0,
            }
            if saved_duration is not None:
                row["duration_min"] = saved_duration
            return row

        def on_sets_added(inserted_rows):
            # 追加した行は手元のデータにそのまま足す(DBの再取得はしない)
            if inserted_rows:
                st.session_state.workout_data_cache.extend(inserted_rows)
            else:
                st.session_state["refresh_today_workout"] = True

        # ==================================================
        # 画面2: 種目ごとの記録画面
        # ==================================================
        if selected_ex:
            ex_part = ex_part_map[selected_ex]
            today_rows = today_by_ex.get(selected_ex, [])

            c_back, c_gap, c_ana = st.columns([2, 3, 2], vertical_alignment="center")
            with c_back:
                st.button(
                    "← 戻る", key="ex_back", on_click=open_exercise, args=(None,),
                    use_container_width=True,
                )
            with c_ana:
                if st.button("分析", key="ex_analytics", use_container_width=True):
                    show_exercise_analytics(user_id, selected_ex, supabase)

            red_banner(selected_ex)
            st.caption(f"{ex_part}　{w_date_str.replace('-', '/')}")

            if not today_rows:
                rows_to_insert = [
                    new_set_row(ex_part, selected_ex) for _ in range(INITIAL_SET_COUNT)
                ]
                last_key = (selected_ex, w_date_str)
                last_cache = st.session_state.setdefault("last_sess_cache", {})
                res = None
                try:
                    if last_key in last_cache:
                        res = supabase.table("workout_logs").insert(rows_to_insert).execute()
                    else:
                        # 空欄セットの用意と前回記録の取得を同時に行う(待ち時間を短くする)
                        with ThreadPoolExecutor(max_workers=2) as pool:
                            f_ins = pool.submit(
                                lambda: supabase.table("workout_logs").insert(rows_to_insert).execute()
                            )
                            f_last = pool.submit(
                                _query_last_session, user_id, selected_ex, w_date_str
                            )
                            res = f_ins.result()
                            try:
                                last_cache[last_key] = f_last.result()
                            except Exception:
                                pass
                except Exception as e:
                    res = None
                    st.error(
                        f"セットを用意できませんでした: {e}\n"
                        "workout_logs の weight / reps が NULL を許可していない可能性があります。"
                    )
                if res is not None:
                    if res.data:
                        on_sets_added(res.data)
                        today_rows = res.data  # 再実行せず、そのまま続きを表示する
                    else:
                        st.session_state["refresh_today_workout"] = True
                        st.rerun()

            # --- 前回記録(前回のセッションの全セット) ---
            last_sess = get_last_session(user_id, selected_ex, w_date_str)
            with st.container(border=True):
                if last_sess:
                    lines = "".join(
                        f"<div style='display:flex; gap:14px; line-height:1.7;'>"
                        f"<span style='width:1.4em; color:#6b7280;'>{i}</span>"
                        f"<span>{float(s['weight']):.1f} kg × {int(s['reps'])} 回</span>"
                        f"</div>"
                        for i, s in enumerate(last_sess["sets"], start=1)
                    )
                    st.markdown(
                        f"<div style='font-weight:700; margin-bottom:4px;'>"
                        f"前回記録　{last_sess['date'].replace('-', '/')}</div>{lines}",
                        unsafe_allow_html=True,
                    )
                    if filled_count(today_rows) == 0:
                        if st.button(
                            "前回と同じ内容を入力", key="copy_last_session",
                            use_container_width=True,
                        ):
                            prev_sets = last_sess["sets"]
                            try:
                                # 用意済みの空欄セットに前回の値を入れる(足りない分は追加する)
                                for k, s in enumerate(prev_sets[:len(today_rows)]):
                                    rid = today_rows[k]["id"]
                                    supabase.table("workout_logs").update(
                                        {"weight": s["weight"], "reps": s["reps"]}
                                    ).eq("id", rid).execute()
                                    st.session_state.pop(f"w_{rid}", None)
                                    st.session_state.pop(f"r_{rid}", None)
                                extra = [
                                    new_set_row(ex_part, selected_ex, s["weight"], s["reps"])
                                    for s in prev_sets[len(today_rows):]
                                ]
                                if extra:
                                    supabase.table("workout_logs").insert(extra).execute()
                            except Exception as e:
                                st.error(f"コピーできませんでした: {e}")
                            else:
                                st.session_state["refresh_today_workout"] = True
                                fetch_last_record.clear()
                                st.rerun()
                else:
                    st.markdown(f"**{selected_ex}** の前回記録はまだありません。")

            # --- 今日のセット ---
            day_had_filled = any(
                filled_count(today_by_ex.get(n, [])) > 0 for n in ex_part_map
            )
            if today_rows:
                SET_COLS = [0.7, 0.8, 2.3, 0.8, 2.3, 1.8, 0.9]
                h1, _h_a, h2, _h_b, h3, h4, h5 = st.columns(SET_COLS)
                h1.caption("**セット**")
                h2.caption("**重さ (kg)**")
                h3.caption("**回数**")
                h4.caption("**推定RM**")
                h5.caption("**削除**")

                def paste_from_above(field, prev_id, cur_id):
                    # 一つ上のセットの現在の入力値を、このセットの入力欄に入れる。
                    # 入力欄の値が変わると、下の「数値変更時の自動更新」でDBにも保存される
                    v = st.session_state.get(f"{field}_{prev_id}")
                    if v is not None:
                        st.session_state[f"{field}_{cur_id}"] = v

                prev_id, prev_w, prev_r = None, None, None

                for set_no, row in enumerate(today_rows, start=1):
                    log_id = row["id"]
                    c1, ca, c2, cb, c3, c4, c5 = st.columns(SET_COLS, vertical_alignment="center")
                    c1.write(f"**{set_no}**")
                    ca.button(
                        ":material/arrow_downward:", key=f"paste_w_{log_id}",
                        help="一つ上のセットの重さを入れる",
                        on_click=paste_from_above, args=("w", prev_id, log_id),
                        disabled=(prev_w is None), use_container_width=True,
                    )
                    cb.button(
                        ":material/arrow_downward:", key=f"paste_r_{log_id}",
                        help="一つ上のセットの回数を入れる",
                        on_click=paste_from_above, args=("r", prev_id, log_id),
                        disabled=(prev_r is None), use_container_width=True,
                    )

                    # DBがNULLのときは空欄(None)で表示する
                    w_db = float(row["weight"]) if row.get("weight") is not None else None
                    r_db = int(row["reps"]) if row.get("reps") is not None else None

                    new_weight = c2.number_input(
                        "重さ", value=w_db, step=2.5, format="%.1f", placeholder="0.0",
                        key=f"w_{log_id}", label_visibility="collapsed",
                    )
                    new_reps = c3.number_input(
                        "回数", value=r_db, min_value=0, step=1, placeholder="0",
                        key=f"r_{log_id}", label_visibility="collapsed",
                    )
                    prev_id, prev_w, prev_r = log_id, new_weight, new_reps

                    # 数値変更時の自動更新(空欄同士は変更なし)
                    if new_weight != w_db or new_reps != r_db:
                        supabase.table("workout_logs").update({
                            "weight": new_weight,
                            "reps": new_reps,
                        }).eq("id", log_id).execute()
                        # 手元のデータも書き換える(DBの再取得も、画面の再実行もしない)
                        row["weight"] = new_weight
                        row["reps"] = new_reps
                        fetch_last_record.clear()
                        fetch_part_last_dates.clear()
                        fetch_month_logs.clear()
                        # その日の最初の入力済みセットのときだけ、全体の消費カロリーを反映する
                        if (not day_had_filled) and new_weight is not None and new_reps is not None:
                            day_had_filled = True
                            try:
                                supabase.table("daily_summaries").upsert({
                                    "user_id": user_id,
                                    "date": w_date_str,
                                    "workout_burned_calories": round(
                                        saved_burn + get_cardio_burn_total(user_id, w_date_str), 1
                                    ),
                                }, on_conflict="user_id,date").execute()
                                fetch_summary.clear()
                            except Exception:
                                pass
                        st.toast("記録を更新しました！")

                    # 推定RM(O'Conner式)
                    if new_reps and new_weight is not None:
                        c4.write(f"{new_weight * (1 + 0.025 * new_reps):.1f} kg")
                    else:
                        c4.write("-")

                    if c5.button("×", key=f"del_{log_id}", help="このセットを削除"):
                        supabase.table("workout_logs").delete().eq("id", log_id).execute()
                        invalidate_workout_cache()
                        st.session_state["refresh_today_workout"] = True
                        st.toast("セットを削除しました。")
                        st.rerun()
            else:
                st.info("今日のセットはまだありません。下のボタンで追加してください。")

            if st.button(
                "＋ セットを追加", key=f"add_set_{selected_ex}", type="primary",
                use_container_width=True,
            ):
                try:
                    res = (
                        supabase.table("workout_logs")
                        .insert(new_set_row(ex_part, selected_ex))
                        .execute()
                    )
                except Exception as e:
                    st.error(
                        f"セットを追加できませんでした: {e}\n"
                        "workout_logs の weight / reps が NULL を許可していない可能性があります。"
                    )
                else:
                    on_sets_added(res.data)
                    st.rerun()

        # ==================================================
        # 画面1: 種目一覧
        # ==================================================
        else:
            # --- セッション全体の設定(実施時間・運動強度): 常に表示する ---
            red_banner("セッション設定（実施時間・運動強度）")
            col_dur, col_int = st.columns(2)
            with col_dur:
                duration_input = st.number_input(
                    "全体実施時間 (分)", min_value=0, step=5, value=saved_duration,
                    placeholder="0", key="session_duration_input",
                )
            with col_int:
                intensity = st.selectbox(
                    "運動強度", intensity_options,
                    index=intensity_options.index(saved_intensity),
                    key="session_intensity_select",
                )
            estimated_burn = calculate_workout_burn(p_weight, duration_input or 0, intensity)
            st.info(f"このセッションの推定純消費カロリー: 約 {estimated_burn} kcal")
            if st.button("セッション設定を保存", type="primary", key="save_session_setting"):
                try:
                    supabase.table("daily_summaries").upsert({
                        "user_id": user_id,
                        "date": w_date_str,
                        "duration_min": duration_input,
                        "intensity": intensity,
                        "workout_burned_calories": round(
                            estimated_burn + get_cardio_burn_total(user_id, w_date_str), 1
                        ),
                    }, on_conflict="user_id,date").execute()
                    invalidate_cache()
                    st.toast("セッション時間と消費カロリーを保存しました！")
                    st.rerun()
                except Exception as e:
                    st.error(f"保存時にエラーが発生しました: {e}")

            # 種目の削除(2段階確認)
            del_target = st.session_state.get("del_target")
            if del_target:
                if del_target["stage"] == 1:
                    st.warning(f"種目「**{del_target['name']}**」を削除しますか？")
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button("はい（最終確認へ）", key="del1_yes", type="primary"):
                            del_target["stage"] = 2
                            st.rerun()
                    with d2:
                        if st.button("キャンセル", key="del1_no"):
                            st.session_state["del_target"] = None
                            st.rerun()
                else:
                    st.error(
                        f"**警告：** 「**{del_target['name']}**」に関連する過去のトレーニング記録や"
                        "分析データもすべて削除されます。本当に削除してよろしいですか？"
                    )
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button(
                            "すべてのデータを完全に削除する", key="del2_yes", type="primary"
                        ):
                            try:
                                supabase.table("exercises").delete().eq(
                                    "name", del_target["name"]
                                ).eq("part", del_target["part"]).execute()
                                # 権限(RLS)で拒否されると、エラーにならず0件削除になるため、残っていないか確認する
                                still = (
                                    supabase.table("exercises").select("id")
                                    .eq("name", del_target["name"])
                                    .eq("part", del_target["part"]).execute()
                                )
                                if still.data:
                                    st.error(
                                        "種目を削除できませんでした。Supabase の exercises テーブルに "
                                        "DELETE のポリシーがあるか確認してください。"
                                    )
                                else:
                                    supabase.table("workout_logs").delete().eq(
                                        "user_id", user_id
                                    ).eq("exercise", del_target["name"]).execute()
                                    invalidate_cache()
                                    st.session_state["refresh_today_workout"] = True
                                    st.session_state["del_target"] = None
                                    st.toast(f"「{del_target['name']}」を削除しました。")
                                    st.rerun()
                            except Exception as e:
                                st.error(f"削除処理中にエラーが発生しました: {e}")
                    with d2:
                        if st.button("やめる", key="del2_no"):
                            st.session_state["del_target"] = None
                            st.rerun()

            n_ex_done = sum(1 for n in ex_part_map if filled_count(today_by_ex.get(n, [])) > 0)
            n_sets_done = sum(filled_count(today_by_ex.get(n, [])) for n in ex_part_map)
            if n_sets_done:
                st.caption(f"本日の記録: {n_ex_done}種目 / 計{n_sets_done}セット")
            else:
                st.caption("本日の記録はまだありません。")

            st.markdown(
                """
                <style>
                .st-key-ex_list button { justify-content: flex-start !important; }
                .st-key-ex_list button p { text-align: left !important; }
                </style>
                """,
                unsafe_allow_html=True,
            )

            part_last_dates = fetch_part_last_dates(user_id)
            today_obj = today_jst()

            EX_PREVIEW_COUNT = 3  # 通常表示は3件まで。4件目以降は「すべて表示」で開く

            def toggle_expand(part_name):
                opened = st.session_state.setdefault("ex_expanded_parts", [])
                if part_name in opened:
                    opened.remove(part_name)
                else:
                    opened.append(part_name)

            def start_delete(part_name, ex_name):
                st.session_state["del_target"] = {
                    "part": part_name, "name": ex_name, "stage": 1,
                }

            with st.container(key="ex_list"):
                for part in PART_LIST:
                    names = ex_by_part.get(part, [])
                    if part in part_last_dates:
                        last_d = datetime.datetime.strptime(
                            part_last_dates[part], "%Y-%m-%d"
                        ).date()
                        days_ago = (today_obj - last_d).days
                        days_str = "本日実施" if days_ago == 0 else f"{days_ago}日前"
                    else:
                        days_str = "記録なし"
                    red_banner(f"{part} - {days_str}")

                    opened_parts = st.session_state.setdefault("ex_expanded_parts", [])
                    if edit_mode or part in opened_parts:
                        visible_names = names
                    else:
                        visible_names = [
                            n for i, n in enumerate(names)
                            if i < EX_PREVIEW_COUNT or filled_count(today_by_ex.get(n, [])) > 0
                        ]
                    for name in visible_names:
                        n_done = filled_count(today_by_ex.get(name, []))
                        label = f"✅ {name}　{n_done}セット" if n_done else name
                        if edit_mode:
                            c_open, c_del = st.columns([6, 1])
                            with c_open:
                                st.button(
                                    label, key=f"ex_open_{part}_{name}",
                                    on_click=open_exercise, args=(name,),
                                    use_container_width=True,
                                )
                            with c_del:
                                st.button(
                                    ":material/delete:", key=f"ex_del_{part}_{name}",
                                    help="この種目を削除",
                                    on_click=start_delete, args=(part, name),
                                    use_container_width=True,
                                )
                        else:
                            st.button(
                                label, key=f"ex_open_{part}_{name}",
                                on_click=open_exercise, args=(name,),
                                use_container_width=True,
                            )

                    hidden_count = len(names) - len(visible_names)
                    if (not edit_mode) and len(names) > EX_PREVIEW_COUNT and (
                        part in opened_parts or hidden_count > 0
                    ):
                        st.button(
                            "閉じる" if part in opened_parts else f"すべて表示（あと{hidden_count}件）",
                            key=f"ex_more_{part}",
                            on_click=toggle_expand, args=(part,),
                            use_container_width=True,
                        )

                    with st.expander(f"＋ {part}の種目を追加"):
                        new_ex_name = st.text_input(
                            "種目名", key=f"new_ex_{part}",
                            placeholder="例: ダンベルインクラインフライ",
                        )
                        if st.button("この種目を登録", key=f"reg_ex_{part}", type="primary"):
                            nm = new_ex_name.strip()
                            if not nm:
                                st.warning("種目名を入力してください。")
                            elif nm in names:
                                st.warning("すでに登録されています。")
                            else:
                                try:
                                    supabase.table("exercises").insert(
                                        {"part": part, "name": nm}
                                    ).execute()
                                except Exception as e:
                                    st.error(f"登録できませんでした: {e}")
                                else:
                                    invalidate_cache()
                                    st.toast(f"「{nm}」を登録しました！")
                                    st.rerun()

if __name__ == "__main__":
    main()