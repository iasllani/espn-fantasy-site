# League History & Hall of Shame

A free, static site that pulls your ESPN Fantasy Football league's full history
and turns it into standings, all-time records, head-to-head rivalries, and a
(deliberately crude) "Hall of Shame" roast section. Hosted free on GitHub
Pages; data refreshes automatically via GitHub Actions.

## 1. Create the GitHub repo

```bash
cd espn-fantasy-site
git init
git add .
git commit -m "Initial site scaffold"
git branch -M main
git remote add origin https://github.com/iasllani/YOUR-REPO-NAME.git
git push -u origin main
```

(Create the empty repo on GitHub first at github.com/new, then use its URL above.)

## 2. Add your ESPN credentials as encrypted secrets

**Do not commit your SWID or espn_s2 anywhere.** Instead, in your new repo on
GitHub:

`Settings -> Secrets and variables -> Actions -> New repository secret`

Add two secrets:
- `ESPN_SWID` — your SWID cookie value, including the curly braces, e.g. `{12345678-90AB-CDEF-1234-567890ABCDEF}`
- `ESPN_S2` — your espn_s2 cookie value

These are only ever used inside the GitHub Actions runner (`.github/workflows/update-data.yml`)
and are encrypted at rest by GitHub.

### Optional: AI-generated roasts

If you add a third secret, `ANTHROPIC_API_KEY` (from console.anthropic.com —
note this is a separate paid API account, not a Claude Pro subscription),
the weekly Action will also call Claude to write fresh, stat-specific roast
lines for every owner instead of picking from a fixed set of templates.
Cost is negligible (well under $1/season at the default model). If this
secret isn't set, the site just falls back to the built-in template roasts —
nothing breaks.

## 3. Turn on GitHub Pages

`Settings -> Pages -> Build and deployment -> Deploy from a branch -> Branch: main, folder: /docs`

Your site will be live at `https://iasllani.github.io/YOUR-REPO-NAME/` within a
minute or two.

## 4. Run the data fetch for the first time

Go to the **Actions** tab in your repo, select **Update ESPN Fantasy Data**,
and click **Run workflow**. This pulls every season of your league's history
it can find (auto-discovers how far back it goes) and commits the JSON into
`docs/data/seasons/`. Once that commit lands, refresh the live site.

After the first run, it also fires automatically every Tuesday during the
season (see the `cron` line in the workflow file — tweak it if you want a
different cadence).

## 5. Fix up owner names for past seasons

`docs/data/owners.json` maps ESPN team names to real names. It's pre-filled
with this season's team names. If anyone renamed their team in past years,
their historical seasons will show up on the site as "(unmapped)" — just add
their old team name (or ESPN member id) to `owners.json` and re-push.

## Local development / testing

```bash
cd scripts
pip install -r requirements.txt
ESPN_SWID='{...}' ESPN_S2='...' python3 fetch_espn_data.py --league-id 703243
```

Then open `docs/index.html` in a browser (or run `python3 -m http.server`
from inside `docs/` and visit `localhost:8000`) to preview locally before
pushing.

## Notes / known limitations

- Owner identity is resolved by stable ESPN member ID (`docs/data/owners.json`
  → `memberNameOverrides`), not by team name, so it survives someone renaming
  their team every year. Former league members who left before the earliest
  mapped season still show up under their raw ESPN handle until you add them
  to `memberNameOverrides` yourself — there's no way to auto-discover a real
  name for someone no longer in the league.
- Championship history is parsed (last `WINNERS_BRACKET` game of each season)
  and shown as a "Titles" column plus dedicated roast cards.
- Hall of Shame roasts every owner individually, not just the league-wide
  extremes. With `ANTHROPIC_API_KEY` set, those per-owner roasts are
  AI-generated fresh each week from real stats; otherwise they're drawn from
  a template bank baked into `docs/app.js`.
