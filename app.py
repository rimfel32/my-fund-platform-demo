"""
The platform's web app: a public home page, sign up / sign in, and the portfolio as a website.

    pip install streamlit          # once (it's in the course requirements from Day 3)
    streamlit run app.py           # opens http://localhost:8501 in your browser

Built with Streamlit: every st.something(...) call below draws one thing on the page, top to bottom,
and the whole script re-runs each time someone clicks. No HTML or JavaScript needed (a little HTML is
used only for the logos and the coloured "cards").  Docs: https://docs.streamlit.io

Accounts: the FIRST account created becomes Administrator; everyone after that starts as
"Board (read-only)" until an Administrator gives them a role (Users page). The roles are the user
classes of the requirements list, and each role sees only its pages (role-based access).
Public demo mode (demo_mode: true in config.yaml, or the environment variable PLATFORM_DEMO=1): visitors
choose a role to explore when they sign up, nobody can become Administrator, and the Users page (which
lists emails) is switched off.
Passwords are stored salted and hashed (PBKDF2), never as text, in output/app_users.db, which is
never committed (output/ is in .gitignore). For a real deployment, use your organisation's single
sign-on instead (see README).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import secrets
import shutil
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fundplatform import lineage, pipeline, registry, serving  # noqa: E402
from run import find_data  # noqa: E402

try:                       # added after Day 3: older copies run without them (python update_platform.py adds them)
    from fundplatform import equity, ingest  # noqa: E402
except ImportError:
    equity = ingest = None

import yaml  # noqa: E402

USERS_DB = HERE / "output" / "app_users.db"   # output/ is never committed (.gitignore)
ASSETS = HERE / "assets"

# Who sees what. Page names are the keys of PAGES at the bottom of this file.
ROLES = {
    "Administrator": ["Home", "How to use", "Overview", "Cash-flow plan", "Instruments", "Collections",
                      "Equity & dividends", "Data quality", "Import data", "Users"],
    "Finance office": ["Home", "How to use", "Overview", "Cash-flow plan", "Instruments", "Collections",
                       "Equity & dividends", "Data quality", "Import data"],
    "Investment analyst": ["Home", "How to use", "Overview", "Cash-flow plan", "Instruments", "Collections",
                           "Equity & dividends", "Data quality", "Import data"],
    "Chief Investment Officer": ["Home", "How to use", "Overview", "Cash-flow plan", "Instruments", "Collections",
                                 "Equity & dividends"],
    "Board (read-only)": ["Home", "How to use", "Overview", "Cash-flow plan", "Equity & dividends"],
}
CAN_CHANGE_DATE = {"Administrator", "Finance office"}
CAN_REPLACE_REGISTERS = {"Administrator", "Finance office"}   # analysts can check an import, not apply it
DEFAULTS = {"learn_url": "https://kaineticslabs.com/learn",          # override any of these in config.yaml
            "contact_url": "https://kaineticslabs.com/work-with-us",
            "contact_email": "kaineticslabs@gmail.com",
            "org_name": "Horizon Development Fund"}


def settings() -> dict:
    """config.yaml plus defaults, so older copies of config.yaml keep working."""
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    cfg["demo_mode"] = bool(cfg.get("demo_mode")) or os.environ.get("PLATFORM_DEMO", "") in ("1", "true", "yes")
    return cfg


# ------------------------------------------------------------------ look and feel
def logo(name: str, size: int = 48) -> str:
    """An <img> tag for an SVG in assets/ (embedded, so it works on any host)."""
    path = ASSETS / name
    if not path.exists():
        return ""
    data = base64.b64encode(path.read_bytes()).decode()
    return f'<img src="data:image/svg+xml;base64,{data}" width="{size}" height="{size}" style="border-radius:10px">'


CSS = """
<style>
.hero {background: linear-gradient(135deg,#0B2A3C 0%,#0E7A6D 100%); color:#F2EFE6; padding:28px 32px; border-radius:16px;}
.hero h1 {color:#F2EFE6; margin:0 0 6px 0; font-size:2.1rem;}
.hero p {color:#D7E7E4; font-size:1.05rem; margin:0;}
.card {background:#FFFFFF; border:1px solid #DAD7CC; border-radius:14px; padding:16px 18px; height:100%;}
.card h4 {margin:0 0 6px 0; color:#0B5C52;}
.card p {margin:0; color:#3E4A56; font-size:0.95rem;}
.kl {background:#0A0D10; color:#E8E6E1; padding:24px 28px; border-radius:16px; border-left:6px solid #FFB020;}
.kl h3 {color:#FFB020; margin:0 0 8px 0;}
.kl a {color:#FFB020;}
.brand {display:flex; align-items:center; gap:14px; margin-bottom:8px;}
.brand b {font-size:1.25rem; color:#0B2A3C;}
.small {color:#6B7680; font-size:0.85rem;}
</style>
"""


def brand_header() -> None:
    cfg = settings()
    st.markdown(f'<div class="brand">{logo("horizon_logo.svg", 46)}<div><b>{cfg["org_name"]}</b><br>'
                f'<span class="small">Treasury &amp; Investment Platform · fictional organisation · synthetic data'
                f'</span></div></div>', unsafe_allow_html=True)


def cards(items: list[tuple[str, str]], per_row: int = 3) -> None:
    for i in range(0, len(items), per_row):
        cols = st.columns(per_row)
        for col, (title, text) in zip(cols, items[i:i + per_row]):
            col.markdown(f'<div class="card"><h4>{title}</h4><p>{text}</p></div>', unsafe_allow_html=True)
        st.write("")


def explain(text: str) -> None:
    """A collapsible 'what does this mean?' note under each page title."""
    with st.expander("ℹ️ What does this page show, and what do the terms mean?"):
        st.markdown(text)


def kainetics_section() -> None:
    cfg = settings()
    st.markdown(f"""
<div class="kl">
  <div class="brand">{logo("kainetics_mark.svg", 44)}<div><h3 style="margin:0">Kainetics Labs</h3>
  <span style="color:#B9B6AE">Applied AI research lab · Kigali, Rwanda · from Africa, for Africa, to the world</span></div></div>
  <p><b>Learn to build systems like this one.</b> This platform was built hands-on in a Kainetics Labs course:
  practical labs, a capstone you take back to work, and a certificate. Explore similar courses with labs and
  certification at <a href="{cfg['learn_url']}" target="_blank">kaineticslabs.com/learn</a>.</p>
  <p><b>Want a system like this for your organisation, or expert advice?</b> Kainetics Labs designs and builds
  data and AI platforms and consults on them. Submit a request at
  <a href="{cfg['contact_url']}" target="_blank">kaineticslabs.com/work-with-us</a> or write to
  <a href="mailto:{cfg['contact_email']}">{cfg['contact_email']}</a>.</p>
</div>""", unsafe_allow_html=True)
    st.write("")
    a, b, c = st.columns(3)
    a.link_button("📚 Courses with labs & certification", cfg["learn_url"], width="stretch")
    b.link_button("🛠️ Request a system or consultation", cfg["contact_url"], width="stretch")
    c.link_button(f"✉️ {cfg['contact_email']}", f"mailto:{cfg['contact_email']}", width="stretch")


def footer() -> None:
    cfg = settings()
    st.divider()
    st.markdown(f'<p class="small">Built in the course <i>Data Infrastructure for AI</i> · '
                f'<a href="{cfg["learn_url"]}" target="_blank">Learn with Kainetics Labs</a> · '
                f'<a href="{cfg["contact_url"]}" target="_blank">Work with us</a> · '
                f'<a href="mailto:{cfg["contact_email"]}">{cfg["contact_email"]}</a> · '
                f'{cfg["org_name"]} is fictional and every figure is synthetic.</p>', unsafe_allow_html=True)


# ------------------------------------------------------------------ accounts (SQLite + hashed passwords)
def db() -> sqlite3.Connection:
    USERS_DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(USERS_DB)
    con.execute("CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, role TEXT, "
                "salt TEXT, pw_hash TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)")
    return con


def hash_password(password: str, salt: str) -> str:
    """PBKDF2: a slow, salted hash, so a stolen users.db doesn't reveal passwords."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def create_user(name: str, email: str, password: str, demo_role: str | None = None) -> str:
    with db() as con:
        first = con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        if demo_role:                                       # public demo: the visitor picks a role to explore
            role = demo_role
        else:
            role = "Administrator" if first else "Board (read-only)"
        salt = secrets.token_hex(16)
        con.execute("INSERT INTO users (email, name, role, salt, pw_hash) VALUES (?, ?, ?, ?, ?)",
                    (email.lower().strip(), name.strip(), role, salt, hash_password(password, salt)))
    return role


def check_login(email: str, password: str) -> dict | None:
    with db() as con:
        row = con.execute("SELECT name, role, salt, pw_hash FROM users WHERE email = ?",
                          (email.lower().strip(),)).fetchone()
    if row and secrets.compare_digest(hash_password(password, row[2]), row[3]):
        return {"email": email.lower().strip(), "name": row[0], "role": row[1]}
    return None


# ------------------------------------------------------------------ the platform's results (cached)
@st.cache_data(show_spinner="Running the platform pipeline ...")
def platform(as_of: str) -> dict:
    """Run the pipeline once per as_of date and keep the results in memory for every page and user."""
    cfg = settings()
    cfg.update(data_dir=find_data(cfg["data_dir"]), output_dir=HERE / cfg["output_dir"], as_of=as_of)
    ctx = pipeline.run(cfg, quiet=True)
    keep = ("board", "scorecard", "master", "status", "variance", "income", "upcoming", "extensions",
            "spans", "lineage", "output_dir", "exceptions", "cleaning_log", "as_of", "year", "data_dir")
    p = {k: ctx[k] for k in keep if k in ctx}
    p["equity"] = {k: ctx["reg"][k] for k in ("dividends", "equity_holdings") if k in ctx["reg"]}
    return p


# ------------------------------------------------------------------ home page (public and signed in)
FEATURES = [
    ("📊 Portfolio overview", "Headline figures for the year: interest income received (cash) and earned "
     "(accrual), cash expected in the next 30 days, and instruments close to maturity."),
    ("📅 Master cash-flow plan", "The 12-month plan treasury teams usually rebuild in Excel: income by asset "
     "class, maturities, debt repayments and the closing cash balance each month."),
    ("🏦 Instruments", "Every bond, deposit and loan with its next payment and days to maturity, plus "
     "90/60/30-day maturity alerts and a one-click lookup."),
    ("💰 Collections", "Cash expected in the next 30/60/90 days, budget vs actual collections with "
     "shortfalls highlighted, loan arrears and recovery rates."),
    ("🌍 Equity & dividends", "The fund's stakes in 40 portfolio companies: dividends projected vs received "
     "this year, overdue payers, income by year and a three-year baseline forecast."),
    ("📥 Import from Excel", "Upload the registers as they are kept in Excel. The platform cleans what is safe "
     "to clean, sets aside what needs a person, and says who should fix each row."),
    ("✅ Data quality & lineage", "Is the data fresh, valid, on budget and behaving as usual? Which rows were "
     "set aside? And for any figure: which sources it was built from, and what a wrong source would affect."),
    ("🔐 Accounts & roles", "Board members, the CIO, analysts and the finance office each see only what "
     "their role needs: role-based access, as an audit would expect."),
    ("📖 How to use", "A short guide inside the app: which page answers which question, step-by-step tasks "
     "for each role, and the terms used."),
]


def home_content() -> None:
    cfg = settings()
    st.markdown(CSS, unsafe_allow_html=True)
    brand_header()
    st.markdown("""
<div class="hero">
  <h1>From Excel workbooks to a trusted investment platform</h1>
  <p>Every coupon, deposit, loan instalment and exchange rate computed once, by readable code, and served as a
  live dashboard: with alerts when something is due, warnings when the data can't be trusted, and a trail
  showing where every figure came from.</p>
</div>""", unsafe_allow_html=True)
    st.write("")
    st.subheader(f"About {cfg['org_name']}")
    st.markdown(f"""
**{cfg['org_name']}** is a *fictional* development fund used for teaching. Its treasury and investment
office manages government bonds, term deposits at nine banks, corporate bonds, loans to subsidiary companies
and its own borrowing, in Rwandan francs (RWF) and US dollars. Like many funds, it ran all of this in Excel:
one workbook per asset class, a master cash-flow sheet and an exchange-rate tab typed in by hand.

This demo shows what the same office looks like on a small data platform. **All names and figures are
synthetic.** Counterparties are called Bank A–I and Subsidiary A–C.
""")
    st.subheader("What you can explore")
    cards(FEATURES)
    st.subheader("How it works")
    st.markdown("""
1. **Registers:** the instrument lists (bonds, deposits, loans, exchange rates, dividends) arrive as CSV
   files or Excel workbooks. Formatting is **cleaned** (thousands separators, "10.5%", dd/mm/yyyy dates),
   then every row is checked against its **data contract**. Rows that fail are **set aside as exceptions**,
   each with the reason and the person who should fix it, and are left out of every figure until then.
2. **Schedules:** every future and past payment of every instrument is computed once (*Actual/365* interest,
   equal-instalment loans), and every report is built from that one table.
3. **Serving:** results are written as files for reports (*offline*) and as a fast lookup table for screens
   (*online*).
4. **Observability:** a scorecard checks freshness, validity, collections against budget and changes in
   payment behaviour every run, and **lineage** records which source fed which figure.
5. A **pipeline** runs all steps in order every morning; the same steps can run in Apache Airflow.
""")
    st.subheader("Try it")
    st.markdown("Create an account (top of the page), choose a role, and sign in. "
                "Each role sees a different set of pages. Sign out and sign up with another role to compare. "
                "New here? Read the **📖 How to use** tab first: two minutes."
                if cfg["demo_mode"] else
                "Sign in to explore. The first account on a new installation becomes Administrator. "
                "The **📖 How to use** tab explains every page.")
    st.write("")
    kainetics_section()


# ------------------------------------------------------------------ the in-app guide ("How to use")
PAGE_GUIDE = pd.DataFrame([
    ("Overview", "How are we doing this year? What needs a decision soon?", "Everyone",
     "Income cash vs accrual, cash due in 30 days, year-end cash, maturity alerts, any data warning"),
    ("Cash-flow plan", "Will we have enough cash each month?", "Everyone",
     "The 12-month plan; a warning on the first month the balance goes negative; download for Excel"),
    ("Instruments", "What do we hold, and when does each one pay or mature?", "CIO, analysts, finance office",
     "Every bond, deposit and loan; filter by asset class; look one instrument up"),
    ("Collections", "Is the money that's due actually arriving?", "CIO, analysts, finance office",
     "Due in 30/60/90 days, budget vs actual by month, arrears and recovery rates"),
    ("Equity & dividends", "What do our companies pay us, and who is late?", "Everyone",
     "Dividends projected vs received this year, overdue payers, income by year, 3-year baseline"),
    ("Data quality", "Can we trust today's figures?", "Analysts, finance office",
     "The scorecard, rows set aside (exceptions) with their owner, automatic fixes, lineage, the last run"),
    ("Import data", "How do I bring in this month's Excel registers?", "Finance office, analysts",
     "Upload a workbook or CSV; see fixes, exceptions and accepted rows before anything is used"),
    ("Users", "Who has access, with which role?", "Administrator", "Change a person's role"),
], columns=["Page", "The question it answers", "Who uses it", "What you'll find"])

TASKS = [
    ("🧭 Prepare for a board or investment-committee meeting (Board, CIO)", """
1. **Overview**: read the four headline figures. A red banner means a data check failed; ask the finance
   office before quoting those figures.
2. **Cash-flow plan**: find the first month the closing balance turns negative (a yellow warning names it).
   That is the decision to discuss: which maturities to keep as cash instead of reinvesting.
3. **Equity & dividends**: the dividend income received this year, and any company that is late paying.
4. Every **ℹ️** box under a page title explains the terms on that page.
"""),
    ("📥 Import this month's registers from Excel (Finance office, analysts)", """
1. Name each sheet after its register: *Term Deposits*, *Treasury Bonds*, *Subsidiary Loans*, *FX Rates*,
   *Dividends*… Column names are matched the same way ("Deposit ID" → `deposit_id`).
2. Open **Import data** and upload the workbook (or a single CSV). Not ready yet? Click **Try it with the
   sample workbook**: it's messy on purpose.
3. Read the three results for each register:
   - **Fixes made automatically**: formatting with only one possible meaning ("3,000,000,000", "10.5%",
     15/10/2026, stray spaces, a TOTAL row). Each fix is listed, before and after.
   - **Set aside (exceptions)**: anything that needs judgement (a rate typed as 10.8, an unknown bank, a missing
     date, the same deposit twice). Each shows its Excel row, the problem and who should fix it.
   - **Accepted rows**: what the platform will use.
4. Correct the exceptions **in your workbook** and upload it again until nothing important is set aside.
5. The finance office or an Administrator then clicks **Replace registers**. The previous files are kept as a
   backup. (On the public demo nothing is saved.)
"""),
    ("🛠️ Fix a row that was set aside (owners named on the Data quality page)", """
1. **Data quality → Exceptions** lists every row left out of today's figures: register, Excel row, key, the
   field and the problem, and the owner.
2. Correct the value at the source (the workbook or system it came from), then import it again.
3. Until it is fixed, that row is **not** in any figure, and the Overview page shows a warning, so nobody
   relies on an incomplete total without knowing.
"""),
    ("🔎 Find out where a figure came from (audit questions)", """
1. **Data quality → Where does a figure come from?** Pick a dataset, e.g. `output/board_summary`.
2. *Built from* lists every source behind it; *Feeds* lists everything a wrong source would affect.
"""),
    ("📅 Look at another date (Finance office, Administrator)", """
Change **As of** in the left sidebar. Alerts, arrears, overdue dividends and "next 30 days" are recalculated
as if today were that date. Other roles always see the official date.
"""),
    ("👥 Give a colleague access (Administrator)", """
Ask them to create an account; they start as *Board (read-only)*. Then open **Users**, pick them and choose
their role. The person who sets roles shouldn't also approve transactions (segregation of duties).
"""),
]

GLOSSARY = """
| Term | Meaning |
|---|---|
| Cash vs accrual | Interest *received* this year vs interest *earned* this year, whenever it's paid |
| Maturity alert (30/60/90) | An instrument matures within that many days: time to decide whether to reinvest |
| Register | A list the desk maintains: one row per bond, deposit, loan, rate or dividend |
| Data contract | The agreed rules a register's rows must follow (required fields, types, allowed values) |
| Exception | A row set aside because it breaks its contract; left out of every figure until corrected |
| Cleaning | Safe formatting fixes with one possible meaning; every fix is logged |
| Scorecard | The data-quality checks run every time: fresh, valid, on budget, behaving as usual |
| Lineage | The record of which sources fed which figure |
| RWF m | Rwandan francs, millions |
"""


def help_content(demo: bool) -> None:
    st.header("📖 How to use the platform")
    st.markdown("""
**Start here (two minutes)**
1. Choose a page in the **left sidebar**. You only see the pages your role needs.
2. Under each page title, open **ℹ️ What does this page show** for the terms on that page.
3. Coloured banners matter: **red** = a data check failed, treat the figures with care; **yellow** = a decision
   is coming (a negative cash month, a late payer).
4. Tables have a download button or icon: everything opens in Excel.
""")
    if demo:
        st.info("**Public demo:** the data is synthetic, nothing you upload is saved, and accounts are removed "
                "whenever the app restarts. To see every page, create accounts with different roles.")
    st.subheader("Which page answers which question?")
    st.dataframe(PAGE_GUIDE, hide_index=True, width="stretch")
    st.subheader("Step by step")
    for title, body in TASKS:
        with st.expander(title):
            st.markdown(body)
    st.subheader("Words used in the platform")
    st.markdown(GLOSSARY)


# ------------------------------------------------------------------ pages for signed-in users
def page_home(p: dict, user: dict) -> None:
    home_content()


def page_help(p: dict, user: dict) -> None:
    help_content(settings()["demo_mode"])


def page_overview(p: dict, user: dict) -> None:
    st.header("Portfolio overview")
    explain("""
- **Interest income, cash:** interest actually *received* this year.
- **Interest income, accrual:** interest *earned* this year, whenever it is paid. A deposit placed in June and
  paid back next June earned half of its interest this year, but pays it next year.
- **Collections next 30 days:** coupons, interest, instalments and maturities due in the next 30 days.
- **Closing cash, December:** the cash balance at year end if every maturing investment is reinvested.
- **Maturity alerts:** instruments maturing within 90, 60 or 30 days: time to decide whether to reinvest.
- A red banner means the data-quality checks found a problem: treat the figures with care until it's fixed.
""")
    b = p["board"].set_index("measure")["value"]
    c = st.columns(4)
    c[0].metric("Interest income 2026, cash (RWF m)", f"{b.iloc[0]:,.1f}")
    c[1].metric("Interest income 2026, accrual (RWF m)", f"{b.iloc[1]:,.1f}")
    c[2].metric("Collections next 30 days (RWF m)", f"{b.iloc[4]:,.1f}")
    c[3].metric("Closing cash, December (RWF m)", f"{b.iloc[3]:,.1f}")
    breaches = p["scorecard"][p["scorecard"].status == "BREACH"]
    if len(breaches):
        st.error(f"{len(breaches)} data-quality breach(es): figures below may not be reliable. "
                 + "; ".join(f"{r.name} ({r.value})" for r in breaches.itertuples()))
    exceptions = p.get("exceptions", pd.DataFrame())
    if len(exceptions):
        st.warning(f"{exceptions[['register', 'key']].drop_duplicates().shape[0]} register row(s) were set aside "
                   "and are left out of every figure until corrected: see Data quality → Exceptions.")
    income = p["income"].groupby("asset_class")[["cash", "accrual"]].sum().div(1e6).round(1)
    income.index = income.index.str.replace("_", " ")
    st.subheader("Interest income by asset class (RWF m)")
    st.bar_chart(income, stack=False)
    alerts = p["status"][p["status"].alert != ""]
    st.subheader(f"Maturity alerts ({len(alerts)})")
    st.dataframe(alerts[["instrument_id", "counterparty", "maturity_date", "days_to_maturity", "alert",
                         "next_amount_rwf_m"]], hide_index=True, width="stretch")


def page_cashflow(p: dict, user: dict) -> None:
    st.header("Master cash-flow plan")
    explain("""
- One column per month, all amounts in **RWF millions**.
- **Income rows:** interest and coupons expected from each asset class.
- **Principal maturing / Reinvested:** on a *rollover basis* every investment that matures is assumed to be
  reinvested straight away, so it appears as money in and the same money out.
- **Debt service:** the fund's own borrowing being repaid.
- **Closing balance:** cash at the end of the month. A negative balance means the plan doesn't work as it
  stands: some maturities must be kept as cash instead of being reinvested.
""")
    m = p["master"]
    st.line_chart(m.loc["Closing balance"].rename("Closing balance (RWF m)"))
    if (m.loc["Closing balance"] < 0).any():
        first = m.loc["Closing balance"][m.loc["Closing balance"] < 0].index[0]
        st.warning(f"The plan runs out of cash in **{first}**. Decide which maturities to keep as cash.")
    st.dataframe(m, width="stretch")
    st.download_button("Download as CSV (opens in Excel)", m.to_csv().encode(), "master_cashflow.csv")


def page_instruments(p: dict, user: dict) -> None:
    st.header("Instruments")
    explain("""
- One row per live instrument: its **next payment** (date and amount in RWF millions), **maturity date** and
  **days to maturity**.
- **Alert:** 30, 60 or 90 when maturity is that close.
- **Look one up** reads a single instrument from the *online store*: a small database built for instant
  lookups by a screen or an application, refreshed after every pipeline run.
""")
    s = p["status"]
    classes = st.multiselect("Asset class", sorted(s.asset_class.unique()), default=sorted(s.asset_class.unique()))
    st.dataframe(s[s.asset_class.isin(classes)], hide_index=True, width="stretch")
    st.subheader("Look one up (online store)")
    pick = st.selectbox("Instrument", s.instrument_id)
    row = serving.lookup(Path(p["output_dir"]) / "online_store.db", pick)
    st.json(row)


def page_collections(p: dict, user: dict) -> None:
    st.header("Collections")
    explain("""
- **Expected in the next 30/60/90 days:** all cash due to the fund in that window.
- **Budget vs actual:** what was scheduled each month against what actually arrived. Months more than 10%
  below budget are highlighted. Look at the next month before reacting: a late payment shows as a shortfall
  followed by a surplus (timing), a missed payment doesn't come back (arrears).
- **Arrears / recovery / bank concentration** appear once those requirements are implemented in the platform.
""")
    c = st.columns(3)
    for col, days in zip(c, (30, 60, 90)):
        col.metric(f"Expected in the next {days} days (RWF m)", f"{p['upcoming'][days]:,.1f}")
    st.subheader("Budget vs actual (RWF m)")
    v = p["variance"]
    st.bar_chart(v[["budget", "actual"]], stack=False)
    st.dataframe(v.style.apply(lambda r: ["background-color: #f8d7da" if r.flag else "" for _ in r], axis=1),
                 width="stretch")
    for name, title in (("loan_arrears", "Subsidiary loan arrears"), ("recovery_rates", "Receivables recovery"),
                        ("bank_concentration", "Bank concentration")):
        if name in p["extensions"]:
            st.subheader(title)
            st.dataframe(p["extensions"][name], hide_index=True, width="stretch")
    if not p["extensions"]:
        st.info("Arrears, recovery rates and bank concentration appear here once they are implemented "
                "(fundplatform/extensions.py).")


def page_quality(p: dict, user: dict) -> None:
    st.header("Data quality and lineage")
    explain("""
- **Freshness:** how old the newest data is. Exchange rates should be entered at least weekly.
- **Contract problems:** rows that break the agreed rules for a register (wrong type, missing field, duplicate).
- **Collections below budget:** months more than 10% short.
- **Drift (PSI):** whether behaviour has changed, here how late payers are compared with last year.
  Below 0.1 stable, 0.1–0.25 moderate, above 0.25 significant.
- **Exceptions:** register rows set aside because they break their contract, with the owner who should
  correct them. They are left out of every figure until then.
- **Automatic fixes:** formatting the platform cleaned on the way in (e.g. "10.5%" → 0.105), before and after.
- **Lineage:** pick any dataset to see what it was built from (*upstream*) and what it feeds (*downstream*).
  If a source turns out to be wrong, downstream shows every report to correct.
- **Last pipeline run:** each step, how long it took and whether it succeeded.
""")
    st.dataframe(p["scorecard"], hide_index=True, width="stretch")
    exceptions = p.get("exceptions")
    if exceptions is not None:
        st.subheader(f"Exceptions: rows set aside ({len(exceptions)})")
        if len(exceptions):
            st.dataframe(exceptions, hide_index=True, width="stretch")
        else:
            st.success("None: every register row passed its contract.")
        fixes = pd.DataFrame(p.get("cleaning_log", []))
        with st.expander(f"Automatic fixes in the last run ({len(fixes)})"):
            if len(fixes):
                st.dataframe(fixes, hide_index=True, width="stretch")
            else:
                st.write("None: the registers arrived clean.")
    st.subheader("Where does a figure come from? What does a source affect?")
    datasets = sorted({d["name"] for e in p["lineage"] for d in e["inputs"] + e["outputs"]})
    pick = st.selectbox("Dataset", datasets, index=datasets.index("output/board_summary"))
    a, b = st.columns(2)
    a.write("**Built from (upstream)**")
    a.write(sorted(lineage.upstream(p["lineage"], pick)) or "nothing: it's a source")
    b.write("**Feeds (downstream)**")
    b.write(sorted(lineage.downstream(p["lineage"], pick)) or "nothing: it's a final output")
    st.subheader("Last pipeline run")
    st.dataframe(pd.DataFrame(p["spans"]), hide_index=True, width="stretch")


def page_equity(p: dict, user: dict) -> None:
    st.header("Equity & dividends")
    explain("""
- **Holdings:** the fund's stake in each portfolio company (FR-EQT-001).
- **Projected vs received:** dividends due to the fund this year (its share of what each company declared)
  against what has arrived (FR-EQT-003). **OVERDUE**: the payment date has passed and nothing has arrived.
- **By fiscal year:** a company's dividend for fiscal year 2025 is declared and paid in 2026, after its audit.
- **Next three years (baseline):** each company pays what it paid last time. It's the bar any forecasting
  model must beat (FR-EQT-005, Capstone Day 4); a model that can't beat it shouldn't be used.
""")
    eq = p.get("equity", {})
    if equity is None or "dividends" not in eq or "equity_holdings" not in eq:
        st.info("Equity data isn't in this copy of the platform yet. Run `python update_platform.py`, "
                "then restart the app.")
        return
    dividends, holdings = eq["dividends"], eq["equity_holdings"]
    year, as_of = int(p.get("year", pd.Timestamp(p["as_of"]).year)), pd.Timestamp(p["as_of"])
    inc = equity.income(dividends, holdings, year, as_of)
    overdue = inc[inc.status == "OVERDUE"]
    c = st.columns(4)
    c[0].metric("Portfolio companies", f"{len(holdings)}", f"{int(holdings.is_listed.sum())} listed", delta_color="off")
    c[1].metric(f"Dividends due {year} (RWF m)", f"{inc.fund_share_rwf_m.sum():,.1f}")
    c[2].metric(f"Received {year} (RWF m)", f"{inc.received_rwf_m.sum():,.1f}")
    c[3].metric("Overdue (RWF m)", f"{overdue.outstanding_rwf_m.sum():,.1f}", f"{len(overdue)} companies",
                delta_color="off")
    if len(overdue):
        st.warning("Late payers: " + "; ".join(f"{r.company_name} ({r.outstanding_rwf_m:,.1f} m, "
                                                f"{int(r.days_overdue)} days)" for r in overdue.itertuples()))
    st.subheader(f"Dividends {year}: projected vs received (RWF m)")
    st.dataframe(inc, hide_index=True, width="stretch")
    st.subheader("Dividend income by fiscal year (RWF m)")
    by_year = equity.dividends_by_year(dividends)
    st.bar_chart(by_year[["declared_rwf_m", "received_rwf_m"]], stack=False)
    st.subheader("Next three years: the 'same as last year' baseline (RWF m)")
    base = equity.baseline_forecast(dividends, holdings)
    st.caption("Total per year: " + " · ".join(f"{c} {base[c].sum():,.1f}" for c in base.columns if c.startswith("FY")))
    st.dataframe(base, hide_index=True, width="stretch")
    with st.expander(f"Holdings ({len(holdings)} companies)"):
        st.dataframe(holdings, hide_index=True, width="stretch")


def page_import(p: dict, user: dict) -> None:
    st.header("Import data from Excel or CSV")
    explain("""
- Upload a **workbook** with one sheet per register (*Term Deposits*, *Treasury Bonds*, *Subsidiary Loans*,
  *FX Rates*, *Dividends*…) or one **CSV** file. Sheet and column names are matched ignoring capitals,
  spaces and symbols: "Deposit ID" → `deposit_id`.
- **Fixes made automatically:** formatting with only one meaning: thousands separators, "10.5%" → 0.105,
  dates such as 15/10/2026 (read day first), stray spaces, empty and TOTAL rows. Every fix is listed.
- **Set aside (exceptions):** anything that needs judgement is never guessed: a rate typed as 10.8, a bank
  that isn't approved, a missing date, a duplicate. Each row shows its Excel row number and who should fix it.
- **Nothing is used until it is applied.** Checking an import changes nothing; only the finance office or an
  Administrator can replace a register, and the previous version is kept as a backup.
""")
    if ingest is None:
        st.info("Importing isn't in this copy of the platform yet. Run `python update_platform.py`, then restart.")
        return
    cfg = settings()
    sample = Path(p["data_dir"]) / "sample_import.xlsx"
    upload = st.file_uploader("Workbook (.xlsx) or CSV file", type=["xlsx", "csv"])
    if sample.exists() and st.button("Try it with the sample workbook (messy on purpose)"):
        st.session_state.import_sample = True
    if upload is not None:
        st.session_state.import_sample = False
    source = upload if upload is not None else (sample if st.session_state.get("import_sample") else None)
    if source is None:
        st.info("Upload a file, or try the sample workbook to see cleaning and exceptions at work.")
        return

    name = getattr(source, "name", str(source))
    if name.lower().endswith(".csv"):
        names = [n for _, (n, _) in registry.REGISTERS.items()]
        guess = ingest.register_for(name)
        register = st.selectbox("Which register is this file?", names, index=names.index(guess) if guess else 0)
        raw, ignored = {register: pd.read_csv(source, dtype=object)}, []
    else:
        raw, ignored = ingest.read_workbook(source)
    if ignored:
        st.caption("Sheets that don't match a register (ignored): " + ", ".join(ignored))
    if not raw:
        st.error("No sheet matches a register. Rename the sheets after the registers, e.g. Term Deposits, FX Rates.")
        return

    log = []
    cleaned = {r: ingest.clean(df, r, log) for r, df in raw.items()}
    accepted, exceptions = ingest.quarantine(cleaned, approved={"term_deposits": {"bank": pipeline.APPROVED_BANKS}})
    fixes = pd.DataFrame(log, columns=["register", "excel_row", "column", "before", "after", "rule"])
    summary = pd.DataFrame([{"register": r, "rows in file": len(raw[r]),
                             "fixes made": int((fixes.register == r).sum()),
                             "set aside": int(exceptions[exceptions.register == r].key.nunique()),
                             "accepted": len(accepted.get(r, cleaned[r]))} for r in raw])
    st.subheader(f"Checked: {name}")
    st.dataframe(summary, hide_index=True, width="stretch")

    for tab, r in zip(st.tabs([f"{r} ({int(summary.loc[summary.register == r, 'set aside'].iloc[0])} set aside)"
                               for r in raw]), raw):
        with tab:
            ex = exceptions[exceptions.register == r]
            st.markdown("**Set aside: correct these at the source and import again**")
            if len(ex):
                st.dataframe(ex.drop(columns=["register"]), hide_index=True, width="stretch")
            else:
                st.success("Nothing set aside." if r in ingest.CHECKED else
                           "This register has no data contract yet, so rows are cleaned but not checked.")
            st.markdown(f"**Fixes made automatically ({int((fixes.register == r).sum())})**")
            st.dataframe(fixes[fixes.register == r].drop(columns=["register"]), hide_index=True, width="stretch")
            rows = accepted.get(r, cleaned[r])
            st.markdown(f"**Accepted rows ({len(rows)})**")
            st.dataframe(rows, hide_index=True, width="stretch")
            a, b = st.columns(2)
            a.download_button("Download accepted rows (CSV)", rows.to_csv(index=False, date_format="%Y-%m-%d").encode(),
                              f"{r}_accepted.csv", key=f"acc_{r}")
            b.download_button("Download exceptions (CSV)", ex.to_csv(index=False).encode(), f"{r}_exceptions.csv",
                              key=f"exc_{r}")

    st.subheader("Use the accepted rows")
    data_dir = Path(p["data_dir"]).resolve()
    if cfg["demo_mode"]:
        st.info("Public demo: nothing is saved. Download the accepted rows and the exceptions above.")
    elif user["role"] not in CAN_REPLACE_REGISTERS:
        st.info("Only the finance office or an Administrator can replace a register. Share the exceptions with "
                "their owners, or ask the finance office to apply this import.")
    elif HERE.resolve() not in [data_dir, *data_dir.parents]:
        st.info(f"The data folder ({data_dir}) isn't part of this platform (here, the course materials), so it "
                "is read-only. At work, set `data_dir` in config.yaml to your own folder next to app.py.")
    else:
        files = {n: f for f, (n, _) in registry.REGISTERS.items()}
        pick = st.multiselect("Registers to replace with the accepted rows", list(accepted))
        sure = st.checkbox("I have reviewed the exceptions. Rows set aside will NOT be included.")
        if st.button("Replace registers", disabled=not (pick and sure), type="primary"):
            backup = data_dir / "_backup" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup.mkdir(parents=True)
            for r in pick:
                target = data_dir / files[r]
                if target.exists():
                    shutil.copy2(target, backup / target.name)
                accepted[r].to_csv(target, index=False, date_format="%Y-%m-%d")
            st.cache_data.clear()
            st.success(f"Replaced {', '.join(pick)}. The previous files are in {backup}. Every page now uses "
                       "the new data.")


def page_users(p: dict, user: dict) -> None:
    st.header("Users and roles")
    explain("Everyone who has an account, and their role. The role decides which pages a person sees. "
            "The person who sets up roles shouldn't also approve transactions (segregation of duties).")
    with db() as con:
        users = pd.read_sql("SELECT name, email, role, created FROM users ORDER BY created", con)
    st.dataframe(users, hide_index=True, width="stretch")
    st.subheader("Change a role")
    email = st.selectbox("User", users.email)
    role = st.selectbox("New role", list(ROLES))
    if st.button("Save role"):
        if email == user["email"] and role != "Administrator":
            st.error("You can't remove your own Administrator role.")
        else:
            with db() as con:
                con.execute("UPDATE users SET role = ? WHERE email = ?", (role, email))
            st.success(f"{email} is now {role}. They see the change the next time they sign in.")


PAGES = {"Home": page_home, "How to use": page_help, "Overview": page_overview, "Cash-flow plan": page_cashflow,
         "Instruments": page_instruments, "Collections": page_collections, "Equity & dividends": page_equity,
         "Data quality": page_quality, "Import data": page_import, "Users": page_users}


# ------------------------------------------------------------------ sign in / sign up (public landing page)
def sign_in_page() -> None:
    demo = settings()["demo_mode"]
    st.markdown(CSS, unsafe_allow_html=True)
    if demo:
        st.info("**Public demo.** Create an account with any role to explore what that role sees. All figures "
                "are synthetic. Accounts are deleted whenever the app restarts.")
    tab_home, tab_help, tab_in, tab_up = st.tabs(["🏠 Home", "📖 How to use", "🔑 Sign in", "✨ Create account"])
    with tab_help:
        help_content(demo)
    with tab_in, st.form("signin"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in"):
            user = check_login(email, password)
            if user:
                st.session_state.user = user
                st.rerun()
            st.error("Email or password not recognised.")
    with tab_up, st.form("signup"):
        name = st.text_input("Full name")
        email = st.text_input("Work email")
        password = st.text_input("Password (at least 8 characters)", type="password")
        demo_role = st.selectbox("Role to explore", [r for r in ROLES if r != "Administrator"]) if demo else None
        if st.form_submit_button("Create account"):
            if len(name.strip()) < 2 or "@" not in email or len(password) < 8:
                st.error("Enter your name, a valid email and a password of 8+ characters.")
            else:
                try:
                    role = create_user(name, email, password, demo_role)
                    st.success(f"Account created with the role **{role}**. Sign in on the 🔑 Sign in tab.")
                except sqlite3.IntegrityError:
                    st.error("An account with this email already exists.")
    with tab_home:
        home_content()
    footer()


def main() -> None:
    st.set_page_config(page_title="Treasury & Investment Platform · Horizon Development Fund",
                       page_icon="📊",
                       layout="wide")
    user = st.session_state.get("user")
    if not user:
        sign_in_page()
        return
    cfg = settings()
    allowed = [pg for pg in ROLES.get(user["role"], ROLES["Board (read-only)"])
               if not (cfg["demo_mode"] and pg == "Users")]          # never list emails on a public demo
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown(f'<div class="brand">{logo("horizon_logo.svg", 40)}<div><b>{cfg["org_name"]}</b></div></div>',
                    unsafe_allow_html=True)
        st.write(f"**{user['name']}**  \n{user['role']}")
        if user["role"] in CAN_CHANGE_DATE:
            as_of = str(st.date_input("As of", pd.Timestamp(cfg["as_of"])))
        else:
            as_of = str(cfg["as_of"])
            st.caption(f"As of {as_of}")
        page = st.radio("Page", allowed, index=allowed.index("Overview") if "Overview" in allowed else 0)
        st.caption("New here? Open **How to use**.")
        if st.button("Sign out"):
            del st.session_state["user"]
            st.rerun()
        st.divider()
        if cfg["demo_mode"]:
            st.caption("Public demo · synthetic data")
        st.markdown(f'<div class="brand">{logo("kainetics_mark.svg", 28)}<span class="small">Built with '
                    f'Kainetics Labs</span></div>', unsafe_allow_html=True)
        st.link_button("📚 Courses with labs & certification", cfg["learn_url"], width="stretch")
        st.link_button("🛠️ Work with us", cfg["contact_url"], width="stretch")
    if page != "Home":
        brand_header()
    PAGES[page](platform(as_of), user)
    footer()


main()
