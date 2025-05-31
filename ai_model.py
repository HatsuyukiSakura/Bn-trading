# ai_model.py

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from xgboost import XGBClassifier
import joblib

class FeatureEngineer:
    def __init__(self):
        self.scaler = StandardScaler()

    def extract_features(self, df):
        df = df.copy()
        df['return'] = df['close'].pct_change()
        df['volatility'] = df['return'].rolling(window=5).std()
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['ma10'] = df['close'].rolling(window=10).mean()
        df['rsi'] = self.compute_rsi(df['close'])
        df = df.dropna()
        features = df[['return', 'volatility', 'ma5', 'ma10', 'rsi']]
        scaled = self.scaler.fit_transform(features)
        return scaled, df

    def compute_rsi(self, series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))

class ModelTrainer:
    def __init__(self):
        self.models = {
            'logistic': LogisticRegression(max_iter=1000),
            'random_forest': RandomForestClassifier(),
            'svm': SVC(probability=True),
            'xgb': XGBClassifier(use_label_encoder=False, eval_metric='logloss')
        }

    def train(self, X, y):
        trained = {}
        for name, model in self.models.items():
            model.fit(X, y)
            trained[name] = model
        return trained

    def save_models(self, trained_models):
        for name, model in trained_models.items():
            joblib.dump(model, f"models/{name}.pkl")

class AIModel:
    def __init__(self):
        self.models = {
            'logistic': joblib.load("models/logistic.pkl"),
            'random_forest': joblib.load("models/random_forest.pkl"),
            'svm': joblib.load("models/svm.pkl"),
            'xgb': joblib.load("models/xgb.pkl")
        }

    def predict_signal(self, features):
        votes = []
        for model in self.models.values():
            pred = model.predict(features[-1].reshape(1, -1))[0]
            votes.append(pred)
        buy_votes = votes.count(1)
        sell_votes = votes.count(-1)
        if buy_votes > sell_votes:
            return "buy"
        elif sell_votes > buy_votes:
            return "sell"
        else:
            return "hold"
