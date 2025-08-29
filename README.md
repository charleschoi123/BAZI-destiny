# Bazi Global — Single File + DeepSeek

**No folders** needed. Upload these files to GitHub and deploy on Render:

- `app.py` (front-end inlined + Flask API + DeepSeek call)
- `requirements.txt`
- `Procfile`
- `README.md` (optional)

## Render
- Python 3.11+
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`
- Environment:
  - `DEEPSEEK_API_KEY=sk-...`
  - (optional) `DEEPSEEK_BASE_URL=https://api.deepseek.com`
  - (optional) `DEEPSEEK_MODEL=deepseek-chat` (or `deepseek-reasoner`)
  - (optional) `AI_SOURCE_LABEL=Alsos NeuroMatch`

The app computes the pillars locally (deterministic) and then sends a compact JSON summary to DeepSeek to generate a friendly English interpretation.
