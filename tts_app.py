# Max TTS — Multilingual TTS (EN+KO) · 디자인 리뉴얼
# 기능: 다국어 보이스만, 언어별 그룹, ★즐겨찾기/기본 보이스, 미리듣기+다운로드
# 디자인: 헤더/상태칩/카드형 옵션/큰 CTA 버튼/폰트·색상 커스텀

import asyncio, io, os, re, tempfile, zipfile, json
from uuid import uuid4
import streamlit as st
import edge_tts

# (선택) FFmpeg가 있으면 여러 조각을 한 MP3로 병합
try:
    from pydub import AudioSegment
    from pydub.utils import which
    AudioSegment.converter = which("ffmpeg") or which("ffmpeg.exe")
    HAVE_FFMPEG = AudioSegment.converter is not None
except Exception:
    HAVE_FFMPEG = False

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(APP_DIR, "tts_settings.json")

# ---------- 설정 ----------
def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("favorites", [])
        s.setdefault("default_voice", None)
        return s
    except Exception:
        return {"favorites": ["en-US-JennyMultilingualNeural"],
                "default_voice": "en-US-JennyMultilingualNeural"}

def save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# ---------- 유틸 ----------
def count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))

def split_text(text: str, max_chars: int = 2500):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return [text]
    sents = re.split(r"(?<=[.!?])\s+|(?<=[。！？])\s+", text)
    chunks, buf = [], ""
    for s in sents:
        if len(buf) + len(s) + 1 <= max_chars:
            buf += (" " if buf else "") + s
        else:
            if buf: chunks.append(buf)
            if len(s) > max_chars:
                for i in range(0, len(s), max_chars):
                    chunks.append(s[i:i+max_chars])
                buf = ""
            else:
                buf = s
    if buf: chunks.append(buf)
    return chunks

# ---------- 라벨 ----------
LANG_MAP = {
    "en-US": "영어(미국)", "en-GB": "영어(영국)", "en-AU": "영어(호주)",
    "de-DE": "독일어(독일)", "fr-FR": "프랑스어(프랑스)", "es-ES": "스페인어(스페인)",
    "ja-JP": "일본어(일본)", "zh-CN": "중국어(중국)", "ko-KR": "한국어"
}
GENDER_MAP = {"Female": "여성", "Male": "남성"}

def friendly_label(short_name, locale, gender):
    tail = short_name.split("-", 2)[-1]
    name = tail.replace("MultilingualNeural", "").replace("Neural", "")
    lang = LANG_MAP.get(locale, locale)
    gen = GENDER_MAP.get(gender, gender or "")
    base = f"{name} — {lang}"
    return f"{base} · {gen}" if gen else base

# ---------- 보이스(다국어만) ----------
@st.cache_data(show_spinner=False)
def get_multilingual_voices():
    try:
        voices = asyncio.run(edge_tts.list_voices())
        out = []
        for v in voices:
            sn = v.get("ShortName", "")
            if "Multilingual" not in sn:
                continue
            locale = v.get("Locale", sn.split("-", 2)[0] + "-" + sn.split("-", 2)[1])
            gender = v.get("Gender", "")
            out.append({
                "short": sn, "locale": locale, "gender": gender,
                "label": friendly_label(sn, locale, gender),
            })
        return sorted(out, key=lambda x: (LANG_MAP.get(x["locale"], x["locale"]), x["label"]))
    except Exception:
        fb = [
            ("en-US-JennyMultilingualNeural","en-US","Female"),
            ("en-US-AriaMultilingualNeural", "en-US","Female"),
            ("en-US-BrianMultilingualNeural","en-US","Male"),
        ]
        return [{"short": s, "locale": loc, "gender": g, "label": friendly_label(s,loc,g)}
                for s,loc,g in fb]

# ---------- 합성 ----------
async def synth_one(text: str, voice: str, rate, pitch, out_path: str):
    kwargs = {"voice": voice}
    if rate:  kwargs["rate"]  = rate
    if pitch: kwargs["pitch"] = pitch
    comm = edge_tts.Communicate(text, **kwargs)
    await comm.save(out_path)

async def synth_many(chunks, voice, rate, pitch, progress=None):
    tmp_files = []
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        path = os.path.join(tempfile.gettempdir(), f"tts_{uuid4().hex}_{i}.mp3")
        await synth_one(chunk, voice, rate, pitch, path)
        tmp_files.append(path)
        if progress: progress(i, total)
    return tmp_files

def merge_with_ffmpeg(mp3_files):
    if not HAVE_FFMPEG: return None
    audio = AudioSegment.silent(duration=0)
    for f in mp3_files:
        audio += AudioSegment.from_file(f, format="mp3")
    buf = io.BytesIO(); audio.export(buf, format="mp3", bitrate="192k"); buf.seek(0)
    return buf

def zip_parts(mp3_files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, f in enumerate(mp3_files, 1):
            zf.write(f, f"part{i:02d}.mp3")
    buf.seek(0); return buf

# ---------- 페이지 설정 & 스타일 ----------
st.set_page_config(page_title="Max TTS — Multilingual", page_icon="🎙️", layout="wide")

st.markdown("""
<style>
/* 전체 톤 */
:root {
  --brand:#5B8CFF;           /* 포인트 컬러 */
  --brand-2:#7A5BFF;
  --bg-soft:#f7f8fc;
  --card:#ffffff;
  --text:#0f172a;
  --muted:#64748b;
}
/* 배경 */
section.main > div { background: var(--bg-soft); }
/* 헤더 */
.max-hero {
  padding: 24px 28px;
  border-radius: 16px;
  background: linear-gradient(135deg, var(--brand), var(--brand-2));
  color: white;
  margin-bottom: 18px;
}
.max-hero h1 { margin: 0; font-size: 28px; line-height: 1.25; }
.max-hero p  { margin: 6px 0 0; opacity: .95; }
/* 카드 */
.max-card {
  background: var(--card);
  border-radius: 14px;
  padding: 18px;
  box-shadow: 0 6px 18px rgba(15,23,42,.06);
  border: 1px solid #edf2ff;
}
/* 상태칩 */
.badges { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px;}
.badge {
  background:#fff; color:var(--muted); border:1px solid #e2e8f0;
  padding:6px 10px; border-radius:999px; font-size:12px;
}
/* CTA 버튼 크게 */
div[data-testid="baseButton-secondary"] button,
div[data-testid="baseButton-primary"] button,
.stButton button {
  background: var(--brand);
  border: none;
  color: #fff;
  padding: 12px 18px;
  border-radius: 12px;
  font-weight: 700;
}
.stButton button:hover { filter: brightness(1.05); }
/* 텍스트영역 하단 'Press Ctrl+Enter...' 숨김 */
div[data-testid="stTextArea"] small { display:none !important; }
</style>
""", unsafe_allow_html=True)

# ---------- 헤더 ----------
st.markdown(
    '<div class="max-hero">'
    '<h1>🎙️ Max TTS</h1>'
    '<p>영어·한국어 혼합 문장을 한 목소리로 MP3로 변환하세요.</p>'
    '</div>', unsafe_allow_html=True
)

# ---------- 레이아웃 ----------
left, right = st.columns([1.2, 1])

# 입력 영역 (좌)
with left:
    st.markdown('<div class="max-card">', unsafe_allow_html=True)
    text = st.text_area(
        "여기에 영어/한국어가 섞인 문장을 입력하세요",
        height=240,
        placeholder="예) Hello everyone, 오늘부터 Paraphrasing에 대해 설명을 하겠습니다.",
        key="input_text"
    )
    wc = count_words(text)
    # 상태칩
    st.markdown(
        f'<div class="badges">'
        f'<span class="badge">단어 수: {wc:,} / 9,999</span>'
        f'<span class="badge">FFmpeg: {"사용 가능" if HAVE_FFMPEG else "없음"}</span>'
        f'</div>',
        unsafe_allow_html=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

# 옵션 카드 (우)
with right:
    st.markdown('<div class="max-card">', unsafe_allow_html=True)
    st.subheader("보이스 & 옵션", divider="gray")

    # ---- 설정 로드 ----
    settings = load_settings()

    # ---- 보이스 목록/그룹 ----
    items = get_multilingual_voices()
    fav_set = set(settings.get("favorites", []))

    groups = {}
    fav_items = [dict(x, label="★ " + x["label"]) for x in items if x["short"] in fav_set]
    if fav_items: groups["★ 즐겨찾기"] = sorted(fav_items, key=lambda x: x["label"])
    for x in items:
        g = x["locale"]; groups.setdefault(g, []); groups[g].append(x)
    for k in groups: groups[k] = sorted(groups[k], key=lambda x: x["label"])

    def group_display_name(key):
        return "★ 즐겨찾기" if key == "★ 즐겨찾기" else LANG_MAP.get(key, key)

    preferred = ["★ 즐겨찾기", "en-US", "ko-KR"]
    group_keys = list(groups.keys())
    group_keys = sorted(group_keys, key=lambda g: (preferred.index(g) if g in preferred else 999,
                                                   group_display_name(g)))

    if fav_items: default_group = "★ 즐겨찾기"
    elif "en-US" in group_keys: default_group = "en-US"
    elif "ko-KR" in group_keys: default_group = "ko-KR"
    else: default_group = group_keys[0]

    sel_group_key = st.selectbox("언어별 그룹", group_keys,
                                 index=group_keys.index(default_group),
                                 format_func=group_display_name)

    group_list = groups[sel_group_key]
    def_index = 0
    for i, v in enumerate(group_list):
        if v["short"] == settings.get("default_voice"):
            def_index = i; break

    sel_item = st.selectbox("음성 선택", group_list, index=def_index, format_func=lambda x: x["label"])
    voice = sel_item["short"]

    # 즐겨찾기/기본 설정 버튼
    c1, c2 = st.columns(2)
    with c1:
        if voice in fav_set:
            if st.button("★ 즐겨찾기 해제"):
                settings["favorites"] = [v for v in settings["favorites"] if v != voice]
                save_settings(settings); st.rerun()
        else:
            if st.button("★ 즐겨찾기 추가"):
                settings["favorites"] = [voice] + [v for v in settings.get("favorites", []) if v != voice]
                settings["favorites"] = list(dict.fromkeys(settings["favorites"]))
                save_settings(settings); st.rerun()
    with c2:
        if settings.get("default_voice") != voice:
            if st.button("기본 보이스로 설정"):
                settings["default_voice"] = voice
                if voice not in settings["favorites"]:
                    settings["favorites"].insert(0, voice)
                save_settings(settings); st.rerun()

    st.divider()
    rate_pct = st.slider("속도 (rate)", -30, 30, 0, help="0=기본, + 빠르게, − 느리게")
    pitch_st = st.slider("피치 (pitch, semitone)", -6, 6, 0, help="0=기본, ±반음")
    rate  = None if rate_pct == 0 else f"{rate_pct:+d}%"
    pitch = None if pitch_st == 0 else f"{pitch_st:+d}st"
    max_chars = st.slider("조각 최대 글자수(안전)", 800, 3000, 2500, 100)

    # 현재 선택 보이스 상태칩
    st.markdown(
        f'<div class="badges"><span class="badge">보이스: {voice}</span></div>',
        unsafe_allow_html=True
    )

    st.markdown('</div>', unsafe_allow_html=True)

# ---------- 생성 버튼 & 결과 ----------
st.markdown('<div class="max-card">', unsafe_allow_html=True)
cta = st.button("🎧 MP3 만들기", use_container_width=True)
if cta:
    if not text.strip():
        st.error("먼저 텍스트를 입력하세요.")
    elif count_words(text) > 9999:
        st.error("9,999 단어 이하로 줄여 주세요.")
    else:
        chunks = split_text(text, max_chars=max_chars)
        st.info(f"긴 글을 {len(chunks)}개 조각으로 나눠서 합성합니다.")
        bar = st.progress(0.0); prog = lambda i,t: bar.progress(i/t, text=f"{i}/{t} 생성 중…")
        try:
            files = asyncio.run(synth_many(chunks, voice, rate, pitch, prog))
            if len(files) == 1:
                with open(files[0], "rb") as f: data = f.read()
                st.success("완료!")
                a,b = st.columns([3,1])
                with a: st.audio(data, format="audio/mp3")
                with b: st.download_button("⬇️ MP3 다운로드", data, "tts.mp3", "audio/mpeg")
            else:
                merged = merge_with_ffmpeg(files)
                if merged:
                    st.success("완료! (조각 병합됨)")
                    a,b = st.columns([3,1])
                    with a: st.audio(merged.getvalue(), format="audio/mp3")
                    with b: st.download_button("⬇️ MP3 다운로드", merged.getvalue(), "tts.mp3", "audio/mpeg")
                else:
                    st.warning("FFmpeg가 없어 조각 파일로 제공합니다.")
                    for i, f in enumerate(files, 1):
                        with open(f, "rb") as fh: data = fh.read()
                        st.write(f"part{i:02d}.mp3"); a,b = st.columns([3,1])
                        with a: st.audio(data, format="audio/mp3")
                        with b: st.download_button(f"⬇️ part{i:02d}.mp3", data, f"part{i:02d}.mp3", "audio/mpeg")
                    zipped = zip_parts(files)
                    st.download_button("📦 조각 ZIP 다운로드", zipped.getvalue(), "tts_parts.zip", "application/zip")
        except Exception as e:
            st.error(f"오류: {e}")
        finally:
            bar.empty()
st.markdown('</div>', unsafe_allow_html=True)

# ---------- 풋터 ----------
st.markdown(
    "<div style='text-align:center; color:#94a3b8; font-size:12px; padding:12px 0;'>"
    "© Max TTS — Multilingual Text-to-Speech</div>",
    unsafe_allow_html=True
)
