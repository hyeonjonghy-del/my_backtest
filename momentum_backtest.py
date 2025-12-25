import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend - NO POPUP WINDOWS
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

# Reduced stock list for faster testing
KOREAN_STOCKS = {
    'SamsungElectronics': '005930.KS',
    'SKhynix': '000660.KS',
    'HyundaiMotor': '005380.KS',
    'NAVER': '035420.KS',
    'Kakao': '035720.KS',
    'SamsungSDI': '006400.KS',
    'LGChem': '051910.KS',
    'KBFinancial': '105560.KS',
    'ShinhanFinancial': '055550.KS',
    'LGEnergySolution': '373220.KS',
}

class VolumeWeightedMomentumStrategy:
    def __init__(self, initial_capital=10000000):
        self.initial_capital = initial_capital
        self.top_n = 5  # Reduced from 10 to 5 for faster testing
        self.rebalance_days = 21
        self.min_market_cap = 5000
        
    def get_korean_stock_data(self, tickers, start_date, end_date):
        """
        Get Korean stock data with timeout protection
        """
        print("Downloading data... (This may take a moment)")
        
        all_prices = pd.DataFrame()
        all_volumes = pd.DataFrame()
        
        for name, ticker in tickers.items():
            try:
                print(f"  Loading {name}...", end=' ', flush=True)
                # Reduced data range for testing
                stock = yf.download(ticker, start=start_date, end=end_date, 
                                  progress=False, timeout=10)  # 10 second timeout
                
                if not stock.empty and len(stock) > 50:  # Ensure enough data
                    all_prices[ticker] = stock['Close']
                    all_volumes[ticker] = stock['Volume']
                    print("OK")
                else:
                    print("Insufficient data")
                    
            except Exception as e:
                print(f"Failed: {str(e)[:50]}...")
                continue
        
        if all_prices.empty:
            print("Warning: No data downloaded")
            # Create dummy data for testing
            dates = pd.date_range(start=start_date, end=end_date, freq='B')
            for name, ticker in tickers.items():
                all_prices[ticker] = np.random.randn(len(dates)).cumsum() + 10000
                all_volumes[ticker] = np.random.randint(100000, 1000000, len(dates))
            all_prices.index = dates
            all_volumes.index = dates
            print("Using simulated data for testing")
        
        return all_prices, all_volumes
    
    def calculate_momentum_score(self, prices, volumes):
        """Calculate volume-weighted momentum score"""
        price_returns = prices.pct_change(periods=21)
        volume_ma = volumes.rolling(window=21).mean()
        volume_momentum = volumes / volume_ma - 1
        volume_momentum = volume_momentum.replace([np.inf, -np.inf], np.nan)
        momentum_scores = price_returns * 0.7 + volume_momentum * 0.3
        return momentum_scores
    
    def select_top_stocks(self, momentum_scores, current_date, prices):
        """Select top performing stocks"""
        if current_date not in momentum_scores.index:
            return []
        
        date_scores = momentum_scores.loc[current_date]
        valid_scores = date_scores.dropna()
        
        if len(valid_scores) == 0:
            return []
        
        top_30_threshold = valid_scores.quantile(0.7)
        high_momentum = valid_scores[valid_scores >= top_30_threshold]
        
        if len(high_momentum) == 0:
            return []
        
        selected = high_momentum.nlargest(min(self.top_n, len(high_momentum)))
        return selected.index.tolist()
    
    def run_backtest(self, start_date='2020-01-01', end_date='2021-12-31'):  # Shorter period
        """
        Run backtest with shorter period
        """
        print(f"\nBacktest Period: {start_date} ~ {end_date}")
        print(f"Initial Capital: {self.initial_capital:,.0f} KRW")
        print(f"Stocks to buy: {self.top_n}")
        print("-" * 40)
        
        # Get data
        prices, volumes = self.get_korean_stock_data(KOREAN_STOCKS, start_date, end_date)
        
        if prices.empty:
            print("No data available. Using simulated data.")
        
        print(f"Data period: {prices.index[0].date()} ~ {prices.index[-1].date()}")
        print(f"Number of trading days: {len(prices)}")
        
        # KOSPI data
        print("\nLoading KOSPI index...")
        try:
            kospi = yf.download('^KS11', start=start_date, end=end_date, 
                              progress=False, timeout=5)
            kospi_returns = kospi['Close'].pct_change() if not kospi.empty else pd.Series()
        except:
            print("KOSPI data unavailable")
            kospi_returns = pd.Series()
        
        # Calculate momentum
        print("Calculating momentum...")
        momentum_scores = self.calculate_momentum_score(prices, volumes)
        
        # Run backtest
        print("Running backtest simulation...")
        dates = prices.index
        portfolio_value = pd.Series(index=dates, dtype=float)
        current_cash = self.initial_capital
        current_positions = {}
        
        # Track progress
        total_days = len(dates)
        progress_interval = max(1, total_days // 10)  # 10 progress updates
        
        for i, date in enumerate(dates):
            # Update portfolio value
            position_value = sum(
                shares * prices.loc[date, ticker]
                for ticker, shares in current_positions.items()
                if ticker in prices.columns and date in prices.index
            )
            portfolio_value[date] = current_cash + position_value
            
            # Show progress
            if i % progress_interval == 0:
                print(f"  Progress: {i+1}/{total_days} days ({((i+1)/total_days*100):.0f}%)")
            
            # Rebalance monthly
            if i >= 22 and (i - 22) % self.rebalance_days == 0:
                # Sell existing
                for ticker, shares in list(current_positions.items()):
                    if ticker in prices.columns and date in prices.index:
                        sell_amount = shares * prices.loc[date, ticker]
                        current_cash += sell_amount
                current_positions.clear()
                
                # Buy new
                selected = self.select_top_stocks(momentum_scores, date, prices)
                if selected:
                    capital_per = current_cash / len(selected)
                    for ticker in selected:
                        if ticker in prices.columns and date in prices.index:
                            shares = int(capital_per / prices.loc[date, ticker])
                            if shares > 0:
                                current_cash -= shares * prices.loc[date, ticker]
                                current_positions[ticker] = shares
        
        # Final liquidation
        last_date = dates[-1]
        for ticker, shares in list(current_positions.items()):
            if ticker in prices.columns and last_date in prices.index:
                current_cash += shares * prices.loc[last_date, ticker]
        
        portfolio_value[last_date] = current_cash
        strategy_returns = portfolio_value.pct_change().fillna(0)
        
        print("\nBacktest simulation completed!")
        return portfolio_value, strategy_returns, pd.DataFrame(), kospi_returns
    
    def analyze_results(self, portfolio_value, strategy_returns, kospi_returns):
        """Analyze results"""
        print("\n" + "="*50)
        print("RESULTS SUMMARY")
        print("="*50)
        
        initial = portfolio_value.iloc[0]
        final = portfolio_value.iloc[-1]
        total_return = (final - initial) / initial * 100
        
        days = (portfolio_value.index[-1] - portfolio_value.index[0]).days
        years = max(days / 365.25, 0.1)
        cagr = (final / initial) ** (1/years) - 1
        
        # Drawdown
        cumulative_max = portfolio_value.expanding().max()
        drawdown = (portfolio_value - cumulative_max) / cumulative_max
        mdd = drawdown.min() * 100
        
        print(f"\nPerformance:")
        print(f"  Initial: {initial:,.0f} KRW")
        print(f"  Final:   {final:,.0f} KRW")
        print(f"  Return:  {total_return:.1f}%")
        print(f"  CAGR:    {cagr*100:.1f}%")
        print(f"  MDD:     {mdd:.1f}%")
        
        if not kospi_returns.empty:
            try:
                kospi_final = (1 + kospi_returns.fillna(0)).cumprod().iloc[-1] - 1
                kospi_cagr = (1 + kospi_final) ** (1/years) - 1
                print(f"\nKOSPI Comparison:")
                print(f"  KOSPI Return: {kospi_final*100:.1f}%")
                print(f"  KOSPI CAGR:   {kospi_cagr*100:.1f}%")
                print(f"  Outperformance: {(cagr - kospi_cagr)*100:.1f}%")
            except:
                pass
        
        return {
            'initial': initial, 'final': final, 
            'return': total_return, 'cagr': cagr, 'mdd': mdd
        }
    
    def save_plot(self, portfolio_value, strategy_returns, kospi_returns):
        """Save plot to file instead of showing"""
        try:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
            
            # Portfolio growth
            portfolio_norm = portfolio_value / portfolio_value.iloc[0]
            ax1.plot(portfolio_norm.index, portfolio_norm.values, 
                    label='Strategy', linewidth=2, color='blue')
            
            if not kospi_returns.empty:
                kospi_norm = (1 + kospi_returns.fillna(0)).cumprod()
                ax1.plot(kospi_norm.index, kospi_norm.values,
                        label='KOSPI', linewidth=2, color='red', alpha=0.7)
            
            ax1.set_title('Portfolio Performance vs KOSPI', fontsize=14)
            ax1.set_xlabel('Date')
            ax1.set_ylabel('Growth (Multiple)')
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            
            # Monthly returns
            monthly_returns = strategy_returns.resample('M').apply(
                lambda x: (1 + x).prod() - 1
            ) * 100
            
            colors = ['green' if x >= 0 else 'red' for x in monthly_returns]
            ax2.bar(range(len(monthly_returns)), monthly_returns.values, color=colors, alpha=0.7)
            ax2.set_title('Monthly Returns (%)', fontsize=14)
            ax2.set_xlabel('Month')
            ax2.set_ylabel('Return (%)')
            ax2.grid(True, alpha=0.3)
            
            plt.tight_layout()
            plt.savefig('backtest_results.png', dpi=100, bbox_inches='tight')
            plt.close(fig)
            
            print("\nChart saved to 'backtest_results.png'")
            
        except Exception as e:
            print(f"\nCould not save chart: {e}")

# Main execution
if __name__ == "__main__":
    print("VOLUME-WEIGHTED MOMENTUM STRATEGY BACKTEST")
    print("=" * 60)
    
    try:
        # Create strategy with smaller parameters
        strategy = VolumeWeightedMomentumStrategy(
            initial_capital=10000000
        )
        
        # Run with SHORTER period for testing
        portfolio_value, strategy_returns, trades_df, kospi_returns = strategy.run_backtest(
            start_date='2022-01-01',  # Recent 2 years
            end_date='2023-12-31'
        )
        
        # Analyze
        results = strategy.analyze_results(portfolio_value, strategy_returns, kospi_returns)
        
        # Save plot
        strategy.save_plot(portfolio_value, strategy_returns, kospi_returns)
        
        print("\n" + "=" * 50)
        print("BACKTEST COMPLETED SUCCESSFULLY!")
        print("=" * 50)
        
    except KeyboardInterrupt:
        print("\n\nBacktest interrupted by user.")
    except Exception as e:
        print(f"\nError during backtest: {e}")
    
    print("\nPress Enter to exit...")
    input()