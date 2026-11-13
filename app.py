import os
import pandas as pd
import requests
import streamlit as st
from datetime import datetime, timezone

st.set_page_config(page_title='NBA Prop Scanner', page_icon='🏀', layout='wide')
BASE='https://api.prop-line.com'
MARKETS=['player_points','player_rebounds','player_assists','player_threes','player_steals','player_blocks','player_turnovers','player_points_assists','player_points_rebounds','player_points_rebounds_assists','player_rebounds_assists','player_blocks_steals','player_double_double','player_triple_double','player_field_goals_made','player_field_goals_attempted','player_threes_attempted','player_twos_made','player_twos_attempted','player_free_throws_made','player_free_throws_attempted','player_offensive_rebounds','player_defensive_rebounds']
DFS={'prizepicks','underdog'}
BOOKS=['prizepicks','underdog','draftkings','fanduel','pinnacle','betmgm','betrivers','fanatics']

def secret_key():
    try: return st.secrets.get('PROPLINE_API_KEY','')
    except Exception: return os.getenv('PROPLINE_API_KEY','')

def fetch():
    key=secret_key()
    if not key: raise ValueError('PROPLINE_API_KEY is missing. Add it to Streamlit app secrets.')
    r=requests.get(f'{BASE}/v1/sports/basketball_nba/odds',params={'markets':','.join(MARKETS),'bookmakers':','.join(BOOKS)},headers={'X-API-Key':key},timeout=55)
    if not r.ok:
        if r.status_code==401: raise ValueError('API authentication failed. Check your key in Streamlit secrets.')
        if r.status_code==429: raise ValueError('PropLine rate or daily quota reached. Wait for the reset before refreshing.')
        raise ValueError(f'PropLine returned HTTP {r.status_code}: {r.text[:200]}')
    return r.json(),r.headers.get('X-Daily-Remaining','unknown'),r.headers.get('X-PropLine-Missing-Markets','')

def flatten(payload):
    events=payload if isinstance(payload,list) else payload.get('events',payload.get('data',[]))
    if isinstance(events,dict): events=events.get('events',[])
    rows=[]
    for ev in events:
        if not isinstance(ev,dict): continue
        for book in ev.get('bookmakers',[]):
            bk=book.get('key','')
            for market in book.get('markets',[]):
                mk=market.get('key','')
                for out in market.get('outcomes',[]):
                    side=out.get('name','')
                    if side.lower() not in ('over','under','higher','lower'): continue
                    point=out.get('point')
                    if point is None: continue
                    player=out.get('description') or out.get('player_name') or ''
                    if not player: continue
                    flavor=out.get('dfs_odds_type') or ('standard' if bk=='prizepicks' else 'multiplier' if bk=='underdog' else 'sportsbook')
                    rows.append({'Event ID':str(ev.get('id','')),'Game':f"{ev.get('away_team','')} @ {ev.get('home_team','')}",'Start (UTC)':ev.get('commence_time',''),'Player':player,'Player ID':out.get('player_id') or '', 'Market':mk,'Site':bk,'Direction':'Higher' if side.lower() in ('over','higher') else 'Lower','Line':point,'Type':flavor,'Odds':out.get('price'),'UD multiplier':out.get('payout_multiplier'),'Line gap':out.get('line_gap'),'Updated':book.get('last_update','')})
    df=pd.DataFrame(rows)
    if not df.empty:
        df=df.drop_duplicates(subset=['Event ID','Player','Market','Site','Direction','Line','Type'])
    return df

st.title('🏀 NBA Prop Scanner')
st.caption('Live market research • PrizePicks / Underdog / sportsbook comparison • No automatic bets')
st.info('The API key is read on the server from Streamlit Secrets. It is never requested in the browser or written into the CSV.')
if not secret_key():
    st.warning('Add PROPLINE_API_KEY in your Streamlit Cloud app settings → Secrets, then reboot the app.')
    st.code('PROPLINE_API_KEY = "paste-your-key-in-streamlit-secrets"',language='toml')
    st.stop()
if 'board' not in st.session_state: st.session_state.board=None
if st.button('🔄 Refresh live NBA board',type='primary'):
    with st.spinner('Fetching the full NBA board...'):
        try:
            payload,remaining,missing=fetch()
            st.session_state.board=flatten(payload)
            st.session_state.fetched=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            st.session_state.remaining=remaining
            st.session_state.missing=missing
        except (requests.RequestException,ValueError) as e: st.error(str(e))
df=st.session_state.board
if df is None:
    st.write('Tap **Refresh live NBA board** to load the first results. Each refresh uses an API request.')
    st.stop()
st.caption(f"Fetched {st.session_state.fetched} · API requests remaining today: {st.session_state.remaining}")
if st.session_state.missing: st.warning('Markets absent from response: '+st.session_state.missing)
if df.empty:
    st.warning('No player props returned. This may be a quiet period or the providers may not currently offer the requested markets.')
    st.stop()
st.metric('All returned prop rows',f'{len(df):,}')
a,b,c=st.columns(3)
with a: sites=st.multiselect('Sites',sorted(df['Site'].unique()),default=[x for x in ['prizepicks','underdog'] if x in df['Site'].unique()])
with b: markets=st.multiselect('Stat categories',sorted(df['Market'].unique()),default=sorted(df['Market'].unique()))
with c: dirs=st.multiselect('Direction',['Higher','Lower'],default=['Higher','Lower'])
types=st.multiselect('Line type',sorted(df['Type'].unique()),default=sorted(df['Type'].unique()))
search=st.text_input('Search player','')
filtered=df[df['Site'].isin(sites)&df['Market'].isin(markets)&df['Direction'].isin(dirs)&df['Type'].isin(types)].copy()
if search: filtered=filtered[filtered['Player'].str.contains(search,case=False,regex=False,na=False)]
st.subheader(f'Board — {len(filtered):,} rows')
st.dataframe(filtered,hide_index=True,use_container_width=True,height=440)
st.download_button('⬇️ Download filtered CSV',filtered.to_csv(index=False).encode('utf-8'),file_name='nba_prop_board.csv',mime='text/csv')
st.divider()
st.subheader('Same-player PP vs UD comparison')
pickem=df[df['Site'].isin(DFS)&df['Type'].eq('standard')].copy()
# Underdog's nonstandard multiplier lines are displayed in the main board, not treated as standard.
if not pickem.empty:
    pickem=pickem.sort_values('Updated').drop_duplicates(['Event ID','Player','Market','Direction','Site'],keep='last')
    comp=pickem.pivot_table(index=['Event ID','Player','Market','Direction'],columns='Site',values='Line',aggfunc='first').reset_index()
    if {'prizepicks','underdog'}.issubset(comp.columns):
        comp=comp.dropna(subset=['prizepicks','underdog'])
        comp['PP minus UD']=comp['prizepicks']-comp['underdog']
        comp['Better line']=comp.apply(lambda r: ('PrizePicks' if r['prizepicks']<r['underdog'] else 'Underdog' if r['underdog']<r['prizepicks'] else 'Tie') if r['Direction']=='Higher' else ('PrizePicks' if r['prizepicks']>r['underdog'] else 'Underdog' if r['underdog']>r['prizepicks'] else 'Tie'),axis=1)
        st.caption('Line-only comparison. A better threshold does NOT establish a better payout or positive expected value.')
        st.dataframe(comp,hide_index=True,use_container_width=True)
        st.download_button('⬇️ Download comparison CSV',comp.to_csv(index=False).encode(),file_name='nba_pp_ud_comparison.csv',mime='text/csv')
    else: st.info('No overlapping standard PP/UD lines found in this board.')
else: st.info('No standard PrizePicks/Underdog lines returned.')
st.caption('No projected probabilities or EV are calculated in this version. PrizePicks synthetic +100 prices are not true two-way odds; Goblin/Demon payouts must be checked in the app. Data may lag provider boards.')
