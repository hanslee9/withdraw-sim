"""
withdraw-sim : 장기투자 + 정기 인출 시뮬레이터 (Streamlit)
"""

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from simulator import (
    resolve_and_fetch_prices,
    get_effective_start_date,
    buy_and_hold_metrics,
    get_annual_returns,
    fetch_us_annual_inflation,
    simulate_withdrawal,
)

st.set_page_config(page_title="withdraw-sim", layout="wide")
st.title("📉 장기투자 + 정기 인출 시뮬레이터")
st.caption("예시: AAPL 장기 보유 중 매년 일정 금액을 인출할 때 원리금 추이 분석")

# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("입력")

    ticker = st.text_input("종목 티커 (예: AAPL, 005930.KS)", value="AAPL")

    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input(
            "분석 시작일",
            value=dt.date(2015, 1, 1),
            max_value=dt.date.today(),
        )
    with col2:
        end_date = st.date_input(
            "분석 종료일 (default=최근 거래일)",
            value=dt.date.today(),
            max_value=dt.date.today(),
        )

    initial_investment = st.number_input(
        "초기 투자원금 ($)", min_value=0.0, value=100000.0, step=1000.0
    )
    initial_withdrawal = st.number_input(
        "매년 인출금액 (첫해 기준, $)", min_value=0.0, value=4000.0, step=500.0
    )

    st.divider()
    manual_inflation = st.checkbox("인플레이션율 수동 입력 (자동 크롤링 실패 시)", value=False)
    manual_rate = None
    if manual_inflation:
        manual_rate = st.number_input("고정 연간 인플레이션율 (%)", value=3.0, step=0.1) / 100

    run = st.button("시뮬레이션 실행", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
if run:
    try:
        with st.spinner("주가 데이터 수집 중..."):
            price = resolve_and_fetch_prices(ticker, start_date, end_date)
            effective_start = get_effective_start_date(price, start_date)

        if effective_start != start_date:
            st.info(f"'{ticker}' 상장일이 입력한 시작일보다 늦어 **{effective_start}** 부터 분석합니다.")

        # --- Buy & Hold 벤치마크 (CAGR, MDD) ---
        bh = buy_and_hold_metrics(price)
        st.subheader("1️⃣ Buy & Hold 벤치마크 (원종목 기준)")
        c1, c2, c3 = st.columns(3)
        c1.metric("분석 기간", f"{bh['years']:.1f}년")
        c2.metric("CAGR", f"{bh['cagr']*100:.2f}%")
        c3.metric("MDD", f"{bh['mdd']*100:.2f}%")

        # --- 연도별 수익률 ---
        annual_returns = get_annual_returns(price)
        years = annual_returns.index.tolist()

        # --- 인플레이션 ---
        st.subheader("2️⃣ 인플레이션 데이터")
        if manual_inflation:
            annual_inflation = pd.Series({y: manual_rate for y in years}, name="inflation")
            st.warning(f"수동 입력값 {manual_rate*100:.1f}% 를 전 기간에 동일 적용했습니다.")
        else:
            try:
                with st.spinner("미국 CPI(인플레이션) 데이터 수집 중 (FRED)..."):
                    annual_inflation = fetch_us_annual_inflation(years)
                if annual_inflation.isna().any():
                    st.warning("일부 연도 인플레이션 데이터를 가져오지 못해 0%로 처리됩니다. 필요시 수동 입력을 사용하세요.")
            except Exception as e:
                st.error(f"인플레이션 자동 수집 실패: {e}\n사이드바에서 수동 입력을 사용해주세요.")
                st.stop()

        st.dataframe(
            (annual_inflation * 100).round(2).rename("인플레이션(%)").to_frame(),
            use_container_width=True,
        )

        # --- 인출 시뮬레이션 ---
        st.subheader("3️⃣ 인출 시뮬레이션 결과")
        result = simulate_withdrawal(
            annual_returns, annual_inflation, initial_investment, initial_withdrawal
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("End Value", f"${result.end_value:,.0f}")
        c2.metric("총 수익률", f"{result.total_return*100:.2f}%")
        c3.metric("연환산 복리수익률 (CAGR)", f"{result.cagr*100:.2f}%")

        display_table = result.table.copy()
        pct_cols = ["stock_return", "inflation"]
        money_cols = [
            "balance_start", "balance_after_return", "profit",
            "withdrawal_target", "actual_withdrawal",
            "balance_after_withdrawal", "balance_next_start",
        ]
        for c in pct_cols:
            display_table[c] = (display_table[c] * 100).round(2)
        for c in money_cols:
            display_table[c] = display_table[c].round(0)

        st.dataframe(display_table, use_container_width=True)

        # --- 잔고 추이 차트 ---
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=result.table.index, y=result.table["balance_after_withdrawal"],
            mode="lines+markers", name="인출 후 잔고",
        ))
        fig.update_layout(
            title="연도별 인출 후 잔고 추이",
            xaxis_title="연도", yaxis_title="잔고 ($)",
            height=450,
        )
        st.plotly_chart(fig, use_container_width=True)

        csv = result.table.to_csv().encode("utf-8-sig")
        st.download_button("결과 CSV 다운로드", csv, file_name=f"{ticker}_withdraw_sim.csv")

    except Exception as e:
        st.error(f"오류 발생: {e}")
else:
    st.info("왼쪽 사이드바에서 값을 입력하고 **시뮬레이션 실행** 버튼을 눌러주세요.")
