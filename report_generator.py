# report_generator.py

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import os

class ReportGenerator:
    def __init__(self, trade_log_df, report_dir="reports"):
        self.df = trade_log_df.copy()
        self.report_dir = report_dir
        os.makedirs(report_dir, exist_ok=True)

    def summary_stats(self):
        stats = {
            "總交易數": len(self.df),
            "總盈虧": self.df["pnl"].sum(),
            "平均盈虧": self.df["pnl"].mean(),
            "勝率": (self.df["pnl"] > 0).mean(),
            "最大獲利": self.df["pnl"].max(),
            "最大虧損": self.df["pnl"].min(),
        }
        return stats

    def generate_plot(self):
        self.df["累積損益"] = self.df["pnl"].cumsum()
        plt.figure(figsize=(10, 5))
        sns.lineplot(x=self.df["date"], y=self.df["累積損益"])
        plt.xticks(rotation=45)
        plt.title("累積損益走勢")
        plt.tight_layout()
        plot_path = os.path.join(self.report_dir, "cumulative_pnl.png")
        plt.savefig(plot_path)
        return plot_path

    def export_report(self):
        csv_path = os.path.join(self.report_dir, "trade_log.csv")
        self.df.to_csv(csv_path, index=False)
        plot_path = self.generate_plot()
        return {
            "csv": csv_path,
            "plot": plot_path,
            "summary": self.summary_stats()
        }

# 用法：
# from backtester import Backtester
# bt = Backtester(df)
# result_df = bt.run()
# report = ReportGenerator(result_df)
# report.export_report()

