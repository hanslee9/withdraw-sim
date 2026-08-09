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
    compute_year_fractions,
    fetch_us_annual_inflation,
    simulate_withdrawal,
    simulate_two_bucket_withdrawal,
)

st.set_page_config(page_title="withdraw-sim", layout="wide")
st.markdown(
    """
    <style>
    [data-testid="stDataFrame"] * { font-size: 0.82rem !important; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown("### 📉 장기투자 + 정기 인출 시뮬레이터")
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
            min_value=dt.date(1950, 1, 1),
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
    use_buffer = st.checkbox("현금버퍼 계좌 사용 (주식+현금 2-트랙 운용)", value=False)
    stock_ratio_pct = 100.0
    buffer_rate = 0.03
    alpha = 1.0
    if use_buffer:
        stock_ratio_pct = st.slider("주식계좌 비중 (%)", min_value=50, max_value=95, value=80, step=5)
        st.caption(f"주식계좌 ${initial_investment*stock_ratio_pct/100:,.0f} · 버퍼계좌 ${initial_investment*(1-stock_ratio_pct/100):,.0f}")
        buffer_rate_pct = st.number_input(
            "버퍼계좌 이자율 (%, SGOV 등 단기국채 ETF 가정, 고정값)", value=3.0, step=0.1
        )
        buffer_rate = buffer_rate_pct / 100
        extra_pct = st.number_input(
            "주식계좌 추가인출비율 (%) — 여유 있는 해에 이만큼 더 인출해 버퍼계좌를 채움",
            value=30.0, step=5.0,
        )
        alpha = 1 + extra_pct / 100

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

        # --- Buy & Hold 벤치마크 (Start/End Value, 총수익률, CAGR, MDD) ---
        bh = buy_and_hold_metrics(price)
        bh_start_value = initial_investment
        bh_end_value = initial_investment * (bh["end_price"] / bh["start_price"])
        bh_total_return = bh_end_value / bh_start_value - 1

        st.markdown("##### 1️⃣ Buy & Hold 벤치마크 (원종목 기준, 초기 투자원금 적용)")
        r1c1, r1c2, r1c3 = st.columns(3)
        r1c1.metric("Start Value", f"${bh_start_value:,.0f}")
        r1c2.metric("End Value", f"${bh_end_value:,.0f}")
        r1c3.metric("총 수익률", f"{bh_total_return*100:.2f}%")

        r2c1, r2c2, r2c3 = st.columns(3)
        r2c1.metric("분석 기간", f"{bh['years']:.1f}년")
        r2c2.metric("CAGR", f"{bh['cagr']*100:.2f}%")
        r2c3.metric("MDD", f"{bh['mdd']*100:.2f}%")

        # --- 연도별 수익률 및 보유비율(첫해/마지막해 프로레이션용) ---
        annual_returns = get_annual_returns(price)
        year_fractions = compute_year_fractions(price)
        years = annual_returns.index.tolist()

        # --- 인플레이션 (연도별 상세는 생략하고, 시뮬레이션 계산에는 그대로 사용) ---
        if manual_inflation:
            annual_inflation = pd.Series({y: manual_rate for y in years}, name="inflation")
        else:
            try:
                with st.spinner("미국 CPI(인플레이션) 데이터 수집 중 (FRED)..."):
                    annual_inflation = fetch_us_annual_inflation(years)
                if annual_inflation.isna().any():
                    st.warning("일부 연도 인플레이션 데이터를 가져오지 못해 0%로 처리됩니다. 필요시 수동 입력을 사용하세요.")
            except Exception as e:
                st.error(f"인플레이션 자동 수집 실패: {e}\n사이드바에서 수동 입력을 사용해주세요.")
                st.stop()

        avg_inflation = annual_inflation.mean()

        # --- 인출 시뮬레이션 (단일계좌 vs 주식+현금버퍼 2-트랙) ---
        if use_buffer:
            stock_initial = initial_investment * stock_ratio_pct / 100
            buffer_initial = initial_investment * (1 - stock_ratio_pct / 100)

            st.markdown("##### 2️⃣ 인출 시뮬레이션 결과 (주식+버퍼 2-트랙)")
            st.caption(f"평균 인플레이션 {avg_inflation*100:.2f}% · 상세 연도별 데이터는 표에서 확인")
            result = simulate_two_bucket_withdrawal(
                annual_returns, annual_inflation,
                stock_initial, buffer_initial, buffer_rate,
                initial_withdrawal, alpha,
                year_fractions=year_fractions,
            )

            c1, c2, c3 = st.columns(3)
            c1.metric("End Value (합산)", f"${result.end_value:,.0f}")
            c2.metric("총 수익률", f"{result.total_return*100:.2f}%")
            c3.metric("연환산 복리수익률 (CAGR)", f"{result.cagr*100:.2f}%")

            last = result.table.iloc[-1]
            c4, c5 = st.columns(2)
            c4.metric("주식계좌 최종 잔고", f"${last['stock_balance_after_withdrawal']:,.0f}")
            c5.metric("버퍼계좌 최종 잔고", f"${last['buffer_balance_after_withdrawal']:,.0f}")

            total_w_target = result.table["w_target"].sum()
            total_actual = result.table["actual_total_withdrawal"].sum()
            total_from_stock = result.table["stock_withdrawal"].sum()
            total_from_buffer = result.table["buffer_withdrawal"].sum()
            st.caption(
                f"기간 전체 필요 인출금액(누계): **${total_w_target:,.0f}** · "
                f"실제 인출액(누계): **${total_actual:,.0f}** "
                f"(주식계좌 인출 ${total_from_stock:,.0f} / 버퍼계좌 인출 ${total_from_buffer:,.0f})"
            )
            if result.total_shortfall > 1:
                st.warning(
                    f"⚠️ 일부 연도에 주식·버퍼 계좌 모두 잔고가 부족해 목표 인출액을 채우지 못했습니다 "
                    f"(누적 미인출액 ${result.total_shortfall:,.0f}). CSV의 'unmet_shortfall' 컬럼을 확인하세요. "
                    f"자동 보정하지 않으므로 실제 운용 시 수동 대응이 필요합니다."
                )

            # 화면 표시: 사용자 지정 9개 항목(연도 포함 10개)으로 압축.
            # "연말 ○○잔액"은 Summary/그래프와 완전히 동일한 기준(그 해 수익률+인출 반영 후 명목값)을
            # 사용해서, 마지막 행이 Summary의 최종 잔고와 정확히 일치하도록 함 (누계 개념).
            display_table = pd.DataFrame({
                "주식수익률(%)": (result.table["stock_return"] * 100).round(2),
                "인플레이션(%)": (result.table["inflation"] * 100).round(2),
                "연말 주식잔액": result.table["stock_balance_after_withdrawal"].round(0),
                "주가수익": result.table["stock_profit"].round(0),
                "필요인출(A)": result.table["w_target"].round(0),
                "주식인출(B)": result.table["stock_withdrawal"].round(0),
                "버퍼인출(C)": (result.table["buffer_withdrawal"] - result.table["buffer_deposit"]).round(0),
            })
            display_table["B-C"] = (display_table["주식인출(B)"] - display_table["버퍼인출(C)"]).round(0)
            display_table["연말 버퍼잔액"] = result.table["buffer_balance_after_withdrawal"].round(0)

            st.dataframe(display_table, use_container_width=True)
            st.caption("표의 마지막 행 '연말 주식잔액'·'연말 버퍼잔액'은 위 Summary의 최종 잔고와 동일한 값입니다.")

            # --- 잔고 추이 차트 (주식/버퍼/합계 동일 기준) ---
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=result.table.index, y=result.table["stock_balance_after_withdrawal"],
                mode="lines+markers", name="주식계좌 잔고",
            ))
            fig.add_trace(go.Scatter(
                x=result.table.index, y=result.table["buffer_balance_after_withdrawal"],
                mode="lines+markers", name="버퍼계좌 잔고",
            ))
            fig.add_trace(go.Scatter(
                x=result.table.index, y=result.table["total_balance_after_withdrawal"],
                mode="lines+markers", name="합산 잔고", line=dict(dash="dash"),
            ))
            fig.update_layout(
                title="연도별 계좌별 잔고 추이 (인출 후)",
                xaxis_title="연도", yaxis_title="잔고 ($)",
                height=450,
            )
            st.plotly_chart(fig, use_container_width=True)

            csv = result.table.to_csv().encode("utf-8-sig")
            st.download_button("결과 CSV 다운로드", csv, file_name=f"{ticker}_withdraw_sim_2bucket.csv")

        else:
            st.markdown("##### 2️⃣ 인출 시뮬레이션 결과")
            st.caption(f"평균 인플레이션 {avg_inflation*100:.2f}% · 상세 연도별 데이터는 표에서 확인")
            result = simulate_withdrawal(
                annual_returns, annual_inflation, initial_investment, initial_withdrawal,
                year_fractions=year_fractions,
            )

            c1, c2, c3 = st.columns(3)
            c1.metric("End Value", f"${result.end_value:,.0f}")
            c2.metric("총 수익률", f"{result.total_return*100:.2f}%")
            c3.metric("연환산 복리수익률 (CAGR)", f"{result.cagr*100:.2f}%")

            total_target = result.table["withdrawal_target"].sum()
            total_actual = result.table["actual_withdrawal"].sum()
            st.caption(
                f"기간 전체 필요 인출금액(누계): **${total_target:,.0f}** · "
                f"실제 인출금액(누계): **${total_actual:,.0f}** "
                f"(미인출 차액: ${total_target - total_actual:,.0f})"
            )

            display_table = pd.DataFrame({
                "필요인출": result.table["withdrawal_target"],
                "실제인출": result.table["actual_withdrawal"],
                "인출후잔고": result.table["balance_after_withdrawal"],
            }).round(0)

            st.dataframe(display_table, use_container_width=True)
            st.caption("수익률·인플레이션 등 상세 데이터는 아래 CSV 다운로드에 전체 포함되어 있습니다.")

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
