/* League History & Hall of Shame — client-side data loading + roast engine.
 * Reads static JSON produced by scripts/fetch_espn_data.py (see /docs/data).
 * No build step: this just fetches JSON relative to the page and renders it.
 */

const DATA_DIR = "data";

/* ---------------- roast copy banks ---------------- */
/* Crude, trash-talk tone on purpose -- this is a private site for friends
 * who fully expect to get clowned. Keep it playful-mean, not genuinely
 * hateful: no slurs, nothing about protected traits, just fantasy football
 * incompetence getting roasted the way it deserves. */

const ROASTS = {
  worstRecord: [
    "{owner} finished {record}. A wet paper bag has a better win rate.",
    "{owner} went {record}. At some point 'bad luck' becomes 'bad at this'.",
    "{owner} posted a {record} season. Congrats on the daily reminder from ESPN that you suck.",
  ],
  mostBlownOut: [
    "{owner} got demolished {margin} points in a single week by {opponent}. Hope the popcorn was good watching that massacre.",
    "{owner} lost by {margin} to {opponent}. That's not a fantasy loss, that's a restraining-order-worthy beatdown.",
    "{owner} got run over by {opponent}, {margin}-point margin. Somebody call an ambulance, that lineup needs one.",
  ],
  closestHeartbreak: [
    "{owner} lost to {opponent} by a soul-crushing {margin} points. Bench that kicker forever.",
    "{owner} choked away a game to {opponent} by just {margin}. The bench points alone would've won it. Embarrassing.",
  ],
  lowestScore: [
    "{owner} put up a laughable {points} points in a single week. Did you even set your lineup?",
    "{owner} scored {points} in one week. That's not a fantasy team, that's a cry for help.",
  ],
  losingStreak: [
    "{owner} dropped {streak} in a row at one point. An actual black hole of a roster.",
    "{owner} rattled off a {streak}-game losing streak. Somebody check on this man's will to live.",
  ],
  mostPointsAgainst: [
    "{owner} has given up {points} total points for their career -- basically running a soup kitchen for opposing lineups.",
  ],
  bestRecord: [
    "{owner} sits on top with a {record} all-time record. Insufferable, but earned.",
  ],
  mostChampionships: [
    "{owner} has {count} title{plural} to their name. Somebody's compensating for something with all that hardware.",
  ],
  ringless: [
    "{owner} has played {seasons} seasons and won exactly zero championships. Zero. A perfect, humiliating streak.",
    "{owner} is {seasons} seasons deep with no rings. At this point it's not bad luck, it's a personality trait.",
  ],
};

function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

function fmt(tpl, vars) {
  return tpl.replace(/\{(\w+)\}/g, (_, k) => (vars[k] !== undefined ? vars[k] : `{${k}}`));
}

/* ---------------- data loading ---------------- */

async function fetchJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch ${path}: ${res.status}`);
  return res.json();
}

async function loadEverything() {
  const meta = await fetchJSON(`${DATA_DIR}/league_meta.json`);
  const owners = await fetchJSON(`${DATA_DIR}/owners.json`).catch(() => ({ currentTeamNames: {}, memberNameOverrides: {} }));

  const seasons = [];
  for (const year of meta.years || []) {
    try {
      const s = await fetchJSON(`${DATA_DIR}/seasons/season_${year}.json`);
      if (s) seasons.push(s);
    } catch (e) {
      console.warn(`Could not load season ${year}`, e);
    }
  }
  seasons.sort((a, b) => a.seasonId - b.seasonId);
  return { meta, owners, seasons };
}

/* ---------------- owner resolution ---------------- */

function teamDisplayName(team) {
  if (team.name) return team.name.trim();
  return [team.location, team.nickname].filter(Boolean).join(" ").trim() || `Team ${team.id}`;
}

function resolveOwnerName(team, season, owners) {
  const teamName = teamDisplayName(team);
  if (owners.currentTeamNames && owners.currentTeamNames[teamName]) {
    return owners.currentTeamNames[teamName];
  }
  // try matching by ESPN member id override
  const memberIds = team.owners || [];
  for (const id of memberIds) {
    if (owners.memberNameOverrides && owners.memberNameOverrides[id]) {
      return owners.memberNameOverrides[id];
    }
  }
  // fall back to ESPN's own display name for the first owner on the team
  const member = (season.members || []).find(m => memberIds.includes(m.id));
  if (member && member.displayName) return member.displayName;
  return teamName + " (unmapped)";
}

/* ---------------- aggregation ---------------- */

function buildOwnerAggregates(seasons, owners) {
  const byOwner = new Map();

  function getOwner(name) {
    if (!byOwner.has(name)) {
      byOwner.set(name, {
        name,
        wins: 0, losses: 0, ties: 0,
        pointsFor: 0, pointsAgainst: 0,
        championships: 0,
        seasons: [],
        worstSeasonRecord: null,
      });
    }
    return byOwner.get(name);
  }

  for (const season of seasons) {
    for (const team of season.teams) {
      const owner = resolveOwnerName(team, season, owners);
      const agg = getOwner(owner);
      const w = team.wins || 0, l = team.losses || 0, t = team.ties || 0;
      agg.wins += w; agg.losses += l; agg.ties += t;
      agg.pointsFor += team.points_for || 0;
      agg.pointsAgainst += team.points_against || 0;
      if (season.champion != null && team.id === season.champion) agg.championships += 1;
      agg.seasons.push({ year: season.seasonId, wins: w, losses: l, ties: t, teamId: team.id, teamName: teamDisplayName(team) });

      const winPct = (w + t * 0.5) / Math.max(1, w + l + t);
      if (!agg.worstSeasonRecord || winPct < agg.worstSeasonRecord.winPct) {
        agg.worstSeasonRecord = { year: season.seasonId, wins: w, losses: l, ties: t, winPct };
      }
    }
  }

  return Array.from(byOwner.values());
}

function ownerForTeamInSeason(season, teamId, owners) {
  const team = season.teams.find(t => t.id === teamId);
  if (!team) return `Team ${teamId}`;
  return resolveOwnerName(team, season, owners);
}

/* ---------------- roast computation ---------------- */

function computeRoasts(seasons, owners, ownerAggs) {
  const cards = [];

  // worst all-time record
  const sortedByWinPct = [...ownerAggs].map(o => ({
    ...o,
    winPct: (o.wins + o.ties * 0.5) / Math.max(1, o.wins + o.losses + o.ties),
  })).sort((a, b) => a.winPct - b.winPct);

  if (sortedByWinPct.length) {
    const worst = sortedByWinPct[0];
    cards.push({
      key: "worstRecord",
      text: fmt(pick(ROASTS.worstRecord), { owner: worst.name, record: `${worst.wins}-${worst.losses}${worst.ties ? "-" + worst.ties : ""}` }),
      stat: `All-time record: ${worst.wins}-${worst.losses}${worst.ties ? "-" + worst.ties : ""}`,
    });

    const best = sortedByWinPct[sortedByWinPct.length - 1];
    cards.push({
      key: "bestRecord",
      text: fmt(pick(ROASTS.bestRecord), { owner: best.name, record: `${best.wins}-${best.losses}${best.ties ? "-" + best.ties : ""}` }),
      stat: `All-time record: ${best.wins}-${best.losses}${best.ties ? "-" + best.ties : ""}`,
    });
  }

  // most championships, career
  const mostTitles = [...ownerAggs].sort((a, b) => b.championships - a.championships)[0];
  if (mostTitles && mostTitles.championships > 0) {
    cards.push({
      key: "mostChampionships",
      text: fmt(pick(ROASTS.mostChampionships), { owner: mostTitles.name, count: mostTitles.championships, plural: mostTitles.championships === 1 ? "" : "s" }),
      stat: `Championships: ${mostTitles.championships}`,
    });
  }

  // most seasons played with zero championships
  const ringless = ownerAggs.filter(o => o.championships === 0).sort((a, b) => b.seasons.length - a.seasons.length)[0];
  if (ringless && ringless.seasons.length > 1) {
    cards.push({
      key: "ringless",
      text: fmt(pick(ROASTS.ringless), { owner: ringless.name, seasons: ringless.seasons.length }),
      stat: `${ringless.seasons.length} seasons played, 0 championships`,
    });
  }

  // most points against, career
  const mostPA = [...ownerAggs].sort((a, b) => b.pointsAgainst - a.pointsAgainst)[0];
  if (mostPA) {
    cards.push({
      key: "mostPointsAgainst",
      text: fmt(pick(ROASTS.mostPointsAgainst), { owner: mostPA.name, points: mostPA.pointsAgainst.toFixed(1) }),
      stat: `Career points against: ${mostPA.pointsAgainst.toFixed(1)}`,
    });
  }

  // game-level stats: biggest blowout, closest game, lowest single score
  let biggestBlowout = null;
  let closestGame = null;
  let lowestScore = null;

  for (const season of seasons) {
    for (const m of season.matchups || []) {
      const home = m.home, away = m.away;
      if (home.totalPoints == null || away.totalPoints == null) continue;
      if (home.totalPoints === 0 && away.totalPoints === 0) continue;

      const margin = Math.abs(home.totalPoints - away.totalPoints);
      const winnerIsHome = home.totalPoints > away.totalPoints;
      const winnerTeamId = winnerIsHome ? home.teamId : away.teamId;
      const loserTeamId = winnerIsHome ? away.teamId : home.teamId;

      const winnerName = ownerForTeamInSeason(season, winnerTeamId, owners);
      const loserName = ownerForTeamInSeason(season, loserTeamId, owners);

      if (!biggestBlowout || margin > biggestBlowout.margin) {
        biggestBlowout = { margin, year: season.seasonId, winnerName, loserName };
      }
      if (margin > 0 && (!closestGame || margin < closestGame.margin)) {
        closestGame = { margin, year: season.seasonId, winnerName, loserName };
      }

      for (const [teamId, pts, name] of [[home.teamId, home.totalPoints, ownerForTeamInSeason(season, home.teamId, owners)], [away.teamId, away.totalPoints, ownerForTeamInSeason(season, away.teamId, owners)]]) {
        if (pts > 0 && (!lowestScore || pts < lowestScore.points)) {
          lowestScore = { points: pts, year: season.seasonId, name };
        }
      }
    }
  }

  if (biggestBlowout) {
    cards.push({
      key: "mostBlownOut",
      text: fmt(pick(ROASTS.mostBlownOut), { owner: biggestBlowout.loserName, opponent: biggestBlowout.winnerName, margin: biggestBlowout.margin.toFixed(1) }),
      stat: `Week margin: ${biggestBlowout.margin.toFixed(1)} pts, ${biggestBlowout.year} season`,
    });
  }
  if (closestGame) {
    cards.push({
      key: "closestHeartbreak",
      text: fmt(pick(ROASTS.closestHeartbreak), { owner: closestGame.loserName, opponent: closestGame.winnerName, margin: closestGame.margin.toFixed(1) }),
      stat: `Nail-biter margin: ${closestGame.margin.toFixed(1)} pts, ${closestGame.year} season`,
    });
  }
  if (lowestScore) {
    cards.push({
      key: "lowestScore",
      text: fmt(pick(ROASTS.lowestScore), { owner: lowestScore.name, points: lowestScore.points.toFixed(1) }),
      stat: `${lowestScore.points.toFixed(1)} pts, ${lowestScore.year} season`,
    });
  }

  return cards;
}

/* ---------------- head-to-head ---------------- */

function computeHeadToHead(seasons, owners) {
  const records = new Map(); // key "A|B" (A<B alphabetically) -> {A: wins, B: wins, ties}

  for (const season of seasons) {
    for (const m of season.matchups || []) {
      const home = m.home, away = m.away;
      if (home.totalPoints == null || away.totalPoints == null) continue;
      if (home.totalPoints === 0 && away.totalPoints === 0) continue;
      const nameH = ownerForTeamInSeason(season, home.teamId, owners);
      const nameA = ownerForTeamInSeason(season, away.teamId, owners);
      if (nameH === nameA) continue;

      const [first, second] = [nameH, nameA].sort();
      const key = `${first}|${second}`;
      if (!records.has(key)) records.set(key, { [first]: 0, [second]: 0, ties: 0 });
      const rec = records.get(key);

      if (home.totalPoints === away.totalPoints) {
        rec.ties += 1;
      } else {
        const winner = home.totalPoints > away.totalPoints ? nameH : nameA;
        rec[winner] = (rec[winner] || 0) + 1;
      }
    }
  }

  return records;
}

/* ---------------- rendering ---------------- */

function renderStandings(seasons, owners) {
  const el = document.getElementById("standings-content");
  if (!seasons.length) { el.innerHTML = '<p class="empty-state">No season data yet. Run the fetch script to populate this.</p>'; return; }

  const latest = seasons[seasons.length - 1];
  const rows = latest.teams
    .map(t => ({ ...t, owner: resolveOwnerName(t, latest, owners) }))
    .sort((a, b) => (b.wins - a.wins) || (b.points_for - a.points_for));

  let html = `<p class="footer-note" style="margin-bottom:16px">${latest.seasonId} season</p>`;
  html += "<table><thead><tr><th>#</th><th>Owner</th><th>Team</th><th class='num'>W-L-T</th><th class='num'>PF</th><th class='num'>PA</th></tr></thead><tbody>";
  rows.forEach((t, i) => {
    html += `<tr><td>${i + 1}</td><td class="owner-name">${escapeHTML(t.owner)}</td><td>${escapeHTML(teamDisplayName(t))}</td><td class="num">${t.wins}-${t.losses}${t.ties ? "-" + t.ties : ""}</td><td class="num">${(t.points_for || 0).toFixed(1)}</td><td class="num">${(t.points_against || 0).toFixed(1)}</td></tr>`;
  });
  html += "</tbody></table>";
  el.innerHTML = html;
}

function renderAllTime(ownerAggs) {
  const el = document.getElementById("alltime-content");
  if (!ownerAggs.length) { el.innerHTML = '<p class="empty-state">No data yet.</p>'; return; }

  const rows = [...ownerAggs].sort((a, b) => {
    const pctA = (a.wins + a.ties * 0.5) / Math.max(1, a.wins + a.losses + a.ties);
    const pctB = (b.wins + b.ties * 0.5) / Math.max(1, b.wins + b.losses + b.ties);
    return pctB - pctA;
  });

  let html = "<table><thead><tr><th>#</th><th>Owner</th><th class='num'>W-L-T</th><th class='num'>Win%</th><th class='num'>PF</th><th class='num'>PA</th><th class='num'>Titles</th><th class='num'>Seasons</th></tr></thead><tbody>";
  rows.forEach((o, i) => {
    const pct = (o.wins + o.ties * 0.5) / Math.max(1, o.wins + o.losses + o.ties);
    html += `<tr><td>${i + 1}</td><td class="owner-name">${escapeHTML(o.name)}</td><td class="num">${o.wins}-${o.losses}${o.ties ? "-" + o.ties : ""}</td><td class="num">${(pct * 100).toFixed(1)}%</td><td class="num">${o.pointsFor.toFixed(1)}</td><td class="num">${o.pointsAgainst.toFixed(1)}</td><td class="num">${o.championships || ""}</td><td class="num">${o.seasons.length}</td></tr>`;
  });
  html += "</tbody></table>";
  el.innerHTML = html;
}

function renderRoasts(cards) {
  const el = document.getElementById("roasts-content");
  if (!cards.length) { el.innerHTML = '<p class="empty-state">Not enough data yet to roast anyone. Give it a season.</p>'; return; }

  el.innerHTML = cards.map(c => `
    <div class="roast-card">
      <h3>${labelFor(c.key)}</h3>
      <p>${escapeHTML(c.text)}</p>
      <div class="stat">${escapeHTML(c.stat)}</div>
    </div>
  `).join("");
}

function labelFor(key) {
  const labels = {
    worstRecord: "Human Bye Week",
    bestRecord: "Insufferable Champion Energy",
    mostPointsAgainst: "Community Soup Kitchen",
    mostBlownOut: "Total Annihilation",
    closestHeartbreak: "Kicker's Fault, Probably",
    lowestScore: "Did Not Set Lineup (Allegedly)",
    losingStreak: "Free Fall",
    mostChampionships: "Ring Collector",
    ringless: "Perennial Choker",
  };
  return labels[key] || key;
}

function renderH2H(records) {
  const el = document.getElementById("h2h-content");
  const entries = Array.from(records.entries());
  if (!entries.length) { el.innerHTML = '<p class="empty-state">No head-to-head data yet.</p>'; return; }

  let html = "<table><thead><tr><th>Matchup</th><th class='num'>Record</th></tr></thead><tbody>";
  entries
    .sort((a, b) => a[0].localeCompare(b[0]))
    .forEach(([key, rec]) => {
      const [a, b] = key.split("|");
      const ties = rec.ties ? ` (${rec.ties} tie${rec.ties > 1 ? "s" : ""})` : "";
      html += `<tr><td>${escapeHTML(a)} vs ${escapeHTML(b)}</td><td class="num">${rec[a] || 0}-${rec[b] || 0}${ties}</td></tr>`;
    });
  html += "</tbody></table>";
  el.innerHTML = html;
}

function escapeHTML(str) {
  return String(str).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- tabs ---------------- */

function setupTabs() {
  const buttons = document.querySelectorAll("nav.tabs button");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      buttons.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll("main > section.panel").forEach(s => s.hidden = true);
      document.getElementById(`tab-${btn.dataset.tab}`).hidden = false;
    });
  });
}

/* ---------------- boot ---------------- */

(async function init() {
  setupTabs();
  try {
    const { meta, owners, seasons } = await loadEverything();
    const leagueName = seasons.length ? seasons[seasons.length - 1].leagueName : "Fantasy League";
    document.getElementById("league-title").textContent = leagueName || "Fantasy League";
    document.getElementById("league-sub").textContent = seasons.length
      ? `${seasons[0].seasonId}–${seasons[seasons.length - 1].seasonId} · ${seasons.length} season${seasons.length > 1 ? "s" : ""} of receipts`
      : "no data yet -- run the fetch script";

    const ownerAggs = buildOwnerAggregates(seasons, owners);
    renderStandings(seasons, owners);
    renderAllTime(ownerAggs);
    renderRoasts(computeRoasts(seasons, owners, ownerAggs));
    renderH2H(computeHeadToHead(seasons, owners));
  } catch (e) {
    console.error(e);
    document.getElementById("league-title").textContent = "Couldn't load league data";
    document.getElementById("league-sub").textContent = String(e.message || e);
  }
})();
