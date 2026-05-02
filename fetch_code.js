const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Berlin' });
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

return [{ json: { todayDate: today, dateShort, nhlFixtures, nhlCount, mlbFixtures, mlbCount } }];