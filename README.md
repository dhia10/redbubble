# 🎨 Redbubble Automation System

A fully automated print-on-demand publishing pipeline that detects trends, generates AI artwork, writes SEO metadata, and uploads products to Redbubble — every day, on autopilot.

---

## 📦 Project Structure

```
redbubble_automation/
├── config/
│   ├── settings.yaml        ← All system configuration
│   └── prompts.yaml         ← All LLM prompt templates
│
├── data/
│   ├── database.db          ← SQLite database (auto-created)
│   ├── images/              ← Generated artwork files
│   └── exports/             ← Screenshots, exports
│
├── logs/
│   └── pipeline.log         ← Daily run logs
│
├── modules/
│   ├── trend_detector.py    ← Google Trends + Redbubble + Pinterest scraper
│   ├── niche_scorer.py      ← GPT-powered niche scoring
│   ├── idea_generator.py    ← Design concept generation
│   ├── prompt_builder.py    ← Image-gen prompt construction
│   ├── image_generator.py   ← AI artwork generation (DALL-E / Leonardo / Ideogram)
│   ├── seo_generator.py     ← Titles, descriptions, 50 tags
│   ├── database_manager.py  ← All SQLite CRUD operations
│   ├── redbubble_uploader.py← Playwright browser automation
│   ├── analytics.py         ← Performance tracking & niche ranking
│   └── scheduler.py         ← Daily pipeline scheduling
│
├── dashboard/
│   └── app.py               ← Streamlit monitoring dashboard
│
├── tests/                   ← Unit tests
├── main.py                  ← Pipeline entry point
├── requirements.txt
├── .env.template            ← Copy to .env and fill in secrets
└── README.md
```

---

## 🚀 Quick Start

### 1. Clone & set up environment

```bash
git clone https://github.com/yourname/redbubble-automation.git
cd redbubble-automation

python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

### 2. Configure secrets

```bash
cp .env.template .env
```

Edit `.env`:
```env
OPENAI_API_KEY=sk-...
REDBUBBLE_EMAIL=your@email.com
REDBUBBLE_PASSWORD=yourpassword
```

### 3. Edit settings (optional)

Open `config/settings.yaml` and adjust:
- `pipeline.niches_per_run` — how many niches to process each day
- `pipeline.ideas_per_niche` — designs per niche
- `image_generation.provider` — `openai` | `leonardo` | `ideogram`
- `pipeline.dry_run: true` — test without uploading

### 4. Run the pipeline

```bash
# Run once immediately:
python main.py --run-now

# Test without uploading to Redbubble:
python main.py --run-now --dry-run

# Start the daily scheduler (blocks):
python main.py

# Launch the dashboard:
python main.py --dashboard
# or directly:
streamlit run dashboard/app.py
```

---

## 🗄 Database Schema

```sql
-- Trending topics / keyword niches
CREATE TABLE niches (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword                 TEXT NOT NULL UNIQUE,
    source                  TEXT NOT NULL,
    commercial_potential    REAL,
    evergreen_score         REAL,
    competition_estimate    REAL,
    originality_opportunity REAL,
    overall_score           REAL,
    reasoning               TEXT,
    times_used              INTEGER DEFAULT 0,
    last_used_at            TEXT,
    created_at              TEXT,
    updated_at              TEXT
);

-- Design concepts per niche
CREATE TABLE ideas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    niche_id        INTEGER REFERENCES niches(id),
    title           TEXT,
    description     TEXT,
    emotion         TEXT,
    colors          TEXT,        -- JSON list
    best_products   TEXT,        -- JSON list
    status          TEXT,        -- pending|prompted|generated|uploaded|failed
    created_at      TEXT,
    updated_at      TEXT
);

-- AI image generation prompts
CREATE TABLE prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id     INTEGER REFERENCES ideas(id),
    prompt_text TEXT,
    provider    TEXT,
    model       TEXT,
    created_at  TEXT
);

-- Generated artwork files
CREATE TABLE designs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id       INTEGER REFERENCES prompts(id),
    idea_id         INTEGER REFERENCES ideas(id),
    file_path       TEXT,
    file_size_bytes INTEGER,
    width_px        INTEGER,
    height_px       INTEGER,
    format          TEXT,
    is_valid        INTEGER,
    validation_msg  TEXT,
    created_at      TEXT
);

-- Redbubble product records
CREATE TABLE products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id           INTEGER REFERENCES designs(id),
    idea_id             INTEGER REFERENCES ideas(id),
    niche_id            INTEGER REFERENCES niches(id),
    seo_title           TEXT,
    seo_description     TEXT,
    seo_tags            TEXT,    -- JSON list
    redbubble_url       TEXT,
    redbubble_work_id   TEXT UNIQUE,
    status              TEXT,    -- draft|published|failed|removed
    published_at        TEXT,
    created_at          TEXT,
    updated_at          TEXT
);

-- Upload attempt audit trail
CREATE TABLE upload_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER REFERENCES products(id),
    attempt_number  INTEGER,
    status          TEXT,        -- success|failed|retrying
    message         TEXT,
    screenshot_path TEXT,
    duration_ms     INTEGER,
    created_at      TEXT
);

-- Periodic performance snapshots
CREATE TABLE analytics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER REFERENCES products(id),
    views       INTEGER DEFAULT 0,
    favorites   INTEGER DEFAULT 0,
    sales       INTEGER DEFAULT 0,
    revenue_usd REAL    DEFAULT 0.0,
    recorded_at TEXT
);
```

---

## 🖥 Windows Deployment (Local)

### Option A — Built-in scheduler (simplest)

```bat
:: create a batch file: run_automation.bat
cd C:\path\to\redbubble_automation
call venv\Scripts\activate
python main.py
```

Set this batch file to run at Windows startup via **Task Scheduler**:

```
Action: Start a program
Program: C:\path\to\redbubble_automation\venv\Scripts\python.exe
Arguments: main.py
Start in: C:\path\to\redbubble_automation
```

### Option B — Windows Task Scheduler (calls `--run-now`)

```bat
schtasks /Create /SC DAILY /TN "RedbubbleAutomation" ^
  /TR "C:\path\to\venv\Scripts\python.exe C:\path\to\main.py --run-now" ^
  /ST 08:00 /F
```

---

## ☁️ Cloud Deployment (Azure VM)

### 1. Provision the VM

```bash
az vm create \
  --resource-group rg-redbubble \
  --name vm-redbubble \
  --image Ubuntu2204 \
  --size Standard_B2s \
  --admin-username azureuser \
  --generate-ssh-keys
```

### 2. Install dependencies

```bash
ssh azureuser@<VM_IP>
sudo apt update && sudo apt install -y python3.12 python3-pip git
git clone https://github.com/yourname/redbubble-automation /opt/redbubble_automation
cd /opt/redbubble_automation
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

### 3. Configure

```bash
cp .env.template .env
nano .env   # fill in secrets
```

### 4. Set up cron

```bash
crontab -e
# Add:
0 8 * * * /usr/bin/python3 /opt/redbubble_automation/main.py --run-now >> /opt/redbubble_automation/logs/cron.log 2>&1
```

### 5. Run dashboard with reverse proxy

```bash
# Install nginx
sudo apt install nginx

# Start Streamlit on port 8501
nohup streamlit run dashboard/app.py --server.port 8501 &

# Configure nginx to proxy /dashboard to 8501
```

---

## 🔧 Module Reference

| Module | Purpose |
|---|---|
| `trend_detector.py` | Scrapes Google Trends, Redbubble, Pinterest for keywords |
| `niche_scorer.py` | GPT-scores keywords as POD opportunities |
| `idea_generator.py` | Generates original design concepts per niche |
| `prompt_builder.py` | Converts ideas into AI image-gen prompts |
| `image_generator.py` | Generates artwork via OpenAI / Leonardo / Ideogram |
| `seo_generator.py` | Writes title, description, 50 tags |
| `database_manager.py` | SQLite CRUD for all entities |
| `redbubble_uploader.py` | Playwright browser automation for Redbubble uploads |
| `analytics.py` | Tracks views/sales, ranks niches |
| `scheduler.py` | Daily pipeline scheduling (built-in loop or cron helper) |
| `dashboard/app.py` | Streamlit monitoring UI |

---

## ⚙️ Configuration Reference (`settings.yaml`)

| Key | Default | Description |
|---|---|---|
| `pipeline.niches_per_run` | 3 | Niches to process each daily run |
| `pipeline.ideas_per_niche` | 2 | Design ideas per niche |
| `pipeline.dry_run` | false | Skip actual Redbubble upload |
| `image_generation.provider` | openai | Image AI provider |
| `image_generation.model` | dall-e-3 | Model to use |
| `redbubble.headless` | true | Run browser without GUI |
| `scheduler.run_time` | 08:00 | Daily UTC run time |

---

## 🔮 Future Enhancements

1. **Multi-platform upload** — extend to Teepublic, Merch by Amazon, Society6
2. **A/B title testing** — generate 2 titles and auto-select the better-performing one
3. **Sales webhook integration** — receive real-time sales notifications via Redbubble webhooks when available
4. **Competitor analysis** — track top sellers in each niche to identify gaps
5. **Design style learning** — fine-tune prompt templates based on which styles convert best
6. **Email/Slack notifications** — daily run summary delivered to your inbox
7. **Docker deployment** — one-command containerised deploy
8. **Proxy rotation** — rotate IPs for scraping at scale
9. **Human-in-the-loop review** — optional approval step before upload via Telegram bot

---

## ⚠️ Legal & Policy Notes

- This system is designed to generate **100% original artwork** using AI.
- The IP risk filter in `niche_scorer.py` blocks keywords associated with trademarked/copyrighted content.
- Always review Redbubble's [content policies](https://help.redbubble.com/hc/en-us/articles/200049487) before enabling automated uploads.
- Set `pipeline.dry_run: true` during testing to avoid accidental uploads.
- Automation tools used must comply with Redbubble's Terms of Service.

---

## 📄 License

MIT — free to use, modify, and distribute.
"# redbubble" 
