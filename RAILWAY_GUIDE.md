# دليل التشغيل على Railway

## خطوات الرفع على Railway

### 1. إنشاء حساب Railway
- ادخل على [railway.app](https://railway.app)
- سجل حساب جديد (يمكنك التسجيل بـ GitHub)

### 2. إنشاء مشروع جديد
- اضغط على **"New Project"**
- اختر **"Deploy from GitHub repo"**
- اربط حساب GitHub الخاص بك
- أنشئ repository جديد على GitHub وارفع الملفات إليه:

```bash
cd telegram_bot
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/telegram-bot.git
git push -u origin main
```

### 3. إعداد المتغيرات (Variables)

في Railway Dashboard:
- اذهب لـ **Variables**
- أضف المتغيرات التالية:

| المتغير | القيمة | الوصف |
|---------|--------|-------|
| `BOT_TOKEN` | توكن_البوت | من BotFather |
| `API_ID` | الرقم | من my.telegram.org |
| `API_HASH` | الكود | من my.telegram.org |
| `OWNER_ID` | معرفك | اكتب `/userinfo` في تليجرام |
| `WORKERS` | 50 | عدد العمال |
| `QUEUE_SIZE` | 10000 | حجم الطابور |

### 4. إضافة Persistent Storage (للجلسات)

- اذهب لـ **Settings** في المشروع
- فعّل **"Volume"** أو **"Persistent Storage"**
- المسار: `/app/sessions`
- الحجم: 1GB (يكفي)

### 5. التشغيل

- اضغط على **Deploy**
- Railway سيشغل البوت تلقائياً
- شاهد الـ Logs للتأكد من التشغيل

---

## أوامر البوت

### أول مرة - إعداد البوت:

1. **إضافة حساب جديد:**
```
/addaccount
```
أتبع الخطوات:
- أرسل API ID
- أرسل API HASH  
- أرسل رقم الهاتف (+966XXXXXXXXX)
- أرسل معرف مجموعة الهدف

2. **تشغيل الحساب:**
```
/startacc +966XXXXXXXXX
```
سيرسل البوت كود لرقمك، أدخله:
```
/verify +966XXXXXXXXX 12345
```

3. **إذا طلب 2FA:**
```
/verify2fa +966XXXXXXXXX yourpassword
```

### الأوامر الأساسية:

| الأمر | الوظيفة |
|-------|---------|
| `/accounts` | عرض الحسابات |
| `/startacc <phone>` | تشغيل حساب |
| `/stopacc <phone>` | إيقاف حساب |
| `/delacc <phone>` | حذف حساب |
| `/setmode <phone> <mode>` | تعيين الوضع (forward/reply/both) |
| `/stats` | الإحصائيات |

### إدارة الكلمات:

| الأمر | الوظيفة |
|-------|---------|
| `/keywords` | عرض الكلمات المفتاحية |
| `/addkeyword <word>` | إضافة كلمة |
| `/delkeyword <word>` | حذف كلمة |
| `/triggers` | عرض المحفزات |
| `/addtrigger <phrase>` | إضافة محفز |
| `/replies` | عرض الردود التلقائية |
| `/addreply <message>` | إضافة رد |

### الحماية:

| الأمر | الوظيفة |
|-------|---------|
| `/blocked` | عرض المحظورين |
| `/blockuser <id>` | حظر مستخدم |
| `/unblockuser <id>` | إلغاء حظر |
| `/setthreshold <n>` | عدد الردود قبل الحظر |

### أدوات:

| الأمر | الوظيفة |
|-------|---------|
| `/reload` | إعادة تحميل الإعدادات |
| `/backup` | نسخة احتياطية |
| `/metrics` | مقاييس الأداء |
| `/broadcast <msg>` | إذاعة لجميع المجموعات |

---

## ملاحظات مهمة

### الجلسات والت persistency:
- Railway يمسح الملفات عند إعادة التشغيل
- **الحل**: تم تفعيل Volume Storage
- الجلسات تُحفظ في `/app/sessions/`
- قاعدة البيانات SQLite في `/app/bot_data.db`

### عند مشاكل الاتصال:
1. تأكد من صحة API_ID و API_HASH
2. تأكد أن الرقم صحيح مع رمز الدولة
3. شيك الـ Logs في Railway

### للتحديث:
```bash
git add .
git commit -m "Update"
git push origin main
```
Railway سيعيد التشغيل تلقائياً!

### إيقاف مؤقت:
- في Railway Dashboard اضغط على **"Stop"**

### تكلفة Railway:
- الخطة المجانية: 500 ساعة/شهر + $5 credits
- يكفي لتشغيل بوت واحد طوال الشهر

---

## استكشاف الأخطاء

### البوت لا يستجيب:
1. تأكد من BOT_TOKEN صحيح
2. تأكد من OWNER_ID صحيح
3. شيك الـ Logs

### خطأ في الاتصال:
```
Session not authorized
```
**الحل**: أعد `/startacc` وأدخل الكود من جديد

### خطأ FloodWait:
```
FloodWaitError
```
**الحل**: البوت ينتظر تلقائياً، لا تقلق

### خطأ AuthKeyDuplicated:
```
AuthKeyDuplicatedError
```
**الحل**: الحساب يُستخدم من جهاز آخر. أوقفه ثم أعد التوثيق.
