#!/usr/bin/env python3
# fix_images.py - جلب وضغط صور النماذج من CDN

import requests
from PIL import Image
from io import BytesIO
import os

# إنشاء مجلد static إذا لم يكن موجوداً
os.makedirs("static", exist_ok=True)

# قائمة الصور: (الاسم المحلي, رابط CDN)
images = [
    ("deepseek.webp", "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/deepseek-color.png"),
    ("mistral.webp", "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/mistral-color.png"),
    ("meta.webp", "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/meta-color.png"),
    ("gemma.webp", "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/gemma-color.png"),
    ("kimi.webp", "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-png@latest/light/kimi-color.png"),
]

print("🚀 جاري تحميل وضغط الصور...")
print("-" * 50)

for filename, url in images:
    try:
        # تحميل الصورة
        print(f"⬇️  تحميل: {filename.replace('.webp', '')}...")
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        # فتح الصورة
        img = Image.open(BytesIO(response.content))

        # تحويل RGBA إلى RGB إذا لزم الأمر (للحفظ بصيغة WebP)
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            if img.mode in ('RGBA', 'LA'):
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # تصغير إلى 84x84 (الحجم الذي يستخدمه PageSpeed)
        img.thumbnail((84, 84), Image.Resampling.LANCZOS)

        # حفظ بصيغة WebP مع جودة عالية
        filepath = os.path.join("static", filename)
        img.save(filepath, "WEBP", quality=85, method=6)

        # حساب التوفير
        original_size = len(response.content)
        new_size = os.path.getsize(filepath)
        saved = (original_size - new_size) / 1024

        print(f"✅ {filename}: {original_size/1024:.1f}KB → {new_size/1024:.1f}KB (وفرنا {saved:.1f}KB)")

    except Exception as e:
        print(f"❌ خطأ في {filename}: {str(e)}")

print("-" * 50)
print("✨ انتهى! الصور جاهزة في مجلد static/")
print("\n🔍 التحقق من الملفات:")
for f in os.listdir("static"):
    if f.endswith(".webp"):
        size = os.path.getsize(os.path.join("static", f)) / 1024
        print(f"   📄 {f}: {size:.1f}KB")
