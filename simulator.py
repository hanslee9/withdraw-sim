"""
withdraw-sim : 장기투자 + 정기 인출(Withdrawal) 시뮬레이터 핵심 로직
====================================================================

전체 흐름 (AAPL 예시 기준)
--------------------------
1. 분석 시작일 / 종료일을 입력받는다.
   - 입력한 시작일보다 종목 상장일이 늦으면 상장일을 시작일로 사용한다.
   - 종료일 기본값은 데이터가 존재하는 가장 최근 거래일이다.
2. yfinance로 수정주가(Adjusted Close)를 수집한다.
3. 원종목(Buy & Hold) 기준 CAGR, MDD를 계산한다. (벤치마크 지표)
4. 매년 인플레이션율을 수집하고, 그 해 원리금을 인플레이션율만큼 낮춰
   다음 해 투자 원금으로 이월한다.
5. 매년 수익률 반영 후 잔고에서 인출금을 뺀다. 인출금도 매년 인플레이션율만큼
   증액한다.
6. (핵심 규칙) 그 해 수익금액이 인출 목표액보다 작으면 수익금액만 인출하고,
   수익이 마이너스면 인출하지 않는다(원금 훼손 방지).
7. 최종적으로 End Value, 총수익률, 연환산 복리수익률(CAGR)을 산출한다.

주의
----
- 이 모듈은 로컬 개발 환경(Anthropic 샌드박스)에서는 외부 금융 데이터
  도메인(finance.yahoo.com, fred.stlouisfed.org 등)에 대한 네트워크 접근이
  막혀 있어 실제 호출 테스트를 하지 못했다. Streamlit Cloud 등 실제
  배포 환경에서는 정상적으로 인터넷에 접근 가능하므로 문제없이 동작해야
  하지만, 배포 후 반드시 1회 실제 데이터로 검증이 필요하다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yfinance as yf
import requests


# ---------------------------------------------------------------------------
# 1~2. 주가 데이터 수집
# ---------------------------------------------------------------------------

def resolve_and_fetch_prices(
    ticker: str,
    start_date: dt.date,
    end_date: dt.date | None = None,
) -> pd.Series:
    """
    수정주가(Adjusted Close)를 수집한다.
    - start_date보다 실제 상장일이 늦으면 자동으로 상장일부터 데이터가 온다
      (yfinance 특성상 없는 구간은 그냥 반환되지 않으므로 별도 clamp 불필요).
    - end_date가 None이면 최신 거래일까지 수집한다.

    Returns
    -------
    pd.Series : index=거래일(DatetimeIndex), value=수정종가
    """
    if end_date is None:
        end_date = dt.date.today()

    tk = yf.Ticker(ticker)
    # auto_adjust=True -> Close 컬럼 자체가 배당/액면분할 반영된 수정주가
    hist = tk.history(
        start=start_date.isoformat(),
        end=(end_date + dt.timedelta(days=1)).isoformat(),
        auto_adjust=True,
    )

    if hist.empty:
        raise ValueError(f"'{ticker}' 데이터를 가져오지 못했습니다. 티커를 확인하세요.")

    price = hist["Close"].copy()
    price.index = price.index.tz_localize(None)
    price.name = ticker
    return price


def get_effective_start_date(price: pd.Series, requested_start: dt.date) -> dt.date:
    """실제 사용된 시작일(상장일 clamp 여부 확인용)을 반환."""
    actual_first = price.index[0].date()
    return actual_first if actual_first > requested_start else requested_start


# ---------------------------------------------------------------------------
# 3. 성과 지표: CAGR, MDD
# ---------------------------------------------------------------------------

def calc_cagr(begin_value: float, end_value: float, years: float) -> float:
    """연환산 복리수익률(CAGR). years는 소수 연 단위(예: 3.5년)."""
    if begin_value <= 0 or years <= 0:
        return float("nan")
    return (end_value / begin_value) ** (1 / years) - 1


def calc_mdd(value_series: pd.Series) -> float:
    """
    Maximum Drawdown. value_series는 시간순 잔고/가격 시계열.
    반환값은 음수(예: -0.35 = 최대 -35% 낙폭).
    """
    cummax = value_series.cummax()
    drawdown = value_series / cummax - 1
    return drawdown.min()


def buy_and_hold_metrics(price: pd.Series) -> dict:
    """원종목 Buy & Hold 기준 CAGR / MDD (벤치마크 지표)."""
    begin, end = price.iloc[0], price.iloc[-1]
    years = (price.index[-1] - price.index[0]).days / 365.25
    return {
        "start_price": begin,
        "end_price": end,
        "years": years,
        "cagr": calc_cagr(begin, end, years),
        "mdd": calc_mdd(price),
    }


# ---------------------------------------------------------------------------
# 4. 연도별 수익률 / 인플레이션 데이터 가공
# ---------------------------------------------------------------------------

def compute_year_fractions(price: pd.Series) -> pd.Series:
    """
    연도별 '보유 비율'을 계산한다. 중간 연도는 1.0, 첫 해/마지막 해는
    실제 보유 일수 / 365.25 로 프로레이션한다 (인출금액 계산에 사용).
    """
    idx = price.index
    years = sorted(set(idx.year))
    actual_start = idx[0].date()
    actual_end = idx[-1].date()

    fractions = {}
    for y in years:
        year_start = dt.date(y, 1, 1)
        year_end = dt.date(y, 12, 31)
        eff_start = max(year_start, actual_start)
        eff_end = min(year_end, actual_end)

        if len(years) > 1 and years[0] < y < years[-1]:
            frac = 1.0
        else:
            frac = (eff_end - eff_start).days / 365.25
            frac = max(0.0, min(frac, 1.0))
        fractions[y] = frac

    return pd.Series(fractions, name="year_fraction")


def get_annual_returns(price: pd.Series) -> pd.Series:
    """
    연도별 수익률을 계산한다.
    각 연도의 '연말가(또는 분석 종료일가)'를 기준으로 전년 대비 수익률을 구한다.
    첫 해는 (실제 시작일 가격) 대비 (해당 연도 12/31 또는 데이터 마지막일) 가격.
    """
    yearly_last = price.groupby(price.index.year).last()

    # 시작연도의 '전년도 기준값'은 실제 분석 시작일 가격
    start_price = price.iloc[0]
    years = yearly_last.index.tolist()

    returns = {}
    prev_value = start_price
    for y in years:
        cur_value = yearly_last.loc[y]
        returns[y] = cur_value / prev_value - 1
        prev_value = cur_value
    return pd.Series(returns, name="stock_return")


def fetch_us_annual_inflation(years: list[int]) -> pd.Series:
    """
    FRED(CPIAUCSL, 비계절조정 CPI)를 이용해 연도별 인플레이션율(전년 대비, %)을 계산한다.
    미국 종목(AAPL 등) 기준. 실패 시 예외를 던지므로 UI에서 수동 입력 대체 필요.
    """
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()

    from io import StringIO

    df = pd.read_csv(StringIO(resp.text))
    df.columns = ["date", "cpi"]
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    annual_cpi = df.groupby("year")["cpi"].mean()  # 연평균 CPI

    inflation = {}
    all_years = sorted(set(years) | {min(years) - 1})
    for y in years:
        if y in annual_cpi.index and (y - 1) in annual_cpi.index:
            inflation[y] = annual_cpi.loc[y] / annual_cpi.loc[y - 1] - 1
        else:
            inflation[y] = np.nan
    return pd.Series(inflation, name="inflation")


# ---------------------------------------------------------------------------
# 5~7. 인출 시뮬레이션
# ---------------------------------------------------------------------------

@dataclass
class WithdrawSimResult:
    table: pd.DataFrame
    end_value: float
    total_return: float
    cagr: float
    years: float


def simulate_withdrawal(
    annual_returns: pd.Series,
    annual_inflation: pd.Series,
    initial_investment: float,
    initial_withdrawal: float,
    year_fractions: pd.Series | None = None,
) -> WithdrawSimResult:
    """
    연도별 시뮬레이션.

    규칙
    ----
    - balance_after_return = balance_start * (1 + 그해 주가수익률)
    - profit = balance_after_return - balance_start
    - full_withdrawal_target: 만약 해당 연도를 '온전히 1년' 보유했다면 인출했을 금액.
      첫해는 initial_withdrawal, 이후 매년 그 해 인플레이션율만큼 증액.
    - withdrawal_target(실제 인출 목표액) = full_withdrawal_target * year_fraction
      (첫해/마지막해처럼 실제 보유 일수가 1년 미만이면 비율만큼 축소, 중간 연도는 1.0)
    - 실제 인출액:
        profit >= withdrawal_target  -> withdrawal_target 전액 인출
        0 < profit < withdrawal_target -> profit 만큼만 인출
        profit <= 0                  -> 인출하지 않음(0)
    - balance_after_withdrawal = balance_after_return - actual_withdrawal
    - 다음 해로 넘어갈 때 원리금을 그 해 인플레이션율만큼 하향 조정(실질가치 반영):
        balance_next_start = balance_after_withdrawal / (1 + 그해 인플레이션율)
    """
    years = annual_returns.index.tolist()
    if year_fractions is None:
        year_fractions = pd.Series({y: 1.0 for y in years})

    rows = []

    balance_start = initial_investment
    full_withdrawal_target = initial_withdrawal

    for i, y in enumerate(years):
        r = annual_returns.loc[y]
        infl = annual_inflation.loc[y] if y in annual_inflation.index else 0.0
        if pd.isna(infl):
            infl = 0.0
        frac = year_fractions.loc[y] if y in year_fractions.index else 1.0

        if i > 0:
            full_withdrawal_target = full_withdrawal_target * (1 + infl)

        withdrawal_target = full_withdrawal_target * frac

        balance_after_return = balance_start * (1 + r)
        profit = balance_after_return - balance_start

        if profit <= 0:
            actual_withdrawal = 0.0
        elif profit < withdrawal_target:
            actual_withdrawal = profit
        else:
            actual_withdrawal = withdrawal_target

        balance_after_withdrawal = balance_after_return - actual_withdrawal
        balance_next_start = balance_after_withdrawal / (1 + infl)

        rows.append({
            "year": y,
            "stock_return": r,
            "inflation": infl,
            "year_fraction": frac,
            "balance_start": balance_start,
            "balance_after_return": balance_after_return,
            "profit": profit,
            "withdrawal_target": withdrawal_target,
            "actual_withdrawal": actual_withdrawal,
            "balance_after_withdrawal": balance_after_withdrawal,
            "balance_next_start": balance_next_start,
        })

        balance_start = balance_next_start

    table = pd.DataFrame(rows).set_index("year")

    end_value = table["balance_after_withdrawal"].iloc[-1]
    total_return = end_value / initial_investment - 1
    n_years = len(years)
    cagr = calc_cagr(initial_investment, end_value, n_years)

    return WithdrawSimResult(
        table=table,
        end_value=end_value,
        total_return=total_return,
        cagr=cagr,
        years=n_years,
    )


# ---------------------------------------------------------------------------
# 8. 주식계좌 + 현금버퍼계좌 (Two-Bucket) 인출 시뮬레이션
# ---------------------------------------------------------------------------

@dataclass
class TwoBucketResult:
    table: pd.DataFrame
    end_value: float          # 주식계좌 + 버퍼계좌 합산 최종 잔고
    total_return: float
    cagr: float
    years: float
    total_shortfall: float    # 목표 인출액을 다 채우지 못한 누적 부족액 (0이면 정상)


def simulate_two_bucket_withdrawal(
    annual_returns: pd.Series,
    annual_inflation: pd.Series,
    stock_initial: float,
    buffer_initial: float,
    buffer_rate: float,
    initial_withdrawal: float,
    alpha: float,
    year_fractions: pd.Series | None = None,
) -> TwoBucketResult:
    """
    주식계좌 + 현금버퍼계좌(SGOV류 단기채 ETF 가정, 고정 이율) 인출 시뮬레이션.

    용어
    ----
    W        : 실제 필요 인출금액(생활비, 인플레이션·프로레이션 반영, 사용자에게 실제 지출)
    alpha(α) : 여유 인출 배수. 주식계좌 수익이 충분한 해에는 W*alpha 만큼 인출을 시도해서,
               W는 지출하고 나머지(W*(alpha-1))는 버퍼계좌로 이체(리필)한다.

    연도별 처리 순서
    ----------------
    1. 주식계좌 수익(profit) 계산 (수익률 반영 후 잔고 - 반영 전 잔고)
    2. profit >= W*alpha  : 주식계좌에서 W*alpha 인출 -> W는 지출, 나머지는 버퍼 입금
       0 < profit < W*alpha:
           profit >= W  -> W는 지출, 나머지(profit-W)는 버퍼 입금
           profit <  W  -> profit 전액 지출로 사용(주식계좌 인출은 profit까지만),
                            부족분(W-profit)은 버퍼계좌에서 추가 인출 시도
       profit <= 0       : 주식계좌 인출 없음. W 전액을 버퍼계좌에서 인출 시도
    3. 버퍼계좌는 매년 먼저 고정 이율(buffer_rate)로 이자가 붙은 뒤, 위 입금/출금이 반영된다.
    4. 버퍼계좌 잔고가 부족하면 인출 가능한 만큼만 인출한다 (그 해 실제 인출액이 W보다 작아짐,
       shortfall로 기록 - 자동으로 더 개입하지 않고 화면에 표시만 함. 실전에서는 수동 대응 필요).
    5. 두 계좌 모두, 그 해 처리가 끝난 잔고를 인플레이션율만큼 나눠 다음 해로 이월한다
       (주식계좌·버퍼계좌 동일 기준).
    """
    years = annual_returns.index.tolist()
    if year_fractions is None:
        year_fractions = pd.Series({y: 1.0 for y in years})

    rows = []

    stock_balance = stock_initial
    buffer_balance = buffer_initial
    full_w_target = initial_withdrawal
    total_shortfall = 0.0

    for i, y in enumerate(years):
        r = annual_returns.loc[y]
        infl = annual_inflation.loc[y] if y in annual_inflation.index else 0.0
        if pd.isna(infl):
            infl = 0.0
        frac = year_fractions.loc[y] if y in year_fractions.index else 1.0

        if i > 0:
            full_w_target = full_w_target * (1 + infl)

        w_target = full_w_target * frac
        stock_target = w_target * alpha

        # --- 주식계좌 ---
        stock_after_return = stock_balance * (1 + r)
        profit = stock_after_return - stock_balance

        if profit >= stock_target:
            stock_withdrawal = stock_target
            spend_from_stock = w_target
            buffer_deposit = stock_target - w_target
            shortfall_before_buffer = 0.0
        elif profit > 0:
            stock_withdrawal = profit
            if profit >= w_target:
                spend_from_stock = w_target
                buffer_deposit = profit - w_target
                shortfall_before_buffer = 0.0
            else:
                spend_from_stock = profit
                buffer_deposit = 0.0
                shortfall_before_buffer = w_target - profit
        else:
            stock_withdrawal = 0.0
            spend_from_stock = 0.0
            buffer_deposit = 0.0
            shortfall_before_buffer = w_target

        stock_after_withdrawal = stock_after_return - stock_withdrawal

        # --- 버퍼계좌: 이자 -> 입금 -> (부족분) 출금 ---
        buffer_after_interest = buffer_balance * (1 + buffer_rate)
        buffer_after_deposit = buffer_after_interest + buffer_deposit

        buffer_withdrawal = min(shortfall_before_buffer, max(buffer_after_deposit, 0.0))
        buffer_after_withdrawal = buffer_after_deposit - buffer_withdrawal

        unmet_shortfall = shortfall_before_buffer - buffer_withdrawal
        total_shortfall += unmet_shortfall

        actual_total_withdrawal = spend_from_stock + buffer_withdrawal

        # --- 다음 해 이월 (인플레이션 디플레이터, 양 계좌 동일 기준) ---
        stock_next = stock_after_withdrawal / (1 + infl)
        buffer_next = buffer_after_withdrawal / (1 + infl)

        rows.append({
            "year": y,
            "stock_return": r,
            "inflation": infl,
            "year_fraction": frac,
            "w_target": w_target,
            "stock_target": stock_target,
            "stock_balance_start": stock_balance,
            "stock_after_return": stock_after_return,
            "stock_profit": profit,
            "stock_withdrawal": stock_withdrawal,
            "buffer_deposit": buffer_deposit,
            "buffer_balance_start": buffer_balance,
            "buffer_after_interest": buffer_after_interest,
            "buffer_withdrawal": buffer_withdrawal,
            "actual_total_withdrawal": actual_total_withdrawal,
            "unmet_shortfall": unmet_shortfall,
            "stock_balance_after_withdrawal": stock_after_withdrawal,
            "buffer_balance_after_withdrawal": buffer_after_withdrawal,
            "total_balance_after_withdrawal": stock_after_withdrawal + buffer_after_withdrawal,
            "stock_balance_next_start": stock_next,
            "buffer_balance_next_start": buffer_next,
        })

        stock_balance = stock_next
        buffer_balance = buffer_next

    table = pd.DataFrame(rows).set_index("year")

    initial_total = stock_initial + buffer_initial
    end_value = table["total_balance_after_withdrawal"].iloc[-1]
    total_return = end_value / initial_total - 1
    n_years = len(years)
    cagr = calc_cagr(initial_total, end_value, n_years)

    return TwoBucketResult(
        table=table,
        end_value=end_value,
        total_return=total_return,
        cagr=cagr,
        years=n_years,
        total_shortfall=total_shortfall,
    )
