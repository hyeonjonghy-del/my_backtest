# Korea Sector Momentum Strategy

한국 주식시장의 10개 섹터를 대상으로 연 1회 투자 섹터를 선정하고, 월 1회 모멘텀 비중을 조절하는 전략입니다.

## Final rules

1. 섹터별 대표 종목 2개를 동일가중해 섹터 월말 가격지수를 만듭니다.
2. 매년 12월 말 12개월 수익률이 높은 5개 섹터를 다음 해 투자 유니버스로 선정합니다.
3. 매월 말 선정된 5개 섹터의 12개월 수익률과 현금의 0% 수익률을 함께 순위화합니다.
4. 상위 5개 순위에 `45% / 30% / 15% / 5% / 5%`를 배정합니다.
5. 현금이 순위 안에 들면 그 순위의 비중은 현금으로 보유합니다.
6. 별도의 6개월 하락 필터는 사용하지 않습니다.
7. 다음 달 첫 거래일에 목표 비중으로 리밸런싱한다고 가정합니다.

## Universe

- 반도체: 삼성전자, SK하이닉스
- 전력인프라: LS ELECTRIC, 두산에너빌리티
- 방산: 한화에어로스페이스, LIG넥스원
- 조선: HD한국조선해양, 삼성중공업
- 바이오: 셀트리온, 한미약품
- 자동차: 현대차, 기아
- 이차전지: LG화학, 삼성SDI
- 인터넷플랫폼: NAVER, 카카오
- 금융: KB금융, 신한지주
- K뷰티 ODM: 코스맥스, 한국콜마

## Backtest assumptions

- KRX 일별 종가를 월말 가격으로 변환합니다.
- 배당금, 세금, ETF 추적오차는 기본적으로 반영하지 않습니다.
- 거래비용은 포트폴리오 회전율에 `0.10%`를 곱해 반영합니다.
- 전략 파라미터는 예시 기본값이며, 과최적화를 피하기 위해 별도 검증 구간을 사용해야 합니다.
- 현재 대표 종목을 과거에 적용하는 방식은 생존자 편향이 있을 수 있습니다.

## Run

```bash
pip install -r requirements.txt
python run.py --start 2017-01-01 --end 2026-08-31
```

The script prints portfolio metrics and writes monthly results to `results/monthly_returns.csv`.

This repository is for research and education, not investment advice.
