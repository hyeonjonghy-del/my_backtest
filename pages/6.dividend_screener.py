"""
배당 종목 스크리너 — Streamlit App
실행: streamlit run dividend_screener.py

필요 라이브러리: pip install streamlit pandas openpyxl plotly
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import io

st.set_page_config(
    page_title="배당 종목 스크리너",
    page_icon="💰",
    layout="wide"
)

# ── 커스텀 CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #FAFAF8; }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    h1 { color: #1A3A2A; font-size: 1.6rem !important; }
    h2, h3 { color: #1A3A2A; }
    .metric-card {
        background: white;
        border: 1px solid #E0EBE4;
        border-radius: 10px;
        padding: 1rem 1.2rem;
        text-align: center;
    }
    .metric-label { font-size: 12px; color: #6B8C7A; margin-bottom: 4px; }
    .metric-value { font-size: 24px; font-weight: 600; color: #1A3A2A; }
    .metric-sub { font-size: 11px; color: #9DB8A8; margin-top: 2px; }
    .tag {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 600;
        margin: 1px;
    }
    .tag-green { background: #E6F4ED; color: #1A7A45; }
    .tag-orange { background: #FFF0E0; color: #C05A00; }
    .tag-red { background: #FDE8E8; color: #C0392B; }
    .stDataFrame { border: 1px solid #E0EBE4; border-radius: 8px; }
    div[data-testid="stSidebar"] { background-color: #F0F7F3; }
    .sidebar-title { font-size: 13px; font-weight: 600; color: #1A3A2A;
                     padding: 6px 0; border-bottom: 1px solid #D0E8DA; margin-bottom: 8px; }
    .stSlider > div > div { background: #00703C; }
</style>
""", unsafe_allow_html=True)

# ── 헤더 ──────────────────────────────────────────────────────────────────────
st.markdown("## 💰 배당 종목 스크리너")
st.markdown("네이버페이 증권 배당 데이터 기반 · 조건을 자유롭게 조절하세요")
st.markdown("---")

# ── 데이터 로드 ───────────────────────────────────────────────────────────────
@st.cache_data
def load_data(file):
    df_raw = pd.read_excel(file, header=None)
    df = df_raw.iloc[2:].copy()
    df.columns = ['종목명','종목코드','현재가','기준월','배당금','수익률',
                   '배당성향','ROE','PER','PBR','1년전배당금','2년전배당금','3년전배당금']
    df = df.reset_index(drop=True)
    num_cols = ['현재가','배당금','수익률','배당성향','ROE','PER','PBR',
                '1년전배당금','2년전배당금','3년전배당금']
    for col in num_cols:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(',','').str.replace('-','').str.strip(),
            errors='coerce'
        )
    # 수정주가 오류 제거
    df = df[df['수익률'] <= 20].copy()
    # 배당 지속성
    df['배당지속3년'] = (
        (df['1년전배당금'] > 0) &
        (df['2년전배당금'] > 0) &
        (df['3년전배당금'] > 0)
    )
    # 3년 배당 CAGR
    mask = (df['3년전배당금'] > 0) & (df['배당금'] > 0)
    df.loc[mask, '3년배당CAGR'] = (
        (df.loc[mask, '배당금'] / df.loc[mask, '3년전배당금']) ** (1/3) - 1
    ) * 100
    df['3년배당CAGR'] = df['3년배당CAGR'].round(1)
    # YoY 성장률
    mask2 = (df['1년전배당금'] > 0) & (df['배당금'] > 0)
    df.loc[mask2, 'YoY성장률'] = (
        (df.loc[mask2, '배당금'] - df.loc[mask2, '1년전배당금'])
        / df.loc[mask2, '1년전배당금'] * 100
    ).round(1)
    return df

# ── 파일 업로드 ───────────────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "📂 네이버 배당 종목 엑셀 파일 업로드",
    type=['xlsx'],
    help="naver_dividend_scraper_v3.py로 수집한 파일을 사용하세요"
)

if uploaded is None:
    st.info("👆 엑셀 파일을 업로드하면 분석이 시작됩니다.")
    st.markdown("""
    **파일 준비 방법:**
    1. `naver_dividend_scraper_v3.py` 실행
    2. 생성된 `네이버_배당종목_v3_YYYYMMDD.xlsx` 업로드
    """)
    st.stop()

df = load_data(uploaded)

# ══════════════════════════════════════════════════════════════════════════════
# ── 사이드바 — 스크리닝 조건 ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("### ⚙️ 스크리닝 조건")

    st.markdown('<div class="sidebar-title">📈 배당 관련</div>', unsafe_allow_html=True)
    div_min   = st.slider("배당수익률 최소 (%)",  0.0, 15.0, 3.0, 0.5)
    div_max   = st.slider("배당수익률 최대 (%)",  0.0, 20.0, 15.0, 0.5)
    pay_min   = st.slider("배당성향 최소 (%)",    0,   100,  10,   5)
    pay_max   = st.slider("배당성향 최대 (%)",    0,   200,  60,   5)
    req_3yr   = st.checkbox("3년 연속 배당 지급 필수", value=True)

    st.markdown('<div class="sidebar-title">📊 수익성·밸류에이션</div>', unsafe_allow_html=True)
    roe_min   = st.slider("ROE 최소 (%)",   0.0, 30.0, 8.0, 0.5)
    per_min   = st.slider("PER 최소 (배)",  0.0, 20.0, 5.0, 0.5)
    per_max   = st.slider("PER 최대 (배)",  0.0, 100.0, 50.0, 1.0)
    pbr_max   = st.slider("PBR 최대 (배)",  0.0, 5.0,  2.0, 0.1)

    st.markdown('<div class="sidebar-title">📅 배당 성장</div>', unsafe_allow_html=True)
    cagr_min  = st.slider("3년 배당 CAGR 최소 (%)", -50.0, 50.0, 0.0, 1.0)

    st.markdown('<div class="sidebar-title">🏆 종합점수 가중치</div>', unsafe_allow_html=True)
    w_yield  = st.slider("배당수익률 가중치", 0, 100, 40, 5)
    w_roe    = st.slider("ROE 가중치",       0, 100, 20, 5)
    w_growth = st.slider("배당성장 가중치",   0, 100, 20, 5)
    w_value  = st.slider("저평가 가중치",     0, 100, 20, 5)

    st.markdown("---")
    st.caption(f"총 합계: {w_yield+w_roe+w_growth+w_value}점 만점")

    # 초기화 버튼
    if st.button("🔄 조건 초기화", use_container_width=True):
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# ── 스크리닝 로직 ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
def apply_screening(df):
    cond = (
        (df['수익률'] >= div_min) &
        (df['수익률'] <= div_max) &
        (df['배당성향'] >= pay_min) &
        (df['배당성향'] <= pay_max) &
        (df['ROE'] >= roe_min) &
        (df['PER'] >= per_min) &
        (df['PER'] <= per_max) &
        (df['PBR'] <= pbr_max)
    )
    if req_3yr:
        cond &= (df['배당지속3년'] == True)
    if cagr_min > -50:
        cond &= (df['3년배당CAGR'].fillna(-999) >= cagr_min)

    result = df[cond].copy()

    # 종합점수 계산
    total_w = w_yield + w_roe + w_growth + w_value
    if total_w == 0: total_w = 100

    def score(row):
        s = 0
        s += min(row['수익률'] / 10 * w_yield, w_yield)
        s += min(row['ROE'] / 20 * w_roe, w_roe)
        if pd.notna(row['3년배당CAGR']) and row['3년배당CAGR'] > 0:
            s += min(row['3년배당CAGR'] / 20 * w_growth, w_growth)
        s += max(0, (pbr_max - row['PBR']) / pbr_max * w_value)
        return round(s / total_w * 100, 1)

    result['종합점수'] = result.apply(score, axis=1)
    return result.sort_values('종합점수', ascending=False).reset_index(drop=True)

screened = apply_screening(df)

# ── 상단 요약 지표 ────────────────────────────────────────────────────────────
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.markdown(f"""<div class="metric-card">
        <div class="metric-label">전체 종목</div>
        <div class="metric-value">{len(df):,}</div>
        <div class="metric-sub">개</div>
    </div>""", unsafe_allow_html=True)
with col2:
    pct = len(screened)/len(df)*100 if len(df) > 0 else 0
    st.markdown(f"""<div class="metric-card">
        <div class="metric-label">스크리닝 통과</div>
        <div class="metric-value" style="color:#00703C">{len(screened):,}</div>
        <div class="metric-sub">{pct:.1f}%</div>
    </div>""", unsafe_allow_html=True)
with col3:
    avg_div = screened['수익률'].mean() if len(screened) > 0 else 0
    st.markdown(f"""<div class="metric-card">
        <div class="metric-label">평균 배당수익률</div>
        <div class="metric-value">{avg_div:.2f}%</div>
        <div class="metric-sub">통과 종목 기준</div>
    </div>""", unsafe_allow_html=True)
with col4:
    avg_roe = screened['ROE'].mean() if len(screened) > 0 else 0
    st.markdown(f"""<div class="metric-card">
        <div class="metric-label">평균 ROE</div>
        <div class="metric-value">{avg_roe:.1f}%</div>
        <div class="metric-sub">통과 종목 기준</div>
    </div>""", unsafe_allow_html=True)
with col5:
    avg_pbr = screened['PBR'].mean() if len(screened) > 0 else 0
    st.markdown(f"""<div class="metric-card">
        <div class="metric-label">평균 PBR</div>
        <div class="metric-value">{avg_pbr:.2f}</div>
        <div class="metric-sub">배</div>
    </div>""", unsafe_allow_html=True)

st.markdown("")

if len(screened) == 0:
    st.warning("⚠️ 조건을 만족하는 종목이 없습니다. 사이드바에서 조건을 완화해보세요.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# ── 탭 구성 ──────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🏆 종합 순위",
    "🛡️ 고배당 안정형",
    "📈 배당 성장형",
    "💎 저평가 배당형",
    "📊 차트 분석"
])

# ── 공통 표시 컬럼 정의 ───────────────────────────────────────────────────────
DISPLAY_COLS = ['종목명','종목코드','현재가','수익률','배당성향','ROE','PER','PBR','3년배당CAGR','종합점수']

def format_df(df_show, cols=DISPLAY_COLS, n=50):
    df_out = df_show[cols].head(n).copy()
    # 포맷팅
    if '현재가' in df_out.columns:
        df_out['현재가'] = df_out['현재가'].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else '-')
    for col in ['수익률','배당성향','ROE']:
        if col in df_out.columns:
            df_out[col] = df_out[col].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else '-')
    for col in ['PER','PBR']:
        if col in df_out.columns:
            df_out[col] = df_out[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else '-')
    if '3년배당CAGR' in df_out.columns:
        df_out['3년배당CAGR'] = df_out['3년배당CAGR'].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else '-')
    if '종합점수' in df_out.columns:
        df_out['종합점수'] = df_out['종합점수'].apply(lambda x: f"{x:.1f}점" if pd.notna(x) else '-')
    return df_out.reset_index(drop=True)
    df_out.index = df_out.index + 1
    return df_out

def download_btn(df_src, label, filename):
    buf = io.BytesIO()
    df_src.to_excel(buf, index=False, engine='openpyxl')
    st.download_button(
        label=f"📥 {label} 다운로드",
        data=buf.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

# ── Tab 1: 종합 순위 ──────────────────────────────────────────────────────────
with tab1:
    col_l, col_r = st.columns([3, 1])
    with col_l:
        st.markdown(f"#### 종합 점수 TOP {min(50, len(screened))}위")
        st.caption("종합점수 = 배당수익률·ROE·배당성장·저평가 가중 합산 (사이드바 가중치 조절 가능)")
    with col_r:
        download_btn(screened, "전체 결과", "배당스크리너_결과.xlsx")

    df_show = format_df(screened)
    st.dataframe(df_show, use_container_width=True, height=500)

# ── Tab 2: 고배당 안정형 ──────────────────────────────────────────────────────
with tab2:
    stable_cond = (screened['수익률'] >= 5) & (screened['배당성향'] <= 50)
    stable = screened[stable_cond].sort_values('수익률', ascending=False).reset_index(drop=True)

    col_l, col_r = st.columns([3,1])
    with col_l:
        st.markdown(f"#### 고배당 안정형 — {len(stable)}개")
        st.caption("수익률 5% 이상 + 배당성향 50% 이하 — 높은 배당을 지속 가능하게 지급하는 종목")
    with col_r:
        download_btn(stable, "고배당 안정형", "배당_안정형.xlsx")

    if len(stable) == 0:
        st.info("조건을 완화하면 더 많은 종목이 표시됩니다.")
    else:
        cols_stable = ['종목명','종목코드','현재가','수익률','배당성향','ROE','PER','PBR','3년배당CAGR','종합점수']
        st.dataframe(format_df(stable, cols_stable), use_container_width=True, height=450)

        # 수익률 분포 바 차트
        st.markdown("##### 배당수익률 순위")
        fig = px.bar(
            stable.head(20),
            x='종목명', y='수익률',
            color='PBR',
            color_continuous_scale='Greens_r',
            labels={'수익률': '배당수익률 (%)', '종목명': ''},
            text=stable.head(20)['수익률'].apply(lambda x: f"{x:.1f}%")
        )
        fig.update_layout(
            height=320, showlegend=True,
            plot_bgcolor='white', paper_bgcolor='white',
            xaxis_tickangle=-35, font_size=12,
            coloraxis_colorbar_title="PBR"
        )
        fig.update_traces(textposition='outside')
        st.plotly_chart(fig, use_container_width=True)

# ── Tab 3: 배당 성장형 ────────────────────────────────────────────────────────
with tab3:
    growth_cond = (screened['3년배당CAGR'].fillna(-999) >= 10) & (screened['수익률'] >= 2)
    growth = screened[growth_cond].sort_values('3년배당CAGR', ascending=False).reset_index(drop=True)

    col_l, col_r = st.columns([3,1])
    with col_l:
        st.markdown(f"#### 배당 성장형 — {len(growth)}개")
        st.caption("3년 배당 CAGR 10% 이상 + 수익률 2% 이상 — 배당이 꾸준히 성장하는 종목")
    with col_r:
        download_btn(growth, "배당성장형", "배당_성장형.xlsx")

    if len(growth) == 0:
        st.info("사이드바에서 3년 배당 CAGR 조건을 낮추거나 수익률 조건을 완화해 보세요.")
    else:
        cols_growth = ['종목명','종목코드','현재가','수익률','3년배당CAGR','YoY성장률','배당성향','ROE','PBR','종합점수']
        avail = [c for c in cols_growth if c in growth.columns]
        df_g = growth[avail].head(50).copy()
        for col in ['수익률','배당성향','ROE']:
            if col in df_g.columns:
                df_g[col] = df_g[col].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else '-')
        if '3년배당CAGR' in df_g.columns:
            df_g['3년배당CAGR'] = df_g['3년배당CAGR'].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else '-')
        if 'YoY성장률' in df_g.columns:
            df_g['YoY성장률'] = df_g['YoY성장률'].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else '-')
        if '현재가' in df_g.columns:
            df_g['현재가'] = df_g['현재가'].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else '-')
        if 'PBR' in df_g.columns:
            df_g['PBR'] = df_g['PBR'].apply(lambda x: f"{x:.2f}" if pd.notna(x) else '-')
        if '종합점수' in df_g.columns:
            df_g['종합점수'] = df_g['종합점수'].apply(lambda x: f"{x:.1f}점" if pd.notna(x) else '-')
        df_g = df_g.reset_index(drop=True)
        st.dataframe(df_g, use_container_width=True, height=450)

        # 배당 성장 추이 (TOP 10)
        st.markdown("##### TOP 10 종목 배당금 3년 추이")
        top10_g = growth.head(10)
        fig2 = go.Figure()
        for _, row in top10_g.iterrows():
            years = ['3년전','2년전','1년전','올해']
            vals  = [row['3년전배당금'], row['2년전배당금'], row['1년전배당금'], row['배당금']]
            if any(pd.notna(v) for v in vals):
                fig2.add_trace(go.Scatter(
                    x=years, y=vals,
                    mode='lines+markers',
                    name=row['종목명'],
                    line=dict(width=2),
                    marker=dict(size=6)
                ))
        fig2.update_layout(
            height=300,
            plot_bgcolor='white', paper_bgcolor='white',
            xaxis_title='', yaxis_title='배당금 (원)',
            legend=dict(font_size=11),
            font_size=12
        )
        st.plotly_chart(fig2, use_container_width=True)

# ── Tab 4: 저평가 배당형 ──────────────────────────────────────────────────────
with tab4:
    value_cond = (screened['PBR'] <= 0.5) & (screened['수익률'] >= 3)
    value = screened[value_cond].sort_values('수익률', ascending=False).reset_index(drop=True)

    col_l, col_r = st.columns([3,1])
    with col_l:
        st.markdown(f"#### 저평가 배당형 — {len(value)}개")
        st.caption("PBR 0.5 이하 + 수익률 3% 이상 — 자산 대비 저평가된 배당주")
    with col_r:
        download_btn(value, "저평가배당형", "배당_저평가형.xlsx")

    if len(value) == 0:
        st.info("사이드바에서 PBR 최대값을 높이거나 수익률 조건을 낮춰보세요.")
    else:
        cols_value = ['종목명','종목코드','현재가','수익률','ROE','PER','PBR','배당성향','3년배당CAGR','종합점수']
        st.dataframe(format_df(value, cols_value), use_container_width=True, height=450)

        # 버블 차트: PBR vs 수익률
        st.markdown("##### PBR vs 배당수익률 분포 (버블 크기 = ROE)")
        fig3 = px.scatter(
            value.head(30),
            x='PBR', y='수익률',
            size='ROE', color='3년배당CAGR',
            hover_name='종목명',
            color_continuous_scale='RdYlGn',
            labels={'수익률':'배당수익률 (%)', 'PBR':'PBR (배)', '3년배당CAGR':'3년 배당CAGR(%)'},
            size_max=30
        )
        fig3.update_layout(
            height=350, plot_bgcolor='white', paper_bgcolor='white',
            font_size=12
        )
        st.plotly_chart(fig3, use_container_width=True)

# ── Tab 5: 차트 분석 ──────────────────────────────────────────────────────────
with tab5:
    st.markdown("#### 📊 스크리닝 통과 종목 종합 분석")

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("##### 배당수익률 분포")
        fig4 = px.histogram(
            screened, x='수익률', nbins=30,
            color_discrete_sequence=['#00703C'],
            labels={'수익률': '배당수익률 (%)', 'count': '종목 수'}
        )
        fig4.update_layout(height=280, plot_bgcolor='white', paper_bgcolor='white', font_size=12)
        st.plotly_chart(fig4, use_container_width=True)

    with col_b:
        st.markdown("##### ROE vs PBR 분포")
        fig5 = px.scatter(
            screened.head(100), x='PBR', y='ROE',
            color='수익률', hover_name='종목명',
            color_continuous_scale='Greens',
            labels={'ROE':'ROE (%)','PBR':'PBR (배)','수익률':'배당수익률(%)'},
            size_max=12
        )
        fig5.update_layout(height=280, plot_bgcolor='white', paper_bgcolor='white', font_size=12)
        st.plotly_chart(fig5, use_container_width=True)

    col_c, col_d = st.columns(2)
    with col_c:
        st.markdown("##### 배당성향 분포")
        fig6 = px.histogram(
            screened, x='배당성향', nbins=25,
            color_discrete_sequence=['#1D6FA4'],
            labels={'배당성향': '배당성향 (%)', 'count': '종목 수'}
        )
        fig6.update_layout(height=280, plot_bgcolor='white', paper_bgcolor='white', font_size=12)
        st.plotly_chart(fig6, use_container_width=True)

    with col_d:
        st.markdown("##### 종합점수 TOP 20 종목")
        top20 = screened.head(20)
        fig7 = px.bar(
            top20, x='종합점수', y='종목명',
            orientation='h',
            color='수익률',
            color_continuous_scale='Greens',
            labels={'종합점수':'종합점수','종목명':'','수익률':'배당수익률(%)'}
        )
        fig7.update_layout(
            height=380, plot_bgcolor='white', paper_bgcolor='white',
            font_size=11, yaxis={'categoryorder':'total ascending'}
        )
        st.plotly_chart(fig7, use_container_width=True)

    st.markdown("---")
    st.markdown("#### 📋 종목 개별 조회")
    selected = st.selectbox("종목 선택", screened['종목명'].tolist())
    if selected:
        row = screened[screened['종목명'] == selected].iloc[0]
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("배당수익률", f"{row['수익률']:.2f}%")
        c2.metric("배당성향",   f"{row['배당성향']:.1f}%")
        c3.metric("ROE",        f"{row['ROE']:.1f}%")
        c4.metric("PER",        f"{row['PER']:.1f}배")
        c5.metric("PBR",        f"{row['PBR']:.2f}배")
        c6.metric("3년CAGR",    f"{row['3년배당CAGR']:+.1f}%" if pd.notna(row['3년배당CAGR']) else "-")

        # 배당금 추이
        years = ['3년전','2년전','1년전','올해']
        vals  = [row['3년전배당금'], row['2년전배당금'], row['1년전배당금'], row['배당금']]
        fig8 = go.Figure(go.Bar(
            x=years, y=vals,
            marker_color=['#9DB8A8','#6B9E83','#3A7D5A','#00703C'],
            text=[f"{v:,.0f}원" if pd.notna(v) else '-' for v in vals],
            textposition='outside'
        ))
        fig8.update_layout(
            title=f"{selected} 배당금 추이",
            height=280, plot_bgcolor='white', paper_bgcolor='white',
            yaxis_title='배당금 (원)', font_size=12,
            showlegend=False
        )
        st.plotly_chart(fig8, use_container_width=True)
