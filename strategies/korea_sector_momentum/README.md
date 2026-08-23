# Korea Sector Momentum Strategy

한국 주식시장의 10개 섹터를 대상으로 연 1회 투자 섹터를 선정하고, 월 1회 모멘텀 비중을 조절하는 전략입니다.

## Rules

1. 섹터별 대표 종목 2개를 동일가중해 섹터 월말 가격지수를 만듭니다.
2. 매년 12월 말 12개월 수익률이 높은 5개 섹터를 다음 해 투자 유니버스로 선정합니다.
3. 매월 말 선정된 5개 섹터를 12개월 수익률 순으로 정렬합니다.
4. 순위별 목표 비중은 `45% / 30% / 15% / 5% / 5%`입니다.
5. 6개월 수익률이 0 이하인 섹터는 해당 월 비중을 0%로 줄이고 현금으로 보유합니다.
6. 다음 달 첫 거래일에 목표 비중으로 리밸런싱한다고 가정합니다.

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

