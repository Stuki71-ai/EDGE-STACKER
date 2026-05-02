"""Build hybrid TOP 5 Daily workflow with structured API pre-fetch + 9 Perplexity + Gemini."""
import json
from pathlib import Path

base = Path(r'C:\Users\istva\.claude\CODE\EDGE STACKER')
nba = (base / 'NBA_clean.txt').read_text(encoding='utf-8')
nhl = (base / 'NHL_amended.txt').read_text(encoding='utf-8')
mlb = (base / 'MLB_amended.txt').read_text(encoding='utf-8')

GEMINI_KEY = 'AIzaSyBuRGpli2dZQGrP254hpdztSJWi00zXkKc'
ODDS_KEY = 'cb4448f677350327c1acf3df30bd363f'
PERPLEXITY_CRED_ID = 'HJ0eLHdmLVRTgURp'
PERPLEXITY_CRED_NAME = 'Perplexity account'
GMAIL_CRED_ID = 'uDKewb6khKgCV7UJ'
GMAIL_CRED_NAME = 'Gmail account'
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent?key={GEMINI_KEY}'

SELECTOR_PROMPT = (base / 'SELECTOR_PROMPT.txt').read_text(encoding='utf-8') if (base / 'SELECTOR_PROMPT.txt').exists() else None

# If selector prompt not saved yet, reconstruct it from previous version
if SELECTOR_PROMPT is None:
    SELECTOR_PROMPT = '''You are the TOP 5 DAILY SELECTOR. Your sole job is to rank betting picks that have ALREADY been produced by three upstream sport-specific handicappers (NBA, NHL, MLB) and output the 5 absolute best picks for today, ordered from highest conviction to lowest.

=== CRITICAL RULES ===

1. DO NOT re-analyze, re-price, or second-guess any pick. The upstream handicappers applied sport-specific expertise you do not have. Your role is ranking only.

2. DO NOT modify the text of the Pick line or the Why line. Copy them verbatim from the upstream output, including the exact team names, lines, odds, and rationale.

3. DO NOT invent picks. If fewer than 5 picks exist across all three inputs combined, output only as many as exist. Never fabricate a pick to reach 5.

4. DO NOT add picks from sports not in the inputs. You receive NBA, NHL, MLB only.

=== SELECTION CRITERIA AND ORDERING ===

Rank the candidate picks by which ones are most likely to win. Use your full reasoning to weigh whatever signals are visible in each pick's rationale - confidence language, number of selection criteria satisfied, line value versus market, bet-type safety, situational edges, or anything else the upstream handicapper flagged as relevant. Sport balance is NOT a criterion; if the 5 best picks are all from one sport, output them all.

MANDATORY ORDERING: Output the picks strictly from highest conviction to lowest. Pick 1 MUST be the single highest-conviction pick of the day. Pick 2 MUST be the second-highest conviction. Pick 5 MUST be the lowest conviction of the five selected. This ordering is not optional.

=== OUTPUT FORMAT (EXACT) ===

Output exactly this structure, nothing else. No preamble, no explanation, no ranking commentary, no markdown headers, no emoji, no date header.

For each selected pick, output exactly 3 lines:
- Line 1: Start with "Pick N:" where N is 1 to 5 reflecting YOUR conviction ranking (Pick 1 = highest conviction, Pick 5 = lowest). Then on the SAME line, add the sport tag in brackets [NBA], [NHL], or [MLB] followed by the matchup and time, with the upstream leading number (e.g. "3.", "4.") stripped. Format exactly: "Pick N: [SPORT] Away Team @ Home Team - Time ET"
- Line 2: Copy the Pick line from upstream verbatim (the line starting with "Pick:").
- Line 3: Copy the Why line from upstream verbatim (the line starting with "Why:").

Separate each 3-line block with exactly ONE blank line. No trailing text.

EXAMPLE of correct format (note picks are ordered from highest to lowest conviction, sport tags are added, upstream numbering is stripped):

Pick 1: [MLB] Kansas City Royals @ Detroit Tigers - 06:40 PM ET
Pick: F5 Under 4.0
Why: Ragans (3.10 ERA, 11.1 K/9) and Valdez (60 GB%) pitch in 45-degree weather with 12 mph winds blowing in at Comerica Park; both bullpens rank top-10 in FIP.

Pick 2: [NHL] Winnipeg Jets @ Utah Mammoth - 09:00 PM ET
Pick: Winnipeg Jets ML - Starter: Connor Hellebuyck vs. Karel Vejmelka (confirmed)
Why: Hellebuyck boasts a .925 SV% and +41.6 GSAx, creating a massive goaltending mismatch. Winnipeg controls 54.2% of 5v5 xGF% over their last 10 games.

(For NHL picks, the Pick line and Starter line from upstream may be separate; combine them with " - " between them so the output is still exactly 3 lines per pick.)

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

Parse each block, extract all individual picks, rank them against each other using your own judgment under the selection criteria above, and output the top 5 in strict order from highest conviction (Pick 1) to lowest (Pick 5).'''

# ============================================================================
# Code: Pre-fetch Structured Data
# ============================================================================
PREFETCH_CODE = r'''const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Berlin' });
const dateShort = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'America/New_York' });
const ODDS_KEY = '__ODDS_KEY__';

function safeTime(iso, tz='America/New_York') {
  try { return new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: tz, hour12: true }); }
  catch(e) { return 'TBD'; }
}
function nhlTeamName(t) {
  if (!t) return 'TBD';
  const place = t.placeName?.default || '';
  const common = t.commonName?.default || t.teamName?.default || '';
  return (place + ' ' + common).trim() || t.abbrev || 'TBD';
}

// Parallel HTTP fetches
const http = (url) => this.helpers.httpRequest({ method: 'GET', url });

const [nhlSched, mlbSched, oddsNhl, oddsMlb, oddsNba] = await Promise.all([
  http(`https://api-web.nhle.com/v1/schedule/${today}`).catch(()=>null),
  http(`https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${today}&hydrate=probablePitcher,venue(location)`).catch(()=>null),
  http(`https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds?regions=us&markets=h2h,spreads,totals&oddsFormat=american&bookmakers=pinnacle,circasports&apiKey=${ODDS_KEY}`).catch(()=>[]),
  http(`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds?regions=us&markets=h2h,spreads,totals&oddsFormat=american&bookmakers=pinnacle,circasports&apiKey=${ODDS_KEY}`).catch(()=>[]),
  http(`https://api.the-odds-api.com/v4/sports/basketball_nba/odds?regions=us&markets=h2h,spreads,totals&oddsFormat=american&bookmakers=pinnacle,circasports&apiKey=${ODDS_KEY}`).catch(()=>[])
]);

// --- Parse NHL ---
let nhlGames = [];
try {
  const weekDates = nhlSched?.gameWeek || [];
  const todayEntry = weekDates.find(d => d.date === today) || weekDates[0];
  for (const g of (todayEntry?.games || [])) {
    nhlGames.push({
      home: nhlTeamName(g.homeTeam),
      away: nhlTeamName(g.awayTeam),
      timeET: g.startTimeUTC ? safeTime(g.startTimeUTC) : 'TBD',
      venue: g.venue?.default || ''
    });
  }
} catch(e) {}

// --- Parse MLB ---
let mlbGames = [];
try {
  const games = mlbSched?.dates?.[0]?.games || [];
  for (const g of games) {
    mlbGames.push({
      home: g.teams?.home?.team?.name || 'TBD',
      away: g.teams?.away?.team?.name || 'TBD',
      timeET: g.gameDate ? safeTime(g.gameDate) : 'TBD',
      venue: g.venue?.name || '',
      venueLat: g.venue?.location?.defaultCoordinates?.latitude,
      venueLon: g.venue?.location?.defaultCoordinates?.longitude,
      roofType: g.venue?.location?.stateAbbrev, // placeholder for roof info
      homeStarter: g.teams?.home?.probablePitcher?.fullName || 'TBD',
      awayStarter: g.teams?.away?.probablePitcher?.fullName || 'TBD'
    });
  }
} catch(e) {}

// --- Parse NBA (from Odds API) ---
let nbaGames = [];
try {
  for (const ev of (oddsNba || [])) {
    nbaGames.push({
      home: ev.home_team,
      away: ev.away_team,
      timeET: ev.commence_time ? safeTime(ev.commence_time) : 'TBD'
    });
  }
} catch(e) {}

// --- Format odds per sport ---
function formatOdds(events, sport) {
  if (!events || !Array.isArray(events)) return 'ODDS UNAVAILABLE';
  return events.map(ev => {
    const match = `${ev.away_team} @ ${ev.home_team}`;
    const lines = [];
    for (const bk of (ev.bookmakers || [])) {
      const bkName = bk.title || bk.key;
      const h2h = bk.markets?.find(m=>m.key==='h2h');
      const spr = bk.markets?.find(m=>m.key==='spreads');
      const tot = bk.markets?.find(m=>m.key==='totals');
      let s = `  ${bkName}:`;
      if (h2h) s += ` ML ${h2h.outcomes.map(o => `${o.name} ${o.price>0?'+':''}${o.price}`).join(' / ')}`;
      if (spr) s += ` | ${sport==='NHL'?'PL':sport==='MLB'?'RL':'Spread'} ${spr.outcomes.map(o=>`${o.name} ${o.point>0?'+':''}${o.point} (${o.price>0?'+':''}${o.price})`).join(' / ')}`;
      if (tot) s += ` | Total ${tot.outcomes.find(o=>o.name==='Over')?.point || '?'} (O ${tot.outcomes.find(o=>o.name==='Over')?.price||'?'} / U ${tot.outcomes.find(o=>o.name==='Under')?.price||'?'})`;
      lines.push(s);
    }
    return match + '\n' + lines.join('\n');
  }).join('\n\n');
}

const nhlOdds = formatOdds(oddsNhl, 'NHL');
const mlbOdds = formatOdds(oddsMlb, 'MLB');
const nbaOdds = formatOdds(oddsNba, 'NBA');

// --- Weather for MLB outdoor venues ---
const DOME_KEYWORDS = ['rogers centre','tropicana','minute maid','globe life','chase field','american family','loandepot','t-mobile park'];
let mlbWeather = '';
try {
  const outdoor = mlbGames.filter(g => g.venueLat && g.venueLon && !DOME_KEYWORDS.some(k => g.venue.toLowerCase().includes(k)));
  const weatherResults = await Promise.all(outdoor.map(g =>
    http(`https://api.open-meteo.com/v1/forecast?latitude=${g.venueLat}&longitude=${g.venueLon}&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m,precipitation_probability&forecast_days=1&temperature_unit=fahrenheit&wind_speed_unit=mph`).catch(()=>null)
  ));
  mlbWeather = outdoor.map((g, i) => {
    const w = weatherResults[i];
    if (!w?.hourly) return `${g.away} @ ${g.home}: weather unavailable`;
    const gameHour = g.timeET ? parseInt(g.timeET.split(':')[0]) + (g.timeET.includes('PM') && !g.timeET.startsWith('12') ? 12 : 0) : 19;
    const idx = Math.min(gameHour, 23);
    const temp = w.hourly.temperature_2m?.[idx];
    const wind = w.hourly.wind_speed_10m?.[idx];
    const windDir = w.hourly.wind_direction_10m?.[idx];
    const hum = w.hourly.relative_humidity_2m?.[idx];
    const precip = w.hourly.precipitation_probability?.[idx];
    return `${g.away} @ ${g.home} (${g.venue}): ${temp}°F, wind ${wind} mph @ ${windDir}°, humidity ${hum}%, precip ${precip}%`;
  }).join('\n');
} catch(e) { mlbWeather = 'Weather fetch error: ' + e.message; }

// --- Format fixtures blocks ---
const nhlFixtures = nhlGames.map(g => `${g.away} @ ${g.home} | ${g.timeET} ET | ${g.venue}`).join('\n') || 'No NHL games.';
const mlbFixtures = mlbGames.map(g => `${g.away} (${g.awayStarter}) @ ${g.home} (${g.homeStarter}) | ${g.timeET} ET | ${g.venue}`).join('\n') || 'No MLB games.';
const nbaFixtures = nbaGames.map(g => `${g.away} @ ${g.home} | ${g.timeET} ET`).join('\n') || 'No NBA games.';

return [{
  json: {
    todayDate: today,
    dateShort,
    nhlCount: nhlGames.length,
    mlbCount: mlbGames.length,
    nbaCount: nbaGames.length,
    nhlFixtures,
    mlbFixtures,
    nbaFixtures,
    nhlOdds,
    mlbOdds,
    nbaOdds,
    mlbWeather
  }
}];'''.replace('__ODDS_KEY__', ODDS_KEY)

# ============================================================================
# 9 Perplexity queries (3 per sport)
# ============================================================================
PERP_QUERIES = {
    'NHL_1_Lines_Movement_Injuries': '''=Today is {{ $('Pre-fetch Structured Data').first().json.dateShort }}. For EACH of today's NHL games listed below, report in a structured per-game format:
1. Current Pinnacle and Circa moneyline, puck line +/-1.5, and total.
2. Opening line vs current line (line movement direction and size).
3. Public bet % and public money % if available.
4. Active injuries - all skaters listed Day-to-Day, Doubtful, IR with average TOI and role (PP1, PK1, top-6 F, top-4 D).
5. Recent deadline acquisitions affecting chemistry.

Games:
{{ $('Pre-fetch Structured Data').first().json.nhlFixtures }}

Provide raw facts only, no analysis or picks. Cite sources.''',

    'NHL_2_Goalies_Form': '''=Today is {{ $('Pre-fetch Structured Data').first().json.dateShort }}. For EACH of today's NHL games, report confirmed goalie information from Daily Faceoff, Rotowire, and NHL Official Injury Report:
1. Confirmed starting goaltender for each team (NAME - confirmed or projected).
2. Each starter's season SV%, GSAx, last-10-starts SV%, home/away splits, record.
3. Workload: starts in last 14 days. Flag any starter on 3+ starts in 5 days (fatigue) or 0 starts in 10+ days (rust).
4. Career record vs today's opponent.
5. Each team's season goals for/against per game and days of rest before today's game.

Games:
{{ $('Pre-fetch Structured Data').first().json.nhlFixtures }}

Raw facts only. Cite sources.''',

    'NHL_3_Analytics': '''=Today is {{ $('Pre-fetch Structured Data').first().json.dateShort }}. For EACH of today's NHL games, pull team analytics from MoneyPuck, HockeyViz, Natural Stat Trick, Dom Luszczyszyn's model, and Evolving Hockey:
1. Each team's 5-on-5 xGF%, CF%, HDCF%, PDO for full season AND last 10 games.
2. PP% and PK% for full season AND last 10.
3. Penalties drawn per game and PIM per game.
4. Score-close 5v5 record and shot share.
5. SU record and puckline cover record at home and on road, season and last 10.
6. Head-to-head season series: result, margin, goaltender used.
7. Referee crew assigned today and their season penalties/game and goals/game averages.

Games:
{{ $('Pre-fetch Structured Data').first().json.nhlFixtures }}

Raw numbers only, no picks. Cite sources.''',

    'MLB_1_Starters_Form': '''=Today is {{ $('Pre-fetch Structured Data').first().json.dateShort }}. For EACH of today's MLB games (shown with probable pitchers below), report from Baseball Savant, FanGraphs, and Rotowire:
1. Confirm the probable starter shown is still scheduled (vs late scratches).
2. Each starter: season ERA, FIP, xFIP, WHIP, K/9, BB/9, HR/9, L/R splits, home/away splits.
3. Last 3 starts: pitch count, IP, ERA, K/BB. Flag starters on short rest (<=4 days), exceeding season-high pitch count, or velocity drop >=1 mph vs season average per Baseball Savant.
4. Career stats vs today's opposing lineup (min. 30 PA sample).

Games (with probable pitchers):
{{ $('Pre-fetch Structured Data').first().json.mlbFixtures }}

Raw facts only. Cite sources.''',

    'MLB_2_Bullpen_Injuries_Umpire': '''=Today is {{ $('Pre-fetch Structured Data').first().json.dateShort }}. For EACH of today's MLB games, report:
1. Bullpen state for each team: relievers who threw 20+ pitches yesterday or 35+ in last 2 days, relievers currently unavailable (IL or rest). Bullpen season ERA/FIP rank.
2. Closer and primary setup man status (available or not, last used date).
3. Lineup cards (posted or projected): note platoon changes, late scratches, any lineup missing 2+ regular starters.
4. Active injuries - all position players on IL or day-to-day with WAR, OPS, lineup position.
5. Home plate umpire assigned today and their career/current-season: runs/game, K rate, BB rate, O/U percentage.

Games:
{{ $('Pre-fetch Structured Data').first().json.mlbFixtures }}

Raw data only. Cite Rotowire, MLB Official Injury Report, UmpScorecards, or similar.''',

    'MLB_3_Lines_Weather_Movement': '''=Today is {{ $('Pre-fetch Structured Data').first().json.dateShort }}. For EACH of today's MLB games, report:
1. Opening and current Pinnacle moneyline, run line -1.5/+1.5, and total. Line movement direction.
2. Public bet % and public money % per Action Network, Sports Insights, or BetQL. Flag reverse line movement (line moves against >=60% public money).
3. Weather at game time for outdoor parks: temperature, wind speed, wind direction relative to home plate, humidity, precipitation probability. Confirm whether stadium is dome/roof-closed.
4. FanGraphs park factor for runs (1-year and 3-year).
5. Head-to-head season series: record, run differential, starter used.
6. Each team's home/road record, OPS, ERA this season and last 30 days.

Games:
{{ $('Pre-fetch Structured Data').first().json.mlbFixtures }}

Raw facts only. Cite sources.''',

    'NBA_1_Games_Lines_Injuries': '''=Today is {{ $('Pre-fetch Structured Data').first().json.dateShort }}. For EACH of today's NBA games (listed below with current Pinnacle/Circa lines), report:
1. Confirm games, tip-off times, and lines. Cross-check opening-to-current movement from Action Network, Sports Insights, BetQL.
2. Public bet % and public money %. Flag reverse line movement (line moves against >=60% public money).
3. Active injuries - all players listed Questionable or Doubtful per NBA Official Injury Report, Rotowire, and ESPN. Note minutes per game and usage rate of any absent starter.
4. Rest days per team, back-to-back status, travel distance, time zone changes.

Games (with current odds):
{{ $('Pre-fetch Structured Data').first().json.nbaFixtures }}

{{ $('Pre-fetch Structured Data').first().json.nbaOdds }}

Raw facts only. Cite sources.''',

    'NBA_2_Ratings_Pace_ATS': '''=Today is {{ $('Pre-fetch Structured Data').first().json.dateShort }}. For EACH of today's NBA games, report from Cleaning The Glass, BasketballReference, NBA.com/Stats, or similar:
1. Each team's offensive rating (ORTG) and defensive rating (DRTG) for last 10 games AND full season.
2. Team pace (possessions per game) both last 10 and season. Flag pace mismatches >=3 possessions/game.
3. Each team's ATS (against the spread) record at home and on the road, season and last 10 games.
4. Each team's record SU and cover %.
5. Projections from Dimers, SportsLine, FanDuel Research, VegasInsider, ScoresAndOdds - note divergence from current Pinnacle line (>=1.5 pts spread or >=3 pts total).

Games:
{{ $('Pre-fetch Structured Data').first().json.nbaFixtures }}

Raw numbers only. Cite sources.''',

    'NBA_3_H2H_Context': '''=Today is {{ $('Pre-fetch Structured Data').first().json.dateShort }}. For EACH of today's NBA games, report:
1. Head-to-head this season: ATS result and margin in prior meetings.
2. Playoff seeding implications: is this an elimination game, do standings matter, rivalry intensity, rest-starters risk for clinched teams.
3. Any "letdown" spots or "look-ahead" spots (following a marquee matchup or preceding one).
4. Notable recent roster changes or rotation shifts.
5. Key individual matchups (star vs star) that drive the game plan.

Games:
{{ $('Pre-fetch Structured Data').first().json.nbaFixtures }}

Raw facts only. Cite sources.''',
}

# ============================================================================
# Build 3 Requests (Code node) - combines ALL pre-fetched data into 3 Gemini requests
# ============================================================================
BUILD_CODE = r'''const pre = $('Pre-fetch Structured Data').first().json;
const dateShort = pre.dateShort;
const todayDate = pre.todayDate;

const NBA_PROMPT = __NBA__;
const NHL_PROMPT = __NHL__;
const MLB_PROMPT = __MLB__;

function getPerp(nodeName) {
  try {
    return $(nodeName).first().json?.choices?.[0]?.message?.content || $(nodeName).first().json?.message?.content || $(nodeName).first().json?.content || JSON.stringify($(nodeName).first().json);
  } catch(e) { return '(PERPLEXITY DATA UNAVAILABLE: ' + e.message + ')'; }
}

const nhlData1 = getPerp('Perplexity NHL 1 Lines Movement Injuries');
const nhlData2 = getPerp('Perplexity NHL 2 Goalies Form');
const nhlData3 = getPerp('Perplexity NHL 3 Analytics');

const mlbData1 = getPerp('Perplexity MLB 1 Starters Form');
const mlbData2 = getPerp('Perplexity MLB 2 Bullpen Injuries Umpire');
const mlbData3 = getPerp('Perplexity MLB 3 Lines Weather Movement');

const nbaData1 = getPerp('Perplexity NBA 1 Games Lines Injuries');
const nbaData2 = getPerp('Perplexity NBA 2 Ratings Pace ATS');
const nbaData3 = getPerp('Perplexity NBA 3 H2H Context');

function makeReq(systemPrompt, userMessage) {
  return JSON.stringify({
    systemInstruction: { parts: [{ text: systemPrompt }] },
    contents: [{ role: 'user', parts: [{ text: userMessage }] }],
    tools: [{ google_search: {} }],
    generationConfig: { temperature: 0.1, maxOutputTokens: 12000 }
  });
}

// ===== NHL user message =====
const nhlUser = pre.nhlCount === 0
  ? `TODAY IS ${dateShort} (${todayDate}). No NHL games are scheduled today per the NHL Stats API. Output exactly: No NHL games scheduled today.`
  : `TODAY IS ${dateShort} (${todayDate}). Use this exact date in the output header.

You have been provided with extensive pre-fetched data below. TRUST this data over your training knowledge. Use Google Search ONLY to fill specific gaps (e.g., stat a pre-fetch did not cover).

=== AUTHORITATIVE FIXTURES (NHL Stats API) ===
${pre.nhlFixtures}

=== CURRENT LINES (The Odds API - Pinnacle/Circa) ===
${pre.nhlOdds}

=== LINES, MOVEMENT, INJURIES (Perplexity sonar-pro) ===
${nhlData1}

=== CONFIRMED GOALIES, SV%/GSAx, FORM (Perplexity sonar-pro) ===
${nhlData2}

=== 5v5 ANALYTICS, SPECIAL TEAMS, REFEREE (Perplexity sonar-pro) ===
${nhlData3}

Produce the Top 5 picks per the system prompt's Selection Criteria and Rationale Rules using the data above. First line MUST be: NHL Slate — ${dateShort} — Top Plays`;

// ===== MLB user message =====
const mlbUser = pre.mlbCount === 0
  ? `TODAY IS ${dateShort} (${todayDate}). No MLB games are scheduled today per the MLB Stats API. Output exactly: No MLB games scheduled today.`
  : `TODAY IS ${dateShort} (${todayDate}). Use this exact date in the output header.

You have been provided with extensive pre-fetched data below. TRUST this data over your training knowledge. Use Google Search ONLY to fill specific gaps.

=== AUTHORITATIVE FIXTURES with CONFIRMED STARTERS (MLB Stats API) ===
${pre.mlbFixtures}

=== CURRENT LINES (The Odds API - Pinnacle/Circa) ===
${pre.mlbOdds}

=== WEATHER AT OUTDOOR STADIUMS (Open-Meteo API) ===
${pre.mlbWeather}

=== STARTER STATS, FORM, L/R SPLITS (Perplexity sonar-pro) ===
${mlbData1}

=== BULLPEN STATE, INJURIES, UMPIRE (Perplexity sonar-pro) ===
${mlbData2}

=== LINES, MOVEMENT, WEATHER CROSS-CHECK, PARK FACTORS (Perplexity sonar-pro) ===
${mlbData3}

Produce the Top 5 picks per the system prompt's Selection Criteria and Rationale Rules using the data above. First line MUST be: MLB Slate — ${dateShort} — Top Plays`;

// ===== NBA user message =====
const nbaUser = pre.nbaCount === 0
  ? `TODAY IS ${dateShort} (${todayDate}). No NBA games found via The Odds API. Output exactly: No NBA games scheduled today.`
  : `TODAY IS ${dateShort} (${todayDate}). Use this exact date in the output header.

You have been provided with extensive pre-fetched data below. TRUST this data over your training knowledge. Use Google Search ONLY to fill specific gaps.

=== GAMES AND CURRENT LINES (The Odds API - Pinnacle/Circa) ===
${pre.nbaFixtures}

${pre.nbaOdds}

=== LINE MOVEMENT, INJURIES, REST/TRAVEL (Perplexity sonar-pro) ===
${nbaData1}

=== ORTG/DRTG LAST 10, PACE, ATS (Perplexity sonar-pro) ===
${nbaData2}

=== H2H, CONTEXT, MATCHUPS (Perplexity sonar-pro) ===
${nbaData3}

Produce the Top 5 picks per the system prompt's Selection Criteria and Rationale Rules using the data above. First line MUST be: NBA Slate – ${dateShort} – Top Plays`;

return [
  { json: { sport: 'NBA', dateShort, todayDate, requestBody: makeReq(NBA_PROMPT, nbaUser) } },
  { json: { sport: 'NHL', dateShort, todayDate, requestBody: makeReq(NHL_PROMPT, nhlUser) } },
  { json: { sport: 'MLB', dateShort, todayDate, requestBody: makeReq(MLB_PROMPT, mlbUser) } }
];'''

build_code = (BUILD_CODE
    .replace('__NBA__', json.dumps(nba))
    .replace('__NHL__', json.dumps(nhl))
    .replace('__MLB__', json.dumps(mlb)))

EXTRACT_CODE = r'''const response = $input.item.json;
const srcItem = $('Build 3 Requests').item;
const sport = srcItem?.json?.sport || 'UNKNOWN';
const dateShort = srcItem?.json?.dateShort;
const todayDate = srcItem?.json?.todayDate;
const text = response?.candidates?.[0]?.content?.parts?.[0]?.text || ('ERROR: No response from Gemini for ' + sport);
return { json: { sport, dateShort, todayDate, text } };'''

SELECTOR_CODE = f'''const all = $input.all();
const findSport = (s) => all.find(i => i.json.sport === s)?.json.text || 'No output.';
const nbaText = findSport('NBA');
const nhlText = findSport('NHL');
const mlbText = findSport('MLB');
const dateShort = all[0]?.json?.dateShort || '';
const todayDate = all[0]?.json?.todayDate || '';

const SELECTOR_PROMPT = {json.dumps(SELECTOR_PROMPT)};

const userMessage = `--- NBA PICKS ---\\n${{nbaText}}\\n\\n--- NHL PICKS ---\\n${{nhlText}}\\n\\n--- MLB PICKS ---\\n${{mlbText}}`;

const requestBody = JSON.stringify({{
  systemInstruction: {{ parts: [{{ text: SELECTOR_PROMPT }}] }},
  contents: [{{ role: 'user', parts: [{{ text: userMessage }}] }}],
  generationConfig: {{ temperature: 0.1, maxOutputTokens: 24000 }}
}});

return [{{ json: {{ requestBody, dateShort, todayDate, nbaText, nhlText, mlbText }} }}];'''

# ============================================================================
# Build workflow nodes
# ============================================================================
def perp_node(id_, name, x, y, prompt):
    return {
        "id": id_,
        "name": name,
        "type": "n8n-nodes-base.perplexity",
        "typeVersion": 1,
        "position": [x, y],
        "parameters": {
            "model": "sonar-pro",
            "messages": {
                "message": [
                    {"role": "user", "content": prompt}
                ]
            },
            "options": {
                "searchRecency": "day",
                "temperature": 0.1
            },
            "requestOptions": {}
        },
        "credentials": {"perplexityApi": {"id": PERPLEXITY_CRED_ID, "name": PERPLEXITY_CRED_NAME}}
    }

nodes = [
    # Trigger
    {"id": "trigger", "name": "Daily 23:30 CET", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.3, "position": [100, 600], "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "30 23 * * *"}]}}},

    # Pre-fetch
    {"id": "prefetch", "name": "Pre-fetch Structured Data", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [320, 600], "onError": "continueRegularOutput", "parameters": {"jsCode": PREFETCH_CODE}},

    # 3 NHL Perplexity nodes (serial chain within branch)
    perp_node("p-nhl-1", "Perplexity NHL 1 Lines Movement Injuries", 540, 300, PERP_QUERIES['NHL_1_Lines_Movement_Injuries']),
    perp_node("p-nhl-2", "Perplexity NHL 2 Goalies Form", 760, 300, PERP_QUERIES['NHL_2_Goalies_Form']),
    perp_node("p-nhl-3", "Perplexity NHL 3 Analytics", 980, 300, PERP_QUERIES['NHL_3_Analytics']),

    # 3 MLB Perplexity nodes (serial within branch)
    perp_node("p-mlb-1", "Perplexity MLB 1 Starters Form", 540, 600, PERP_QUERIES['MLB_1_Starters_Form']),
    perp_node("p-mlb-2", "Perplexity MLB 2 Bullpen Injuries Umpire", 760, 600, PERP_QUERIES['MLB_2_Bullpen_Injuries_Umpire']),
    perp_node("p-mlb-3", "Perplexity MLB 3 Lines Weather Movement", 980, 600, PERP_QUERIES['MLB_3_Lines_Weather_Movement']),

    # 3 NBA Perplexity nodes
    perp_node("p-nba-1", "Perplexity NBA 1 Games Lines Injuries", 540, 900, PERP_QUERIES['NBA_1_Games_Lines_Injuries']),
    perp_node("p-nba-2", "Perplexity NBA 2 Ratings Pace ATS", 760, 900, PERP_QUERIES['NBA_2_Ratings_Pace_ATS']),
    perp_node("p-nba-3", "Perplexity NBA 3 H2H Context", 980, 900, PERP_QUERIES['NBA_3_H2H_Context']),

    # Merge 1: NHL end + MLB end
    {"id": "merge1", "name": "Merge NHL MLB", "type": "n8n-nodes-base.merge", "typeVersion": 3.2, "position": [1200, 450], "parameters": {"mode": "combine", "combineBy": "combineByPosition"}},
    # Merge 2: Merge1 + NBA end
    {"id": "merge2", "name": "Merge All", "type": "n8n-nodes-base.merge", "typeVersion": 3.2, "position": [1420, 600], "parameters": {"mode": "combine", "combineBy": "combineByPosition"}},

    # Build 3 Requests
    {"id": "build", "name": "Build 3 Requests", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [1640, 600], "parameters": {"jsCode": build_code}},

    # Gemini Sport Handicapper
    {"id": "gemini-sport", "name": "Gemini Sport Handicapper", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.4, "position": [1860, 600], "parameters": {"method": "POST", "url": GEMINI_URL, "sendHeaders": True, "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]}, "sendBody": True, "specifyBody": "json", "jsonBody": "={{ $json.requestBody }}", "options": {"timeout": 600000}}},

    # Extract
    {"id": "extract", "name": "Extract Sport Output", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [2080, 600], "parameters": {"mode": "runOnceForEachItem", "jsCode": EXTRACT_CODE}},

    # Build Selector Request
    {"id": "build-sel", "name": "Build Selector Request", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [2300, 600], "parameters": {"jsCode": SELECTOR_CODE}},

    # Gemini Selector
    {"id": "gemini-sel", "name": "Gemini Selector", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.4, "position": [2520, 600], "parameters": {"method": "POST", "url": GEMINI_URL, "sendHeaders": True, "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]}, "sendBody": True, "specifyBody": "json", "jsonBody": "={{ $json.requestBody }}", "options": {"timeout": 600000}}},

    # Gmail
    {"id": "gmail", "name": "Send TOP 5", "type": "n8n-nodes-base.gmail", "typeVersion": 2.2, "position": [2740, 600], "webhookId": "top5-daily-gmail-v2", "parameters": {"operation": "send", "sendTo": "Stuki71.alert@gmail.com", "subject": "={{ 'TOP 5 – ' + $now.format('MMMM d, yyyy') }}", "message": "={{ (() => { const raw = $json.candidates?.[0]?.content?.parts?.[0]?.text || 'ERROR: No response from Gemini Selector'; return '<div style=\"font-family: Consolas, Monaco, Courier New, monospace; font-size: 14px; white-space: pre-wrap; line-height: 1.7; color: #111111; padding: 24px; text-align: left;\">' + raw.trim().replace(/\\n/g, '<br>') + '</div>'; })() }}", "options": {}}, "credentials": {"gmailOAuth2": {"id": GMAIL_CRED_ID, "name": GMAIL_CRED_NAME}}}
]

connections = {
    "Daily 23:30 CET": {"main": [[{"node": "Pre-fetch Structured Data", "type": "main", "index": 0}]]},
    "Pre-fetch Structured Data": {"main": [[
        {"node": "Perplexity NHL 1 Lines Movement Injuries", "type": "main", "index": 0},
        {"node": "Perplexity MLB 1 Starters Form", "type": "main", "index": 0},
        {"node": "Perplexity NBA 1 Games Lines Injuries", "type": "main", "index": 0}
    ]]},
    "Perplexity NHL 1 Lines Movement Injuries": {"main": [[{"node": "Perplexity NHL 2 Goalies Form", "type": "main", "index": 0}]]},
    "Perplexity NHL 2 Goalies Form": {"main": [[{"node": "Perplexity NHL 3 Analytics", "type": "main", "index": 0}]]},
    "Perplexity NHL 3 Analytics": {"main": [[{"node": "Merge NHL MLB", "type": "main", "index": 0}]]},
    "Perplexity MLB 1 Starters Form": {"main": [[{"node": "Perplexity MLB 2 Bullpen Injuries Umpire", "type": "main", "index": 0}]]},
    "Perplexity MLB 2 Bullpen Injuries Umpire": {"main": [[{"node": "Perplexity MLB 3 Lines Weather Movement", "type": "main", "index": 0}]]},
    "Perplexity MLB 3 Lines Weather Movement": {"main": [[{"node": "Merge NHL MLB", "type": "main", "index": 1}]]},
    "Perplexity NBA 1 Games Lines Injuries": {"main": [[{"node": "Perplexity NBA 2 Ratings Pace ATS", "type": "main", "index": 0}]]},
    "Perplexity NBA 2 Ratings Pace ATS": {"main": [[{"node": "Perplexity NBA 3 H2H Context", "type": "main", "index": 0}]]},
    "Perplexity NBA 3 H2H Context": {"main": [[{"node": "Merge All", "type": "main", "index": 1}]]},
    "Merge NHL MLB": {"main": [[{"node": "Merge All", "type": "main", "index": 0}]]},
    "Merge All": {"main": [[{"node": "Build 3 Requests", "type": "main", "index": 0}]]},
    "Build 3 Requests": {"main": [[{"node": "Gemini Sport Handicapper", "type": "main", "index": 0}]]},
    "Gemini Sport Handicapper": {"main": [[{"node": "Extract Sport Output", "type": "main", "index": 0}]]},
    "Extract Sport Output": {"main": [[{"node": "Build Selector Request", "type": "main", "index": 0}]]},
    "Build Selector Request": {"main": [[{"node": "Gemini Selector", "type": "main", "index": 0}]]},
    "Gemini Selector": {"main": [[{"node": "Send TOP 5", "type": "main", "index": 0}]]}
}

workflow = {
    "name": "TOP 5 – Daily Hybrid (23:30 CET)",
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v1", "timezone": "Europe/Zurich", "callerPolicy": "workflowsFromSameOwner"}
}

out = base / 'workflow_hybrid_payload.json'
out.write_text(json.dumps(workflow, ensure_ascii=False), encoding='utf-8')
# Save selector prompt so we don't lose it
(base / 'SELECTOR_PROMPT.txt').write_text(SELECTOR_PROMPT, encoding='utf-8')
print(f'Payload written: {out.stat().st_size} bytes, {len(nodes)} nodes')
for n in nodes:
    print(f'  - {n["name"]}')
