const response = $input.item.json;
const srcItem = $('Build 3 Requests').all()[$itemIndex];
const sport = srcItem?.json?.sport || 'UNKNOWN';
const dateShort = srcItem?.json?.dateShort;
const todayDate = srcItem?.json?.todayDate;
const text = response?.candidates?.[0]?.content?.parts?.[0]?.text || ('ERROR: No response from Gemini for ' + sport);
return { json: { sport, dateShort, todayDate, text } };