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

## Notes / known limitations (v1)

- Championship history isn't parsed yet — the "All-Time" table is regular
  season record + points only, not playoff results. Can add if you want it.
- Owner identity across years is matched by team name (this season) and
  falls back to ESPN's own account display name for past seasons. Not
  perfect if someone renamed their team AND their ESPN display name.
- The roast engine works off aggregate stats and single-game results —
  it'll get funnier and more targeted as more seasons of data accumulate.
