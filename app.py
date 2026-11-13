
import os
from datetime import datetime, timezone
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='PropLine NBA Scanner', page_icon='🏀', layout='wide')
BASE = 'https://api.prop-line.com'
MARKETS = ['player_points','player_rebounds','player_assists','player_threes','player_steals','player_blocks','player_turnovers','player_points_assists','player_points_rebounds','player_points_rebounds_assists','player_rebounds_assists','player_blocks_steals','player_double_double','player_triple_double','player_field_goals_made','player_field_goals_attempted','player_threes_attempted','player_twos_made','player_twos_attempted','player_free_throws_made','player_free_throws_attempted','player_offensive_rebounds','player_defensive_rebounds']
BOOKS = ['prizepicks','underdog','draftkings','fanduel','pinnacle','betmgm','betrivers','fanatics']
DFS = {'prizepicks','underdog'}


def secret_key():
    try:
        return st.secrets.get('PROPLINE_API_KEY', '') or os.getenv('PROPLINE_API_KEY', '')
    except Exception:
        return os.getenv('PROPLINE_API_KEY', '')


def fetch():
    r = requests.get(f'{BASE}/v1/sports/basketball_nba/odds', params={'markets': ','.join(MARKETS), 'bookmakers': ','.join(BOOKS)}, headers={'X-API-Key': secret_key()}, timeout=55)
    if r.status_code == 401:
        raise ValueError('API authentication failed. Check Streamlit Secrets.')
    if r.status_code == 429:
        raise ValueError('API rate limit reached. Try again after reset.')
    r.raise_for_status()
    return r.json(), r.headers.get('X-Daily-Remaining', 'unknown'), r.headers.get('X-PropLine-Missing-Markets', '')


def flatten(payload):
    events = payload if isinstance(payload, list) else payload.get('events', payload.get('data', []))
    if isinstance(events, dict):
        events = events.get('events', [])
    rows = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        for book in ev.get('bookmakers', []):
            site = str(book.get('key', '')).lower()
            for market in book.get('markets', []):
                for outcome in market.get('outcomes', []):
                    side = str(outcome.get('name', '')).lower()
                    if side not in ('over','under','higher','lower') or outcome.get('point') is None:
                        continue
                    player = outcome.get('description') or outcome.get('player_name')
                    if not player:
                        continue
                    flavor = str(outcome.get('dfs_odds_type') or ('standard' if site == 'prizepicks' else 'multiplier' if site == 'underdog' else 'sportsbook')).lower()
                    rows.append({'Event ID': str(ev.get('id', '')), 'Game': f"{ev.get('away_team', '')} @ {ev.get('home_team', '')}", 'Start (UTC)': ev.get('commence_time', ''), 'Player': player, 'Player ID': outcome.get('player_id') or '', 'Market': market.get('key', ''), 'Site': site, 'Direction': 'Higher' if side in ('over','higher') else 'Lower', 'Line': outcome.get('point'), 'Type': flavor, 'Odds': outcome.get('price'), 'UD multiplier': outcome.get('payout_multiplier'), 'Updated': book.get('last_update', '')})
    df = pd.DataFrame(rows)
    if not df.empty:
        df['Line'] = pd.to_numeric(df['Line'], errors='coerce')
        df = df.dropna(subset=['Line']).drop_duplicates(subset=['Event ID','Player','Market','Site','Direction','Line','Type'])
    return df


def compare_lines(df):
    # Never mix promotional thresholds with standard selections.
    pickem = df[df['Site'].isin(DFS) & df['Type'].eq('standard')].copy()
    if pickem.empty:
        return pd.DataFrame()
    pickem = pickem.sort_values('Updated').drop_duplicates(['Event ID','Player','Market','Direction','Site'], keep='last')
    comp = pickem.pivot_table(index=['Event ID','Game','Player','Market','Direction'], columns='Site', values='Line', aggfunc='first').reset_index()
    if not {'prizepicks','underdog'}.issubset(comp.columns):
        return pd.DataFrame()
    comp = comp.dropna(subset=['prizepicks','underdog']).copy()
    comp['Advantage (units)'] = (comp['underdog'] - comp['prizepicks']).where(comp['Direction'].eq('Higher'), comp['prizepicks'] - comp['underdog']).abs()
    comp['Better line'] = comp.apply(lambda r: 'Tie' if r['prizepicks'] == r['underdog'] else ('PrizePicks' if (r['prizepicks'] < r['underdog']) == (r['Direction'] == 'Higher') else 'Underdog'), axis=1)
    return comp.sort_values('Advantage (units)', ascending=False)


def sportsbook_consensus(df):
    # Informational only: thresholds are not probabilities, and sportsbook vig is not removed.
    books = df[~df['Site'].isin(DFS) & df['Type'].eq('sportsbook')].copy()
    if books.empty:
        return pd.DataFrame()
    books = books.sort_values('Updated').drop_duplicates(['Event ID','Player','Market','Site','Direction'], keep='last')
    return books.groupby(['Event ID','Player','Market','Direction'], as_index=False).agg(**{'Sportsbook median': ('Line','median'), 'Book count': ('Site','nunique')})


def ranked_watchlist(df):
    picks = df[df['Site'].isin(DFS) & df['Type'].eq('standard')].copy()
    if picks.empty:
        return pd.DataFrame()
    picks = picks.sort_values('Updated').drop_duplicates(['Event ID','Player','Market','Site','Direction'], keep='last')
    med = sportsbook_consensus(df)
    if med.empty:
        return pd.DataFrame()
    merged = picks.merge(med, on=['Event ID','Player','Market','Direction'], how='inner')
    # Higher wants lower DFS threshold; Lower wants higher DFS threshold.
    merged['Threshold advantage'] = (merged['Sportsbook median'] - merged['Line']).where(merged['Direction'].eq('Higher'), merged['Line'] - merged['Sportsbook median'])
    merged = merged[(merged['Threshold advantage'] > 0) & (merged['Book count'] >= 2)].copy()
    if merged.empty:
        return merged
    merged['Relative gap %'] = (100 * merged['Threshold advantage'] / merged['Sportsbook median'].clip(lower=0.5)).round(1)
    return merged.sort_values(['Relative gap %','Book count'], ascending=[False,False])

st.title('🏀 PropLine NBA Scanner')
st.caption('Research dashboard · Standard Higher/Lower only in rankings · No automatic bets')
if not secret_key():
    st.error('Add PROPLINE_API_KEY to Streamlit Cloud → App settings → Secrets. Never paste it into GitHub.')
    st.stop()
if 'board' not in st.session_state:
    st.session_state.board = None
if st.button('🔄 Refresh live NBA board', type='primary'):
    with st.spinner('Fetching NBA markets...'):
        try:
            payload, remaining, missing = fetch()
            st.session_state.board = flatten(payload)
            st.session_state.fetched = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            st.session_state.remaining = remaining
            st.session_state.missing = missing
        except (requests.RequestException, ValueError) as e:
            st.error(str(e))
df = st.session_state.board
if df is None:
    st.info('Tap Refresh live NBA board to load data. Each refresh consumes an API request.')
    st.stop()
if df.empty:
    st.warning('No NBA player props returned by the provider.')
    st.stop()
st.caption(f"Fetched {st.session_state.fetched} · Requests remaining: {st.session_state.remaining}")
if st.session_state.missing:
    st.warning('Missing API markets: ' + str(st.session_state.missing))
st.metric('Available rows', f'{len(df):,}')
counts = df.groupby('Site').size().rename('Rows').reset_index()
st.subheader('Provider health')
st.dataframe(counts, hide_index=True, use_container_width=True)
if 'underdog' not in set(df['Site']):
    st.warning('Underdog is missing from the API response. Cannot compare PP vs UD until the provider returns Underdog lines.')
if not any(~df['Site'].isin(DFS)):
    st.warning('No sportsbook lines returned. Cannot create a sportsbook-relative watchlist.')

st.subheader('🔎 Top 10 sportsbook-relative watchlist')
st.caption('Ranks favorable DFS thresholds versus the median sportsbook threshold, with at least two books. NOT projected hit rates, expected value, or validated bets.')
watch = ranked_watchlist(df)
if watch.empty:
    st.info('No qualifying comparisons available. This is not evidence that there are no good bets.')
else:
    show = ['Player','Game','Market','Direction','Site','Line','Sportsbook median','Book count','Threshold advantage','Relative gap %']
    st.dataframe(watch[show].head(10), hide_index=True, use_container_width=True)
    st.download_button('⬇️ Download ranked watchlist', watch.to_csv(index=False), 'nba_watchlist.csv', 'text/csv')

st.subheader('⚖️ PrizePicks vs Underdog standard-line comparison')
comp = compare_lines(df)
if comp.empty:
    st.info('No matching standard lines across both platforms are available.')
else:
    st.dataframe(comp.head(100), hide_index=True, use_container_width=True)
    st.download_button('⬇️ Download PP vs UD comparison', comp.to_csv(index=False), 'nba_pp_ud_comparison.csv', 'text/csv')

st.subheader('📋 Full NBA board')
a,b,c = st.columns(3)
with a:
    sites = st.multiselect('Sites', sorted(df['Site'].unique()), default=sorted(df['Site'].unique()))
with b:
    markets = st.multiselect('Markets', sorted(df['Market'].unique()), default=sorted(df['Market'].unique()))
with c:
    types = st.multiselect('Line types', sorted(df['Type'].unique()), default=['standard'] if 'standard' in df['Type'].values else sorted(df['Type'].unique()))
search = st.text_input('Search player')
filtered = df[df['Site'].isin(sites) & df['Market'].isin(markets) & df['Type'].isin(types)].copy()
if search:
    filtered = filtered[filtered['Player'].str.contains(search, case=False, regex=False, na=False)]
st.dataframe(filtered, hide_index=True, use_container_width=True, height=420)
st.download_button('⬇️ Download filtered CSV', filtered.to_csv(index=False), 'nba_prop_board.csv', 'text/csv')
st.caption('Limitations: No independent player projections, injuries, L5/L10 hit rates, or line movement history are available from this endpoint. A true model-based edge requires additional licensed data. Sportsbook comparisons do not account for vig, push rules, or payout multipliers. Verify every line in the app before placing an entry.')
