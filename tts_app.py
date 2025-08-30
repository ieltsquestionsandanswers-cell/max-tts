# Max TTS — Multilingual (EN+KO) for Hugging Face Spaces
# ✔ Azure 불필요: edge-tts 우선 시도, 실패하면 gTTS로 자동 폴백
# ✔ 한국어/영어가 섞인 문장을 문자 종류로 세그먼트 → 각각 합성 → (ffmpeg 있으면) 병합
# ✔ 빈 조각/기호만 있는 조각은 건너뜀 → gTTS의 "No text to send" 오류 방지
# -----------------------------------------------------------------------------

import asyncio, io, os, re, tempfile, zipfile, html
from uuid import uuid4

import streamlit as st
import edge_tts
from gtts import gTTS

# ---------- (선택) FFmpeg가 있으면 여러 MP3 병합 ----------
try:
    from pydub import AudioSegment
    from pydub.utils import which
    AudioSegment.converter = which("ffmpeg") or which("ffmpeg.exe")
    HAVE_FFMPEG = AudioSegment.converter is not None
except Exception:
    HAVE_FFMPEG = False

# ---------- 라벨/도움 함수 ----------
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

@st.cache_data(show_spinner=False)
def get_multilingual_voices():
    """edge-tts 목록에서 'Multilingual' 보이스만 가져오기(실패 시 기본셋)."""
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
        if not out:
            raise RuntimeError("no voices")
        return sorted(out, key=lambda x: (LANG_MAP.get(x["locale"], x["locale"]), x["label"]))
    except Exception:
        fb = [
            ("en-US-JennyMultilingualNeural","en-US","Female"),
            ("en-US-AndrewMultilingualNeural","en-US","Male"),
            ("en-US-AriaMultilingualNeural","en-US","Female")
        ]
        return [{"short": s, "locale": loc, "gender": g, "label": friendly_label(s,loc,g)}
                for s,loc,g in fb]

def count_words(t: str) -> int:
    return len(re.findall(r"\S+", t or ""))

def split_text(text: str, max_chars: int = 2500):
    """길이 제한에 맞춰 문장을 나눔."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
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

# ---------- 텍스트가 발화 가능한지 / 스크립트별 분할 ----------
def _speakable(s: str) -> bool:
    """영문/숫자/한글 중 하나라도 포함하면 True."""
    return bool(re.search(r"[A-Za-z0-9\uAC00-\uD7A3]", s or ""))

def segment_by_script(text: str):
    """한글/영문을 문자 단위로 모아 세그먼트 생성, 의미 없는 조각은 제외."""
    if not text:
        return []
    segs = []
    cur = ""
    cur_lang = None
    for ch in text:
        if '\uac00' <= ch <= '\ud7a3':
            lang = "ko"
        elif ch.isascii() and ch.isprintable():
            lang = "en"
        else:
            lang = None  # 기호/이모지/공백 등
        if lang is None or ch.isspace():
            cur += ch
            continue
        if cur_lang is None:
            cur_lang = lang
        if lang == cur_lang:
            cur += ch
        else:
            if _speakable(cur):
                segs.append((cur_lang, cur.strip()))
            cur = ch
            cur_lang = lang
    if _speakable(cur):
        segs.append((cur_lang or "en", cur.strip()))
    return segs

# ---------- gTTS로 합성(조각 무시 처리 포함) ----------
def synth_with_gtts(text: str, out_path: str):
    segs = segment_by_script(text)
    if not segs and _speakable(text):
        segs = [("en", text.strip())]
    parts = []
    for lang, seg in segs:
        if not _speakable(seg):
            continue
        tmp = os.path.join(tempfile.gettempdir(), f"gtts_{uuid4().hex}.mp3")
        gTTS(seg, lang=("ko" if lang == "ko" else "en")).save(tmp)
        parts.append(tmp)
    if not parts:
        raise ValueError("입력에 발화할 문자가 없습니다.")
    if len(parts) == 1:
        os.replace(parts[0], out_path)
        return
    if not HAVE_FFMPEG:
        os.replace(parts[0], out_path)
        return
    audio = AudioSegment.silent(duration=0)
    for p in parts:
        audio += AudioSegment.from_file(p, format="mp3")
    audio.export(out_path, format="mp3", bitrate="192k")

# ---------- 1개 조각 합성: edge-tts 우선, 실패 시 gTTS ----------
async def synth_one(text: str, voice: str, rate, pitch, out_path: str):
    try:
        kwargs = {"voice": voice}
        if rate:  kwargs["rate"]  = rate
        if pitch: kwargs["pitch"] = pitch
        comm = edge_tts.Communicate(text, **kwargs)
        await comm.save(out_path)
        return
    except Exception:
        # (클라우드에서 403 등) → gTTS 폴백
        synth_with_gtts(text, out_path)

async def synth_many(chunks, voice, rate, pitch, progress=None):
    outs = []; total = len(chunks)
    for i, ck in enumerate(chunks, 1):
        p = os.path.join(tempfile.gettempdir(), f"tts_{uuid4().hex}_{i}.mp3")
        await synth_one(ck, voice, rate, pitch, p)
        outs.append(p)
        if progress: progress(i, total)
    return outs

def merge_with_ffmpeg(mp3s):
    if not HAVE_FFMPEG: return None
    a = AudioSegment.silent(duration=0)
    for f in mp3s:
        a += AudioSegment.from_file(f, format="mp3")
    buf = io.BytesIO()
    a.export(buf, format="mp3", bitrate="192k")
    buf.seek(0)
    return buf

def zip_parts(mp3s):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, f in enumerate(mp3s, 1):
            zf.write(f, f"part{i:02d}.mp3")
    buf.seek(0)
    return buf

# ---------- UI ----------
st.set_page_config(page_title="Max TTS — Multilingual", page_icon="🎙️", layout="wide")
st.markdown("""
<style>
:root{--brand:#5B8CFF;--brand2:#7A5BFF;--bg:#f7f8fc;--card:#fff;--muted:#64748b}
section.main>div{background:var(--bg)}
.max-hero{padding:24px;border-radius:16px;background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff;margin-bottom:18px}
.max-hero h1{margin:0;font-size:28px}
.max-card{background:var(--card);border-radius:14px;padding:18px;box-shadow:0 6px 18px rgba(15,23,42,.06);border:1px solid #edf2ff}
.badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.badge{background:#fff;color:var(--muted);border:1px solid #e2e8f0;padding:6px 10px;border-radius:999px;font-size:12px}
.stButton button{background:var(--brand);color:#fff;border:none;border-radius:12px;padding:12px 18px;font-weight:700}
div[data-testid="stTextArea"] small{display:none!important}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="max-hero"><h1>🎙️ Max TTS</h1><p>영어·한국어 혼합 문장을 한 번에 MP3로 변환 (Azure 없이)</p></div>', unsafe_allow_html=True)

left, right = st.columns([1.2, 1])

# 입력 카드
with left:
    st.markdown('<div class="max-card">', unsafe_allow_html=True)
    text = st.text_area(
        "여기에 영어/한국어가 섞인 문장을 입력하세요",
        height=240,
        placeholder="예) Hello everyone, 오늘부터 Paraphrasing에 대해 설명을 하겠습니다."
    )
    wc = count_words(text)
    st.markdown(
        f'<div class="badges"><span class="badge">단어 수: {wc:,} / 9,999</span>'
        f'<span class="badge">FFmpeg: {"사용" if HAVE_FFMPEG else "없음"}</span></div>',
        unsafe_allow_html=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

# 보이스/옵션 카드
with right:
    st.markdown('<div class="max-card">', unsafe_allow_html=True)
    st.subheader("보이스 & 옵션", divider="gray")

    items = get_multilingual_voices()
    groups = {}
    for v in items:
        groups.setdefault(v["locale"], []).append(v)
    for k in groups:
        groups[k] = sorted(groups[k], key=lambda x: x["label"])

    def gname(k): return LANG_MAP.get(k, k)
    keys = sorted(groups.keys(), key=lambda g: (0 if g == "en-US" else 1 if g == "ko-KR" else 9, gname(g)))
    if not keys: keys = ["en-US"]
    sel_group = st.selectbox("언어별 그룹", keys, format_func=gname)
    voice = st.selectbox("음성 선택", groups.get(sel_group, [{"short":"en-US-JennyMultilingualNeural","label":"Jenny — 영어(미국)"}]),
                         format_func=lambda x: x.get("label",""))["short"]

    rate_pct = st.slider("속도 (rate) — edge-tts에서만 적용", -30, 30, 0)
    pitch_st = st.slider("피치 (pitch) — edge-tts에서만 적용", -6, 6, 0)
    rate  = None if rate_pct == 0 else f"{rate_pct:+d}%"
    pitch = None if pitch_st == 0 else f"{pitch_st:+d}st"
    max_chars = st.slider("조각 최대 글자수(안전)", 800, 3000, 2500, 100)

    st.markdown(f'<div class="badges"><span class="badge">보이스(우선 시도): {voice}</span></div>',
                unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# 실행 카드
st.markdown('<div class="max-card">', unsafe_allow_html=True)
if st.button("🎧 MP3 만들기", use_container_width=True):
    chunks = split_text(text, max_chars=max_chars)
    chunks = [c for c in chunks if _speakable(c)]  # 빈/무의미 조각 제거

    if not chunks:
        st.error("읽을 수 있는 텍스트가 없습니다. 문장을 입력해 주세요.")
        st.stop()

    if not HAVE_FFMPEG:
        st.info("FFmpeg가 없어도 동작하지만, 여러 조각을 하나로 합치지 못할 수 있어요. (apt.txt에 ffmpeg 추가 권장)")
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
                st.success("완료! (병합)")
                a,b = st.columns([3,1])
                with a: st.audio(merged.getvalue(), format="audio/mp3")
                with b: st.download_button("⬇️ MP3 다운로드", merged.getvalue(), "tts.mp3", "audio/mpeg")
            else:
                st.warning("FFmpeg가 없어 조각 파일로 제공합니다.")
                for i, fpath in enumerate(files, 1):
                    with open(fpath, "rb") as fh: data = fh.read()
                    st.write(f"part{i:02d}.mp3")
                    a,b = st.columns([3,1])
                    with a: st.audio(data, format="audio/mp3")
                    with b: st.download_button(f"⬇️ part{i:02d}.mp3", data, f"part{i:02d}.mp3", "audio/mpeg")
                zipped = zip_parts(files)
                st.download_button("📦 조각 ZIP 다운로드", zipped.getvalue(), "tts_parts.zip", "application/zip")
    except Exception as e:
        st.error(f"오류: {e}")
    finally:
        bar.empty()
st.markdown('</div>', unsafe_allow_html=True)

st.markdown(
    "<div style='text-align:center;color:#94a3b8;font-size:12px;padding:12px 0;'>"
    "© Max TTS — Multilingual Text-to-Speech</div>",
    unsafe_allow_html=True
)
