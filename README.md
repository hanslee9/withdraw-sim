# withdraw-sim

투자 수익 및 정기 인출(은퇴) 시뮬레이션

## 로직 요약
1. 분석 시작일/종료일 입력 (상장일보다 이르면 상장일부터, 종료일 기본값=최근 거래일)
2. yfinance로 수정주가 수집
3. Buy & Hold 기준 CAGR, MDD 산출
4. 매년 인플레이션율(FRED CPI) 수집 → 원리금을 인플레이션율만큼 낮춰 다음 해로 이월
5. 매년 수익률 반영 후 인출 (인출금액도 매년 인플레이션율만큼 증액)
6. 그 해 수익금액 < 인출목표액이면 수익금액만 인출, 수익이 마이너스면 인출 안 함
7. 최종 End Value, 총수익률, CAGR 산출

## 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## 파일 구조
- `app.py` : Streamlit UI
- `simulator.py` : 시뮬레이션 핵심 로직
- `requirements.txt` : 의존 패키지

## 참고
- 인플레이션 데이터는 현재 미국 CPI(FRED, CPIAUCSL) 기준으로만 구현되어 있습니다. 한국 종목 등 확장 시 별도 데이터 소스 추가가 필요합니다.
- 샌드박스 개발 환경에서는 금융 데이터 도메인 접근이 제한되어 실제 크롤링 테스트를 하지 못했습니다. Streamlit Cloud 배포 후 실제 데이터로 1회 검증이 필요합니다.
