import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


class FourierPredictionService:
    @staticmethod
    def fit_and_predict_fourier_continuous(history_balance, forecast_steps=30, n_harmonics=10):
        values = np.asarray(history_balance, dtype=float)
        values = values[np.isfinite(values)]
        if len(values) < max(30, 2 * n_harmonics + 1):
            n_harmonics = max(1, (len(values) - 1) // 2)
        t = np.arange(len(values), dtype=float)
        period = len(values)
        mean_val = float(np.mean(values))
        detrended = values - mean_val
        cos_coeffs, sin_coeffs = [], []
        for k in range(1, n_harmonics + 1):
            angle = 2 * np.pi * k * t / period
            cos_coeffs.append(float(np.mean(detrended * np.cos(angle))))
            sin_coeffs.append(float(np.mean(detrended * np.sin(angle))))
        t_future = np.arange(period - 1, period + forecast_steps, dtype=float)
        wave = np.zeros_like(t_future)
        for k, (c_k, s_k) in enumerate(zip(cos_coeffs, sin_coeffs), start=1):
            angle = 2 * np.pi * k * t_future / period
            wave += 2 * (c_k * np.cos(angle) + s_k * np.sin(angle))
        pred = mean_val + wave
        if len(pred) > 0 and len(values) > 0:
            pred += values[-1] - pred[0]
            pred[0] = values[-1]
        return pred

    @staticmethod
    def analyze_risk(history, forecast, last_date, reserve_days=7):
        history = np.asarray(history, dtype=float)
        future = np.asarray(forecast[1:], dtype=float)
        current = float(history[-1]) if len(history) > 0 else 0.0
        recent_changes = np.diff(history[-min(30, len(history)):]) if len(history) > 1 else np.array([0.0])
        typical_move = float(np.median(np.abs(recent_changes))) if len(recent_changes) else 0.0
        reserve = max(0.0, typical_move * reserve_days)

        below_zero = np.where(future < 0)[0]
        below_reserve = np.where(future < reserve)[0]
        critical_idx = int(below_zero[0]) if len(below_zero) else (
            int(below_reserve[0]) if len(below_reserve) else None
        )
        min_idx = int(np.argmin(future)) if len(future) > 0 else 0
        min_balance = float(future[min_idx]) if len(future) > 0 else current
        deficit = max(0.0, -min_balance)

        scale = max(typical_move * 10, abs(current) * 0.05, 1.0)
        depth = min(55.0, max(0.0, (reserve - min_balance) / scale * 20.0))
        timing = 0.0 if critical_idx is None else max(0.0, 35.0 * (len(future) - critical_idx) / max(len(future), 1))
        downtrend = float(np.mean(recent_changes)) < 0 if len(recent_changes) else False
        risk_score = int(round(min(100.0, depth + timing + (10.0 if downtrend and min_balance < current else 0.0))))

        if critical_idx is None:
            risk_date = None
            days_to_risk = None
        else:
            days_to_risk = critical_idx + 1
            risk_date = pd.Timestamp(last_date) + pd.Timedelta(days=days_to_risk)

        if min_balance < 0:
            reason = "Прогнозируемый остаток становится отрицательным. Возможен кассовый разрыв: ожидаемые списания могут превысить доступные средства."
        elif critical_idx is not None:
            reason = "Остаток опускается ниже расчётного резерва. Проверьте ближайшие списания и сроки платежей."
        elif downtrend:
            reason = "Баланс положителен, но недавняя динамика показывает снижение. Рекомендуется контролировать крупные списания."
        else:
            reason = "В прогнозном периоде критическое снижение остатка не выявлено."

        level = "высокий" if risk_score >= 70 else "средний" if risk_score >= 40 else "низкий"
        return {
            "risk_score": risk_score,
            "risk_level": level,
            "risk_date": risk_date,
            "days_to_risk": days_to_risk,
            "minimum_forecast_balance": min_balance,
            "estimated_deficit": deficit,
            "reason": reason,
        }

    @staticmethod
    def save_four_graphs(company_id, dates, balances, future_dates, prediction, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        future = prediction[1:] if len(prediction) > 1 else prediction
        fig, axes = plt.subplots(2, 2, figsize=(14, 9))
        fig.suptitle(f"Анализ денежных потоков компании {company_id}", fontsize=14, fontweight="bold")

        # 1. Исторический баланс
        ax = axes[0, 0]
        ax.plot(dates, balances, color="#263238", linewidth=1.5, label="Фактический баланс")
        ax.fill_between(dates, balances, 0, where=np.asarray(balances) >= 0, color="#2e8b57", alpha=0.2, label="Положительный")
        ax.fill_between(dates, balances, 0, where=np.asarray(balances) < 0, color="#d9534f", alpha=0.2, label="Отрицательный")
        ax.axhline(0, color="red", linestyle=":", linewidth=1)
        ax.set_title("1. Фактический баланс")
        ax.set_ylabel("Остаток, ₽")
        ax.legend(fontsize=7)

        # 2. Прогноз баланса (Фурье)
        ax = axes[0, 1]
        ax.plot(dates, balances, color="#333333", label="История")
        if len(future_dates) == len(future):
            ax.plot(future_dates, future, color="#1f77b4", linestyle="--", linewidth=2, label="Прогноз Фурье")
        ax.axhline(0, color="red", linestyle=":", label="Нуль")
        ax.set_title("2. Прогноз баланса (Фурье)")
        ax.legend(fontsize=7)

        # 3. Сравнение прогнозов по разной длине истории
        ax = axes[1, 0]
        hist_lens = [min(90, len(balances)), min(120, len(balances)), min(150, len(balances))]
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c"]
        for h_len, color in zip(hist_lens, colors):
            if h_len >= 30:
                sub_hist = balances[-h_len:]
                pred_sub = FourierPredictionService.fit_and_predict_fourier_continuous(sub_hist, len(future))
                sub_future_dates = pd.date_range(dates[-1] + pd.Timedelta(days=1), periods=len(pred_sub)-1, freq="D")
                if len(sub_future_dates) == len(pred_sub[1:]):
                    ax.plot(sub_future_dates, pred_sub[1:], color=color, linestyle="--", linewidth=1.5, label=f"{h_len} дн. истории")
        ax.set_title("3. Сравнение срез-прогнозов Фурье")
        ax.legend(fontsize=7)

        # 4. Информация о модели
        ax = axes[1, 1]
        ax.set_title("4. Структура сценарного моделирования")
        ax.text(0.5, 0.5, f"Компания: {company_id}\nМодель: Гармоники Фурье (N=10)\nСтатус: Активен", ha="center", va="center", transform=ax.transAxes, fontsize=11, bbox=dict(boxstyle="round,pad=0.5", fc="#252b3b", ec="#38ffffff", alpha=0.8), color="#ffffff")
        ax.grid(False)

        for a in axes.flat:
            if a != axes[1, 1]:
                a.grid(True, linestyle="--", alpha=0.3)
            a.tick_params(axis="x", rotation=20, labelsize=8)
            a.tick_params(axis="y", labelsize=8)

        fig.tight_layout(rect=[0, 0, 1, 0.95])
        chart_file = output_dir / f"four_graphs_{company_id}.png"
        fig.savefig(chart_file, dpi=140)
        plt.close(fig)
        return chart_file