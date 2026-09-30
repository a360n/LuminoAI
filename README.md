# LuminoAI (تطبيق تقطيع وفحص ألواح الطاقة الشمسية بالذكاء الاصطناعي)

تطبيق ويب محلي تفاعلي مبني باستخدام **Python & FastAPI & PyTorch & Vanilla JS & HTML/CSS** لتقطيع ألواح EL الشمسية بصيغة TIF إلى 144 خلية مربعة (من A1 إلى F24) بحجم 224x224 PNG وفحص العيوب تلقائياً عبر نموذج الذكاء الاصطناعي ومراقبة خط الإنتاج مباشرة.

---

## 💻 طريقة التشغيل على ويندوز (Windows)

### 1️⃣ الاستنساخ من جيت هاب (Git Clone)
افتح **Command Prompt (cmd)** أو **PowerShell** ونفذ الأمر التالي:

```cmd
git clone https://github.com/a360n/LuminoAI.git
cd LuminoAI
```

### 2️⃣ تثبيت المكتبات المطلوبة (Install Dependencies)
```cmd
pip install -r requirements.txt
```

### 3️⃣ تشغيل التطبيق (Run Application)
يمكنك التشغيل بنقرة مزدوجة على الملف `run_LuminoAI.bat` أو تنفيذ الأمر:

```cmd
python -m uvicorn main:app --host 127.0.0.1 --port 8005 --reload
```

ثم افتح المتصفح على العنوان:
👉 **[http://localhost:8005](http://localhost:8005)**

---

## 🍏 طريقة التشغيل على ماك ولينكس (macOS / Linux)

يمكنك التشغيل المباشر بنقرة مزدوجة على ملف **`run_LuminoAI.command`** في الـ Finder، أو عبر الطرفية (Terminal):

```bash
git clone https://github.com/a360n/LuminoAI.git
cd LuminoAI
pip3 install -r requirements.txt
./run_LuminoAI.command
# أو مباشرة:
# python3 -m uvicorn main:app --host 127.0.0.1 --port 8005 --reload
```

---

## ⚙️ ميزات الخوارزمية المطبقة
1. **ارتفاع زوجي**: ضمان أن ارتفاع الصورة عدد زوجي.
2. **نسبة أبعاد 2:1**: قص متساوي من الجوانب أو الأعلى والأسفل للحصول على $MH = 2 \times MW$.
3. **أبعاد الخلايا Base**: $CH = MW / 6$ (عرض الخلية الأفقية) و $CW = MH / 24$ (ارتفاع الخلية).
4. **انعكاس متناظر مكتمل 100%**:
   - `pad_x = (SL - CH) / 2 = 0.15 * CH`
   - `pad_y = (SL - CW) / 2 = 0.80 * CW`
5. **طول ضلغ المربع المقطوع**: $SL = 1.3 \times CH$.
6. **مصفوفة الخلايا 144**: استخراج المربعات من A1 إلى F24 بدون أي اقتطاع أو انحراف.
7. **الحجم المباشر 224x224**: تحويل كل خلية إلى صيغة PNG عالية الجودة.
