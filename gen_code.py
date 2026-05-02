import json
from pathlib import Path

base = Path(r'C:\Users\istva\.claude\CODE\EDGE STACKER')
nba = (base / 'NBA_clean.txt').read_text(encoding='utf-8')
nhl = (base / 'NHL_clean.txt').read_text(encoding='utf-8')
mlb = (base / 'MLB_clean.txt').read_text(encoding='utf-8')

# Verify no problematic chars
for name, text in [('NBA', nba), ('NHL', nhl), ('MLB', mlb)]:
    if '`' in text:
        print(f'WARNING: backtick in {name}')
    if '${' in text:
        print(f'WARNING: ${{}} in {name}')

selector = '''You are the TOP 5 DAILY SELECTOR. Your sole job is to rank betting picks that have ALREADY been produced by three upstream sport-specific handicappers (NBA, NHL, MLB) and output the 5 absolute best picks for today.

=== CRITICAL RULES ===

1. DO NOT re-analyze, re-price, or second-guess any pick. The upstream handicappers applied sport-specific expertise you do not have. Your role is ranking only.

2. DO NOT modify the text of any pick you select. Copy the 3-line block verbatim from the upstream output, including the exact team names, lines, odds, and rationale.

3. DO NOT invent picks. If fewer than 5 picks exist across all three inputs combined, output only as many as exist. Never fabricate a pick to reach 5.

4. DO NOT add picks from sports not in the inputs. You receive NBA, NHL, MLB only.

=== SELECTION CRITERIA ===

Rank the candidate picks by which ones are most likely to win. Use your full reasoning to weigh whatever signals are visible in each pick's rationale - confidence language, number of selection criteria satisfied, line value versus market, bet-type safety, situational edges, or anything else the upstream handicapper flagged as relevant. Sport balance is NOT a criterion; if the 5 best picks are all from one sport, output them all.

=== OUTPUT FORMAT (EXACT) ===

Output exactly this structure, nothing else. No preamble, no explanation, no ranking commentary, no markdown headers, no emoji, no date header.

Pick 1:
[LINE 1 from upstream - verbatim]
[LINE 2 from upstream - verbatim]
[LINE 3 from upstream - verbatim]

Pick 2:
[LINE 1 from upstream - verbatim]
[LINE 2 from upstream - verbatim]
[LINE 3 from upstream - verbatim]

Pick 3:
[... same pattern ...]

Pick 4:
[... same pattern ...]

Pick 5:
[... same pattern ...]

Separate each pick block with exactly ONE blank line. No trailing text.

=== IF NO PICKS EXIST ===

If all three upstream outputs contain zero picks (e.g., no games scheduled, all games failed criteria), output only this single line:

No qualifying picks today across NBA, NHL, MLB.

=== INPUT FORMAT ===

You will receive three blocks in the user message, clearly labeled:

--- NBA PICKS ---
[raw NBA handicapper output]

--- NHL PICKS ---
[raw NHL handicapper output]

--- MLB PICKS ---
[raw MLB handicapper output]

Parse each block, extract all individual picks, rank them against each other using your own judgment under the selection criteria above, and output the top 5.'''

fetch_code = r"""const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Berlin' });
const dateShort = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'America/New_York' });

function nhlTeam(t) {
  if (!t) return 'TBD';
  const place = t.placeName?.default || '';
  const common = t.commonName?.default || t.teamName?.default || '';
  const full = (place + ' ' + common).trim();
  return full || t.abbrev || 'TBD';
}

let nhlFixtures = '';
let nhlCount = 0;
try {
  const resp = await this.helpers.httpRequest({ method: 'GET', url: `https://api-web.nhle.com/v1/schedule/${today}` });
  const weekDates = resp.gameWeek || [];
  const todayEntry = weekDates.find(d => d.date === today) || weekDates[0];
  const games = todayEntry?.games || [];
  nhlCount = games.length;
  nhlFixtures = games.map(g => {
    const home = nhlTeam(g.homeTeam);
    const away = nhlTeam(g.awayTeam);
    const t = g.startTimeUTC ? new Date(g.startTimeUTC).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', hour12: true }) : 'TBD';
    const v = g.venue?.default || '';
    return away + ' @ ' + home + ' | ' + t + ' ET | ' + v;
  }).join('\n');
} catch(e) { /* continue */ }

let mlbFixtures = '';
let mlbCount = 0;
try {
  const resp = await this.helpers.httpRequest({ method: 'GET', url: `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${today}&hydrate=probablePitcher,venue` });
  const games = (resp.dates?.[0]?.games) || [];
  mlbCount = games.length;
  mlbFixtures = games.map(g => {
    const home = g.teams?.home?.team?.name || 'TBD';
    const away = g.teams?.away?.team?.name || 'TBD';
    const t = g.gameDate ? new Date(g.gameDate).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', hour12: true }) : 'TBD';
    const v = g.venue?.name || '';
    const hp = g.teams?.home?.probablePitcher?.fullName || 'TBD';
    const ap = g.teams?.away?.probablePitcher?.fullName || 'TBD';
    return away + ' (' + ap + ') @ ' + home + ' (' + hp + ') | ' + t + ' ET | ' + v;
  }).join('\n');
} catch(e) { /* continue */ }

return [{ json: { todayDate: today, dateShort, nhlFixtures, nhlCount, mlbFixtures, mlbCount } }];"""

# Build Build-3-Requests code with prompts embedded as JSON-escaped strings
build_code_template = r"""const d = $input.first().json;
const dateShort = d.dateShort;
const todayDate = d.todayDate;

const NBA_PROMPT = __NBA__;
const NHL_PROMPT = __NHL__;
const MLB_PROMPT = __MLB__;

function makeReq(systemPrompt, userMessage) {
  return JSON.stringify({
    systemInstruction: { parts: [{ text: systemPrompt }] },
    contents: [{ role: 'user', parts: [{ text: userMessage }] }],
    tools: [{ google_search: {} }],
    generationConfig: { temperature: 0.1, maxOutputTokens: 8000 }
  });
}

const nbaUser = `TODAY IS ${dateShort} (${todayDate}). Use this exact date in the output header.\n\nUse Google Search to identify today's NBA games and produce the Top 5 picks per the system prompt's Selection Criteria and Rationale Rules. First line MUST be: NBA Slate – ${dateShort} – Top Plays`;

const nhlFixturesBlock = d.nhlCount > 0
  ? `Authoritative fixtures from NHL Stats API — ONLY pick from these games:\n\n${d.nhlFixtures}\n\n`
  : '';
const nhlUser = d.nhlCount === 0
  ? `TODAY IS ${dateShort} (${todayDate}). No NHL games are scheduled today per the NHL Stats API. Output exactly: No NHL games scheduled today.`
  : `TODAY IS ${dateShort} (${todayDate}). Use this exact date in the output header.\n\n${nhlFixturesBlock}Use Google Search to research these games and produce the Top 5 picks per the system prompt's Selection Criteria and Rationale Rules. First line MUST be: NHL Slate — ${dateShort} — Top Plays`;

const mlbFixturesBlock = d.mlbCount > 0
  ? `Authoritative fixtures from MLB Stats API — ONLY pick from these games:\n\n${d.mlbFixtures}\n\n`
  : '';
const mlbUser = d.mlbCount === 0
  ? `TODAY IS ${dateShort} (${todayDate}). No MLB games are scheduled today per the MLB Stats API. Output exactly: No MLB games scheduled today.`
  : `TODAY IS ${dateShort} (${todayDate}). Use this exact date in the output header.\n\n${mlbFixturesBlock}Use Google Search to research these games and produce the Top 5 picks per the system prompt's Selection Criteria and Rationale Rules. First line MUST be: MLB Slate — ${dateShort} — Top Plays`;

return [
  { json: { sport: 'NBA', dateShort, todayDate, requestBody: makeReq(NBA_PROMPT, nbaUser) } },
  { json: { sport: 'NHL', dateShort, todayDate, requestBody: makeReq(NHL_PROMPT, nhlUser) } },
  { json: { sport: 'MLB', dateShort, todayDate, requestBody: makeReq(MLB_PROMPT, mlbUser) } }
];"""

build_code = (build_code_template
    .replace('__NBA__', json.dumps(nba))
    .replace('__NHL__', json.dumps(nhl))
    .replace('__MLB__', json.dumps(mlb)))

extract_code = r"""const response = $input.item.json;
const srcItem = $('Build 3 Requests').all()[$itemIndex];
const sport = srcItem?.json?.sport || 'UNKNOWN';
const dateShort = srcItem?.json?.dateShort;
const todayDate = srcItem?.json?.todayDate;
const text = response?.candidates?.[0]?.content?.parts?.[0]?.text || ('ERROR: No response from Gemini for ' + sport);
return { json: { sport, dateShort, todayDate, text } };"""

selector_code_template = r"""const all = $input.all();
const findSport = (s) => all.find(i => i.json.sport === s)?.json.text || 'No output.';
const nbaText = findSport('NBA');
const nhlText = findSport('NHL');
const mlbText = findSport('MLB');
const dateShort = all[0]?.json?.dateShort || '';
const todayDate = all[0]?.json?.todayDate || '';

const SELECTOR_PROMPT = __SELECTOR__;

const userMessage = `--- NBA PICKS ---\n${nbaText}\n\n--- NHL PICKS ---\n${nhlText}\n\n--- MLB PICKS ---\n${mlbText}`;

const requestBody = JSON.stringify({
  systemInstruction: { parts: [{ text: SELECTOR_PROMPT }] },
  contents: [{ role: 'user', parts: [{ text: userMessage }] }],
  generationConfig: { temperature: 0.1, maxOutputTokens: 4000 }
});

return [{ json: { requestBody, dateShort, todayDate, nbaText, nhlText, mlbText } }];"""

selector_code = selector_code_template.replace('__SELECTOR__', json.dumps(selector))

# Save all
(base / 'fetch_code.js').write_text(fetch_code, encoding='utf-8')
(base / 'build_code.js').write_text(build_code, encoding='utf-8')
(base / 'extract_code.js').write_text(extract_code, encoding='utf-8')
(base / 'selector_code.js').write_text(selector_code, encoding='utf-8')

# Build workflow JSON payload
workflow = {
    "name": "TOP 5 – Daily (23:30 CET)",
    "nodes": [
        {
            "id": "trigger",
            "name": "Daily 23:30 CET",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.3,
            "position": [100, 400],
            "parameters": {
                "rule": {
                    "interval": [{"field": "cronExpression", "expression": "30 23 * * *"}]
                }
            }
        },
        {
            "id": "fetch",
            "name": "Fetch Schedules",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [320, 400],
            "onError": "continueRegularOutput",
            "parameters": {"jsCode": fetch_code}
        },
        {
            "id": "build",
            "name": "Build 3 Requests",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [540, 400],
            "parameters": {"jsCode": build_code}
        },
        {
            "id": "gemini-sport",
            "name": "Gemini Sport Handicapper",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.4,
            "position": [760, 400],
            "parameters": {
                "method": "POST",
                "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent?key=AIzaSyBuRGpli2dZQGrP254hpdztSJWi00zXkKc",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ $json.requestBody }}",
                "options": {"timeout": 600000}
            }
        },
        {
            "id": "extract",
            "name": "Extract Sport Output",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [980, 400],
            "parameters": {
                "mode": "runOnceForEachItem",
                "jsCode": extract_code
            }
        },
        {
            "id": "build-selector",
            "name": "Build Selector Request",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1200, 400],
            "parameters": {"jsCode": selector_code}
        },
        {
            "id": "gemini-selector",
            "name": "Gemini Selector",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.4,
            "position": [1420, 400],
            "parameters": {
                "method": "POST",
                "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent?key=AIzaSyBuRGpli2dZQGrP254hpdztSJWi00zXkKc",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ $json.requestBody }}",
                "options": {"timeout": 600000}
            }
        },
        {
            "id": "gmail",
            "name": "Send TOP 5",
            "type": "n8n-nodes-base.gmail",
            "typeVersion": 2.2,
            "position": [1640, 400],
            "webhookId": "top5-daily-gmail",
            "parameters": {
                "sendTo": "Stuki71.alert@gmail.com",
                "subject": "={{ 'TOP 5 – ' + $now.format('MMMM d, yyyy') }}",
                "message": "={{ (() => { const raw = $json.candidates?.[0]?.content?.parts?.[0]?.text || 'ERROR: No response from Gemini Selector'; return '<div style=\"font-family: Consolas, Monaco, Courier New, monospace; font-size: 14px; white-space: pre-wrap; line-height: 1.7; color: #111111; padding: 24px; text-align: left;\">' + raw.trim().replace(/\\n/g, '<br>') + '</div>'; })() }}",
                "options": {}
            },
            "credentials": {
                "gmailOAuth2": {"id": "uDKewb6khKgCV7UJ", "name": "Gmail account"}
            }
        }
    ],
    "connections": {
        "Daily 23:30 CET": {"main": [[{"node": "Fetch Schedules", "type": "main", "index": 0}]]},
        "Fetch Schedules": {"main": [[{"node": "Build 3 Requests", "type": "main", "index": 0}]]},
        "Build 3 Requests": {"main": [[{"node": "Gemini Sport Handicapper", "type": "main", "index": 0}]]},
        "Gemini Sport Handicapper": {"main": [[{"node": "Extract Sport Output", "type": "main", "index": 0}]]},
        "Extract Sport Output": {"main": [[{"node": "Build Selector Request", "type": "main", "index": 0}]]},
        "Build Selector Request": {"main": [[{"node": "Gemini Selector", "type": "main", "index": 0}]]},
        "Gemini Selector": {"main": [[{"node": "Send TOP 5", "type": "main", "index": 0}]]}
    },
    "settings": {
        "executionOrder": "v1",
        "timezone": "Europe/Zurich",
        "callerPolicy": "workflowsFromSameOwner"
    }
}

(base / 'workflow_payload.json').write_text(json.dumps(workflow, ensure_ascii=False), encoding='utf-8')
print(f'Workflow payload written. Total size: {len((base / "workflow_payload.json").read_text(encoding="utf-8"))} chars')
print(f'build_code size: {len(build_code)} chars')
