# إضافة PostgreSQL في Railway — للبيانات الدائمة

> **هذا أهم خطوة!** بدون PostgreSQL، كل بياناتك (الحسابات، الكلمات، الردود) تُحذف عند إعادة التشغيل.

---

## الخطوات بالصور (مفصلة)

### 1️⃣ اذهب لـ Dashboard

افتح مشروعك في Railway:
```
https://railway.app/project/PROJECT-NAME
```

### 2️⃣ أضف PostgreSQL

- في الـ Dashboard، اضغط على **`+ New`**
- اختر **Database**
- اختر **Add PostgreSQL**

![الخطوات](https://i.imgur.com/add-postgres.png)

### 3️⃣ Railway يضيفها تلقائياً

بمجرد الإضافة، Railway يضيف متغير البيئة `DATABASE_URL` تلقائياً لمشروعك!

الرابط يكون شكله كذا:
```
postgresql://postgres:password@containers.railway.app:1234/railway
```

### 4️⃣ تأكد من المتغير

- اذهب لـ **Variables** في مشروعك
- تأكد من وجود `DATABASE_URL`
- لو ما لقيته، اضغط **+ New Variable** → **Add Reference** → اختر الـ PostgreSQL

### 5️⃣ أعد الـ Deploy

- اضغط على الـ Deploy مرة ثانية (أو ادفع commit جديد)
- البوت راح يتصل بـ PostgreSQL تلقائياً!

---

## كيف أعرف أنه شغال؟

شيك الـ Logs، لازم تشوف:
```
Using PostgreSQL database - data is persistent!
```

لو شفت:
```
Using SQLite database (local only - data may be lost on redeploy)
```

معناها PostgreSQL ما تضافت صح. راجع الخطوات فوق.

---

## ملاحظات مهمة

| الميزة | مع PostgreSQL | بدون (SQLite) |
|--------|---------------|---------------|
| البيانات تبقى بعد إعادة التشغيل | ✅ نعم | ❌ تُحذف |
| الحسابات تبقى | ✅ نعم | ❌ تُحذف |
| الجلسات تبقى | ✅ نعم | ❌ تُحذف |
| السرعة | ✅ أسرع | ⚠️ جيدة |
| التكلفة | ✅ مجاني | ✅ مجاني |

### الخطة المجانية في Railway تشمل:
- 500 ساعة تشغيل
- PostgreSQL مجاني
- يكفي لبوت واحد يشتغل طوال الشهر

---

## عند مشاكل

### "Failed to connect to PostgreSQL"
**الحل:**
1. تأكد من إضافة PostgreSQL
2. تأكد من `DATABASE_URL` في Variables
3. أعد الـ Deploy

### البوت يشتغل لكن البيانات تروح
**الحل:** تأكد من رسالة الـ Logs — لازم تقول:
```
Using PostgreSQL database - data is persistent!
```

---

## ملخص

1. ✅ أضف PostgreSQL من Railway Dashboard
2. ✅ تأكد من `DATABASE_URL` في Variables
3. ✅ أعد Deploy
4. ✅ البيانات دائمة!
