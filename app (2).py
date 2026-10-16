import streamlit as st
import os, json, time, subprocess, asyncio, cv2, tempfile

st.set_page_config(page_title="🎬 ملخص الأفلام بالدارجة", page_icon="🎬", layout="centered")
st.title("🎬 ملخص الأفلام بالذكاء الاصطناعي — بالدارجة المغربية")
st.caption("رفع فيلم ← تلخيص 12-14 دقيقة ← تعليق صوتي رجالي ← حماية عائلية")

import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
from mutagen.mp3 import MP3

GROQ_KEY = st.secrets.get("GROQ_API_KEY", "")

with st.sidebar:
    st.header("⚙️ الإعدادات")
    target_min = st.slider("مدة الملخص (دقائق)", 10, 16, 13)
    max_seg = st.slider("أقصى طول مشهد (ثوانٍ)", 4, 10, 6)
    nude_thr = st.slider("حساسية حماية العري", 0.4, 0.8, 0.6, 0.05)
    st.divider()
    st.markdown("**🔑 مفتاح Groq** (مجاني: console.groq.com)")
    groq_key = st.text_input("GROQ_API_KEY", value=GROQ_KEY, type="password")
    st.divider()
    banned = st.text_area("كلمات ممنوعة (افصل بفاصلة)",
        value="fuck,shit,bitch,كس,زب,قحبة,شرموطة")
    BANNED = [w.strip() for w in banned.split(",") if w.strip()]

video = st.file_uploader("📤 ارفع الفيلم (mp4/mkv)", type=["mp4", "mkv", "avi"])

def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)

@st.cache_resource
def load_whisper():
    import whisper
    return whisper.load_model("medium")

@st.cache_resource
def load_detector():
    from nudenet import NudeDetector
    return NudeDetector()

def tts_sync(text, path, voice):
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, voice, rate="+5%").save(path))

def pick_voice(tmp):
    import edge_tts
    for v in ["ar-MA-JamalNeural", "ar-DZ-IsmaelNeural", "ar-SA-HamedNeural", "ar-EG-ShakirNeural"]:
        try:
            tp = os.path.join(tmp, "t.mp3")
            asyncio.run(edge_tts.Communicate("اختبار", v).save(tp))
            if os.path.getsize(tp) > 1000:
                return v
        except Exception:
            pass
    return "ar-EG-ShakirNeural"

def mp3_dur(p):
    return float(MP3(p).info.length)

if st.button("🚀 ابدأ المعالجة", type="primary", disabled=not video):
    if not groq_key:
        st.error("أدخل مفتاح Groq API أولاً من الشريط الجانبي")
        st.stop()
    tmp = tempfile.mkdtemp()
    film = os.path.join(tmp, "film.mp4")
    with open(film, "wb") as f:
        f.write(video.read())

    bar = st.progress(0, text="⏳ تفريغ الصوت...")
    from groq import Groq
    client = Groq(api_key=groq_key)
    model = load_whisper()
    segments = model.transcribe(film, verbose=False)["segments"]
    bar.progress(20, text=f"✅ {len(segments)} مقطعاً — جاري الترجمة للدارجة...")

    def process_chunk(segs):
        lines = "\n".join([f'{i}|{s["start"]:.1f}|{s["end"]:.1f}|{s["text"]}'
                           for i, s in enumerate(segs)])
        prompt = f'''أنت مترجم أفلام محترف. لكل سطر: رقم|بداية|نهاية|نص
أعد JSON فقط:
1. ترجم إلى عربية فصحى مبسطة ممزوجة بالدارجة المغربية
2. أهمية المقطع للقصة من 1 إلى 10
3. كلمات بذيئة: احذفها وضع prof=true
4. غير مهم (موسيقى/ضجيج): drop=true
كلمات ممنوعة ضع drop=true: {BANNED}
السطور:
{lines}
الصيغة: [{{"i":0,"ar":"...","imp":8,"prof":false,"drop":false}},...]'''
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, response_format={"type": "json_object"})
        txt = r.choices[0].message.content
        return json.loads(txt if txt.strip().startswith("[") else txt[txt.find("["):txt.rfind("]")+1])

    processed = []
    for si in range(0, len(segments), 20):
        try:
            processed.extend(process_chunk(segments[si:si+20]))
        except Exception:
            pass
        time.sleep(0.5)
    bar.progress(45, text="✅ تمت الترجمة — جاري اختيار أهم المشاهد...")

    target = target_min * 60
    cand = [p for p in processed if not p.get("drop") and not p.get("prof")
            and p.get("imp", 0) >= 4 and p["i"] < len(segments)]
    chosen, total = [], 0
    for p in sorted(cand, key=lambda x: -x["imp"]):
        s = segments[p["i"]]
        d = min(s["end"] - s["start"], max_seg)
        if total + d > target: continue
        chosen.append({"seg": s, "ar": p["ar"]}); total += d
    chosen.sort(key=lambda x: x["seg"]["start"])
    bar.progress(55, text=f"✅ {len(chosen)} مشهداً — جاري تجهيز الصوت الرجالي...")

    VOICE = pick_voice(tmp)
    detector = load_detector()

    clips = []
    NUDE = ("female_breast_exposed","female_genitalia_exposed","buttocks_exposed",
            "male_genitalia_exposed","anus_exposed")
    for idx, c in enumerate(chosen):
        s = c["seg"]["start"]; e = min(c["seg"]["end"], s + max_seg); dur = e - s
        vid = os.path.join(tmp, f"{idx:04d}_v.mp4")
        out = os.path.join(tmp, f"{idx:04d}.mp4")
        mp3 = os.path.join(tmp, f"{idx:04d}.mp3")

        nude = False
        for frac in [0.25, 0.5, 0.75]:
            cap = cv2.VideoCapture(film)
            cap.set(cv2.CAP_PROP_POS_MSEC, (s + dur*frac)*1000)
            ok, frame = cap.read(); cap.release()
            if not ok: continue
            fp = os.path.join(tmp, "f.jpg"); cv2.imwrite(fp, frame)
            try:
                if any(d["score"] > nude_thr and d["class"].lower() in NUDE
                       for d in detector.detect(fp)): nude = True
            except Exception: pass

        vf = "boxblur=30:5" if nude else "null"
        run([FFMPEG, "-y", "-ss", str(s), "-to", str(e), "-i", film,
             "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", vid])
        tts_sync(c["ar"], mp3, VOICE)
        factor = min(max(dur / max(mp3_dur(mp3), 0.1), 0.6), 1.7)
        run([FFMPEG, "-y", "-i", vid, "-i", mp3, "-c:v", "copy",
             "-af", f"atempo={factor:.2f},apad,atrim=0:{dur:.2f}", "-c:a", "aac", out])
        clips.append(out)
        bar.progress(55 + int(40*(idx+1)/max(len(chosen),1)),
                     text=f"⏳ معالجة المشهد {idx+1}/{len(chosen)}" + (" 🔒" if nude else ""))

    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        for cf in clips: f.write(f"file '{cf}'\n")
    final = os.path.join(tmp, "final.mp4")
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", final])
    bar.progress(100, text="🎉 اكتمل!")

    st.success(f"✅ الملخص جاهز — {total/60:.1f} دقيقة بصوت رجالي")
    with open(final, "rb") as f:
        st.download_button("📥 تحميل الفيديو النهائي", f, "ملخص_الفيلم.mp4")
    st.video(final)
    script = "\n".join(f"[{int(c['seg']['start']//60):02d}:{int(c['seg']['start']%60):02d}] {c['ar']}" for c in chosen)
    st.download_button("📥 تحميل السكريبت", script, "السكريبت.txt")
