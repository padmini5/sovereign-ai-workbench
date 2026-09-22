/* Step 19 UI strings — chrome labels ONLY (12 languages).
   Technical IDs, citations, filenames, doc content stay in English.
   Backend (i18n.py) is the authority for answer translation. */

export const LANGS = [
  ['en', 'English'], ['hi', 'हिन्दी'], ['te', 'తెలుగు'], ['ta', 'தமிழ்'],
  ['kn', 'ಕನ್ನಡ'], ['ml', 'മലയാളം'], ['mr', 'मराठी'], ['bn', 'বাংলা'],
  ['gu', 'ગુજરાતી'], ['pa', 'ਪੰਜਾਬੀ'], ['or', 'ଓଡ଼ିଆ'], ['ur', 'اردو'],
];

const en = { login: 'Login', logout: 'Logout', dashboard: 'Dashboard', chat: 'AI Chat', documents: 'Documents', users: 'User management', audit: 'Audit log', send: 'Send', clear: 'Clear', retry: 'Retry', language: 'Language', mode: 'Mode', general: 'General AI', myDocs: 'My Documents' };

export const STR = {
  en,
  hi: { login: 'लॉगिन', logout: 'लॉगआउट', dashboard: 'डैशबोर्ड', chat: 'AI चैट', documents: 'दस्तावेज़', users: 'उपयोगकर्ता प्रबंधन', audit: 'ऑडिट लॉग', send: 'भेजें', clear: 'साफ़ करें', retry: 'पुनः प्रयास', language: 'भाषा', mode: 'मोड', general: 'सामान्य AI', myDocs: 'मेरे दस्तावेज़' },
  te: { login: 'లాగిన్', logout: 'లాగ్అవుట్', dashboard: 'డాష్‌బోర్డ్', chat: 'AI చాట్', documents: 'పత్రాలు', users: 'వినియోగదారుల నిర్వహణ', audit: 'ఆడిట్ లాగ్', send: 'పంపు', clear: 'క్లియర్', retry: 'మళ్లీ', language: 'భాష', mode: 'మోడ్', general: 'సాధారణ AI', myDocs: 'నా పత్రాలు' },
  ta: { login: 'உள்நுழை', logout: 'வெளியேறு', dashboard: 'டாஷ்போர்டு', chat: 'AI அரட்டை', documents: 'ஆவணங்கள்', users: 'பயனர் நிர்வாகம்', audit: 'தணிக்கை பதிவு', send: 'அனுப்பு', clear: 'அழி', retry: 'மீண்டும்', language: 'மொழி', mode: 'முறை', general: 'பொது AI', myDocs: 'என் ஆவணங்கள்' },
  kn: { login: 'ಲಾಗಿನ್', logout: 'ಲಾಗ್ಔಟ್', dashboard: 'ಡ್ಯಾಶ್‌ಬೋರ್ಡ್', chat: 'AI ಚಾಟ್', documents: 'ದಾಖಲೆಗಳು', users: 'ಬಳಕೆದಾರ ನಿರ್ವಹಣೆ', audit: 'ಆಡಿಟ್ ಲಾಗ್', send: 'ಕಳುಹಿಸಿ', clear: 'ತೆರವುಗೊಳಿಸಿ', retry: 'ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ', language: 'ಭಾಷೆ', mode: 'ಮೋಡ್', general: 'ಸಾಮಾನ್ಯ AI', myDocs: 'ನನ್ನ ದಾಖಲೆಗಳು' },
  ml: { login: 'ലോഗിൻ', logout: 'ലോഗ്ഔട്ട്', dashboard: 'ഡാഷ്ബോർഡ്', chat: 'AI ചാറ്റ്', documents: 'രേഖകൾ', users: 'ഉപയോക്തൃ പരിപാലനം', audit: 'ഓഡിറ്റ് ലോഗ്', send: 'അയയ്ക്കൂ', clear: 'മായ്ക്കൂ', retry: 'വീണ്ടും', language: 'ഭാഷ', mode: 'മോഡ്', general: 'പൊതു AI', myDocs: 'എന്റെ രേഖകൾ' },
  mr: { login: 'लॉगिन', logout: 'लॉगआउट', dashboard: 'डॅशबोर्ड', chat: 'AI चॅट', documents: 'दस्तऐवज', users: 'वापरकर्ता व्यवस्थापन', audit: 'ऑडिट लॉग', send: 'पाठवा', clear: 'साफ करा', retry: 'पुन्हा प्रयत्न', language: 'भाषा', mode: 'मोड', general: 'सामान्य AI', myDocs: 'माझे दस्तऐवज' },
  bn: { login: 'লগইন', logout: 'লগআউট', dashboard: 'ড্যাশবোর্ড', chat: 'AI চ্যাট', documents: 'নথি', users: 'ব্যবহারকারী ব্যবস্থাপনা', audit: 'অডিট লগ', send: 'পাঠান', clear: 'মুছুন', retry: 'আবার', language: 'ভাষা', mode: 'মোড', general: 'সাধারণ AI', myDocs: 'আমার নথি' },
  gu: { login: 'લૉગિન', logout: 'લૉગઆઉટ', dashboard: 'ડેશબોર્ડ', chat: 'AI ચેટ', documents: 'દસ્તાવેજો', users: 'વપરાશકર્તા સંચાલન', audit: 'ઑડિટ લૉગ', send: 'મોકલો', clear: 'સાફ કરો', retry: 'ફરી પ્રયાસ', language: 'ભાષા', mode: 'મોડ', general: 'સામાન્ય AI', myDocs: 'મારા દસ્તાવેજો' },
  pa: { login: 'ਲਾਗਇਨ', logout: 'ਲਾਗਆਉਟ', dashboard: 'ਡੈਸ਼ਬੋਰਡ', chat: 'AI ਚੈਟ', documents: 'ਦਸਤਾਵੇਜ਼', users: 'ਵਰਤੋਂਕਾਰ ਪ੍ਰਬੰਧਨ', audit: 'ਆਡਿਟ ਲੌਗ', send: 'ਭੇਜੋ', clear: 'ਸਾਫ਼ ਕਰੋ', retry: 'ਮੁੜ ਕੋਸ਼ਿਸ਼', language: 'ਭਾਸ਼ਾ', mode: 'ਮੋਡ', general: 'ਆਮ AI', myDocs: 'ਮੇਰੇ ਦਸਤਾਵੇਜ਼' },
  or: { login: 'ଲଗଇନ୍', logout: 'ଲଗଆଉଟ୍', dashboard: 'ଡ୍ୟାସବୋର୍ଡ', chat: 'AI ଚାଟ୍', documents: 'ଦସ୍ତାବିଜ', users: 'ଉପଯୋଗକର୍ତ୍ତା ପରିଚାଳନା', audit: 'ଅଡିଟ୍ ଲଗ୍', send: 'ପଠାନ୍ତୁ', clear: 'ସଫା କରନ୍ତୁ', retry: 'ପୁଣି ଚେଷ୍ଟା', language: 'ଭାଷା', mode: 'ମୋଡ୍', general: 'ସାଧାରଣ AI', myDocs: 'ମୋ ଦସ୍ତାବିଜ' },
  ur: { login: 'لاگ ان', logout: 'لاگ آؤٹ', dashboard: 'ڈیش بورڈ', chat: 'AI چیٹ', documents: 'دستاویزات', users: 'صارفین کا انتظام', audit: 'آڈٹ لاگ', send: 'بھیجیں', clear: 'صاف کریں', retry: 'دوبارہ', language: 'زبان', mode: 'موڈ', general: 'عمومی AI', myDocs: 'میری دستاویزات' },
};

export const t = (lang, key) => (STR[lang] && STR[lang][key]) || en[key] || key;
