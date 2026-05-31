# دليل التشغيل على Railway

> **ملاحظة مهمة:** الجلسات تُحفظ تلقائياً في قاعدة البيانات (SQLite) — لا تحتاج أي إعداد إضافي!

---

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

### 3. إعداد المتغيرات (Variables) — الخطوة الأهم!

في Railway Dashboard:
- اذهب لـ **Variables**
- أضف المتغيرات التالية:

| المتغير | القيمة | من أين أجيبها |
|---------|--------|---------------|
| `BOT_TOKEN` | مثال: `123456789:ABCdef...` | من [@BotFather](https://t.me/BotFather) |
| `API_ID` | مثال: `12345678` | من [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | مثال: `a1b2c3d4...` | من [my.telegram.org](https://my.telegram.org) |
| `OWNER_ID` | مثال: `123456789` | من [@userinfobot](https://t.me/userinfobot) |

**اختياري:**
| المتغير | القيمة | الوصف |
|---------|--------|-------|
| `WORKERS` | `50` | عدد العمال (افتراضي: 50) |
| `QUEUE_SIZE` | `10000` | حجم الطابور (افتراضي: 10000) |

### 4. التشغيل

- اضغط **Deploy**
- Railway سيشغل البوت تلقائياً
- شاهد الـ Logs للتأكد من التشغيل ✅

---

## كيف أضيف حساب تليجرام للبوت؟

### أول مرة — إضافة حساب جديد:

**الخطوة 1:** أرسل للبوت:
```
/addaccount
```
البوت يسألك عن API ID → أرسله

**الخطوة 2:** البوت يسألك عن API HASH → أرسله

**الخطوة 3:** البوت يسألك عن رقم الهاتف → أرسله بالصيغة:
```
+966XXXXXXXXX
```

**الخطوة 4:** البوت يسألك عن معرف المجموعة → أرسله أو اكتب `0`

**الخطوة 5:** شغل الحساب:
```
/startacc +966XXXXXXXXX
```

**الخطوة 6:** راح يوصلك كود على تليجرام، أرسله للبوت:
```
/verify +966XXXXXXXXX 12345
```

**إذا طلب 2FA:**
```
/verify2fa +966XXXXXXXXX yourpassword
```

✅ **تم!** الحساب يشتغل والجلسة تُحفظ تلقائياً في قاعدة البيانات!

---

## الأوامر الأساسية

### إدارة الحسابات:
| الأمر | الوظيفة |
|-------|---------|
| `/addaccount` | إضافة حساب جديد (تفاعلي) |
| `/accounts` | عرض الحسابات المسجلة |
| `/startacc <phone>` | تشغيل حساب |
| `/stopacc <phone>` | إيقاف حساب |
| `/delacc <phone>` | حذف حساب |
| `/setmode <phone> <mode>` | forward / reply / both / self |
| `/settarget <phone> <group_id>` | تعيين مجموعة الهدف |

### إدارة الكلمات والردود:
| الأمر | الوظيفة |
|-------|---------|
| `/keywords` | عرض الكلمات المفتاحية |
| `/addkeyword <word>` | إضافة كلمة |
| `/delkeyword <word>` | حذف كلمة |
| `/triggers` | عرض المحفزات |
| `/addtrigger <phrase>` | إضافة محفز |
| `/replies` | عرض الردود التلقائية |
| `/addreply <message>` | إضافة رد |
| `/delreply <message>` | حذف رد |

### الحماية:
| الأمر | الوظيفة |
|-------|---------|
| `/blocked` | عرض المحظورين |
| `/blockuser <user_id>` | حظر مستخدم |
| `/unblockuser <user_id>` | إلغاء حظر |
| `/setthreshold <n>` | عدد الردود قبل الحظر (افتراضي: 4) |

### أدوات:
| الأمر | الوظيفة |
|-------|---------|
| `/stats` | الإحصائيات الكاملة |
| `/metrics` | مقاييس الأداء |
| `/reload` | إعادة تحميل الإعدادات |
| `/backup` | نسخة احتياطية |
| `/broadcast <msg>` | إذاعة لجميع المجموعات |
| `/help` | عرض كل الأوامر |

---

## هل تحتاج Persistent Storage؟

**الجواب: لا!** ❌

البوت يستخدم تقنية `StringSession` — الجلسات تُحفظ كـ نص في قاعدة البيانات SQLite نفسها. هذا يعني:

✅ الجلسات تستمر حتى بعد إعادة التشغيل
✅ لا تحتاج أي إعداد إضافي في Railway
✅ يعمل على أي خطة (مجانية أو مدفوعة)

---

## استكشاف الأخطاء

### البوت لا يستجيب:
- تأكد من `BOT_TOKEN` صحيح
- تأكد من `OWNER_ID` صحيح
- شيك الـ Logs في Railway Dashboard

### "Session not authorized":
**الحل:** أعد `/startacc` و `/verify` من جديد

### "FloodWaitError":
**الحل:** البوت ينتظر تلقائياً — لا تقلق ولا تعيد المحاولة

### "AuthKeyDuplicatedError":
**الحل:** الحساب يُستخدم من جهاز آخر. أوقفه ثم أعد التوثيق.

---

## للتحديث

```bash
git add .
git commit -m "Update"
git push origin main
```

Railway سيعيد التشغيل تلقائياً! 🚀
