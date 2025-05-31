# backtester.py

import pandas as pd
from ai_model import FeatureEngineer, AIModel
from trade_manager import simulate_trade

class Backtester:
    def __init__(self, historical_df, strategy_name="AI Ensemble"):
        self.df = historical_df.copy()
        self.strategy = AIModel()
        self.engineer = FeatureEngineer()
        self.strategy_name = strategy_name
        self.trades = []

    def run(self):
        features, _ = self.engineer.extract_features(self.df)
        signals = []
        for row in features:
            signal = self.strategy.predict_signal(row.reshape(1, -1))
            signals.append(signal)
        self.df = self.df.iloc[-len(signals):].copy()
        self.df['signal'] = signals

        entry_price = None
        position = None

        for i, row in self.df.iterrows():
            price = row['close']
            signal = row['signal']

            if position is None and signal in ['buy', 'sell']:
                position = signal
                entry_price = price

            elif position and signal != position:
                pnl = (price - entry_price) if position == 'buy' else (entry_price - price)
                self.trades.append({
                    "entry": entry_price,
                    "exit": price,
                    "side": position,
                    "pnl": pnl,
                    "date": row.name
                })
                entry_price = None
                position = None

        return pd.DataFrame(self.trades)

    def report(self):
        df = pd.DataFrame(self.trades)
        return {
            "total_trades": len(df),
            "total_profit": df["pnl"].sum(),
            "win_rate": (df["pnl"] > 0).mean(),
            "avg_pnl": df["pnl"].mean()
        }

# 用法
# df = pd.read_csv("historical_data.csv")
# backtester = Backtester(df)
# backtester.run()
# print(backtester.report())
