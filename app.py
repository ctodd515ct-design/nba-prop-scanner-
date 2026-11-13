import os
from datetime import datetime, timezone
from io import BytesIO
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='PropLine NBA Scanner v3', page_icon='🏀', layout='wide')
BASE='https://api.prop-line.com'
MARKETS=['player_points','player_rebounds','player_assists','player_threes','player_steals','player_blocks','player_turnovers','player_points_assists','player_points_rebounds','player_points_rebounds_assists','player_rebounds_assists','player_blocks_steals','player_double_double','player_triple_double','player_field_goals_made','player_field_goals_attempted','player_threes_attempted','player_twos_made','player_twos_attempted','player_free_throws_made','player_free_throws_attempted','player_offensive_rebounds','player_defensive_rebounds']
BOOKS=['prizepicks','underdog','draftkings','fanduel','pinnacle','betmgm','betrivers','fanatics']
DFS={'prizepicks','underdog'}
KEY=['Event ID','Player','Market','Site','Direction']

def secret_key():
    try: return st.secrets.get('PROPLINE_API_KEY','') or os.getenv('PROPLINE_API_KEY','')
    except Exception: return os.getenv('PROPLINE_API_KEY','')

def fetch():
    r=requests.get(f'{BASE}/v1/sports/basketball_nba/odds',params={'markets':','.join(MARKETS),'bookmakers':','.join(BOOKS)},headers={'X-API-Key':secret_key()},timeout=55)
    if r.status_code==401: raise ValueError('API authentication failed: check Streamlit Secrets.')
    if r.status_code==429: raise ValueError('API rate limit reached. Try later.')
    r.raise_for_status()
    return r.json(),r.headers.get('X-Daily-Remaining','unknown'),r.headers.get('X-PropLine-Missing-Markets','')

def flatten(payload):
    events=payload if isinstance(payload,list) else payload.get('events',payload.get('data',[]))
    if isinstance(events,dict): events=events.get('events',[])
    rows=[]
    for ev in events:
        if not isinstance(ev,dict): continue
        for book in ev.get('bookmakers',[]):
            site=str(book.get('key','')).lower()
            for market in book.get('markets',[]):
                for outcome in market.get('outcomes',[]):
                    side=str(outcome.get('name','')).lower()
                    if side not in ('over','under','higher','lower') or outcome.get('point') is None: continue
                    player=outcome.get('description') or outcome.get('player_name')
                    if not player: continue
                    flavor=str(outcome.get('dfs_odds_type') or ('standard' if site=='prizepicks' else 'unknown' if site=='underdog' else 'sportsbook')).lower()
                    rows.append({'Event ID':str(ev.get('id','')),'Game':f"{ev.get('away_team','')} @ {ev.get('home_team','')}",'Start (UTC)':ev.get('commence_time',''),'Player':str(player),'Player ID':outcome.get('player_id') or '', 'Market':market.get('key',''),'Site':site,'Direction':'Higher' if side in ('over','higher') else 'Lower','Line':outcome.get('point'),'Type':flavor,'Odds':outcome.get('price'),'UD multiplier':outcome.get('payout_multiplier'),'Updated':book.get('last_update','')})
    df=pd.DataFrame(rows)
    if not df.empty:
        df['Line']=pd.to_numeric(df['Line'],errors='coerce')
        df['Odds']=pd.to_numeric(df['Odds'],errors='coerce')
        df=df.dropna(subset=['Line']).sort_values('Updated').drop_duplicates(KEY+['Line','Type'],keep='last')
    return df

def latest(df):
    return df.sort_values('Updated').drop_duplicates(KEY,keep='last').copy()

def american_prob(odds):
    if pd.isna(odds) or odds==0: return float('nan')
    return 100/(odds+100) if odds>0 else -odds/(-odds+100)

def sportsbook_reference(df):
    books=latest(df[(~df['Site'].isin(DFS)) & (df['Type']=='sportsbook')].copy())
    if books.empty: return pd.DataFrame()
    # A sportsbook's over/under lines should agree; take one threshold per book.
    books=books.sort_values('Updated').drop_duplicates(['Event ID','Player','Market','Site'],keep='last')
    return books.groupby(['Event ID','Player','Market'],as_index=False).agg(**{'Sportsbook median':('Line','median'),'Book count':('Site','nunique')})

def comparisons(df):
    picks=latest(df[(df['Site'].isin(DFS)) & (df['Type']=='standard')].copy())
    if picks.empty: return pd.DataFrame()
    med=sportsbook_reference(df)
    if med.empty: return pd.DataFrame()
    out=picks.merge(med,on=['Event ID','Player','Market'],how='inner')
    out['Threshold advantage']=out.apply(lambda r: (r['Sportsbook median']-r['Line']) if r['Direction']=='Higher' else (r['Line']-r['Sportsbook median']),axis=1)
    out=out[(out['Threshold advantage']>0)&(out['Book count']>=2)].copy()
    if out.empty:return out
    out['Relative gap %']=(100*out['Threshold advantage']/out['Sportsbook median'].clip(lower=.5)).round(1)
    return out.sort_values(['Book count','Threshold advantage'],ascending=[False,False])

def pp_ud(df):
    picks=latest(df[(df['Site'].isin(DFS))&(df['Type']=='standard')].copy())
    if picks.empty:return pd.DataFrame()
    pivot=picks.pivot_table(index=['Event ID','Game','Player','Market','Direction'],columns='Site',values='Line',aggfunc='first').reset_index()
    if not {'prizepicks','underdog'}.issubset(pivot.columns):return pd.DataFrame()
    pivot=pivot.dropna(subset=['prizepicks','underdog']).copy()
    pivot['Better line']=pivot.apply(lambda r: 'Tie' if r['prizepicks']==r['underdog'] else ('PrizePicks' if ((r['prizepicks']<r['underdog'])==(r['Direction']=='Higher')) else 'Underdog'),axis=1)
    pivot['Line gap']=(pivot['prizepicks']-pivot['underdog']).abs()
    return pivot.sort_values('Line gap',ascending=False)

def read_optional(file,required):
    if file is None:return None
    try:
        d=pd.read_csv(file)
        missing=set(required)-set(d.columns)
        if missing: st.error(f'Missing columns in {file.name}: {", ".join(sorted(missing))}');return None
        d['Player']=d['Player'].astype(str).str.strip()
        d['Market']=d['Market'].astype(str).str.strip()
        return d
    except Exception as e:st.error(f'Could not read {file.name}: {e}');return None

def enrich(watch,stats,injuries):
    if watch.empty:return watch
    w=watch.copy()
    w['Evidence']='Threshold only'
    w['Review priority']=w['Book count']*2+w['Threshold advantage'].clip(upper=2)
    if stats is not None:
        cols=['Player','Market','Projection','L5 Hit %','L10 Hit %','Season Hit %','Potential Assists','Rebound Chances','Minutes','Usage %']
        for c in cols[2:]:
            if c not in stats.columns:stats[c]=float('nan')
        s=stats[cols].drop_duplicates(['Player','Market'],keep='last').copy()
        for c in cols[2:]:s[c]=pd.to_numeric(s[c],errors='coerce')
        w=w.merge(s,on=['Player','Market'],how='left')
        w['Projection edge']=w.apply(lambda r: (r['Projection']-r['Line']) if r['Direction']=='Higher' else (r['Line']-r['Projection']),axis=1)
        w['Evidence']=w['Projection'].notna().map({True:'Projection supplied',False:'Threshold only'})
        # No fake probability/confidence: hits are shown as provided, not used as calibrated EV.
        w['Review priority']+=w['Projection edge'].fillna(0).clip(lower=-2,upper=2)*2
    if injuries is not None:
        if 'Status' not in injuries.columns:injuries['Status']=''
        w=w.merge(injuries[['Player','Status']].drop_duplicates('Player'),on='Player',how='left')
        w['Status']=w['Status'].fillna('Unverified')
        w.loc[w['Status'].str.lower().isin(['out','inactive','doubtful']),'Review priority']=-999
    return w.sort_values(['Review priority','Book count'],ascending=[False,False])

st.title('🏀 PropLine NBA Scanner v3')
st.caption('Research dashboard • Standard Higher/Lower • No Goblins • No automated wagering')
if not secret_key():
    st.error('Set PROPLINE_API_KEY in Streamlit Cloud → App settings → Secrets. Never commit secrets to GitHub.')
    st.stop()
for name,default in [('board',None),('history',[]),('fetched',''),('remaining','unknown'),('missing','')]:
    if name not in st.session_state:st.session_state[name]=default
if st.button('🔄 Refresh NBA lines',type='primary'):
    try:
        with st.spinner('Fetching current odds...'):
            payload,remaining,missing=fetch()
            board=flatten(payload)
            if board.empty:st.warning('Provider returned no eligible player prop lines.')
            else:
                now=datetime.now(timezone.utc).isoformat()
                snap=latest(board[board['Site'].isin(DFS)&(board['Type']=='standard')].copy())
                snap['Captured UTC']=now
                st.session_state.history.append(snap)
                st.session_state.history=st.session_state.history[-12:]
                st.session_state.board=board
                st.session_state.fetched=now
                st.session_state.remaining=remaining
                st.session_state.missing=missing
    except (requests.RequestException,ValueError,KeyError) as e:st.error(str(e))
if st.session_state.board is None:
    st.info('Tap Refresh NBA lines to start. Refreshes consume API requests.');st.stop()
df=st.session_state.board
st.caption(f"Last fetch: {st.session_state.fetched} · API requests remaining: {st.session_state.remaining}")
if st.session_state.missing:st.warning(f"Missing provider markets: {st.session_state.missing}")
if df.empty:st.stop()

st.subheader('Provider health')
counts=df.groupby(['Site','Type']).size().rename('Rows').reset_index()
st.dataframe(counts,hide_index=True,use_container_width=True)
if 'underdog' not in set(df['Site']):st.warning('Underdog not returned by the API. This app cannot manufacture missing Underdog lines. Check provider coverage/plan.')
elif not len(df[(df['Site']=='underdog')&(df['Type']=='standard')]):st.warning('Underdog exists, but no lines are labeled standard. Review the provider’s DFS type mapping before comparing.')

with st.expander('Optional verified research data (CSV uploads)',expanded=False):
    st.write('Upload independently sourced player stats/projections and injury status. No stats or projections are fabricated. Keep the files on your phone or computer; uploads last for this app session.')
    st.caption('Stats CSV required columns: Player, Market, Projection. Optional: L5 Hit %, L10 Hit %, Season Hit %, Potential Assists, Rebound Chances, Minutes, Usage %. Hit rates must correspond to the actual line and direction being evaluated; otherwise leave blank.')
    stats_file=st.file_uploader('Player research CSV',type='csv',key='stats')
    st.caption('Injuries CSV required columns: Player, Status (e.g., Active, Questionable, Out). Confirm source timestamps before relying on statuses.')
    injury_file=st.file_uploader('Injury CSV',type='csv',key='injuries')
    st.download_button('Download blank research template',pd.DataFrame(columns=['Player','Market','Projection','L5 Hit %','L10 Hit %','Season Hit %','Potential Assists','Rebound Chances','Minutes','Usage %']).to_csv(index=False),'research_template.csv','text/csv')
stats=read_optional(stats_file,['Player','Market','Projection'])
injuries=read_optional(injury_file,['Player','Status']) if injury_file else None

st.subheader('🔎 Top 10 research watchlist')
st.caption('Priority = sportsbook support + favorable threshold, optionally adjusted by supplied projections. NOT win probability, calibrated confidence, or expected value. Always verify in the pick’em app.')
w=enrich(comparisons(df),stats,injuries)
if w.empty:st.info('No favorable standard pick’em thresholds supported by at least two sportsbooks in this fetch.')
else:
    columns=['Player','Game','Market','Direction','Site','Line','Sportsbook median','Book count','Threshold advantage','Evidence']
    if stats is not None:columns+=['Projection','Projection edge','L5 Hit %','L10 Hit %','Season Hit %','Potential Assists','Rebound Chances','Minutes','Usage %']
    if injuries is not None:columns+=['Status']
    st.dataframe(w[columns].head(10),hide_index=True,use_container_width=True)
    st.download_button('⬇️ Download full ranked watchlist',w.to_csv(index=False),'nba_watchlist_v3.csv','text/csv')

st.subheader('⚖️ PrizePicks vs Underdog')
comparison=pp_ud(df)
if comparison.empty:st.info('No matching standard lines from both platforms. Check provider health above.')
else:
    st.dataframe(comparison.head(100),hide_index=True,use_container_width=True)
    st.download_button('⬇️ Download platform comparison',comparison.to_csv(index=False),'nba_pp_ud_v3.csv','text/csv')

st.subheader('📈 Early-line movement (this app session)')
st.caption('Compares refresh snapshots only while this Streamlit session remains active. Restart/redeployment clears history; this is not a persistent historical feed.')
if len(st.session_state.history)<2:st.info('Refresh again later to compare line changes with the first snapshot.')
else:
    first=st.session_state.history[0].drop_duplicates(KEY).set_index(KEY)
    last=st.session_state.history[-1].drop_duplicates(KEY).set_index(KEY)
    moves=first[['Line']].rename(columns={'Line':'Earlier line'}).join(last[['Line']].rename(columns={'Line':'Latest line'}),how='inner').reset_index()
    moves['Movement']=moves['Latest line']-moves['Earlier line']
    moves=moves[moves['Movement']!=0].sort_values('Movement',key=lambda s:s.abs(),ascending=False)
    if moves.empty:st.info('No observed standard-line movements between snapshots.')
    else:
        st.dataframe(moves,hide_index=True,use_container_width=True)
        st.download_button('⬇️ Download line movements',moves.to_csv(index=False),'nba_line_moves.csv','text/csv')

st.subheader('📋 Full NBA board')
a,b,c=st.columns(3)
with a:sites=st.multiselect('Platforms',sorted(df['Site'].unique()),default=sorted(df['Site'].unique()))
with b:markets=st.multiselect('Markets',sorted(df['Market'].unique()),default=sorted(df['Market'].unique()))
with c:types=st.multiselect('Line types',sorted(df['Type'].unique()),default=['standard'] if 'standard' in set(df['Type']) else sorted(df['Type'].unique()))
search=st.text_input('Player search')
f=df[df['Site'].isin(sites)&df['Market'].isin(markets)&df['Type'].isin(types)].copy()
if search:f=f[f['Player'].str.contains(search,case=False,regex=False,na=False)]
st.dataframe(f,hide_index=True,use_container_width=True,height=420)
st.download_button('⬇️ Download filtered board',f.to_csv(index=False),'nba_prop_board_v3.csv','text/csv')
st.caption('Important: Sportsbook medians are thresholds, not projections. Odds are raw provider values, may be placeholders, and are not used to infer expected value. Push rules, payout multipliers, injuries, and matchups must be verified before betting.')
