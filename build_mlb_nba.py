"""Create MLB and NBA workflows that mirror the NHL Picks architecture.
Prompts embedded VERBATIM from the original .docx files. No amendments."""
import json, os, urllib.request
from pathlib import Path

os.environ['N8N_API_KEY'] = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZTM4ZjI2ZS0xZjM4LTQ1OTQtOTFhYy02ZDdlNjJkZGU3MGQiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwiaWF0IjoxNzY5MjA1NzQ2fQ.r2CCZ2xWDdp_5RsMfXEg6cVodSrowhRh3xyOtFS0REY'

base = Path(r'C:\Users\istva\.claude\CODE\EDGE STACKER')
mlb_prompt = (base / 'MLB_ORIGINAL.txt').read_text(encoding='utf-8')
nba_prompt = (base / 'NBA_ORIGINAL.txt').read_text(encoding='utf-8')

GEMINI_KEY = 'AIzaSyBuRGpli2dZQGrP254hpdztSJWi00zXkKc'
GMAIL_CRED = {'gmailOAuth2': {'id': 'uDKewb6khKgCV7UJ', 'name': 'Gmail account'}}
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent?key={GEMINI_KEY}'

# ============================================================================
# MLB workflow - mirrors NHL structure but fetches MLB schedule + pitchers
# ============================================================================
MLB_FETCH_CODE = r'''const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Berlin' });

let games = [];
try {
  const resp = await this.helpers.httpRequest({ method: 'GET', url: `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${today}&hydrate=probablePitcher,venue` });
  const gameList = resp?.dates?.[0]?.games || [];
  for (const g of gameList) {
    games.push({
      gamePk: g.gamePk,
      home_team: g.teams?.home?.team?.name || 'TBD',
      away_team: g.teams?.away?.team?.name || 'TBD',
      gameDate: g.gameDate,
      gameTimeET: g.gameDate ? new Date(g.gameDate).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York', hour12: true }) : 'TBD',
      venue: g.venue?.name || '',
      homeStarter: g.teams?.home?.probablePitcher?.fullName || 'TBD',
      awayStarter: g.teams?.away?.probablePitcher?.fullName || 'TBD'
    });
  }
} catch(e) { /* continue */ }

const dateShort = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'America/New_York' });
const fixturesBlock = games.map(g => g.away_team + ' (' + g.awayStarter + ') @ ' + g.home_team + ' (' + g.homeStarter + ') | ' + g.gameTimeET + ' ET | ' + g.venue).join('\n');

return [ { json: { fixtureCount: games.length, fixturesBlock: fixturesBlock, todayDate: today, dateShort: dateShort } } ];'''

MLB_BUILD_CODE = r'''const data = $input.first().json;
const fixtureCount = data.fixtureCount;
const fixturesBlock = data.fixturesBlock;
const dateShort = data.dateShort;
const todayDate = data.todayDate;

if (fixtureCount === 0) {
  return [ { json: { requestBody: JSON.stringify({ contents: [{ role: 'user', parts: [{ text: 'Reply with exactly: No MLB games scheduled today.' }] }] }), dateShort: dateShort, fixtureCount: 0 } } ];
}

const systemPrompt = __MLB_PROMPT__;

const userMessage = `TODAY IS ${dateShort} (${todayDate}). Use this exact date in the output header.\n\nAuthoritative fixtures from MLB Stats API (with probable starting pitchers) — ONLY pick from these games:\n\n${fixturesBlock}\n\nUse Google Search to research these games and produce the Top 5 picks per the system prompt's Selection Criteria and Rationale Rules. First line MUST be: MLB Slate — ${dateShort} — Top Plays`;

const requestBody = JSON.stringify({
  systemInstruction: { parts: [{ text: systemPrompt }] },
  contents: [ { role: 'user', parts: [{ text: userMessage }] } ],
  tools: [{ google_search: {} }],
  generationConfig: { temperature: 0.1, maxOutputTokens: 8000 }
});

return [ { json: { requestBody: requestBody, dateShort: dateShort, fixtureCount: fixtureCount } } ];'''

mlb_build_code = MLB_BUILD_CODE.replace('__MLB_PROMPT__', json.dumps(mlb_prompt))

# ============================================================================
# NBA workflow - mirrors NHL but has no public schedule API, so uses Gemini grounding directly
# ============================================================================
NBA_BUILD_CODE = r'''const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Berlin' });
const dateShort = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'America/New_York' });

const systemPrompt = __NBA_PROMPT__;

const userMessage = `TODAY IS ${dateShort} (${today}). Use this exact date in the output header.\n\nUse Google Search to identify today's NBA games and produce the Top 5 picks per the system prompt's Selection Criteria and Rationale Rules. First line MUST be: NBA Slate – ${dateShort} – Top Plays`;

const requestBody = JSON.stringify({
  systemInstruction: { parts: [{ text: systemPrompt }] },
  contents: [ { role: 'user', parts: [{ text: userMessage }] } ],
  tools: [{ google_search: {} }],
  generationConfig: { temperature: 0.1, maxOutputTokens: 8000 }
});

return [ { json: { requestBody: requestBody, dateShort: dateShort } } ];'''

nba_build_code = NBA_BUILD_CODE.replace('__NBA_PROMPT__', json.dumps(nba_prompt))

# ============================================================================
# Gmail message expression (same pattern as NHL workflow)
# ============================================================================
def gmail_message(slate_header):
    # Strip the slate header line (first line) the same way NHL does
    return '={{ (() => { const raw = $json.candidates?.[0]?.content?.parts?.[0]?.text || \'ERROR: No response from Gemini\'; const lines = raw.split(\'\\n\'); const filtered = lines.filter(l => !l.startsWith(\'' + slate_header + '\')).join(\'\\n\').trim(); return \'<div style="font-family: Consolas, Monaco, Courier New, monospace; font-size: 14px; white-space: pre-wrap; line-height: 1.7; color: #111111; padding: 24px; text-align: left;">\' + filtered.replace(/\\n/g, \'<br>\') + \'</div>\'; })() }}'

# ============================================================================
# MLB workflow definition (mirrors NHL structure)
# ============================================================================
mlb_workflow = {
    "name": "MLB Picks – Daily 23:15 CET",
    "nodes": [
        {
            "id": "cron-trigger", "name": "Daily 23:15 CET",
            "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.3,
            "position": [112, 400],
            "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "15 23 * * *"}]}}
        },
        {
            "id": "get-schedule", "name": "Get MLB Schedule",
            "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": [336, 400], "onError": "continueRegularOutput",
            "parameters": {"jsCode": MLB_FETCH_CODE}
        },
        {
            "id": "prepare-queries", "name": "Build Gemini Request",
            "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": [560, 400],
            "parameters": {"jsCode": mlb_build_code}
        },
        {
            "id": "gemini-http", "name": "Gemini 3.1 Pro (Google Search grounded)",
            "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.4,
            "position": [784, 400],
            "parameters": {
                "method": "POST", "url": GEMINI_URL,
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True, "specifyBody": "json",
                "jsonBody": "={{ $json.requestBody }}",
                "options": {"timeout": 600000}
            }
        },
        {
            "id": "send-gmail", "name": "Send Slate",
            "type": "n8n-nodes-base.gmail", "typeVersion": 2.2,
            "position": [1008, 400],
            "webhookId": "mlb-picks-daily-gmail",
            "parameters": {
                "operation": "send",
                "sendTo": "Stuki71.alert@gmail.com",
                "subject": "={{ 'MLB Picks – ' + $now.format('MMMM d, yyyy') }}",
                "message": gmail_message('MLB Slate'),
                "options": {}
            },
            "credentials": GMAIL_CRED
        }
    ],
    "connections": {
        "Daily 23:15 CET": {"main": [[{"node": "Get MLB Schedule", "type": "main", "index": 0}]]},
        "Get MLB Schedule": {"main": [[{"node": "Build Gemini Request", "type": "main", "index": 0}]]},
        "Build Gemini Request": {"main": [[{"node": "Gemini 3.1 Pro (Google Search grounded)", "type": "main", "index": 0}]]},
        "Gemini 3.1 Pro (Google Search grounded)": {"main": [[{"node": "Send Slate", "type": "main", "index": 0}]]}
    },
    "settings": {"executionOrder": "v1", "timezone": "Europe/Zurich", "callerPolicy": "workflowsFromSameOwner"}
}

# ============================================================================
# NBA workflow definition (mirrors NHL structure, no schedule fetch - Gemini finds games)
# ============================================================================
nba_workflow = {
    "name": "NBA Picks – Daily 23:15 CET",
    "nodes": [
        {
            "id": "cron-trigger", "name": "Daily 23:15 CET",
            "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.3,
            "position": [112, 400],
            "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "15 23 * * *"}]}}
        },
        {
            "id": "prepare-queries", "name": "Build Gemini Request",
            "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": [336, 400],
            "parameters": {"jsCode": nba_build_code}
        },
        {
            "id": "gemini-http", "name": "Gemini 3.1 Pro (Google Search grounded)",
            "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.4,
            "position": [560, 400],
            "parameters": {
                "method": "POST", "url": GEMINI_URL,
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True, "specifyBody": "json",
                "jsonBody": "={{ $json.requestBody }}",
                "options": {"timeout": 600000}
            }
        },
        {
            "id": "send-gmail", "name": "Send Slate",
            "type": "n8n-nodes-base.gmail", "typeVersion": 2.2,
            "position": [784, 400],
            "webhookId": "nba-picks-daily-gmail",
            "parameters": {
                "operation": "send",
                "sendTo": "Stuki71.alert@gmail.com",
                "subject": "={{ 'NBA Picks – ' + $now.format('MMMM d, yyyy') }}",
                "message": gmail_message('NBA Slate'),
                "options": {}
            },
            "credentials": GMAIL_CRED
        }
    ],
    "connections": {
        "Daily 23:15 CET": {"main": [[{"node": "Build Gemini Request", "type": "main", "index": 0}]]},
        "Build Gemini Request": {"main": [[{"node": "Gemini 3.1 Pro (Google Search grounded)", "type": "main", "index": 0}]]},
        "Gemini 3.1 Pro (Google Search grounded)": {"main": [[{"node": "Send Slate", "type": "main", "index": 0}]]}
    },
    "settings": {"executionOrder": "v1", "timezone": "Europe/Zurich", "callerPolicy": "workflowsFromSameOwner"}
}

# ============================================================================
# Deploy both via n8n API
# ============================================================================
def create_workflow(wf):
    body = {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"], "settings": wf["settings"]}
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        "http://localhost:5678/api/v1/workflows",
        data=data,
        headers={"Content-Type": "application/json", "X-N8N-API-KEY": os.environ['N8N_API_KEY']},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode('utf-8'))

mlb_result = create_workflow(mlb_workflow)
print(f"MLB workflow created: id={mlb_result['id']}")

nba_result = create_workflow(nba_workflow)
print(f"NBA workflow created: id={nba_result['id']}")

# Save IDs for audit
(base / 'new_workflow_ids.txt').write_text(f"MLB: {mlb_result['id']}\nNBA: {nba_result['id']}\n", encoding='utf-8')
