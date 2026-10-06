# AIM Lag — Free Fire MAX / Android 10 fix

## সমস্যাটি কী ছিল

মূল APK-টির Android VPN bridge-এ target app হিসেবে শুধু `com.dts.freefireth` (সাধারণ Free Fire) hard-code করা ছিল। কিন্তু **Free Fire MAX**-এর package ID হলো `com.dts.freefiremax`।

তাই Free Fire MAX-ই শুধু ইনস্টল থাকলে অ্যাপটি target পাওয়া যায়নি ধরে নেয়। `Start` flow-টি `VpnService.prepare()` পর্যন্তই পৌঁছাত না—সেই কারণেই Android-এর VPN permission dialog আসত না। এটি ফোনের VPN setting-এর সমস্যা নয়।

## কী পরিবর্তন করা হয়েছে

প্রথম patch approach-এ এক অক্ষর বড় package ID বসাতে গিয়ে DEX string-ID order নষ্ট হওয়ার ঝুঁকি ছিল; Android 10 এটিকে process crash হিসেবে দেখাতে পারে। বর্তমান build-এ DEX layout অপরিবর্তিত রেখে নিরাপদে দুইটি call বাদ দেওয়া হয়েছে:

1. standard Free Fire package না থাকলে যে target-check ব্যর্থ হতো, সেটি আর Start আটকে রাখে না;
2. অনুপস্থিত standard Free Fire-এর জন্য VPN allow-list তৈরির call বাদ দেওয়া হয়েছে, যাতে `NameNotFoundException` না হয়।

ফলে Start চাপলে Android-এর স্বাভাবিক VPN permission dialog আসবে এবং Free Fire MAX চলতে পারবে। এই build চলার সময় VPN-টি app-specific না হয়ে device-wide হতে পারে; দরকার না থাকলে Stop চাপুন। Free Fire MAX নিজে থেকে launch নাও হতে পারে—VPN allow করার পরে সেটি হাতে করে খুলুন। লাইসেন্স/লগইন যাচাই পরিবর্তন করা হয়নি।

## ইনস্টল করার নিয়ম (Xiaomi / Android 10)

> এই build নতুন signing certificate দিয়ে sign করা। তাই পুরনো AIM Lag APK **আগে uninstall** না করলে Android `App not installed` বা signature-conflict দেখাবে। একই সঙ্গে পুরনো app data মুছে যাবে; বৈধ key থাকলে আবার লগইন করতে হবে।

1. পুরনো AIM Lag app uninstall করুন।
2. `aimlag-freefiremax-android10.apk` install করুন। প্রয়োজন হলে যে Files/Browser app দিয়ে install করছেন তার জন্য **Install unknown apps** allow করুন।
3. App খুলে বৈধ account/key দিয়ে লগইন করুন এবং **Start** চাপুন। এবার Android-এর VPN permission dialog-এ **Allow/OK** দিন।
4. MIUI-তে স্থিতিশীল রাখার জন্য: **Settings → Apps → Manage apps → AimLag → Battery saver → No restrictions** দিন। Autostart allow করলেও app background-এ বন্ধ হওয়ার সম্ভাবনা কমে।
5. যদি অন্য কোনো VPN চালু থাকে, আগে সেটি disconnect/remove করুন; Android এক সময়ে একটি active VPN রাখে।

VPN prompt আসার পরও যদি connection সঙ্গে সঙ্গে বন্ধ হয়ে যায়, app-এর Logs/Status-এর error text এবং ফোনের MIUI version দিতে হবে—সেটি target-package সমস্যার বাইরে আলাদা runtime error হবে।

## Build / verification

মূল source project দেওয়া ছিল না, কেবল signed APK দেওয়া ছিল। তাই `tools/patch_freefiremax_apk.py` একটি reproducible binary patch করে এবং Android 10 sideload-এর উপযোগী APK v1 signature বানায়:

```bash
python3 tools/patch_freefiremax_apk.py aimlag.apk aimlag-freefiremax-android10.apk
```

স্ক্রিপ্টটির জন্য `python3` এবং `openssl` প্রয়োজন। এটি DEX checksum/SHA-1 আবার হিসাব করে; generated APK-এর ZIP, DEX এবং v1 signature যাচাই করা হয়েছে।
