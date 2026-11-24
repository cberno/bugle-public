# Setup

About an hour, mostly waiting on accounts (half that if you only want it by
email). Everything happens in a web browser except one command in Part 4B,
which you can copy and paste.

**What you need**

- A **GitHub** account (free): github.com
- An **Anthropic API** account: console.anthropic.com. It runs the editor and
  the fact checks. Typically a few dollars a month. Set a monthly spend
  limit under Settings → Limits so there are never surprises.
- An **eBird API key** (free): ebird.org/api/keygen (needs an eBird account)
- **Optional:** a **TRMNL X** e-ink display (usetrmnl.com) to hang on the
  wall. Without one, the paper arrives by email every morning instead, or as
  well. The layout is made for the X's 1404×1872 screen hung in portrait; the
  original TRMNL (800×480) is too small for it.

---

## Part 1 — Make your own copy (5 minutes)

1. On this template's GitHub page, click **Use this template → Create a new
   repository**.
2. Name it whatever you like (`my-bugle`, `the-herald`, ...).
3. Choose **Private**. Your settings, history, and archive live here, and
   nobody else needs to see them.
4. Click **Create repository**.

## Part 2 — The keys (10 minutes)

Keys go in GitHub's locked secret store, never in the code. In your
repository: **Settings → Secrets and variables → Actions → New repository
secret**. Add each of these (name exactly as written):

| Secret name | Where to get it | Needed? |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys → Create Key | **Required** |
| `EBIRD_API_KEY` | ebird.org/api/keygen | Strongly recommended (no key = no Bird of the Day) |
| `BUGLE_BIRTHDAYS_ICS_URL` | Google Calendar → ⚙ Settings → click the **Birthdays** calendar on the left → *Integrate calendar* → **Secret address in iCal format** | Optional |
| `BUGLE_ICS_URL` | Same screen, for your main calendar. Events with "birthday" in the title print. | Optional |
| `OPENAI_API_KEY` | platform.openai.com → API keys. A backup editor if Claude is ever down. | Optional |

Birthdays print **first names only**. That's enforced in the code, because
the finished image is public.

## Part 3 — Set it up for where you live (5 minutes)

1. In your repository, open the **Actions** tab. If GitHub asks, click
   **"I understand my workflows, go ahead and enable them."**
2. Click **Set up my paper** in the left sidebar → **Run workflow**, and fill in:
   - **Where do you live?** A town, neighborhood, or ZIP code:
     `Takoma Park, MD`, `20912`, `Bristol, England`. Add the state or country
     if the name is common.
   - **Your paper's name.** Anything you like: *The Takoma Times*,
     *The Daily Dachshund*. Long names shrink to fit.
   - **Who reads it**, in a sentence (optional): *two adults, a toddler,
     and a terrier*. The editor uses it for tone.
3. Click **Run workflow**. In about a minute it:
   - finds your coordinates (rounded to about half a mile, so your address is
     never stored), time zone, and units (°F or °C)
   - turns on National Weather Service alerts if you're in the US
   - looks for your local news sources, then **fetches every feed** and keeps
     only the ones that work and are current, with Google News for your town
     as the backstop
   - sizes the circle for Bird of the Day from how many species eBird has
     near you: tight in a city, wider in the country
4. Click the finished run to see what it chose. Not the right place? Run it
   again with more detail. Want to look before saving? Check **Preview only**.

Everything it chose is in **`bugle.toml`**. Click the file, then the pencil
icon, to change anything: add a favorite news feed, set your weather
preferences (do you love heat? hate wind?), list birds never to feature, or
change the motto. Running setup again later re-finds location, news, and
birds, and keeps the rest.

## Part 4 — Delivery: email, the wall, or both

Set up either one, or both. With neither, the paper still prints every
morning and `latest.png` in your repository is always today's edition.

### 4A — By email (5 minutes)

The paper arrives each morning as an image. It's attached, or shown right in
the email if you also do 4B.

1. **Make an app password.** Your Google account needs 2-Step Verification
   (myaccount.google.com/security). Then go to
   **myaccount.google.com/apppasswords**, name it `Bugle`, and click Create.
   Google shows a 16-character password. Copy it.
2. **Add two secrets** (Settings → Secrets and variables → Actions → New
   repository secret):
   - `GMAIL_ADDRESS`: your Gmail address. The paper is sent from it, and to it.
   - `GMAIL_APP_PASSWORD`: the 16-character password, spaces and all.
3. **Send it to others too** (optional): on the same page, open the
   **Variables** tab → New variable `BUGLE_EMAIL_TO` with a comma-separated
   list, e.g. `me@gmail.com, partner@example.com`.
4. **Not Gmail?** Any provider that offers SMTP works (Fastmail, iCloud,
   Outlook...). Put that account's address and app password in the same two
   secrets, and add Variables `SMTP_SERVER` (e.g. `smtp.fastmail.com`) and
   `SMTP_PORT` (usually `465`).

### 4B — On the wall (15 minutes; only if you have a TRMNL)

The TRMNL needs a public web address for the image. GitHub Pages provides one
for free, but only from a public repository, so the press keeps your code
private and pushes *only the finished image* to a second, public repository.

1. **Create the public display repository.** GitHub → **+** → **New
   repository** → name it **`bugle-display`** → **Public** → check **Add a
   README** → **Create repository**.
   (If you use a different name, go back to your private repo → Settings →
   Secrets and variables → Actions → **Variables** tab → New variable named
   `DISPLAY_REPO` with the value `yourusername/your-display-name`.)

2. **Turn on Pages** in the display repo: **Settings → Pages** → Source:
   **Deploy from a branch** → Branch: **main**, folder **/ (root)** → Save.

3. **Make a key that lets the press publish there.** On a Mac, open
   Terminal (Windows: PowerShell) and paste:

   ```
   ssh-keygen -t ed25519 -N "" -C "bugle-press" -f bugle_deploy_key
   ```

   That creates two files in your home folder: `bugle_deploy_key` (private)
   and `bugle_deploy_key.pub` (public). Open them in any text editor (Mac:
   `open -e bugle_deploy_key.pub`; Windows: `notepad bugle_deploy_key.pub`).

4. **Public half → display repo.** In `bugle-display`: **Settings → Deploy
   keys → Add deploy key**. Title `bugle-press`, paste the entire contents of
   `bugle_deploy_key.pub`, **check "Allow write access"**, Add key.

5. **Private half → your private repo.** In your private repo: **Settings →
   Secrets and variables → Actions → New repository secret**. Name
   `BUGLE_FEED_DEPLOY_KEY`, paste the entire contents of `bugle_deploy_key`,
   including the `-----BEGIN` and `-----END` lines. Save.

6. Delete both key files from your computer. GitHub has what it needs.

## Part 5 — First edition (10 minutes)

1. **Actions** → **Print the paper** → **Run workflow** → check **Dry run**
   → **Run workflow**. A dry run builds the paper without publishing it.
2. When it turns green (about 3 minutes), click the run, scroll to
   **Artifacts**, download the zip, and open `latest.png`. That's your paper.
   Not happy with something? Edit `bugle.toml` and dry-run again.
3. Ready? **Run workflow** again with **no boxes checked**. When it's green,
   check your inbox (4A) and/or open
   `https://yourusername.github.io/bugle-display/latest.png` (4B; lowercase
   username). Pages can take a few minutes the first time.

From now on it prints itself in the early morning, between about 3 and 4am
your time anywhere in the US. GitHub checks a few times each morning and the
first check after 3am prints; the rest skip in seconds. To reprint today's
paper after a change, run **Print the paper** with **Force print** checked.

**Outside the US?** In the UK and western Europe it prints mid-morning
without any changes. Elsewhere, edit the `cron:` line in
`.github/workflows/bugle.yml` to the UTC hours that match about 3–9am for you.

## Part 6 — The wall (10 minutes; only if you have a TRMNL)

1. Set up the TRMNL X with the TRMNL app and claim it at **usetrmnl.com**.
2. **Plugins → Image Display** → paste your display URL from Part 5 → Save.
3. **Playlist:** make Image Display the only item, so it never rotates.
4. **Device settings:** set the orientation to match how it hangs (the paper
   is portrait) and refresh hourly. It only redraws when the image changes,
   so the screen updates once a day and the battery lasts.

## Part 7 — Changing things later

- **Name, motto, feeds, weather preferences, birds to skip:** edit `bugle.toml`.
  Tomorrow's paper uses it.
- **Moved?** Run **Set up my paper** again with the new place.
- **Anything bigger** (a new section, layout, tone): open Claude (claude.ai
  or Claude Code), connect GitHub, give it access to your repository, and
  describe the change in plain words, e.g. *"make the quote bigger"* or *"add a
  section for the school lunch menu."* Ask it to do a dry run before it pushes.
- **Quotes:** the curated list is `CURATED_QUOTES` near the top of
  `bugle_action.py`. Add your own; the press won't repeat one too soon.

## Troubleshooting

- **A run is red.** Actions → click the run → click the red step. The error
  is at the bottom. Paste it to Claude.
- **"No ANTHROPIC_API_KEY secret yet"** means the run skipped on purpose.
  Check the secret's exact name in Part 2.
- **Setup picked the wrong town.** Run it again with the state or country:
  `Springfield, Illinois`, or use a ZIP code.
- **The publish step fails with "Permission denied (publickey)".** The deploy
  key is missing, or "Allow write access" wasn't checked (Part 4B, steps 4–5).
- **No email.** Check spam first. Then open the run, expand **Deliver to
  the inbox**, and read the error. "Username and Password not accepted" means
  the app password is wrong or 2-Step Verification is off (Part 4A, step 1).
  If that step doesn't appear at all, a secret name is misspelled.
- **The URL shows yesterday's paper.** Pages caches for a few minutes. If it
  stays stale, check the Actions tab to see whether this morning's run happened.
- **Local News says "Wire down".** One of your feeds didn't answer. Run setup
  again to re-check them, or replace it in `bugle.toml`.
- **No Bird of the Day.** Without an eBird key, or with no nearby sightings
  that have a verified photo, the bottom section becomes a short Grounds
  Report or Almanac note instead. If it happens a lot, raise `radius_km`
  under `[birds]`.

## What's private and what's public

- **Private** (your repo): settings, keys, the editor's history, the archive.
- **Public** (`bugle-display`): only the finished images, which is what's on
  your wall. Birthdays print first names only. Your location is stored in the
  private repo, rounded to about half a mile.
