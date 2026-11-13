# NBA Prop Scanner — phone-friendly cloud dashboard

## Deploy from Android (Streamlit Community Cloud)

1. Create a **private** GitHub repository named `nba-prop-scanner` (or use a private repo you already control).
2. Upload `app.py`, `requirements.txt`, and `.gitignore` from this package to the repository. **Never upload your API key.**
3. Visit https://share.streamlit.io/ and sign in with GitHub. Choose **Create app** → **Deploy a public app from GitHub** (or your available deployment workflow), choose the repository, branch `main`, and entrypoint `app.py`.
4. In the Streamlit app settings, open **Secrets** and paste exactly:

   ```toml
   PROPLINE_API_KEY = "YOUR_ACTUAL_API_KEY"
   ```

   Save secrets and restart the app. Do not paste the key into chat, GitHub files, or browser URLs.
5. Open your Streamlit app URL in Chrome on Android. Chrome menu → **Add to Home screen** for easy access.
6. Press **Refresh live NBA board**. Use filters and **Download filtered CSV**. The first build does not automatically refresh; each tap consumes an API request.

## Important access/privacy notes

- **A private GitHub repository does not automatically make the deployed Streamlit app private.** Streamlit Community Cloud app URLs may be accessible to anyone who knows the URL. Keep the app's data non-sensitive; the API key remains server-side in Secrets, but an exposed app can still consume your API quota. If you need access-controlled private hosting, use a platform with authentication rather than assuming this URL is private.
- API calls are made from the Streamlit server with the `X-API-Key` header. The key is not sent to the browser or CSV.
- The free PropLine tier allows live odds but restricts some historical/EV endpoints. This build uses live `/v1/sports/basketball_nba/odds` only.
- The scanner does **not** claim to predict winners. It shows real posted lines and compares thresholds. PrizePicks Goblin/Demon payouts must be verified inside PrizePicks; Underdog multipliers and odds are displayed as provided.
- All 23 NBA markets documented as of Nov 12, 2026 are requested. The response may omit unavailable markets.

## Local test (optional)

```bash
pip install -r requirements.txt
export PROPLINE_API_KEY='YOUR_KEY'
streamlit run app.py
```

API documentation: https://prop-line.com/llms-full.txt
