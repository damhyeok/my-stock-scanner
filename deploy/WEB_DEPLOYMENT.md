# Lightweight web deployment

Source/Oracle: `damhyeok/my-stock-scanner` (unchanged history and schedules).
Web-only mirror: `damhyeok/my-stock-scanner-web`, branch `main`, entry `app.py`.

Use Python 3.11 in Streamlit advanced settings. The mirror receives
`requirements-web.txt` as `requirements.txt`; Oracle keeps its original packages.
The publish-web workflow exports tracked runtime files and the bootstrap snapshot,
never source Git history, working DBs, reports, credentials, caches or virtualenvs.
Source code changes automatically publish with a target-repository-only SSH deploy key.
Only main-branch workflows can use the source Actions secret WEB_DEPLOY_SSH_KEY.

Before switching production, test a temporary Streamlit app with the same required
Secrets (copy privately in the dashboard, never into Git): ORACLE_TRIGGER_URL and
ORACLE_TRIGGER_SECRET, plus any existing access-control/GitHub configuration.
Keep GITHUB_REPOSITORY pointing to the original source repository: manual analysis
must still dispatch the original workflow. Watchlist writes stay on the same Oracle
endpoint; do not create a new DB or change collection timers.

Verify all tabs, current snapshot date, watchlist read/write and mobile layout.
Do not delete the original app to free its URL without an explicit migration plan.
If the platform cannot retarget an existing app, keep production online and ask the
owner to approve the URL transition. Rollback is the existing app/source revision.
